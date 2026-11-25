"""Portable review stages sharing the app's prompts and evidence validators."""

import copy
import json
from types import SimpleNamespace

from mortis_engine.assessment_dispatch import dispatch, replay_order
from mortis_engine.llm_http import InferenceError
from mortis_engine.security_scans.payload import check_saved_sources

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


async def _assess(repo, env, run, snapshot, previous, session, *, candidate_id=None, pinned_context=None,
                  predecessors=None, rebuild_saved=False):
    options = run.config["settings"]
    saved = None
    if pinned_context is None and candidate_id and getattr(session, "history", None):
        saved = session.history.assessment_input(session.run_id, candidate_id)
        if saved:
            pinned_context = saved["context"]
            options = {**options, "_evidence_policy": planning.saved_evidence_policy(saved["options"])}
            run = SimpleNamespace(base_sha=run.base_sha, head_sha=run.head_sha,
                                  config={**run.config, "settings": options})
    if pinned_context is None or (saved and rebuild_saved):
        context = assessment.build_context(repo, env, env, run, snapshot, previous, lambda: False)
        context["review_sensitivity"] = options.get("review_sensitivity")
        _related_paths(repo, env, context, options, run.base_sha, run.head_sha)
        context = review_context.sanitize(context, session.credentials)
        if saved:
            if context != pinned_context:
                raise ValueError("The saved candidate has different immutable assessment inputs.")
        elif candidate_id and getattr(session, "history", None):
            from mortis_engine.assessments import pin_input
            prepared = pin_input(session, candidate_id, "review", snapshot, context, options,
                                 planning.fingerprint(snapshot), repo, env,
                                 base_sha=run.base_sha, head_sha=run.head_sha,
                                 predecessors=predecessors(context) if predecessors is not None else None)
            if prepared is not None:
                context = prepared
    else:
        context = copy.deepcopy(pinned_context)
    if pinned_context is not None:
        check_saved_sources(context)
    # Same-head history does not establish attribution against this review's merge base.
    result = None if context.get("attribution", {}).get("version") == 1 else assessment.exact_repeat(context)
    if result is not None:
        return result
    instructions = assessment.INSTRUCTIONS + "\n\n" + policy.instructions(options)
    if context.get("attribution", {}).get("version") == 1:
        instructions += "\n\n" + assessment.ATTRIBUTION_INSTRUCTIONS
    if snapshot.get("affected_triggers"):
        instructions += (" The finding concerns a possible dependency on a deleted or renamed file. "
                         "Check reachability, replacement modules, aliases and working fallbacks. A matching "
                         "name alone is insufficient. Request original or current evidence if needed.")
    if options.get("review_mode") == "all":
        instructions = instructions.replace("introduced by the change", "present in the supplied snapshot")
    identity = planning.fingerprint(snapshot)
    for round in range(3):
        result = await _ask(session, "assessment", f"{identity}:{round}", context, instructions,
                            assessment.output_schema(context), assessment.validate_response, options)
        requested = result.pop("required_paths", [])
        base_requested = result.pop("required_base_paths", [])
        if not requested and not base_requested:
            return result
        if round == 2:
            return {**result, "verdict": "uncertain", "relationship": "uncertain", "finding_id": None,
                    "suggestion_supported": False,
                    "explanation": "Assessment still needs evidence after three passes. " + result["explanation"]}
        try:
            if requested:
                assessment.expand_context(repo, env, run, context, requested, lambda: False)
            if base_requested:
                assessment.expand_base_context(repo, env, run, context, base_requested, lambda: False)
        except verification.ResultValidationError as exc:
            context["validation_feedback"] = exc.feedback
    raise AssertionError("Bounded assessment did not produce a result")


def _verification_context(repo, env, original, original_sha, head_sha, options):
    context = verification.build_context(repo, env, env, original, original_sha, head_sha, options,
                                         lambda: False, base_sha=options.get("base_sha", head_sha))
    _related_paths(repo, env, context, options, options.get("base_sha", head_sha), head_sha)
    instructions = verification.INSTRUCTIONS
    if not planning.supports_test_evidence(options):
        instructions = instructions.replace(verification.TEST_EVIDENCE_INSTRUCTIONS, "")
    return context, instructions


