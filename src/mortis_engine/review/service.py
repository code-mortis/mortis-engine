"""Local review workflow from immutable Git inputs through validated findings and history."""

import asyncio
import json
import time
from contextlib import contextmanager
from dataclasses import fields

from mortis_engine.assessment_dispatch import ASSESSMENT_DISPATCH_POLICY, continuous_assessment
from mortis_engine.code_graph.contracts import CodeGraphLimits
from mortis_engine.findings import effective_report
from mortis_engine.findings import snapshot as triage_snapshot
from mortis_engine.history import History
from mortis_engine.security_scans.payload import excluded_source_paths
from mortis_engine.workflow import Session

from . import assessment, context, engine, file_policy, planning, settings, snapshots, source, workflows


def scope_ref(repo, env, ref):
    resolved = source.git(repo, env, "rev-parse", "--symbolic-full-name", "--verify", "--end-of-options", ref).strip()
    return resolved or source.resolve_commit(repo, env, ref)


def effective_connection(connection, options):
    return {**connection, "model": options.get("model") or connection["model"],
            "options": {"timeout_sec": 300, **connection.get("options", {})}}


def credentials_for_history(history, get_credentials):
    from mortis_engine.security_scans.service import secret_rules

    rules = {}
    for run_id in history.previous_ids("scan"):
        for rule in secret_rules(history.report(run_id)):
            rules[rule["identity"]] = rule
    credentials = get_credentials()
    return {**credentials, "redaction_rules": list(rules.values())} if rules else credentials


@contextmanager
def _inputs(history, repository, mode, base, head, resume, overrides):
    if resume is None:
        with snapshots.snapshot(repository, mode=mode, base=base, head=head, object_cache=history.objects,
                                overrides=overrides) as snap:
            yield snap, None
        return
    row = history.row(resume, "review")
    saved = json.loads(row["plan"])
    with source.local_repository(repository, object_cache=history.objects) as (repo, env):
        snap = snapshots.Snapshot(repo=repo, env=env, **saved["snapshot"])
        for sha in (snap.base_sha, snap.head_sha, snap.settings_sha):
            source.resolve_commit(repo, env, sha)
        yield snap, saved



