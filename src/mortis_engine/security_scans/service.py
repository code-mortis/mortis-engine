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

from mortis_engine.review import file_policy, source
from mortis_engine.schemas import StrictModel
from mortis_engine.security import digest

from . import advisories, engine
from .inference import ask
from .scanner import identity_key, trivy


class ScanOptions(StrictModel):
    concurrency: int = Field(default=4, ge=1, le=8, strict=True)
    effort: Literal["low", "medium", "high"] = "high"
    language: str = Field(default="English", min_length=1, max_length=100)
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    token_budget: int | None = Field(default=None, ge=1000, le=5000000)
    assessment_token_budget: int | None = Field(default=None, ge=1000, le=5000000)
    verification_token_budget: int | None = Field(default=None, ge=1000, le=5000000)
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


def assessment_batch(candidates, history, item, concurrency):
    chosen, occupied = [], set()
    for candidate in candidates:
        keys = assessment_keys(candidate[1], item, history)
        if not keys & occupied:
            chosen.append(candidate)
            occupied.update(keys)
            if len(chosen) >= concurrency:
                break
    return chosen


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
    if not isinstance(findings, list) or len(findings) > 10000:
        raise ValueError("Previous security finding history is invalid or exceeds 10000 records.")
    return json.loads(json.dumps(findings))


def secret_rules(report):
    rules = []
    for finding in history_findings(report):
        evidence = finding["evidence"] if isinstance(finding.get("evidence"), dict) else finding
        if evidence.get("kind") == "secret":
            rules.append({key: evidence[key] for key in ("rule_id", "match_bytes", "identity")})
    return rules


async def scan(repository, *, ref="HEAD", session, options=None, previous=None, scanner=None,
               progress=lambda value: None):
    options = ScanOptions.model_validate(options or {}).model_dump()
    deadline = time.monotonic() + options["timeout_seconds"] if options["timeout_seconds"] else None

    def stopped():
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("Security scan reached its overall time limit.")
        return False

    policy = file_policy.policy(options)
    repository_name = str(Path(repository).expanduser().resolve())
    if previous and previous.get("repository", repository_name) != repository_name:
        raise ValueError("Security history belongs to a different local repository.")
    previous_findings = history_findings(previous)
    stages = {name: {"status": "pending"} for name in ("snapshot", "trivy", "analysis", "assessment", "verification", "report")}
    findings, assessments, verifications, problems = [], [], [], []
    units, observed = [], set()
    progress({"phase": "snapshot"})
    with source.local_repository(repository) as (repo, env), tempfile.TemporaryDirectory(prefix="mortis-security-") as temporary:
        sha = source.resolve_commit(repo, env, ref)
        item = engine.Snapshot(Path(temporary), sha, env, stopped, git=repo, key=identity_key(create=not (previous and secret_rules(previous))))
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
        item.secret_rules.extend(secret_rules(previous) if previous else [])
        previous_ids = {old["id"]: old for old in previous_findings}
        by_fingerprint = {old.get("fingerprint"): old for old in previous_findings}
        for finding in result["findings"]:
            old = by_fingerprint.get(finding["fingerprint"])
            observation = observe(finding, old)
            observed.add(observation["id"])
            findings.append(observation)
        code_paths = [path for path, row in item.inventory.items() if row["code"]]
        semaphore = asyncio.Semaphore(options["concurrency"])
        candidates = []
        active_paths = set()

        def report_progress():
            progress({"phase": "analysis", "selected_files": len(code_paths),
                "completed_files": sum(unit["status"] == "completed" for unit in units),
                "failed_files": sum(unit["status"] == "failed" for unit in units),
                "active_requests": len(active_paths), "active_paths": sorted(item.redact(path) for path in active_paths)})

        async def analyze(path):
            async with semaphore:
                active_paths.add(path)
                report_progress()
                try:
                    context = {"assigned_path": path, "head_sha": sha, "diff": "",
                               "sources": {"current": {path: item.source(path)}}}
                    value, context_paths = await ask(item, session, "analysis", context, "analysis:" + path, options)
                    units.append({"task": "analysis:" + path, "status": "completed"})
                    return [(proposal, context_paths) for proposal in value["findings"]]
                except Exception as exc:
                    units.append({"task": "analysis:" + path, "status": "failed"})
                    problems.append(f"Security analysis incomplete for {path}: {item.redact(str(exc))[:1000]}")
                    return []
                finally:
                    active_paths.remove(path)
                    report_progress()

        report_progress()
        for proposed in await asyncio.gather(*(analyze(path) for path in code_paths)):
            candidates.extend(proposed)
        completed = sum(unit["status"] == "completed" for unit in units)
        stages["analysis"] = {"status": "completed" if completed == len(code_paths) else "partial",
                              "total": len(code_paths), "completed": completed}
        async def assess(candidate, history):
            index, proposal, paths = candidate
            try:
                context = {"proposal": proposal, "history": assessment_history(proposal, history, item),
                           "head_sha": sha, "diff": "",
                           "sources": {"current": {path: item.source(path) for path in paths}}}
                assessed, _ = await ask(item, session, "assessment", context, f"assessment:{index}", options)
                return assessed
            except Exception as exc:
                problems.append(f"Security assessment incomplete: {item.redact(str(exc))[:1000]}")
                units.append({"task": f"assessment:{index}", "status": "failed"})
                return None

        remaining = [(index, proposal, paths) for index, (proposal, paths) in enumerate(candidates)]
        progress({"phase": "assessment"})
        while remaining:
            history = {old["id"]: old for old in [*previous_findings, *findings]}
            batch = assessment_batch(remaining, list(history.values()), item, options["concurrency"])
            results = await asyncio.gather(*(assess(candidate, list(history.values())) for candidate in batch))
            for (index, proposal, _paths), assessed in zip(batch, results, strict=True):
                if assessed is None:
                    continue
                assessments.append({"proposal": proposal, "result": assessed})
                units.append({"task": f"assessment:{index}", "status": "completed"})
                if assessed["verdict"] == "uncertain":
                    problems.append(f"Proposed security finding requires further verification: {proposal['title']}")
                if assessed["verdict"] != "confirmed":
                    continue
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
            selected_indices = {candidate[0] for candidate in batch}
            remaining = [candidate for candidate in remaining if candidate[0] not in selected_indices]
        assessment_units = [unit for unit in units if unit["task"].startswith("assessment:")]
        stages["assessment"] = {"status": "completed" if all(a["status"] == "completed" for a in assessment_units)
                               and not any(a["result"]["verdict"] == "uncertain" for a in assessments) else "partial",
                               "total": len(candidates), "completed": len(assessments)}
        advisory_cache = {}
        progress({"phase": "verification"})
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
        progress({"phase": "report"})
        complete = not problems
        rating = grade_for(row["severity"] for row in findings if row.get("state", "open") in {"open", "accepted_risk"})
        report = {"schema_version": "mortis.security/v1", "repository": repository_name, "commit": sha, "ref": ref,
            "status": "completed" if complete else "partial", "grade": rating if complete else None,
            "rating_policy": "highest-unresolved-severity/v1", "findings": findings, "assessments": assessments,
            "verifications": verifications, "stages": stages, "problems": problems,
            "coverage": {"inventory": item.inventory, "excluded": item.excluded, "units": units,
                         "selected_files": len(code_paths), "completed_files": completed},
            "scanner": result.get("metadata", {}), "generated_at": datetime.now(UTC).isoformat()}
        return engine.redacted_context(item, report)
