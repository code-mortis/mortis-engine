"""Portable review stages sharing the app's prompts and evidence validators."""

import asyncio
import copy
import json
from types import SimpleNamespace

from mortis_engine.llm_http import InferenceError

from . import assessment, background, evidence, planning, policy, summary, verification
from . import context as review_context


def _run(report, options, session):
    return SimpleNamespace(base_sha=report["base_sha"], head_sha=report["head_sha"],
                           config={"settings": options, "connection": session.connection},
                           report={"manifest": {"coverage": report.get("coverage", {})}})


def _history(reports):
    findings, candidates = {}, []
    for report in reports:
        for item in report.get("history", []):
            if item.get("id") and isinstance(item.get("snapshot"), dict):
                findings[item["id"]] = copy.deepcopy(item)
        for index, snapshot in enumerate(report.get("findings", [])):
            identity = snapshot.get("id")
            if identity:
                findings[identity] = {
                    **findings.get(identity, {}), "id": identity, "fingerprint": planning.fingerprint(snapshot),
                    "status": snapshot.get("status", "open"), "sha": snapshot.get("source_sha") or report["head_sha"],
                    "snapshot": snapshot, "assessment": snapshot.get("assessment"),
                    "review_sensitivity": report.get("settings", {}).get("review_sensitivity"),
                }
            else:
                candidates.append({"id": f"legacy-{report['head_sha']}-{index}", "snapshot": snapshot, "result": None})
        candidates.extend({"id": row["id"], "snapshot": row["snapshot"], "result": row.get("result")}
                          for row in report.get("candidates", []))
    return {"findings": list(findings.values()), "prior_candidates": candidates}


async def _ask(session, stage, key, context, instructions, schema, validator, options):
    context = review_context.sanitize(context, session.credentials)
    supplied, selected_schema, history = evidence.prepare(context, schema)
    return await session.ask(
        stage, key, instructions=instructions + evidence.SELECTION_INSTRUCTIONS,
        context=supplied, schema=selected_schema,
        validate=lambda raw: validator(evidence.resolve(raw, supplied, history), context),
        effort=options.get("effort", "medium"), token_budget=options.get(f"{stage}_token_budget"),
        max_output_tokens=options.get("max_output_tokens"),
        timeout=options.get("summary_timeout_seconds") if stage == "summary" else options.get("timeout_seconds"),
    )


def _related_paths(repo, env, context, options, base_sha, head_sha):
    path = context.get("current_path")
    graph = options.get("_graph", {}).get(path, {})
    if path and graph.get("commit_sha") == head_sha:
        hints = {item["path"] for item in graph.get("sources", []) if isinstance(item.get("path"), str)}
        permitted = verification.filter_context(repo, env, dict.fromkeys(hints, "# context\n"), options,
                                                lambda: False, base_sha=base_sha, head_sha=head_sha)
        if permitted:
            context["related_paths"] = sorted(permitted)[:20]


async def _assess(repo, env, run, snapshot, previous, session):
    options = run.config["settings"]
    context = assessment.build_context(repo, env, env, run, snapshot, previous, lambda: False)
    context["review_sensitivity"] = options.get("review_sensitivity")
    _related_paths(repo, env, context, options, run.base_sha, run.head_sha)
    result = assessment.exact_repeat(review_context.sanitize(context, session.credentials))
    if result is not None:
        return result
    instructions = assessment.INSTRUCTIONS + "\n\n" + policy.instructions(options)
    if options.get("review_mode") == "all":
        instructions = instructions.replace("introduced by the change", "present in the supplied snapshot")
    identity = planning.fingerprint(snapshot)
    for round in range(3):
        result = await _ask(session, "assessment", f"{identity}:{round}", context, instructions,
                            assessment.output_schema(context), assessment.validate_response, options)
        requested = result.pop("required_paths", [])
        if not requested:
            return result
        if round == 2:
            return {**result, "verdict": "uncertain", "relationship": "uncertain", "finding_id": None,
                    "suggestion_supported": False,
                    "explanation": "Assessment still needs evidence after three passes. " + result["explanation"]}
        try:
            assessment.expand_context(repo, env, run, context, requested, lambda: False)
        except verification.ResultValidationError as exc:
            context["validation_feedback"] = exc.feedback
    raise AssertionError("Bounded assessment did not produce a result")