def unit_byte_limit(prompt_token_ceiling=None):
    """Use the same bounded source units for execution and connection-free previews."""
    if prompt_token_ceiling is not None and (
        type(prompt_token_ceiling) is not int or not 4096 <= prompt_token_ceiling <= 2_000_000
    ):
        raise ValueError("prompt_token_ceiling must be a whole number from 4096 to 2000000 tokens.")
    return max(1024, min(49152, (prompt_token_ceiling - 8192) // 2)) if prompt_token_ceiling is not None else 49152


def plan_snapshot(snap, options, stopped=lambda: False, *, prompt_token_ceiling=None,
                  full_tree_source_policy=planning.FULL_TREE_SOURCE_POLICY):
    return planning.plan_review(snap.repo, snap.env, snap.base_sha, snap.head_sha, snap.merge_base,
        options, stopped, max_unit_bytes=unit_byte_limit(prompt_token_ceiling), trusted_rules_sha=snap.settings_sha,
        full_tree_source_policy=full_tree_source_policy, unavailable=snap.unavailable)


def review_commits(repository, *, base, head="HEAD", connection, get_credentials, on_plan=None,
                   progress=lambda value: None, concurrency=None, overrides=None):
    """Review the changes from the common ancestor to the pinned head."""
    return review_local(repository, mode="commits", base=base, head=head, connection=connection,
                        get_credentials=get_credentials, on_plan=on_plan, progress=progress, concurrency=concurrency,
                        overrides=overrides)


def review_local(repository, *, connection, get_credentials, mode="worktree", base=None, head="HEAD",
                 effort=None, graph=False, resume=None, profile=None, on_plan=None, progress=lambda value: None,
                 concurrency=None, overrides=None):
    """Review local inputs with the saved connection and independent workflow budgets."""
    invocation_started = time.monotonic()
    started = time.time()
    if resume and (concurrency is not None or overrides):
        raise ValueError("--resume uses the inputs and options of the saved run.")
    if not graph and overrides and "code_graph_limits" in overrides:
        raise ValueError("Graph limit overrides require --graph.")
    if resume is None:
        effort = settings.effort_override(effort)
    with History(repository) as history:
        if resume and history.row(resume, "review")["report"] is not None:
            return history.report(resume)
        with _inputs(history, repository, mode, base, head, resume, overrides) as (snap, saved):
            continuous = continuous_assessment(saved)
            options = settings.load(snap.repo, snap.env, snap.settings_sha)
            full_tree_source_policy = saved.get("full_tree_source_policy") if saved else planning.FULL_TREE_SOURCE_POLICY
            source_tool_policy = saved.get("source_tool_policy", "bounded-reads/v2") if saved else engine.SOURCE_TOOL_POLICY
            attribution = saved.get("assessment_attribution") if saved else assessment.ATTRIBUTION_VERSION
            options["_evidence_policy"] = planning.saved_evidence_policy(saved) if saved else planning.EVIDENCE_POLICY
            planning.supports_test_evidence(options)
            if saved and "evidence_policy" not in saved:
                options["_unmarked_verification_policy"] = True
            options["_discovery_result_policy"] = saved.get("discovery_result_policy") if saved else engine.DISCOVERY_RESULT_POLICY
            if options["_discovery_result_policy"] not in {None, engine.DISCOVERY_RESULT_POLICY}:
                raise ValueError("The saved discovery result policy is unsupported.")
            if attribution not in {None, assessment.ATTRIBUTION_VERSION}:
                raise ValueError("The saved assessment attribution version is unsupported.")
            options["_assessment_attribution"] = attribution
            if saved:
                effort, graph = settings.effort_override(saved["effort"]), saved["graph"]
                concurrency = saved.get("concurrency")
                overrides = saved.get("overrides")
                started = history.row(resume)["started"]
            options.update(effort=effort or options["effort"], review_mode=snap.mode, _settings_sha=snap.settings_sha)
            if saved:
                # Legacy plans used these fixed limits before configurable extraction was supported.
                options["code_graph_limits"] = CodeGraphLimits.model_validate(saved.get("code_graph_limits", {
                    "max_files": 1000, "max_file_bytes": 1048576, "max_source_bytes": 8388608,
                }), strict=True).model_dump()
            settings.apply_overrides(options, overrides)
            if concurrency is not None:
                options["concurrency"] = settings.RepoSettings.model_validate({"concurrency": concurrency}, strict=True).concurrency
            connection = effective_connection(connection, options)
            connection_identity = planning.fingerprint({key: value for key, value in connection.items()
                                                        if key != "_admission_key"})
            if saved:
                if connection_identity != saved["connection"]:
                    raise ValueError("The selected connection changed since this run began.")
                run_id = resume
                previous = saved["history"]
                decisions = saved.get("triage_versions", {})
                assessment_runs = saved.get("assessment_runs", [])
            else:
                branch = scope_ref(snap.repo, snap.env, head)
                scope = (scope_ref(snap.repo, snap.env, base) + "..." if snap.mode == "commits" else snap.mode + " ") + branch
                previous = history.previous_ids("review", scope)
                decisions = triage_snapshot(history, "review", scope)
                assessment_runs = history.previous_ids("assessment")
                manifest = {f.name: getattr(snap, f.name) for f in fields(snap) if f.name not in {"env", "repo"}}
                run_id = history.begin("review", scope, {"connection": connection_identity, "profile": profile,
                    "snapshot": manifest, "history": previous, "effort": effort, "graph": graph, "concurrency": concurrency,
                    "overrides": overrides, "code_graph_limits": options["code_graph_limits"],
                    "triage_versions": decisions, "assessment_runs": assessment_runs,
                    "assessment_dispatch": ASSESSMENT_DISPATCH_POLICY,
                    "full_tree_source_policy": full_tree_source_policy, "assessment_attribution": attribution,
                    "source_tool_policy": source_tool_policy, "evidence_policy": options["_evidence_policy"],
                    "discovery_result_policy": options["_discovery_result_policy"]})
            options["_triage_snapshot"] = decisions
            with history.claim(run_id):
                if history.row(run_id)["report"] is not None:
                    return history.report(run_id)
                timeout = options["timeout_seconds"]
                deadline = time.monotonic() + started + timeout - time.time() if timeout else None

                def stopped():
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError("The review reached its configured time limit.")
                    return False

                plan = plan_snapshot(snap, options, stopped,
                    prompt_token_ceiling=connection["options"].get("prompt_token_ceiling"),
                    full_tree_source_policy=full_tree_source_policy)
                if on_plan is not None and plan.batches:
                    on_plan(len(plan.selected), connection["model"])
                # An empty diff needs neither a model nor credentials unless there are prior fixes to verify.
                verify_previous = options["auto_resolve"] and previous and not plan.limit_reason
                credentials = credentials_for_history(history, get_credentials) if plan.batches or verify_previous or plan.limit_reason else {}
                session = Session(connection, credentials, history=history, run_id=run_id, deadline=deadline,
                                  progress=progress)
                graphs, graph_status = {}, None
                if graph and plan.batches:
                    from mortis_engine import code_graph

                    progress({"phase": "graph"})
                    retained = history.review_graph(run_id)
                    if retained is None:
                        legacy = bool(saved and session.records)
                        graphs, graph_status = code_graph.enrich(snap.repo, snap.env, plan, {**options, "_graph": graph},
                                                                 credentials, stopped, legacy=legacy)
                        if not legacy:
                            history.save_review_graph(run_id, graphs, graph_status)
                    else:
                        graphs, graph_status = retained
                        if any(file_policy.excluded(path) for path in graphs) or excluded_source_paths(graphs):
                            graphs, graph_status = {}, {"status": "unavailable",
                                "reason": "Saved graph context includes source excluded by the current file policy."}
                status, raw, findings = engine.run(snap.repo, snap.env, plan, connection, options, credentials,
                    deadline=deadline, session=session, graph_by_path=graphs, progress=progress,
                    source_tool_policy=source_tool_policy)
                result = {"schema_version": "mortis.review/v1", "run_id": run_id, "status": status,
                    "base_sha": snap.base_sha, "head_sha": snap.head_sha, "merge_base": snap.merge_base,
                    "settings_sha": snap.settings_sha, "revision": (snap.settings_sha if snap.mode == "all" else snap.head_sha),
                    "mode": snap.mode, "provider": connection["provider"],
                    "model": connection["model"], "findings": findings, "coverage": raw["manifest"]["coverage"],
                    "units": [{k: v for k, v in unit.items() if k != "comments"} for unit in raw["units"]],
                    "excluded_files": list({i["path"]: i for i in [*snap.excluded, *raw["excluded_files"]]}.values()),
                    "errors": raw["errors"], "warnings": raw["warnings"] + [{"message": w} for w in snap.warnings],
                    "usage": session.usage()}
                if "second_pass" in raw:
                    result["second_pass"] = raw["second_pass"]
                if graph_status is not None:
                    result["code_graph"] = graph_status
                if graphs:
                    options["_graph"] = graphs

                async def finish():
                    async with session:
                        return await workflows.postprocess(snap.repo, snap.env, result, options, session,
                            history=[effective_report(history, row, triage_snapshot=decisions,
                                                      assessment_runs=assessment_runs) for row in previous],
                            progress=progress, continuous=continuous)

                progress({"phase": "postprocess"})
                result = asyncio.run(finish())
                result["usage"] = session.usage()
                result["usage"]["budget_exceeded"] |= raw["summary"]["budget_exceeded"]
                result = context.sanitize(result, credentials)
                result["elapsed_seconds"] = time.monotonic() - invocation_started
                history.finish(run_id, result)
                return result
