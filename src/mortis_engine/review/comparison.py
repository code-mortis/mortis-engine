"""Decide which primary review inputs can use coverage from a saved branch review."""

import json

from mortis_engine import __version__

from . import engine, planning, python_signatures, settings, source
from .errors import ReviewStopped

VERSION = "branch-review/v1"
REUSE_POLICY = "python-caller-signatures/v5"


def decide(snap, options, history, previous, *, connection, profile, graph, decisions, assessments, stopped):
    signature = planning.fingerprint({
        "reuse_policy": REUSE_POLICY,
        "versions": [__version__, planning.VERSION, engine.VERSION, engine.PROMPT_VERSION, engine.SOURCE_CODE_POLICY,
                     engine.SOURCE_TOOL_POLICY],
        "base": snap.base_sha, "merge_base": snap.merge_base, "settings_sha": snap.settings_sha,
        "options": {key: value for key, value in options.items() if key not in settings.COMPARISON_FLAGS},
        "connection": connection, "profile": profile, "graph": graph, "triage": decisions,
        "assessments": assessments, "scan_history": history.previous_ids("scan"),
    })
    full = {"version": VERSION, "signature": signature, "strategy": "full", "reason": "disabled"}
    if not any(options[key] for key in settings.COMPARISON_FLAGS):
        return full
    if graph or options["second_pass"]:
        return {**full, "reason": "full_context_required"}
    if not previous:
        return {**full, "reason": "no_baseline"}

    run_id = previous[-1]
    row = history.row(run_id, "review")
    saved = json.loads(row["plan"]).get("comparison", {})
    if saved.get("version") != VERSION or saved.get("signature") != signature:
        return {**full, "reason": "incompatible_baseline"}
    report = history.report(run_id)
    coverage = report["coverage"]
    covered = {item["path"] for key in ("completed", "reused") for item in coverage.get(key, [])}
    selected = {item["path"] for item in coverage["selected"]}
    affected = report.get("affected_callers")
    if (report.get("workflow_status") != "completed" or report["errors"] or report.get("stage_errors")
            or report["status"] != ("skipped" if saved.get("strategy") == "skipped_unchanged" else "completed")
            or coverage.get("waived") or coverage.get("failed") or covered != selected
            or (not selected and not affected)
            or (affected and affected["status"] not in {"completed", "reused"})):
        return {**full, "reason": "incompatible_baseline"}

    previous_head = report["head_sha"]
    baseline = {**full, "baseline_run_id": run_id, "baseline_head_sha": previous_head}
    repo, env = snap.repo, snap.env
    try:
        source.resolve_commit(repo, env, previous_head)
        if options["skip_unchanged"]:
            trees = source.git(repo, env, "rev-parse", f"{previous_head}^{{tree}}", f"{snap.head_sha}^{{tree}}",
                               stopped=stopped).splitlines()
            if len(trees) == 2 and trees[0] == trees[1]:
                return {**baseline, "strategy": "skipped_unchanged", "reason": "identical_content",
                        "reused_paths": sorted(covered), "reuse_affected_callers": bool(affected)}
        if not options["incremental"]:
            return {**baseline, "reason": "content_changed"}
        if source.git(repo, env, "merge-base", previous_head, snap.head_sha, stopped=stopped).strip() != previous_head:
            return {**baseline, "reason": "baseline_not_ancestor"}

        current = planning._changes(repo, env, snap.merge_base, snap.head_sha, stopped)
        rules, binary = None, None
        if any(status == "D" or status.startswith("R") for _, _, status in current):
            rules = planning.load_rules(repo, env, snap.settings_sha, options, stopped)
            binary = planning._binary_paths(repo, env, snap.merge_base, snap.head_sha, stopped)
            if any(planning.affected_changes(current, options, rules, binary=binary)):
                return {**baseline, "reason": "affected_callers_require_full_review"}
        before = {path: (old, status) for path, old, status in
                  planning._changes(repo, env, snap.merge_base, previous_head, stopped)}
        delta = planning._changes(repo, env, previous_head, snap.head_sha, stopped)
        changed = {name for path, old, _ in delta for name in (path, old) if name is not None}
        reusable = [path for path, old, status in current
                    if path in covered and path not in changed and old not in changed and before.get(path) == (old, status)]
        if reusable:
            # Restoring a deleted dependency can invalidate callers too. Deletions live in
            # affected_callers rather than in the report's primary file coverage.
            reviewed_paths = covered | {item["path"] for item in (affected or {}).get("triggers", ())}
            current_paths = {path for path, _, _ in current}
            current_paths.update(old for _, old, status in current if status.startswith("R"))
            if reviewed_paths - current_paths:
                return {**baseline, "reason": "reverted_paths_require_full_review"}
            modified = [path for path, _, status in delta if status == "M" and python_signatures.is_python(path)]
            candidates = [path for path in reusable if python_signatures.is_python(path)]
            if modified and candidates:
                if rules is None:
                    rules = planning.load_rules(repo, env, snap.settings_sha, options, stopped)
                if binary is None:
                    binary = planning._binary_paths(repo, env, snap.merge_base, snap.head_sha, stopped)
                binary_delta = planning._binary_paths(repo, env, previous_head, snap.head_sha, stopped)
                origins = {path: old for path, old, _ in current}
                eligible = [path for path in modified if all(
                    value not in binary and value not in binary_delta and planning.allowed_path(value, options, rules)
                    for value in (path, origins.get(path), before.get(path, (None, ""))[0]) if value)]
                try:
                    affected_paths = python_signatures.affected(
                        repo, env, previous_head, snap.head_sha, eligible,
                        [path for path in candidates if path not in binary], stopped,
                    )
                except ReviewStopped:
                    raise
                except ValueError:
                    return {**baseline, "reason": "python_caller_inspection_incomplete"}
                reusable = [path for path in reusable if path not in affected_paths]
    except ReviewStopped:
        raise
    except ValueError:
        return {**baseline, "reason": "baseline_unavailable"}
    return {**baseline, "strategy": "incremental", "reason": "compatible_ancestor", "reused_paths": reusable}
