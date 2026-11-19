"""Local security workflow with immutable findings and explicit coverage."""

import asyncio
import inspect
import json
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from mortis_engine.assessment_dispatch import dispatch, replay_order
from mortis_engine.history import encode
from mortis_engine.review import file_policy, source
from mortis_engine.review.settings import RunLimits
from mortis_engine.security import digest

from . import advisories, engine
from .contracts import SecurityAnalysis, SecurityAssessment, SecurityProposal
from .inference import ask
from .scanner import identity_key, trivy


class ScanOptions(RunLimits):
    concurrency: int = Field(default=4, ge=1, le=8, strict=True)
    effort: Literal["low", "medium", "high"] = "high"
    language: str = Field(default="English", min_length=1, max_length=100)
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600, strict=True)
    exclusions: str = Field(default="", max_length=4000)


def grade_for(severities):
    values = set(severities)
    if values - engine.SEVERITIES:
        return None
    return next((grade for severity, grade in (("critical", "F"), ("high", "D"),
                                               ("medium", "C"), ("low", "B")) if severity in values), "A")


def stable_version(value):
    if not re.fullmatch(r"v?\d+(?:\.\d+)*", value):
        return None
    parts = [int(part) for part in value.removeprefix("v").split(".")]
    return tuple(parts + [0] * max(0, 4 - len(parts)))


def safe_dependency_version(current, fixed):
    # shortcut: stable numeric releases support ordering; other schemes require an exact reported fix.
    versions = [part.strip() for part in fixed.split(",") if part.strip()]
    if current in versions:
        return True
    actual, fixes = stable_version(current), [stable_version(value) for value in versions]
    if not actual or not fixes or any(value is None for value in fixes):
        return False
    branch = [value for value in fixes if value[:2] == actual[:2]]
    return actual >= max(branch or fixes)


def deterministic_fix(item, old, result, *, advisory_lookup=None):
    evidence = (old["evidence"] if isinstance(old.get("evidence"), dict) else old)
    original = evidence["source_sha"]
    path = evidence["path"]
    current_path = item.renamed(original, path)
    before = item.read(path, original)
    after = item.current_source(current_path)
    advisory = None
    if before == after or not item.diff(original).strip():
        return {"verdict": "uncertain", "explanation": "No source change establishes a fix; scanner absence is insufficient."}
    if evidence["kind"] == "secret":
        if (old.get("rotation") or {}).get("identity") != evidence["identity"]:
            return {"verdict": "uncertain", "explanation": "Credential rotation or revocation has not been acknowledged."}
        raw = before.encode()[evidence["match_offset"]:evidence["match_offset"] + evidence["match_bytes"]].decode()
        if engine.secure_digest(evidence["rule_id"] + "\0" + raw, item.key) != evidence["identity"]:
            return {"verdict": "uncertain", "explanation": "Original credential identity could not be revalidated."}
        if any(raw in item.read(candidate) for candidate in item.inventory):
            return {"verdict": "still_present", "explanation": "The original credential remains in repository source."}
    elif evidence["kind"] == "dependency":
        if current_path in item.inventory and current_path not in result["targets"]:
            return {"verdict": "uncertain", "explanation": "The affected dependency target was not successfully analyzed."}
        packages = [p for row in result["targets"].values() for p in row["packages"] if p["name"] == evidence["package"]]
        if any(p["version"] == evidence["installed_version"] for p in packages):
            return {"verdict": "still_present", "explanation": "The original affected dependency version remains present."}
        if packages and any(not safe_dependency_version(p["version"], evidence.get("fixed_version", "")) for p in packages):
            if not evidence.get("fixed_version") and advisory_lookup:
                advisory = advisory_lookup(evidence, packages)
            if not advisory:
                return {"verdict": "uncertain", "explanation": "Current package versions are not established safe versions for the retained advisory."}
        if not result.get("metadata", {}).get("database_fresh"):
            return {"verdict": "uncertain", "explanation": "A fresh advisory database is required to establish dependency removal."}
    elif evidence["kind"] == "infrastructure":
        checks = result["targets"].get(current_path, {}).get("checks", [])
        if current_path in item.inventory and not any(check["id"] == evidence["rule_id"] and check["status"] == "PASS"
                and check.get("resource", "") == evidence.get("resource", "") for check in checks):
            return {"verdict": "uncertain", "explanation": "The original configuration check did not explicitly pass."}
    else:
        raise ValueError("Unsupported deterministic finding kind.")
    return {"verdict": "fixed", "explanation": "The original condition changed and the relevant scanner check confirms its removal.",
            "original_sha": original, "current_sha": item.sha, "path": path, "current_path": current_path,
            "original_hash": digest(before), "current_hash": digest(after) if after is not None else None,
            **({"advisory": advisory} if advisory else {})}


