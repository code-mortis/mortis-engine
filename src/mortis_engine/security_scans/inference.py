"""Bounded source requests and strict security response validation."""

import copy
import json
import time
from functools import partial

from mortis_engine.llm_http import InferenceError
from mortis_engine.review import file_policy, planning

from . import engine, payload, policy
from .context import source_navigation
from .contracts import (
    SecurityAnalysis,
    SecurityAnalysisRecovery,
    SecurityAnalysisSelection,
    SecurityAssessment,
    SecurityAssessmentSelection,
    SecurityFix,
    SecurityFixSelection,
)


async def ask(item, session, task, context, key, options, *, prepared=False, pin=None, legacy_contexts=None):
    model, selected = {"analysis": (SecurityAnalysis, SecurityAnalysisSelection),
        "assessment": (SecurityAssessment, SecurityAssessmentSelection),
        "verification": (SecurityFix, SecurityFixSelection)}[task]
    modern = policy.modern_scan(options, prepared=prepared)
    context = copy.deepcopy(context)
    if not prepared:
        context.update(task_contract=policy.TASK_CONTRACT, language=options["language"],
            available_paths=sorted(path for path in item.inventory if not file_policy.excluded(path, item.policy)))
        context = engine.redacted_context(item, context)
        if modern:
            current = context["sources"]["current"]
            primary = (context.get("assigned_path") or context.get("proposal", {}).get("path")
                       or context.get("finding", {}).get("path"))
            if primary in current:
                context["sources"]["current"] = {primary: current[primary],
                    **{path: source for path, source in current.items() if path != primary}}
            if task == "analysis" and "scanner_rules" in context:
                context["scanner_rules"] = policy.scanner_rules(context)
        else:
            # Preserve whole-corpus context for scans created before the v2 input policy.
            code = [path for path in context["available_paths"] if item.inventory[path]["code"]]
            if task in {"analysis", "assessment"} and len(code) <= 16:
                sources = {**context["sources"]["current"]}
                for path in code:
                    if path not in sources:
                        try:
                            sources[path] = item.source(path)
                        except ValueError:
                            continue
                if sum(len(text.encode()) for text in sources.values()) <= 32768:
                    context["sources"]["current"] = sources
        context["source_navigation"] = source_navigation(item, context["sources"]["current"],
                                                           context["available_paths"], item.stopped)
    if pin is not None:
        saved_context = pin(context)
        if saved_context is not None:
            context = saved_context
    if prepared or pin is not None:
        payload.check_saved_sources(context)
    history_ids = {}
    if task == "assessment":
        history = context.get("history", [])
        history_ids = {f"history_{index + 1}": old["id"] for index, old in enumerate(history)}
        context["history"] = [{**old, "id": reference} for reference, old in zip(history_ids, history, strict=True)]
    schema = engine.model_schema(selected)
    if task == "assessment":
        schema["properties"]["finding_id"]["enum"] = [*history_ids, None]
    if modern and task == "analysis":
        instructions = policy.COMPACT_ANALYSIS_INSTRUCTIONS
    else:
        instructions = engine.INSTRUCTIONS + {
            "analysis": "Audit the assigned file and report every evidenced security defect in that file. Supporting files are context.",
            "assessment": engine.ASSESSMENT_INSTRUCTIONS,
            "verification": "Verify the original security defect against both revisions. Absence from a scan never proves a fix. Fixed requires an actual change, original and current citations, and an explanation ruling out the original trigger.",
        }[task] + " " + policy.instructions(task)
    feedback = None
    source_ids = {}
    unavailable_sources = set()
    output_limit_recovery_used = False
    for attempt in range(4):
        catalog = {}
        for revision, sources in sorted(context["sources"].items()):
            for path, source in sorted(sources.items()):
                reference = source_ids.setdefault((revision, path), f"source_{len(source_ids) + 1}")
                catalog[reference] = {"revision": revision, "path": path, "line_count": len(source.splitlines())}
        context["citation_sources"] = catalog
        references = list(catalog) or ["unavailable"]
        schema["$defs"]["SecurityEvidenceSelection"]["properties"]["source_id"]["enum"] = references
        supplied = engine.redacted_context(item, {**context, **({"validation_feedback": feedback} if feedback else {})})
        if len(json.dumps(supplied).encode()) > 1_500_000:
            raise ValueError("Security evidence exceeds the 1.5 MB context limit.")

        def validate(raw, supplied=supplied, *, recovery=False):
            selection = SecurityAnalysisRecovery if recovery else selected
            value = selection.model_validate(raw, strict=True).model_dump()
            for path in value["required_paths"]:
                engine.relative_path(path)
            if task == "analysis":
                for index, proposal in enumerate(value["findings"]):
                    if recovery:
                        proposal["path"] = supplied["assigned_path"]
                        proposal["explanation"] = f"{proposal['trigger']} {proposal['impact']}"
                    if proposal["path"] != supplied["assigned_path"]:
                        raise ValueError("Security analysis returned a finding outside its assigned file.")
                    proposal["evidence"] = engine.selected_citations(proposal["evidence"], supplied,
                                                                     field=f"findings[{index}].evidence")
                    if not any(c["revision"] == "current" and c["path"] == supplied["assigned_path"]
                               for c in proposal["evidence"]):
                        raise ValueError("Security finding must cite its assigned current file.")
            else:
                value["evidence"] = engine.selected_citations(value["evidence"], supplied)
                revisions = engine.validate_citations(value["evidence"], supplied["sources"])
                if task == "assessment":
                    identity = value["finding_id"]
                    if identity is not None and identity not in history_ids:
                        raise ValueError("Assessment must select a supplied history reference or null.")
                    if value["verdict"] == "confirmed" and (not value["evidence"] or "current" not in revisions or value["required_paths"]):
                        raise ValueError("Confirmed finding needs complete current evidence and no missing source.")
                elif value["verdict"] == "fixed" and (revisions != {"original", "current"}
                        or not supplied["diff"].strip() or not value["original_issue_present"]
                        or not value["change_explanation"].strip() or value["required_paths"]):
                    value["verdict"] = "uncertain"
            return engine.redacted_context(item, model.model_validate(value, strict=True).model_dump())

        item.stopped()
        timeout = options.get("timeout_seconds")
        if getattr(item, "deadline", None) is not None:
            timeout = max(0.001, item.deadline - time.monotonic())
        stage, task_key = "security_" + task, key + f":pass:{attempt}"
        request_options = {"instructions": instructions, "schema": schema, "effort": options["effort"],
                           "max_output_tokens": options.get("max_output_tokens")}
        request_context = payload.compact_evidence(supplied)
        cached = session.records.get((stage, task_key)) if legacy_contexts else None
        if cached and planning.fingerprint(
                session.request(stage, task_key, context=request_context, **request_options).model_dump()) != cached[0]:
            for variant in legacy_contexts(supplied):
                alternative = payload.compact_evidence(variant)
                request = session.request(stage, task_key, context=alternative, **request_options)
                if planning.fingerprint(request.model_dump()) == cached[0]:
                    request_context = alternative
                    break
        try:
            result = await session.ask(stage, task_key, context=request_context, validate=validate, **request_options,
                token_budget=options.get({"analysis": "token_budget", "assessment": "assessment_token_budget",
                                          "verification": "verification_token_budget"}[task]),
                timeout=timeout)
        except InferenceError as exc:
            if (not modern or output_limit_recovery_used or exc.code != "output_limit" or exc.consumption_unknown
                    or exc.usage.total_tokens is None):
                raise
            output_limit_recovery_used = True
            recovery_options = {**request_options,
                                "instructions": instructions + " " + policy.OUTPUT_LIMIT_RECOVERY_INSTRUCTIONS}
            recovery_context = {**request_context, "output_limit_recovery": True}
            recovery_key, recovery_validate = task_key + ":output-limit-recovery:v1", validate
            if task == "analysis":
                recovery_schema = engine.model_schema(SecurityAnalysisRecovery)
                recovery_schema["$defs"]["SecurityEvidenceSelection"]["properties"]["source_id"]["enum"] = references
                recovery_options.update(instructions=policy.ANALYSIS_RECOVERY_INSTRUCTIONS, schema=recovery_schema)
                recovery_key = task_key + ":output-limit-recovery:v2"
                recovery_validate = partial(validate, recovery=True)
            result = await session.ask(stage, recovery_key,
                context=recovery_context, validate=recovery_validate, **recovery_options,
                token_budget=options.get({"analysis": "token_budget", "assessment": "assessment_token_budget",
                                          "verification": "verification_token_budget"}[task]),
                timeout=timeout)
        if not isinstance(result, dict):
            raise ValueError("Security inference returned an invalid result.")
        requested = result["required_paths"]
        if not requested:
            complete = not unavailable_sources
            if not complete and task != "analysis":
                result["verdict"] = "uncertain"
            if task == "verification" and result["verdict"] == "still_present" and not any(
                citation["revision"] == "current" for citation in result["evidence"]
            ):
                result = {**result, "verdict": "uncertain",
                          "explanation": "The original defect was not confirmed in current code. " + result["explanation"]}
            if task == "assessment" and result["finding_id"] is not None:
                result["finding_id"] = history_ids[result["finding_id"]]
            return result, sorted(context["sources"]["current"]), complete
        if attempt == 3:
            break
        unavailable = []
        added_source = False
        for path in dict.fromkeys(requested):
            if path not in context["available_paths"]:
                unavailable.append(path)
                continue
            if modern:
                if path not in context["sources"]["current"]:
                    if len(context["sources"]["current"]) >= 100:
                        raise ValueError("Security source requests reached the 100-file evidence limit.")
                    try:
                        context["sources"]["current"][path] = item.source(path)
                    except ValueError:
                        unavailable_sources.add(("current", path))
                        unavailable.append(path)
                        continue
                    unavailable_sources.discard(("current", path))
                    added_source = True
                if task == "verification" and path not in context["sources"].get("original", {}):
                    try:
                        context["sources"].setdefault("original", {})[path] = item.source(
                            path, context["original_sha"])
                    except ValueError:
                        unavailable_sources.add(("original", path))
                        unavailable.append(path)
                    else:
                        unavailable_sources.discard(("original", path))
                        added_source = True
                continue

            # Keep legacy saved-run feedback and request construction byte-for-byte stable.
            if path in context["sources"]["current"]:
                unavailable.append(path)
                continue
            if len(context["sources"]["current"]) >= 100:
                raise ValueError("Security source requests reached the 100-file evidence limit.")
            try:
                context["sources"]["current"][path] = item.source(path)
            except ValueError:
                unavailable_sources.add(("current", path))
                unavailable.append(path)
                continue
            unavailable_sources.discard(("current", path))
            added_source = True
            if task == "verification":
                try:
                    context["sources"].setdefault("original", {})[path] = item.source(path, context["original_sha"])
                except ValueError:
                    unavailable_sources.add(("original", path))
                else:
                    unavailable_sources.discard(("original", path))
        feedback = None
        if unavailable:
            feedback = {"kind": "evidence", "required_paths": unavailable,
                        "reason": "Use available_paths and complete sources already supplied. Missing evidence requires uncertainty."}
        elif not added_source:
            feedback = {"kind": "evidence", "required_paths": [],
                        "reason": "The requested source is already present. Use its supplied content and do not request it again."}
    if task != "analysis":
        result.update(verdict="uncertain", required_paths=[],
                      explanation="Required security evidence remains unavailable after four evidence passes.")
    if task == "assessment":
        result["finding_id"] = None
    return result, sorted(context["sources"]["current"]), False
