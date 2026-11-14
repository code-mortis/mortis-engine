"""Local review workflow from immutable Git inputs through validated findings and history."""

import asyncio
import json
import time
from contextlib import contextmanager
from dataclasses import fields

from mortis_engine.history import History
from mortis_engine.workflow import Session

from . import context, engine, planning, settings, snapshots, source, workflows


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
def _inputs(history, repository, mode, base, head, resume):
    if resume is None:
        with snapshots.snapshot(repository, mode=mode, base=base, head=head, object_cache=history.objects) as snap:
            yield snap, None
        return
    row = history.row(resume, "review")
    saved = json.loads(row["plan"])
    with source.local_repository(repository, object_cache=history.objects) as (repo, env):
        snap = snapshots.Snapshot(repo=repo, env=env, **saved["snapshot"])
        for sha in (snap.base_sha, snap.head_sha, snap.settings_sha):
            source.resolve_commit(repo, env, sha)
        yield snap, saved


def review_commits(repository, *, base, head="HEAD", connection, get_credentials, on_plan=None,
                   progress=lambda value: None):
    """Review the changes from the common ancestor to the pinned head."""
    return review_local(repository, mode="commits", base=base, head=head, connection=connection,
                        get_credentials=get_credentials, on_plan=on_plan, progress=progress)


def review_local(repository, *, connection, get_credentials, mode="worktree", base=None, head="HEAD",
                 effort=None, graph=False, resume=None, profile=None, on_plan=None, progress=lambda value: None):
    """Review local inputs with the saved connection and independent workflow budgets."""
    started = time.time()
    with History(repository) as history:
        if resume and history.row(resume, "review")["report"] is not None:
            return history.report(resume)
        with _inputs(history, repository, mode, base, head, resume) as (snap, saved):
            options = settings.load(snap.repo, snap.env, snap.settings_sha)
            if saved:
                effort, graph = saved["effort"], saved["graph"]
                started = history.row(resume)["started"]
            options.update(effort=effort or options["effort"], review_mode=snap.mode, _settings_sha=snap.settings_sha)
            connection = effective_connection(connection, options)
            if saved:
                if planning.fingerprint(connection) != saved["connection"]:
                    raise ValueError("The selected connection changed since this run began.")
                run_id = resume
                previous = saved["history"]
            else:
                branch = scope_ref(snap.repo, snap.env, head)
                scope = (scope_ref(snap.repo, snap.env, base) + "..." if snap.mode == "commits" else snap.mode + " ") + branch
                previous = history.previous_ids("review", scope)
                manifest = {f.name: getattr(snap, f.name) for f in fields(snap) if f.name not in {"env", "repo"}}
                run_id = history.begin("review", scope, {"connection": planning.fingerprint(connection), "profile": profile,
                    "snapshot": manifest, "history": previous, "effort": effort, "graph": graph})
            with history.claim(run_id):
                if history.row(run_id)["report"] is not None:
                    return history.report(run_id)
                timeout = options["timeout_seconds"]
                deadline = time.monotonic() + started + timeout - time.time() if timeout else None

                def stopped():
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError("The review reached its configured time limit.")
                    return False

                ceiling = connection["options"].get("prompt_token_ceiling")
                unit_bytes = max(1024, min(49152, (ceiling - 8192) // 2)) if ceiling is not None else 49152
                plan = planning.plan_review(snap.repo, snap.env, snap.base_sha, snap.head_sha, snap.merge_base,
                    options, stopped, max_unit_bytes=unit_bytes, trusted_rules_sha=snap.settings_sha)
                if on_plan is not None and plan.batches:
                    on_plan(len(plan.selected), connection["model"])
                # An empty diff needs neither a model nor credentials unless there are prior fixes to verify.
                verify_previous = options["auto_resolve"] and previous and not plan.limit_reason
                credentials = credentials_for_history(history, get_credentials) if plan.batches or verify_previous or plan.limit_reason else {}
                session = Session(connection, credentials, history=history, run_id=run_id, deadline=deadline)
                graphs, graph_status = {}, None
                if graph and plan.batches:
                    from mortis_engine import code_graph
                    progress({"phase": "graph"})
                    graphs, graph_status = code_graph.enrich(snap.repo, snap.env, plan, {**options, "_graph": graph}, credentials, stopped)
                status, raw, findings = engine.run(snap.repo, snap.env, plan, connection, options, credentials,
                    deadline=deadline, session=session, graph_by_path=graphs, progress=progress)
                result = {"schema_version": "mortis.review/v1", "run_id": run_id, "status": status,
                    "base_sha": snap.base_sha, "head_sha": snap.head_sha, "merge_base": snap.merge_base,
                    "settings_sha": snap.settings_sha, "revision": (snap.settings_sha if snap.mode == "all" else snap.head_sha),
                    "mode": snap.mode, "provider": connection["provider"],
                    "model": connection["model"], "findings": findings, "coverage": raw["manifest"]["coverage"],
                    "units": [{k: v for k, v in unit.items() if k != "comments"} for unit in raw["units"]],
                    "excluded_files": list({i["path"]: i for i in [*snap.excluded, *raw["excluded_files"]]}.values()),
                    "errors": raw["errors"], "warnings": raw["warnings"] + [{"message": w} for w in snap.warnings],
                    "usage": session.usage()}
                if graph_status is not None:
                    result["code_graph"] = graph_status
                if graphs:
                    options["_graph"] = graphs

                async def finish():
                    async with session:
                        return await workflows.postprocess(snap.repo, snap.env, result, options, session,
                            history=[history.report(row) for row in previous])

                progress({"phase": "postprocess"})
                result = asyncio.run(finish())
                result["usage"] = session.usage()
                result["usage"]["budget_exceeded"] |= raw["summary"]["budget_exceeded"]
                result = context.sanitize(result, credentials)
                history.finish(run_id, result)
                return result