def finding_evidence(value):
    return value["evidence"] if isinstance(value.get("evidence"), dict) else value


def assessment_keys(value, item, history=()):
    evidence = finding_evidence(value)
    source_sha = evidence.get("source_sha", item.sha)
    citations = [*evidence.get("evidence", []), *evidence.get("locations", []),
                 *evidence.get("ai_analysis", {}).get("evidence", [])]
    paths = {value["path"], *(citation["path"] for citation in citations)}
    if source_sha != item.sha:
        for path in list(paths):
            try:
                paths.add(item.renamed(source_sha, path))
            except ValueError:
                pass
    connected = {("path", path) for path in paths} | {
        ("anchor", citation["quote"].strip()) for citation in citations if citation.get("quote", "").strip()}
    dependencies = [assessment_keys(old, item) for old in history]
    while True:
        expanded = connected.union(*(group for group in dependencies if connected & group))
        if expanded == connected:
            return connected
        connected = expanded


def assessment_history(proposal, history, item):
    connected = assessment_keys(proposal, item, history)
    return [old for old in history if connected & assessment_keys(old, item)]


def assessment_batch(candidates, history, item, concurrency, *, active_keys=None, blocked=()):
    chosen, occupied = [], set(active_keys or ())
    for candidate in candidates:
        keys = assessment_keys(candidate[1], item, history)
        if candidate[0] not in blocked and not keys & occupied:
            chosen.append(candidate)
            occupied.update(keys)
            if len(chosen) >= concurrency:
                break
        elif active_keys is not None:
            occupied.update(keys)
    return chosen


def _legacy_assessment_contexts(context, history_positions, completed):
    # In an old invocation, results before its start came from SQLite in sorted key order.
    proposal_at = completed.get("analysis:" + context["proposal"]["path"], float("inf"))
    observations = []
    cutoffs = {proposal_at}
    for old, position in zip(context["history"], history_positions, strict=True):
        if position is None:
            observations.append((old, False, float("inf"), float("inf")))
            continue
        nested = old.get("kind") != "code" and "ai_analysis" in old
        finding = old["ai_analysis"] if nested else old
        analysis_at = completed.get("analysis:" + finding["path"], float("inf"))
        assessment_at = completed.get(f"assessment:{position}", float("inf"))
        cutoffs.update((analysis_at, assessment_at))
        observations.append((old, nested, analysis_at, assessment_at))

    for cutoff in sorted(cutoffs - {float("inf")}):
        history = []
        for old, nested, analysis_at, assessment_at in observations:
            finding = old["ai_analysis"] if nested else old
            if analysis_at <= cutoff:
                proposal = {key: value for key, value in finding.items() if key in SecurityProposal.model_fields}
                finding = {**json.loads(encode(proposal)),
                           **{key: value for key, value in finding.items() if key not in proposal}}
            if assessment_at <= cutoff:
                finding = {**finding, "assessment": json.loads(encode(finding["assessment"]))}
            history.append({**old, "ai_analysis": finding} if nested else finding)
        proposal = json.loads(encode(context["proposal"])) if proposal_at <= cutoff else context["proposal"]
        yield {**context, "proposal": proposal, "history": history}