async def _verify_one(repo, env, original, original_sha, head_sha, options, session):
    context = verification.build_context(repo, env, env, original, original_sha, head_sha, options,
                                         lambda: False, base_sha=options.get("base_sha", head_sha))
    _related_paths(repo, env, context, options, options.get("base_sha", head_sha), head_sha)
    identity = original.get("id") or planning.fingerprint(original)
    for round in range(3):
        result = await _ask(session, "verification", f"{identity}:{head_sha}:{round}", context,
                            verification.INSTRUCTIONS, verification.output_schema(), verification.validate_response, options)
        requested = result.pop("required_sources", [])
        if not requested:
            return result
        if round == 2:
            return {**result, "verdict": "uncertain",
                    "explanation": "Verification still needs evidence after three passes. " + result["explanation"]}
        try:
            verification.expand_context(repo, env, context, requested, options, lambda: False)
        except verification.ResultValidationError as exc:
            context["validation_feedback"] = exc.feedback
    raise AssertionError("Bounded verification did not produce a result")


async def verify(repo, env, original_report, current_sha, options, session):
    """Verify each retained finding without changing the original report or finding status."""
    rows = []
    for snapshot in original_report.get("findings", []):
        original_sha = snapshot.get("source_sha") or original_report["head_sha"]
        row = {"finding_id": snapshot.get("id"), "snapshot": copy.deepcopy(snapshot),
               "original_sha": original_sha, "head_sha": current_sha}
        try:
            result = await _verify_one(repo, env, snapshot, original_sha, current_sha, options, session)
            row.update(status="completed", verdict=result["verdict"], result=result)
        except (ValueError, TimeoutError, InferenceError) as exc:
            row.update(status="failed", verdict="uncertain", result=None,
                       error=review_context.sanitize(str(exc), session.credentials))
        rows.append(row)
    return {"schema_version": "mortis.verification/v1", "head_sha": current_sha,
            "status": "partial" if any(row["status"] != "completed" or row["verdict"] == "uncertain" for row in rows) else "completed",
            "verifications": rows}


async def generate_summary(repo, env, report, options, session):
    run = _run(report, options, session)
    context = summary.build_context(repo, env, run, lambda: False)
    context["diagram_enabled"] = options.get("summary_diagram_enabled", False)
    instructions = summary.INSTRUCTIONS
    if options.get("review_mode") == "all":
        instructions = instructions.replace("pull request changes", "repository snapshot").replace("changed paths", "paths")
    if context["diagram_enabled"]:
        instructions += " Include a small diagram as nodes and directed edges grounded in supplied paths. Use at most 12 nodes and 20 edges. Do not output raw Mermaid or HTML."
    else:
        instructions += " Do not generate a diagram."
    schema = summary.SummaryResult.model_json_schema()
    if not context["diagram_enabled"]:
        schema["properties"].pop("diagram", None)
    context = review_context.sanitize(context, session.credentials)
    result = await session.ask(
        "summary", report["head_sha"], instructions=instructions, context=context, schema=schema,
        validate=lambda value: summary.validate_response(value, context), effort=options.get("effort", "medium"),
        token_budget=options.get("summary_token_budget"), timeout=options.get("summary_timeout_seconds"),
        max_output_tokens=options.get("max_output_tokens"),
    )
    return {"status": "completed", "result": result, "limited_context": context["limited_context"],
            **({"warning": context["diagram_warning"]} if context.get("diagram_warning") else {})}


