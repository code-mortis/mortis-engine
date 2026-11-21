"""Exact-input, candidate-scoped assessment retries without rewriting their origin."""

import asyncio
import copy
import hashlib
import json
import tempfile
import time
from pathlib import Path

from .history import History, encode, private_directory
from .llm_http import InferenceError
from .review import context as review_context
from .review import planning, source, workflows
from .review.service import credentials_for_history, effective_connection
from .security_scans.payload import check_saved_sources
from .workflow import Session

INPUT_VERSION = "mortis.assessment-input/v1"


def connection_identity(connection):
    return planning.fingerprint({key: value for key, value in connection.items() if key != "_admission_key"})


def _revisions(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"base_sha", "head_sha", "source_sha", "original_sha", "_settings_sha", "settings_sha", "sha"}:
                if isinstance(child, str) and len(child) == 40 and all(c in "0123456789abcdef" for c in child):
                    yield child
            elif key not in {"inventory", "_graph"}:
                yield from _revisions(child)
    elif isinstance(value, list):
        for child in value:
            yield from _revisions(child)


def _retain(session, repo, env, revisions, value):
    from .review import file_policy

    cached = getattr(session, "_assessment_objects", {})
    objects = set()
    options = value["options"]
    for sha in revisions:
        if sha in cached:
            objects.update(cached[sha])
            continue
        retained = set(source.git(repo, env, "rev-list", sha).splitlines())
        retained.add(source.git(repo, env, "rev-parse", sha + "^{tree}").strip())
        entries = source.git(repo, env, "ls-tree", "-r", "-t", "-l", "-z", sha).rstrip("\0").split("\0")
        blobs = {}
        for entry in filter(None, entries):
            metadata, path = entry.split("\t", 1)
            mode, kind, identity, size = metadata.split()
            if kind == "tree":
                retained.add(identity)
            elif mode in {"100644", "100755"} and int(size) <= 1_500_000:
                blobs[path] = (identity, int(size))
        allowed = {path for path in blobs if not file_policy.excluded(path, file_policy.policy(options))}
        if value["workflow_kind"] == "review":
            allowed &= planning.allowed_context(repo, env, value["base_sha"], value["head_sha"],
                                                allowed, options, lambda: False)
        else:
            allowed &= set(value["context"]["available_paths"])
        total = 0
        for path in sorted(allowed):
            try:
                source.read_source(repo, env, sha, path)
            except ValueError:
                continue
            total += blobs[path][1]
            if total > 32_000_000:
                raise ValueError("Original supporting source exceeds the 32 MB retry retention limit.")
            retained.add(blobs[path][0])
        objects.update(retained)
        cached[sha] = retained
    directory = private_directory(source._object_cache(session.history.objects) / "pack")
    source.git(repo, env, "pack-objects", str(directory / "pack"),
               input_bytes=("\n".join(sorted(objects)) + "\n").encode())
    session._assessment_objects = cached
    return sorted(objects)


def pin_input(session, candidate_id, kind, candidate, context, options, task_key, repo, env, *, predecessors=None,
              **evidence):
    """Persist sanitized input before dispatch; existing records can never be replaced."""
    from .findings import snapshot

    if kind == "review":
        options = {**options, "_evidence_policy": options.get("_evidence_policy", planning.EVIDENCE_POLICY)}
    row = session.history.row(session.run_id)
    plan = json.loads(row["plan"])
    value = review_context.sanitize({"schema_version": INPUT_VERSION, "validator_version": 1,
        "workflow_kind": kind, "candidate": candidate, "context": context, "options": options,
        "task_key": task_key, "connection": connection_identity(session.connection),
        "admission_key": session.connection.get("_admission_key"),
        "triage_versions": plan.get("triage_versions", snapshot(session.history, kind, row["scope"])),
        **({"predecessors": predecessors} if predecessors is not None else {}),
        **evidence}, session.credentials)
    revisions = sorted(set(_revisions(value)))
    try:
        value["retained_objects"] = _retain(session, repo, env, revisions, value)
    except (ValueError, TimeoutError, OSError) as exc:
        value["retry_blocked_reason"] = review_context.sanitize(str(exc), session.credentials)
    value["revisions"] = revisions
    session.history.save_assessment_input(session.run_id, candidate_id, value)
    if predecessors is not None:
        # The first request must render the context in the same key order as a replay.
        return json.loads(encode(value["context"]))