def observe(evidence, old=None):
    identity = old["id"] if old else evidence["fingerprint"][:32]
    state = old.get("state", "open") if old else "open"
    recurring = state == "resolved"
    if recurring:
        state = "open"
    if old:
        prior = finding_evidence(old)
        sources = sorted(set(prior.get("sources", [])) | set(evidence.get("sources", [])))
        if evidence["kind"] == "code" and prior["kind"] != "code":
            evidence = {**prior, "ai_analysis": evidence}
        evidence = {**evidence, "sources": sources}
        if state == "false_positive":
            evidence["severity"] = old["severity"]
    return {**evidence, "id": identity, "state": state, "outcome": "present",
            "version": old.get("version", 0) + 1 if old else 1,
            "rotation": None if recurring else old.get("rotation") if old else None,
            **({"triage": old["triage"]} if old and "triage" in old else {})}


def history_findings(previous):
    if previous is None:
        return []
    if not isinstance(previous, dict) or previous.get("schema_version") != "mortis.security/v1":
        raise ValueError("Previous security history must be a Mortis security report.")
    findings = previous.get("findings", [])
    if (not isinstance(findings, list) or len(findings) > 10000
            or any(not isinstance(finding, dict) for finding in findings)):
        raise ValueError("Previous security finding history is invalid or exceeds 10000 records.")
    return json.loads(json.dumps(findings))


def secret_rules(report):
    rules = []
    for finding in history_findings(report):
        evidence = finding["evidence"] if isinstance(finding.get("evidence"), dict) else finding
        if evidence.get("kind") == "secret":
            name, size, identity = evidence.get("rule_id"), evidence.get("match_bytes"), evidence.get("identity")
            if (not isinstance(name, str) or type(size) is not int or size <= 0
                    or not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity)):
                raise ValueError("Historical secret metadata is invalid; source cannot be safely redacted.")
            rules.append({key: evidence[key] for key in ("rule_id", "match_bytes", "identity")})
    return rules