async def generate_background(repo, env, sha, options, session, *, repository="local", branch="HEAD"):
    data = {"repository": repository, "branch": branch, "sha": sha,
            **background.collect(repo, env, sha, lambda: False, exclusions=options.get("exclusions", ""))}
    while len(json.dumps(data, ensure_ascii=False).encode()) > background.CONTEXT_BYTES and data["paths"]:
        data["paths"].pop()
        data["tree_truncated"] = True
    while len(json.dumps(data, ensure_ascii=False).encode()) > background.CONTEXT_BYTES and data["sources"]:
        data["sources"].pop(next(reversed(data["sources"])))
    if not data["sources"]:
        raise ValueError("Repository context exceeded the background generation limit.")
    return await session.ask(
        "background", sha, instructions=background.INSTRUCTIONS,
        context=review_context.sanitize(data, session.credentials), schema=background.output_schema(),
        validate=lambda value: {"background": background.validate_result(value)},
        effort=options.get("effort", "medium"), token_budget=options.get("token_budget"),
        timeout=options.get("timeout_seconds"),
        max_output_tokens=options.get("max_output_tokens"),
    )


def _independent_batch(repo, env, run, pending, previous, concurrency):
    groups = []
    for row in pending:
        _, snapshot, _ = row
        keys = {("path", snapshot["path"])}
        if snapshot.get("existing_code", "").strip():
            keys.add(("anchor", snapshot["existing_code"].strip()))
        related, prior = assessment.related_history(repo, env, env, run.head_sha, snapshot, previous, lambda: False)
        keys.update(("finding", item["id"]) for item in related)
        keys.update(("candidate", item["id"]) for item in prior)
        members, remaining = [row], groups
        while True:
            unmatched, merged = [], False
            for group, connected in remaining:
                if keys & connected:
                    members.extend(group)
                    keys.update(connected)
                    merged = True
                else:
                    unmatched.append((group, connected))
            remaining = unmatched
            if not merged:
                break
        groups = [*remaining, (members, keys)]
    return sorted((min(group, key=lambda row: row[0]) for group, _ in groups), key=lambda row: row[0])[:concurrency]


