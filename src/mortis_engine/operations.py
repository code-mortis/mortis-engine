"""Standalone verification, repository background and defensive security scan entry points."""

import asyncio
import json
import time
from pathlib import Path

from .history import History
from .review import planning, settings, source, workflows
from .review.service import credentials_for_history, effective_connection, scope_ref
from .security_scans import service as security
from .workflow import Session


def run(repository, *, kind, connection, get_credentials, ref="HEAD", profile=None,
        finding_run=None, resume=None, effort=None, rotations=(), concurrency=None, progress=lambda value: None):
    if kind not in {"scan", "verify", "background", "summary"}:
        raise ValueError("Choose a supported local workflow.")
    if concurrency is not None and kind != "scan":
        raise ValueError("Concurrency overrides are supported for scans and reviews.")
    if resume and concurrency is not None:
        raise ValueError("--resume uses the inputs and options of the saved run.")
    with History(repository) as history:
        saved = None
        if resume:
            row = history.row(resume, kind)
            if row["report"] is not None:
                return history.report(resume)
            saved = json.loads(row["plan"])
            ref, finding_run, effort = saved["ref"], saved["finding_run"], saved["effort"]
            concurrency = saved.get("concurrency")
            rotations = saved.get("rotations", [])
        # Normalize subdirectory invocations to the same local repository as its saved history.
        path = Path(repository).resolve()
        root = next((p for p in (path, *path.parents) if (p / ".git").exists()), path)
        with source.local_repository(root, object_cache=history.objects) as (repo, env):
            original = None
            if kind in {"verify", "summary"}:
                history.row(finding_run, "review")
                original = history.report(finding_run)
            sha = (saved["sha"] if saved else original["head_sha"] if kind == "summary"
                   else source.resolve_commit(repo, env, ref))
            options = (settings.load_scan(repo, env, sha) if kind == "scan" else
                       settings.load(repo, env, original["settings_sha"] if kind == "summary" else sha))
            options["_settings_sha"] = original["settings_sha"] if kind == "summary" else sha
            if effort:
                options["effort"] = effort
            if concurrency is not None:
                options["concurrency"] = security.ScanOptions(concurrency=concurrency).concurrency
            connection = effective_connection(connection, options)
            identity = planning.fingerprint(connection)
            if saved:
                if saved["connection"] != identity:
                    raise ValueError("The selected connection changed since this run began.")
                previous, run_id = saved["previous"], resume
            else:
                scope = scope_ref(repo, env, ref) if kind != "summary" else finding_run
                previous = history.previous_ids("scan", scope)[-1:] if kind == "scan" else []
                run_id = history.begin(kind, scope, {"ref": ref, "sha": sha, "connection": identity, "previous": previous,
                    "profile": profile, "finding_run": finding_run, "effort": effort, "rotations": list(rotations),
                    "concurrency": concurrency})
            with history.claim(run_id):
                if history.row(run_id)["report"] is not None:
                    return history.report(run_id)
                credentials = credentials_for_history(history, get_credentials)
                seconds = options["summary_timeout_seconds" if kind == "summary" else "timeout_seconds"]
                deadline = time.monotonic() + history.row(run_id)["started"] + seconds - time.time() if seconds else None
                session = Session(connection, credentials, history=history, run_id=run_id, deadline=deadline,
                                  scan_budget=options.get("token_budget") if kind == "scan" else None)

                async def execute():
                    async with session:
                        if kind == "scan":
                            scan_options = {k: v for k, v in options.items() if k in security.ScanOptions.model_fields}
                            prior = history.report(previous[0]) if previous else None
                            if rotations:
                                acknowledge_rotations(prior, rotations)
                            return await security.scan(root, ref=sha, session=session, options=scan_options, previous=prior,
                                                       progress=progress)
                        if kind == "verify":
                            return await workflows.verify(repo, env, original, sha, options, session)
                        if kind == "summary":
                            result = await workflows.generate_summary(repo, env, original, options, session)
                            return {"schema_version": "mortis.summary/v1", **result}
                        result = await workflows.generate_background(repo, env, sha, options, session,
                                                                    repository=root.name, branch=ref)
                        return {"schema_version": "mortis.background/v1", "status": "completed", **result}

                result = asyncio.run(execute())
                result.update(run_id=run_id, ref=ref, head_sha=sha, model=connection["model"],
                              provider=connection["provider"], usage=session.usage())
                if original:
                    result["review_run_id"] = finding_run
                history.finish(run_id, result)
                return result


def acknowledge_rotations(previous, ids):
    by_id = {row["id"]: row for row in (previous or {}).get("findings", [])}
    for identity in set(ids):
        row = by_id.get(identity)
        evidence = row.get("evidence") if row else None
        evidence = evidence if isinstance(evidence, dict) else row or {}
        if evidence.get("kind") != "secret" or not evidence.get("identity"):
            raise ValueError("Rotation acknowledgement requires a retained secret finding ID from the same scan scope.")
        row["rotation"] = {"identity": evidence["identity"]}