async def scan(repository, *, ref="HEAD", session, options=None, previous=None, scanner=None,
               progress=lambda value: None, continuous=True):
    options = ScanOptions.model_validate(options or {}).model_dump()
    deadline = time.monotonic() + options["timeout_seconds"] if options["timeout_seconds"] else None
    legacy_completed = {}
    if not continuous:
        legacy_completed = {key.rsplit(":pass:", 1)[0]: position
                            for position, ((stage, key), (_, row, _)) in enumerate(
                                getattr(session, "records", {}).items())
                            if stage in {"security_analysis", "security_assessment"} and row["status"] == "completed"}

    def stopped():
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("Security scan reached its overall time limit.")
        return False

    policy = file_policy.policy(options)
    repository_name = str(Path(repository).expanduser().resolve())
    previous_findings = history_findings(previous)
    if previous and previous.get("repository", repository_name) != repository_name:
        raise ValueError("Security history belongs to a different local repository.")
    for old in previous_findings:
        if any(not isinstance(old.get(key), str) or not old[key].strip()
               for key in ("id", "fingerprint", "path", "title", "severity")):
            raise ValueError("Previous security finding history contains invalid identity or report fields.")
        evidence = finding_evidence(old)
        kind = evidence.get("kind")
        if (not isinstance(kind, str) or kind not in {"code", "secret", "dependency", "infrastructure"}
                or not isinstance(evidence.get("source_sha"), str) or not evidence["source_sha"]):
            raise ValueError("Previous security finding history contains invalid source evidence.")
        engine.relative_path(old["path"])
        engine.relative_path(evidence.get("path"))
        if (not isinstance(old.get("state", "open"), str)
                or type(old.get("version", 0)) is not int or old.get("version", 0) < 0
                or old.get("rotation") is not None and not isinstance(old["rotation"], dict)
                or not isinstance(old.get("fix_verification", {}), dict)
                or not isinstance(evidence.get("sources", []), list)
                or any(not isinstance(value, str) for value in evidence.get("sources", []))):
            raise ValueError("Previous security finding history contains invalid finding state.")
        analysis = evidence.get("ai_analysis", {})
        if not isinstance(analysis, dict):
            raise ValueError("Previous security finding history contains invalid AI evidence.")
        for citations in (evidence.get("evidence", []), evidence.get("locations", []), analysis.get("evidence", [])):
            if (not isinstance(citations, list) or any(not isinstance(citation, dict)
                    or not isinstance(citation.get("path"), str)
                    or not isinstance(citation.get("quote", ""), str) for citation in citations)):
                raise ValueError("Previous security finding history contains invalid citations.")
            for citation in citations:
                engine.relative_path(citation["path"])
        required = ({"secret": ("rule_id", "identity"), "dependency": ("rule_id", "package", "installed_version"),
                     "infrastructure": ("rule_id",)}).get(kind, ())
        if (any(not isinstance(evidence.get(key), str) for key in required)
                or kind == "secret" and (type(evidence.get("match_offset")) is not int or evidence["match_offset"] < 0)
                or kind == "dependency" and not isinstance(evidence.get("fixed_version", ""), str)):
            raise ValueError("Previous security finding history contains incomplete scanner evidence.")
    previous_rules = secret_rules(previous)
    stages = {name: {"status": "pending"} for name in ("snapshot", "trivy", "analysis", "assessment", "verification", "report")}
    findings, assessments, verifications, problems = [], [], [], []
    candidate_rows = []
    units, observed = [], set()
    progress({"phase": "snapshot"})
    with source.local_repository(repository, object_cache=getattr(getattr(session, "history", None), "objects", None)) as (repo, env), tempfile.TemporaryDirectory(prefix="mortis-security-") as temporary:
        sha = source.resolve_commit(repo, env, ref)
        item = engine.Snapshot(Path(temporary), sha, env, stopped, git=repo, key=identity_key(create=not previous_rules))
        item.deadline = deadline
        item.repository = Path(repository).expanduser().resolve()
        for candidate in (item.repository, *item.repository.parents):
            if (candidate / ".git").exists():
                item.repository = candidate
                break
        item.policy = policy
        item.populate()
        for path, entry in item.inventory.items():
            if file_policy.excluded(path, policy):
                entry["code"] = False
        stages["snapshot"] = {"status": "completed", "files": len(item.inventory)}
        problems.extend(item.problems)
        selected_scanner = scanner or trivy
        progress({"phase": "trivy"})
        try:
            if inspect.iscoroutinefunction(selected_scanner):
                result = await selected_scanner(item, options.get("timeout_seconds") or 300, item.stopped)
            else:
                result = await asyncio.to_thread(selected_scanner, item, options.get("timeout_seconds") or 300, item.stopped)
        except Exception as exc:
            stages["trivy"] = {"status": "failed", "error": str(exc) if isinstance(exc, ValueError) else type(exc).__name__}
            for stage in ("analysis", "assessment", "verification"):
                stages[stage] = {"status": "skipped"}
            stages["report"] = {"status": "completed"}
            progress({"phase": "report"})
            return {"schema_version": "mortis.security/v1", "repository": repository_name, "commit": sha, "ref": ref,
                "status": "failed", "grade": None, "rating_policy": "highest-unresolved-severity/v1",
                "findings": [{**old, "outcome": "not_observed"} for old in previous_findings],
                "assessments": [], "verifications": [], "stages": stages,
                "problems": ["Trivy did not complete security checks; AI analysis was withheld."],
                "coverage": {"inventory": item.inventory, "excluded": item.excluded, "units": [],
                             "selected_files": sum(row["code"] for row in item.inventory.values()), "completed_files": 0},
                "scanner": {}, "generated_at": datetime.now(UTC).isoformat()}
        problems.extend(result["problems"])
        stages["trivy"] = {"status": "partial" if result["problems"] else "completed", "metadata": result.get("metadata", {})}
        # Restore retained secret identities before preparing any model context.
        item.secret_rules.extend(previous_rules)
        previous_ids = {old["id"]: old for old in previous_findings}
        by_fingerprint = {old.get("fingerprint"): old for old in previous_findings}
        for finding in result["findings"]:
            old = by_fingerprint.get(finding["fingerprint"])
            observation = observe(finding, old)
            observed.add(observation["id"])
            findings.append(observation)
        scanner_findings = len({finding["id"] for finding in findings})
        code_paths = [path for path, row in item.inventory.items() if row["code"]]
        semaphore = asyncio.Semaphore(options["concurrency"])
        proposals_by_path = {}
        active_paths = set()

        def report_progress():
            progress({"phase": "analysis", "selected_files": len(code_paths),
                "completed_files": sum(unit["status"] == "completed" for unit in units),
                "failed_files": sum(unit["status"] == "failed" for unit in units),
                "possible_findings": sum(len(proposals) for proposals in proposals_by_path.values()),
                "scanner_findings": scanner_findings,
                "active_requests": getattr(session, "active_requests", len(active_paths)),
                **({"waiting_requests": session.waiting_requests} if hasattr(session, "waiting_requests") else {}),
                "active_paths": sorted(item.redact(path) for path in active_paths)})

        async def analyze(path):
            async with semaphore:
                active_paths.add(path)
                report_progress()
                try:
                    context = {"assigned_path": path, "head_sha": sha, "diff": "",
                               "sources": {"current": {path: item.source(path)}}}
                    value, context_paths = await ask(item, session, "analysis", context, "analysis:" + path, options)
                    if not continuous:
                        # Recover the original field order after replaying cached JSON.
                        value = SecurityAnalysis.model_validate(value, strict=True).model_dump()
                    proposals_by_path[path] = [(proposal, context_paths) for proposal in value["findings"]]
                    units.append({"task": "analysis:" + path, "status": "completed"})
                except Exception as exc:
                    units.append({"task": "analysis:" + path, "status": "failed"})
                    problems.append(f"Security analysis incomplete for {path}: {item.redact(str(exc))[:1000]}")
                finally:
                    active_paths.remove(path)
                    report_progress()

        report_progress()
        await asyncio.gather(*(analyze(path) for path in code_paths))
        candidates = [proposal for path in code_paths for proposal in proposals_by_path.get(path, [])]
        completed = sum(unit["status"] == "completed" for unit in units)
        stages["analysis"] = {"status": "completed" if completed == len(code_paths) else "partial",
                              "total": len(code_paths), "completed": completed}

        remaining = [(index, proposal, paths) for index, (proposal, paths) in enumerate(candidates)]
        candidate_ids = {index: "candidate-" + digest(json.dumps(
            [getattr(session, "run_id", sha), index, proposal], sort_keys=True))[:32]
            for index, proposal, _ in remaining}
        dependencies, order = replay_order(session, candidate_ids) if continuous else ({}, {})
        finding_positions, finding_origins, admitted_keys = {}, {}, {}
        saved_history = getattr(session, "history", None)
        initial_findings = findings.copy()

        def current_history():
            return list({old["id"]: old for old in [*previous_findings, *findings]}.values())

        def select(waiting, active, blocked, limit, _rank):
            history = current_history()
            if not continuous:
                return assessment_batch(waiting, history, item, limit)
            occupied = set().union(*(admitted_keys[index] | assessment_keys(proposal, item, history)
                                     for index, proposal, _ in active))
            chosen = assessment_batch(waiting, history, item, limit, active_keys=occupied, blocked=blocked)
            for index, proposal, _ in chosen:
                admitted_keys[index] = assessment_keys(proposal, item, history)
            return chosen

        async def assess(candidate):
            index, proposal, paths = candidate
            candidate_id = candidate_ids[index]
            row = {"id": candidate_id, "snapshot": proposal, "position": index}
            try:
                saved = saved_history.assessment_input(session.run_id, candidate_id) if continuous and saved_history else None
                context = saved["context"] if saved else {
                    "proposal": proposal, "history": assessment_history(proposal, current_history(), item),
                    "head_sha": sha, "diff": "",
                    "sources": {"current": {path: item.source(path) for path in paths}}}

                def pin(context):
                    from mortis_engine.assessments import pin_input

                    initial = {"schema_version": "mortis.security/v1",
                               "findings": initial_findings if continuous else findings}
                    redaction = {"secret_rules": [*item.secret_rules, *secret_rules(initial)],
                                 "secret_lines": item.secret_lines, "inventory": item.inventory,
                                 "key_check": engine.secure_digest("assessment-redaction-key", item.key)}
                    predecessors = None
                    if continuous:
                        predecessors = saved["predecessors"] if saved else sorted({
                            finding_origins[old["id"]] for old in context["history"] if old["id"] in finding_origins})
                    return pin_input(session, candidate_id, "scan", proposal, context, options,
                                     f"assessment:{index}", repo, env, head_sha=sha, redaction=redaction,
                                     predecessors=predecessors)

                def legacy_contexts(supplied):
                    return _legacy_assessment_contexts(supplied, (
                        finding_positions.get(old["id"]) for old in context["history"]), legacy_completed)

                assessed, _ = await ask(item, session, "assessment", context, f"assessment:{index}", options,
                    prepared=saved is not None, pin=pin if saved_history else None,
                    legacy_contexts=legacy_contexts if legacy_completed else None)
                if not continuous:
                    assessed = SecurityAssessment.model_validate(assessed, strict=True).model_dump()
                row.update(status="completed", result=assessed)
            except Exception as exc:
                row.update(status="failed", result=None, error=item.redact(str(exc))[:1000])
            return row

        confirmed_ids = set()
        counts = {"possible_findings": len(candidates), "selected_candidates": len(candidates), "assessed_candidates": 0,
                  "confirmed_candidates": 0, "rejected_candidates": 0, "uncertain_candidates": 0, "failed_candidates": 0,
                  "scanner_findings": scanner_findings}

        def assessment_progress(phase):
            progress({"phase": phase, **counts, "confirmed_findings": len(confirmed_ids)})

        def accept(row):
            nonlocal findings
            index, proposal, assessed = row["position"], row["snapshot"], row["result"]
            admitted_keys.pop(index, None)
            candidate_rows.append(row)
            counts["assessed_candidates"] += 1
            counts[(assessed["verdict"] if assessed else "failed") + "_candidates"] += 1
            if assessed and assessed["verdict"] == "confirmed":
                fingerprint = digest(json.dumps(["code", proposal["path"], proposal["category"],
                                                 proposal["evidence"][0]["quote"], proposal["title"]]))
                identity = assessed["finding_id"] or fingerprint[:32]
                evidence = {**proposal, "kind": "code", "source_sha": sha,
                            "source_hash": item.inventory[proposal["path"]]["hash"],
                            "fingerprint": fingerprint, "assessment": assessed, "sources": ["mortis"]}
                old = next((row for row in findings if row["id"] == identity), previous_ids.get(identity))
                finding = observe(evidence, old)
                findings = [row for row in findings if row["id"] != finding["id"]]
                findings.append(finding)
                observed.add(finding["id"])
                confirmed_ids.add(finding["id"])
                finding_positions[finding["id"]] = index
                finding_origins[finding["id"]] = row["id"]
                if continuous:
                    findings.sort(key=lambda old: finding_positions.get(old["id"], -1))
            assessment_progress("assessment")

        assessment_progress("assessment")
        await dispatch(remaining, options["concurrency"], select, assess, accept, continuous=continuous,
                       predecessors=dependencies, order=order)
        if continuous:
            candidate_rows.sort(key=lambda row: row["position"])
        for row in candidate_rows:
            units.append({"task": f"assessment:{row['position']}", "status": row["status"]})
            if row["status"] == "failed":
                problems.append(f"Security assessment incomplete: {row['error']}")
                continue
            assessments.append({"proposal": row["snapshot"], "result": row["result"]})
            if row["result"]["verdict"] == "uncertain":
                problems.append(f"Proposed security finding requires further verification: {row['snapshot']['title']}")
        stages["assessment"] = {"status": "completed" if all(row["status"] == "completed" for row in candidate_rows)
                               and not any(a["result"]["verdict"] == "uncertain" for a in assessments) else "partial",
                               "total": len(candidates), "completed": len(assessments)}
        advisory_cache = {}
        assessment_progress("verification")
        for old in previous_findings:
            if old["id"] in observed:
                continue
            if old.get("state", "open") not in {"open", "accepted_risk"}:
                findings.append(old)
                continue
            evidence = (old["evidence"] if isinstance(old.get("evidence"), dict) else old)
            try:
                if evidence["kind"] == "code":
                    original = evidence["source_sha"]
                    current_path = item.renamed(original, old["path"])
                    context = {"finding": old, "original_sha": original, "head_sha": sha,
                        "diff": item.diff(original, model_context=True), "sources": {
                            "original": {old["path"]: item.source(old["path"], original)},
                            "current": {}}}
                    current_source = item.current_source(current_path, model_context=True)
                    if current_source is not None:
                        context["sources"]["current"][current_path] = current_source
                    verdict, _ = await ask(item, session, "verification", context, "verification:" + old["id"], options)
                else:
                    verdict = await asyncio.to_thread(deterministic_fix, item, old, result,
                        advisory_lookup=lambda evidence, packages: advisories.dependency_advisory(evidence, packages, item.stopped, advisory_cache))
            except Exception as exc:
                verdict = {"verdict": "uncertain", "explanation": item.redact(str(exc))[:1000]}
            verifications.append({"finding_id": old["id"], **verdict})
            if verdict["verdict"] == "uncertain":
                problems.append(f"Fix verification remains uncertain for {old['title']}.")
            findings.append({**old, "fix_verification": verdict, "outcome": {
                "fixed": "fixed", "still_present": "present", "uncertain": "not_observed"}[verdict["verdict"]]})
        if not code_paths and not result["targets"]:
            problems.append("No supported security analysis targets were available.")
        rating = grade_for(row["severity"] for row in findings
            if row.get("state", "open") in {"open", "accepted_risk"}
            and not (not problems and row.get("fix_verification", {}).get("verdict") == "fixed"))
        if rating is None:
            problems.append("Unresolved findings have unknown severity; a security grade cannot be established.")
        # Complete source/scanner coverage is required before changing retained resolution state.
        if not problems:
            for finding in findings:
                if finding.get("fix_verification", {}).get("verdict") == "fixed":
                    finding["state"] = "resolved"
        else:
            for finding in findings:
                if finding.get("outcome") == "fixed":
                    finding["outcome"] = "not_observed"
        stages["verification"] = {"status": "partial" if any(v["verdict"] == "uncertain" for v in verifications) else "completed",
                                  "total": len(verifications), "completed": len(verifications)}
        stages["report"] = {"status": "completed"}
        assessment_progress("report")
        complete = not problems
        report = {"schema_version": "mortis.security/v1", "repository": repository_name, "commit": sha, "ref": ref,
            "status": "completed" if complete else "partial", "grade": rating if complete else None,
            "rating_policy": "highest-unresolved-severity/v1", "findings": findings, "assessments": assessments,
            "candidates": sorted(candidate_rows, key=lambda row: row["position"]),
            "verifications": verifications, "stages": stages, "problems": problems,
            "coverage": {"inventory": item.inventory, "excluded": item.excluded, "units": units,
                         "selected_files": len(code_paths), "completed_files": completed},
            "scanner": result.get("metadata", {}), "generated_at": datetime.now(UTC).isoformat()}
        return engine.redacted_context(item, report)
