"""Decide which primary review inputs can use coverage from a saved branch review."""

import json

from mortis_engine import __version__

from . import engine, planning, settings, source

VERSION = "branch-review/v1"


def decide(snap, options, history, previous, *, connection, profile, graph, decisions, assessments, stopped):
    signature = planning.fingerprint({
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
    if (report.get("workflow_status") != "completed" or report["errors"] or report.get("stage_errors")
            or report["status"] != ("skipped" if saved.get("strategy") == "skipped_unchanged" else "completed")
            or coverage.get("waived") or coverage.get("failed") or not selected or covered != selected):
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
                        "reused_paths": sorted(covered)}
        if not options["incremental"]:
            return {**baseline, "reason": "content_changed"}
        if source.git(repo, env, "merge-base", previous_head, snap.head_sha, stopped=stopped).strip() != previous_head:
            return {**baseline, "reason": "baseline_not_ancestor"}

        before = {path: (old, status) for path, old, status in
                  planning._changes(repo, env, snap.merge_base, previous_head, stopped)}
        changed = {name for path, old, _ in planning._changes(repo, env, previous_head, snap.head_sha, stopped)
                   for name in (path, old) if name is not None}
        reusable = [path for path, old, status in planning._changes(repo, env, snap.merge_base, snap.head_sha, stopped)
                    if path in covered and path not in changed and old not in changed and before.get(path) == (old, status)]
    except ValueError:
        return {**baseline, "reason": "baseline_unavailable"}
    return {**baseline, "strategy": "incremental", "reason": "compatible_ancestor", "reused_paths": reusable}