def _children(history, parent, candidate):
    result = []
    for row in history.db.execute("SELECT * FROM runs WHERE kind='assessment' ORDER BY started, id"):
        plan = json.loads(row["plan"])
        if plan.get("parent_run_id") == parent and plan.get("candidate_id") == candidate:
            result.append(row)
    return result


def _unknown(history, parent, value, children):
    kind = value["workflow_kind"]
    stage = "assessment" if kind == "review" else "security_assessment"
    for (record_stage, key), (_, row, _) in history.attempts(parent).items():
        relevant = (record_stage.startswith("security_") if kind == "scan" else
                    record_stage == stage and (key == value["task_key"] or key.startswith(value["task_key"] + ":")))
        if relevant and row.get("consumption_unknown"):
            return True
    return any(record.get("consumption_unknown") for child in children
               for _, record, _ in history.attempts(child["id"]).values())


def _eligibility(history, origin, candidate, value, children):
    if candidate.get("status") != "failed":
        return "Only failed assessments can be retried."
    if value is None:
        return "This run predates retry support or has no exact saved assessment evidence."
    if value.get("schema_version") != INPUT_VERSION or value.get("validator_version") != 1:
        return "The saved assessment input or validator version is incompatible."
    if value.get("retry_blocked_reason"):
        return value["retry_blocked_reason"]
    if origin["report"] is None:
        return "Resume the original run before retrying its failed assessment."
    if _unknown(history, origin["id"], value, children):
        return "Unknown token consumption in this assessment lineage blocks retry."
    if any(child["report"] is None for child in children):
        return "An unfinished assessment retry exists; resume it using its run ID."
    from .findings import snapshot
    if snapshot(history, origin["kind"], origin["scope"]) != value["triage_versions"]:
        return "Finding decisions changed since the assessment was pinned; its history is stale."
    return None


def _candidates(history, run_id):
    origin = history.row(run_id)
    if origin["kind"] not in {"review", "scan"}:
        raise ValueError("Choose a review or security scan run.")
    report = json.loads(origin["report"]) if origin["report"] else {}
    return origin, report.get("candidates", [])


def _validate_evidence(repo, env, value):
    check_saved_sources(value.get("context", {}))
    try:
        for sha in value["revisions"]:
            source.resolve_commit(repo, env, sha)
            source.git(repo, env, "cat-file", "-e", sha + "^{tree}")
        checked = source.git(repo, env, "cat-file", "--batch-check",
                             input_bytes=("\n".join(value["retained_objects"]) + "\n").encode())
        if any(line.endswith(" missing") for line in checked.splitlines()):
            raise ValueError("Missing retained object")
    except (KeyError, ValueError) as exc:
        raise ValueError("Exact original assessment evidence is unavailable; retry was withheld.") from exc


def list_assessments(repository, *, run_id):
    """List candidates and retry blockers without loading connection credentials."""
    with History(repository) as history:
        origin, candidates = _candidates(history, run_id)
        inputs = history.assessment_inputs(run_id)
        rows = []
        for candidate in candidates:
            children = _children(history, run_id, candidate["id"])
            success = next((json.loads(row["report"]) for row in children if row["report"]
                            and json.loads(row["report"]).get("status") == "completed"), None)
            reason = _eligibility(history, origin, candidate, inputs.get(candidate["id"]), children)
            if success:
                reason = "A successful retry is already saved; assess returns it without dispatch."
            elif reason is None:
                try:
                    with source.local_repository(repository, object_cache=history.objects) as (repo, env):
                        _validate_evidence(repo, env, inputs[candidate["id"]])
                except (ValueError, OSError) as exc:
                    reason = str(exc)
            rows.append({"candidate_id": candidate["id"], "path": candidate["snapshot"].get("path"),
                "status": candidate["status"], "error": candidate.get("error"),
                "retry_eligible": reason is None, "blocked_reason": reason,
                "retry_run_id": success["run_id"] if success else children[-1]["id"] if children else None})
        if origin["kind"] == "scan" and not candidates and report_has_legacy_failures(origin):
            return {"schema_version": "mortis.assessments/v1", "run_id": run_id, "candidates": [],
                    "blocked_reason": "This scan predates durable assessment candidates and retry support."}
        return {"schema_version": "mortis.assessments/v1", "run_id": run_id, "candidates": rows}