def _restore_verification_policy(repo, env, report, head_sha, options, session):
    if not options.get("_unmarked_verification_policy"):
        return {}
    found_saved = False
    for original in report.get("findings", []):
        identity = original.get("id") or planning.fingerprint(original)
        key = f"{identity}:{head_sha}:0"
        cached = getattr(session, "records", {}).get(("verification", key))
        if not cached:
            continue
        found_saved = True
        original_sha = original.get("source_sha") or report["head_sha"]
        # Older standalone plans have no marker; only the actual request proves the policy.
        default = options.get("_evidence_policy")
        for selected_policy in (default, None if default == planning.EVIDENCE_POLICY else planning.EVIDENCE_POLICY):
            selected = {**options, "_evidence_policy": selected_policy}
            try:
                context, instructions = _verification_context(repo, env, original, original_sha, head_sha, selected)
                supplied, schema, _ = evidence.prepare(review_context.sanitize(context, session.credentials),
                                                        verification.output_schema())
                request = session.request("verification", key,
                    instructions=instructions + evidence.SELECTION_INSTRUCTIONS,
                    context=supplied, schema=schema, effort=options.get("effort", "medium"),
                    max_output_tokens=options.get("max_output_tokens"))
            except (ValueError, OSError):
                continue
            if planning.fingerprint(request.model_dump()) == cached[0]:
                options["_evidence_policy"] = selected_policy
                return {identity: (context, instructions)}
    if found_saved:
        raise ValueError("The saved verification task has different immutable inputs.")
    return {}


async def _verify_one(repo, env, original, original_sha, head_sha, options, session, *, prepared=None):
    identity = original.get("id") or planning.fingerprint(original)
    context, instructions = prepared or _verification_context(repo, env, original, original_sha, head_sha, options)
    for round in range(3):
        result = await _ask(session, "verification", f"{identity}:{head_sha}:{round}", context,
                            instructions, verification.output_schema(), verification.validate_response, options)
        requested = result.pop("required_sources", [])
        if not requested:
            # Resumed tasks can return a verdict saved by an earlier validator.
            return verification.validate_verdict(result, context)
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
    options = dict(options)
    rows, prepared, policy_error = {}, {}, None
    try:
        prepared = _restore_verification_policy(repo, env, original_report, current_sha, options, session)
    except ValueError as exc:
        policy_error = exc
    pending = list(enumerate(original_report.get("findings", [])))

    async def verify_row(candidate):
        index, snapshot = candidate
        original_sha = snapshot.get("source_sha") or original_report["head_sha"]
        row = {"finding_id": snapshot.get("id"), "snapshot": copy.deepcopy(snapshot),
               "original_sha": original_sha, "head_sha": current_sha}
        try:
            if policy_error:
                raise policy_error
            identity = snapshot.get("id") or planning.fingerprint(snapshot)
            selected = dict(options)
            selected_prepared = prepared.pop(identity, None)
            if selected_prepared is None:
                key = f"{identity}:{current_sha}:0"
                if (options.get("_unmarked_verification_policy")
                        and ("verification", key) in getattr(session, "records", {})):
                    selected_prepared = _restore_verification_policy(repo, env,
                        {"head_sha": original_report["head_sha"], "findings": [snapshot]}, current_sha, selected, session).get(identity)
            result = await _verify_one(repo, env, snapshot, original_sha, current_sha, selected, session,
                                       prepared=selected_prepared)
            row.update(status="completed", verdict=result["verdict"], result=result)
        except (ValueError, TimeoutError, InferenceError) as exc:
            row.update(status="failed", verdict="uncertain", result=None,
                       error=review_context.sanitize(str(exc), session.credentials))
        return index, row

    def select(waiting, active, blocked, limit, rank):
        # Repeated finding identities share durable request keys and must replay in order.
        occupied = {snapshot.get("id") or planning.fingerprint(snapshot) for _, snapshot in active}
        chosen = []
        for candidate in waiting:
            identity = candidate[1].get("id") or planning.fingerprint(candidate[1])
            if identity not in occupied:
                chosen.append(candidate)
                occupied.add(identity)
                if len(chosen) == limit:
                    break
        return chosen

    def accept(completed):
        index, row = completed
        rows[index] = row

    concurrency = options.get("concurrency", 1)
    await dispatch(pending, concurrency, select, verify_row, accept, continuous=True)
    rows = [rows[index] for index, _ in pending]
    return {"schema_version": "mortis.verification/v1", "head_sha": current_sha,
            "status": "partial" if any(row["status"] != "completed" or row["verdict"] == "uncertain" for row in rows) else "completed",
            "verifications": rows}