async def postprocess(repo, env, report, options, session, *, history=(), progress=lambda value: None):
    """Assess candidates, retain history, optionally verify fixes and summarize pinned changes."""
    result = copy.deepcopy(report)
    result["settings"] = {key: value for key, value in options.items() if not key.startswith("_")}
    run, previous = _run(result, options, session), _history(history)
    result["candidates"], result["findings"], result["stage_errors"] = [], [], []
    if options.get("auto_resolve") and not report.get("coverage", {}).get("waived"):
        original = {"head_sha": report["head_sha"], "findings": [
            item["snapshot"] for item in previous["findings"]
            if item["status"] == "open" and item["sha"] != report["head_sha"]]}
        result["verification"] = await verify(repo, env, original, report["head_sha"],
                                              {**options, "base_sha": report["base_sha"]}, session)
        verified = {row["finding_id"]: row for row in result["verification"]["verifications"]}
        for item in previous["findings"]:
            if item["id"] in verified:
                item["verification"] = verified[item["id"]]
                item["reasons"] = [verified[item["id"]].get("result", {}).get("explanation", "Verification unavailable")
                                   if verified[item["id"]].get("result") else "Verification unavailable"]

    async def assess_row(index, snapshot, identity):
        row = {"id": "candidate-" + identity[:32], "snapshot": snapshot, "position": index}
        try:
            assessment_result = await _assess(repo, env, run, snapshot, previous, session)
            row.update(status="completed", result=assessment_result)
        except (ValueError, TimeoutError, InferenceError) as exc:
            row.update(status="failed", result=None, error=review_context.sanitize(str(exc), session.credentials))
        return row

    pending = []
    for index, snapshot in enumerate(report.get("findings", [])):
        snapshot = {**snapshot, "source_sha": snapshot.get("source_sha") or report["head_sha"]}
        identity = planning.fingerprint({"run": report.get("run_id", report["head_sha"]), "index": index, "snapshot": snapshot})
        pending.append((index, snapshot, identity))
    counts = {"possible_findings": len(pending), "selected_candidates": len(pending), "assessed_candidates": 0,
              "confirmed_candidates": 0, "rejected_candidates": 0, "uncertain_candidates": 0, "failed_candidates": 0}

    def report_progress(phase):
        progress({"phase": phase, **counts, "confirmed_findings": len(result["findings"])})

    report_progress("assessment")
    concurrency = options.get("concurrency", 1) if options.get("assessment_token_budget") is None else 1
    while pending:
        batch = _independent_batch(repo, env, run, pending, previous, concurrency)
        outcomes = await asyncio.gather(*(assess_row(*item) for item in batch))
        for row in outcomes:
            snapshot, assessment_result = row["snapshot"], row.get("result")
            if assessment_result and assessment_result["verdict"] == "confirmed":
                finding_id = assessment_result["finding_id"] or "finding-" + row["id"].removeprefix("candidate-")
                finding = {**snapshot, "id": finding_id, "assessment": assessment_result, "status": "open"}
                if not assessment_result["suggestion_supported"]:
                    finding["suggestion_code"] = ""
                existing = next((item for item in previous["findings"] if item["id"] == finding_id), None)
                if existing:
                    finding["status"] = existing["status"]
                if not any(item["id"] == finding_id for item in result["findings"]):
                    result["findings"].append(finding)
                row["finding_id"] = finding_id
                if existing is None:
                    previous["findings"].append({"id": finding_id, "fingerprint": planning.fingerprint(snapshot),
                        "snapshot": finding, "sha": report["head_sha"], "assessment": assessment_result,
                        "status": "open", "review_sensitivity": options.get("review_sensitivity")})
            if row["status"] == "failed":
                result["stage_errors"].append({"stage": "assessment", "candidate_id": row["id"], "error": row["error"]})
            result["candidates"].append(row)
            previous["prior_candidates"].append({"id": row["id"], "snapshot": snapshot, "result": row.get("result")})
            counts["assessed_candidates"] += 1
            counts[(assessment_result["verdict"] if assessment_result else "failed") + "_candidates"] += 1
            report_progress("assessment")
        finished = {item[0] for item in batch}
        pending = [item for item in pending if item[0] not in finished]
    result["candidates"].sort(key=lambda item: item["position"])
    observed = {item["id"] for item in result["findings"]}
    prior_ids = {item["id"] for item in _history(history)["findings"]}
    result["history"] = [{**item, "observation": "observed" if item["id"] in observed else "not_observed"}
                         for item in previous["findings"] if item["id"] in prior_ids]
    if result.get("coverage", {}).get("selected"):
        report_progress("summary")
        try:
            result["summary"] = await generate_summary(repo, env, report, options, session)
        except (ValueError, TimeoutError, InferenceError) as exc:
            error = review_context.sanitize(str(exc), session.credentials)
            result["summary"] = {"status": "failed", "error": error}
            result["stage_errors"].append({"stage": "summary", "error": error})
    else:
        result["summary"] = {"status": "skipped", "reason": "No eligible changed files to summarize."}
    if (result["status"] == "completed" and not result["stage_errors"]
            and all(row.get("result", {}).get("verdict") != "uncertain" for row in result["candidates"])):
        for item in result["history"]:
            if (item["status"] == "open" and options.get("auto_resolve") and item["observation"] == "not_observed"
                    and item.get("verification", {}).get("verdict") == "fixed"
                    and item["verification"].get("head_sha") == report["head_sha"]):
                item["status"] = "resolved"
    if options.get("ai_fix_prompts_enabled"):
        prompts = {item["id"]: item["snapshot"] for item in result["history"]
                   if item["status"] == "open" and item.get("verification", {}).get("verdict") != "fixed"}
        prompts.update({item["id"]: item for item in result["findings"] if item["status"] == "open"})
        result["fix_prompts"] = summary.fix_section(list(prompts.values()))
    incomplete = bool(result["stage_errors"]) or any(
        row.get("result", {}).get("verdict") == "uncertain" for row in result["candidates"] if row.get("result"))
    if result.get("verification", {}).get("status") == "partial":
        incomplete = True
    result["workflow_status"] = "partial" if incomplete else "completed"
    if incomplete and result["status"] == "completed":
        result["status"] = "partial"
    return result