def report_has_legacy_failures(origin):
    report = json.loads(origin["report"]) if origin["report"] else {}
    return any(unit.get("task", "").startswith("assessment:") and unit.get("status") == "failed"
               for unit in report.get("coverage", {}).get("units", []))


def _lineage_usage(history, parent, value, children):
    records = []
    for run_id in [parent, *(child["id"] for child in children)]:
        for (stage, key), (_, record, _) in history.attempts(run_id).items():
            if run_id == parent and not (stage == ("assessment" if value["workflow_kind"] == "review" else "security_assessment")
                                         and (key == value["task_key"] or key.startswith(value["task_key"] + ":"))):
                continue
            records.append({"run_id": run_id, **record})
    charge = sum(Session.charge(record) for record in records)
    return {"requests": records, "reserved_tokens": charge,
            "total_tokens": None if any(row["consumption_unknown"] for row in records) else charge}


def _finding(value, candidate_id, result):
    if result["verdict"] != "confirmed":
        return None
    candidate = copy.deepcopy(value["candidate"])
    if value["workflow_kind"] == "review":
        identity = result["finding_id"] or "finding-" + candidate_id.removeprefix("candidate-")
        old = next((old for old in value["context"].get("history", []) if old["id"] == identity), None)
        if not result["suggestion_supported"]:
            candidate["suggestion_code"] = ""
        return {**candidate, "id": identity, "assessment": result, "status": old["status"] if old else "open"}
    from .security import digest
    from .security_scans.service import observe
    fingerprint = digest(json.dumps(["code", candidate["path"], candidate["category"],
                                     candidate["evidence"][0]["quote"], candidate["title"]]))
    identity = result["finding_id"] or fingerprint[:32]
    old = next((old for old in value["context"].get("history", []) if old["id"] == identity), None)
    evidence = {**candidate, "kind": "code", "source_sha": value["head_sha"],
                "source_hash": value["redaction"]["inventory"][candidate["path"]]["hash"],
                "fingerprint": fingerprint, "assessment": result, "sources": ["mortis"]}
    return observe(evidence, old)


async def _execute(repository, repo, env, value, session):
    options = value["options"]
    if value["workflow_kind"] == "review":
        options = {**options, "_evidence_policy": planning.saved_evidence_policy(options)}
        report = {"base_sha": value["base_sha"], "head_sha": value["head_sha"]}
        return await workflows._assess(repo, env, workflows._run(report, options, session),
                                       value["candidate"], {}, session, pinned_context=value["context"])
    from .review import file_policy
    from .security_scans import engine, inference
    from .security_scans.scanner import identity_key
    with tempfile.TemporaryDirectory(prefix="mortis-assessment-") as temporary:
        item = engine.Snapshot(Path(temporary), value["head_sha"], env, lambda: False,
                               git=repo, key=identity_key(create=False))
        item.repository = Path(repository).resolve()
        item.policy = file_policy.policy(options)
        item.inventory = value["redaction"]["inventory"]
        item.secret_rules = value["redaction"]["secret_rules"]
        item.secret_lines = value["redaction"]["secret_lines"]
        item.deadline = session.deadline
        result, _, _ = await inference.ask(item, session, "assessment", value["context"], value["task_key"],
                                        options, prepared=True)
        return result