async def generate_summary(repo, env, report, options, session):
    run = _run(report, options, session)
    affected = report.get("affected_callers")
    caller_only = affected and not report.get("coverage", {}).get("selected")

    def local_summary():
        result = summary.caller_summary(repo, env, run, lambda: False, affected)
        return review_context.sanitize(result, session.credentials)

    if caller_only and not any(site.get("unit_key") for site in affected.get("sites", [])):
        return local_summary()
    try:
        context = summary.build_context(repo, env, run, lambda: False, affected=affected)
    except summary.SummaryUnavailable:
        if caller_only:
            return local_summary()
        raise
    if caller_only and not context["paths"]:
        return local_summary()
    context["diagram_enabled"] = options.get("summary_diagram_enabled", False)
    instructions = summary.INSTRUCTIONS
    if affected:
        instructions += (" Identify the removed or renamed dependency and the examined caller sites. "
                         "Caller ranges describe limited review scope. A discovered reference alone "
                         "does not establish breakage.")
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


def _independent_batch(repo, env, run, pending, previous, concurrency, *, active=(), blocked=(), rank=None):
    groups = []
    active_indices = {row[0] for row in active}

    def order(row):
        return rank(row[0]) if rank else row[0]

    for row in (*pending, *active):
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
    heads = [min(group, key=order) for group, _ in groups if active_indices.isdisjoint(row[0] for row in group)]
    return sorted((row for row in heads if row[0] not in blocked), key=order)[:concurrency]


async def postprocess(repo, env, report, options, session, *, history=(), progress=lambda value: None,
                      continuous=True):
    """Assess candidates, retain history, optionally verify fixes and summarize pinned changes."""
    result = copy.deepcopy(report)
    result["settings"] = {key: value for key, value in options.items() if not key.startswith("_")}
    run, previous = _run(result, options, session), _history(history)
    result["candidates"], result["findings"], result["stage_errors"] = [], [], []
    if result.get("comparison", {}).get("strategy") == "skipped_unchanged":
        result["history"] = [{**item, "observation": "not_observed"} for item in previous["findings"]]
        result["summary"] = {"status": "skipped", "reason": "A compatible review already covered identical content."}
        result["workflow_status"] = "completed"
        return result
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

    candidate_positions, finding_positions, created_findings = {}, {}, {}

    def context_predecessors(context):
        return sorted({row["id"] for row in context["prior_candidates"] if row["id"] in candidate_positions}
                      | {created_findings[row["id"]] for row in context["history"] if row["id"] in created_findings})

    async def assess_row(candidate):
        index, snapshot, candidate_id = candidate
        row = {"id": candidate_id, "snapshot": snapshot, "position": index}
        try:
            pinned = {"candidate_id": row["id"]} if getattr(session, "history", None) else {}
            if pinned:
                if continuous:
                    pinned["predecessors"] = context_predecessors
                else:
                    pinned["rebuild_saved"] = True
            assessment_result = await _assess(repo, env, run, snapshot, previous, session, **pinned)
            if not continuous:
                assessment_result = assessment.AssessmentResult.model_validate(assessment_result, strict=True).model_dump()
            row.update(status="completed", result=assessment_result)
        except (ValueError, TimeoutError, InferenceError) as exc:
            row.update(status="failed", result=None, error=review_context.sanitize(str(exc), session.credentials))
        return row

    pending = []
    for index, snapshot in enumerate(report.get("findings", [])):
        snapshot = {**snapshot, "source_sha": snapshot.get("source_sha") or report["head_sha"]}
        identity = planning.fingerprint({"run": report.get("run_id", report["head_sha"]), "index": index, "snapshot": snapshot})
        pending.append((index, snapshot, "candidate-" + identity[:32]))
    counts = {"possible_findings": len(pending), "selected_candidates": len(pending), "assessed_candidates": 0,
              "confirmed_candidates": 0, "rejected_candidates": 0, "uncertain_candidates": 0, "failed_candidates": 0}

    def report_progress(phase):
        progress({"phase": phase, **counts, "confirmed_findings": len(result["findings"])})

    def select(waiting, active, blocked, limit, rank):
        return _independent_batch(repo, env, run, waiting, previous, limit, active=active, blocked=blocked, rank=rank)

    def accept(row):
        snapshot, assessment_result = row["snapshot"], row.get("result")
        if assessment_result and assessment_result["verdict"] == "confirmed":
            finding_id = assessment_result["finding_id"] or "finding-" + row["id"].removeprefix("candidate-")
            finding = {**snapshot, "id": finding_id, "assessment": assessment_result, "status": "open"}
            if not assessment_result["suggestion_supported"]:
                finding["suggestion_code"] = ""
            existing = next((item for item in previous["findings"] if item["id"] == finding_id), None)
            if existing:
                finding["status"] = existing["status"]
            if finding_id not in finding_positions:
                result["findings"].append(finding)
                finding_positions[finding_id] = row["position"]
            row["finding_id"] = finding_id
            if existing is None:
                created_findings[finding_id] = row["id"]
                previous["findings"].append({"id": finding_id, "fingerprint": planning.fingerprint(snapshot),
                    "snapshot": finding, "sha": report["head_sha"], "assessment": assessment_result,
                    "status": "open", "review_sensitivity": options.get("review_sensitivity")})
                if continuous:
                    previous["findings"].sort(key=lambda item: finding_positions[item["id"]]
                                              if item["id"] in created_findings else -1)
        if row["status"] == "failed":
            result["stage_errors"].append({"stage": "assessment", "candidate_id": row["id"], "error": row["error"]})
        result["candidates"].append(row)
        previous["prior_candidates"].append({"id": row["id"], "snapshot": snapshot, "result": row.get("result")})
        candidate_positions[row["id"]] = row["position"]
        if continuous:
            previous["prior_candidates"].sort(key=lambda item: candidate_positions.get(item["id"], -1))
        counts["assessed_candidates"] += 1
        counts[(assessment_result["verdict"] if assessment_result else "failed") + "_candidates"] += 1
        report_progress("assessment")

    report_progress("assessment")
    concurrency = options.get("concurrency", 1)
    if not continuous and options.get("assessment_token_budget") is not None:
        # Legacy runs reconstruct saved inputs from their original serial assessment order.
        concurrency = 1
    dependencies, order = replay_order(session, {row[0]: row[2] for row in pending}) if continuous else ({}, {})
    await dispatch(pending, concurrency, select, assess_row, accept, continuous=continuous,
                   predecessors=dependencies, order=order)
    result["candidates"].sort(key=lambda item: item["position"])
    if continuous:
        result["findings"].sort(key=lambda item: finding_positions[item["id"]])
        result["stage_errors"].sort(key=lambda item: candidate_positions[item["candidate_id"]])
    observed = {item["id"] for item in result["findings"]}
    prior_ids = {item["id"] for item in _history(history)["findings"]}
    result["history"] = [{**item, "observation": "observed" if item["id"] in observed else "not_observed"}
                         for item in previous["findings"] if item["id"] in prior_ids]
    if result.get("coverage", {}).get("selected") or result.get("affected_callers"):
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
    if getattr(session, "history", None):
        from mortis_engine.findings import apply_decisions
        origin = session.history.row(session.run_id)
        apply_decisions(session.history, result, "review", origin["scope"], options.get("_triage_snapshot"))
    if options.get("ai_fix_prompts_enabled"):
        prompts = {item["id"]: item["snapshot"] for item in result["history"]
                   if item["status"] == "open" and item.get("verification", {}).get("verdict") != "fixed"}
        prompts.update({item["id"]: item for item in result["findings"] if item["status"] == "open"})
        result["fix_prompts"] = summary.fix_section(list(prompts.values()))
    incomplete = bool(result["stage_errors"]) or any(
        row.get("result", {}).get("verdict") == "uncertain" for row in result["candidates"] if row.get("result"))
    if result.get("affected_callers", {}).get("status") in {"partial", "failed"}:
        incomplete = True
    if result.get("verification", {}).get("status") == "partial":
        incomplete = True
    result["workflow_status"] = "partial" if incomplete else "completed"
    if incomplete and result["status"] == "completed":
        result["status"] = "partial"
    return result