def retry_assessment(repository, *, run_id=None, candidate_id=None, resume=None,
                     connection, get_credentials, profile=None, progress=lambda value: None):
    """Retry one failed candidate against pinned inputs, under a durable candidate claim."""
    if resume and (run_id or candidate_id):
        raise ValueError("--resume uses the parent and candidate of the saved retry.")
    with History(repository) as history:
        if resume:
            retry = history.row(resume, "assessment")
            saved = json.loads(retry["plan"])
            run_id, candidate_id = saved["parent_run_id"], saved["candidate_id"]
        elif not run_id or not candidate_id:
            raise ValueError("Assessment retry requires a run ID and candidate ID.")
        claim = hashlib.sha256(encode([run_id, candidate_id]).encode()).hexdigest()[:32]
        with history.claim(claim):
            origin, candidates = _candidates(history, run_id)
            candidate = next((item for item in candidates if item["id"] == candidate_id), None)
            if candidate is None:
                raise ValueError("No saved assessment candidate with that ID; this run may predate retry support.")
            children = _children(history, run_id, candidate_id)
            success = next((json.loads(row["report"]) for row in children if row["report"]
                            and json.loads(row["report"]).get("status") == "completed"), None)
            if success:
                return success
            if resume and retry["report"]:
                return json.loads(retry["report"])
            value = history.assessment_input(run_id, candidate_id)
            reason = _eligibility(history, origin, candidate, value,
                                  [child for child in children if child["id"] != resume])
            if reason:
                raise ValueError(reason)
            if _unknown(history, run_id, value, children):
                raise ValueError("Unknown token consumption in this assessment lineage blocks retry.")
            connection = effective_connection(connection, value["options"])
            if (connection_identity(connection) != value["connection"]
                    or connection.get("_admission_key") != value.get("admission_key")):
                raise ValueError("The selected connection changed since the original assessment.")
            if resume and saved["input"] != planning.fingerprint(value):
                raise ValueError("The retry's saved assessment input changed.")
            # Validate retained revisions before credentials or provider-client initialization.
            with source.local_repository(repository, object_cache=history.objects) as (repo, env):
                _validate_evidence(repo, env, value)
                if value["workflow_kind"] == "scan":
                    from .security_scans import engine
                    from .security_scans.scanner import identity_key
                    if engine.secure_digest("assessment-redaction-key", identity_key(create=False)) != value["redaction"]["key_check"]:
                        raise ValueError("The original security redaction key changed; retry was withheld.")
                credentials = credentials_for_history(history, get_credentials)
                if review_context.sanitize(value["context"], credentials) != value["context"]:
                    raise ValueError("The original evidence no longer matches safe redaction; retry was withheld.")
                child_id = resume or history.begin("assessment", origin["scope"], {
                    "parent_run_id": run_id, "candidate_id": candidate_id, "profile": profile,
                    "input": planning.fingerprint(value)})
                timeout = value["options"].get("timeout_seconds")
                deadline = time.monotonic() + timeout if timeout else None
                session = Session(connection, credentials, history=history, run_id=child_id,
                                  deadline=deadline, progress=progress)
                progress({"phase": "assessment"})

                async def execute():
                    async with session:
                        return await _execute(repository, repo, env, value, session)

                try:
                    result = asyncio.run(execute())
                    status, error = "completed", None
                except (ValueError, OSError, InferenceError) as exc:
                    result, status = None, "failed"
                    error = review_context.sanitize(str(exc), credentials)
                from .findings import snapshot
                if snapshot(history, origin["kind"], origin["scope"]) != value["triage_versions"]:
                    status = "failed"
                    error = "Finding decisions changed while retrying; the saved result cannot update newer history."
                children = _children(history, run_id, candidate_id)
                report = {"schema_version": "mortis.assessment/v1", "run_id": child_id,
                    "parent_run_id": run_id, "candidate_id": candidate_id, "workflow_kind": origin["kind"],
                    "status": status, "result": result,
                    "finding": _finding(value, candidate_id, result) if result and status == "completed" else None,
                    "head_sha": value["head_sha"], "model": connection["model"], "provider": connection["provider"],
                    "usage": session.usage(), "lineage_usage": _lineage_usage(history, run_id, value, children)}
                if error:
                    report["error"] = error
                history.finish(child_id, report)
                return report
