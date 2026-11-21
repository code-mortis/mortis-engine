"""Bounded source requests and strict security response validation."""

import copy
import json
import time

from mortis_engine.review import file_policy, planning

from . import engine, payload, policy
from .context import source_navigation
from .contracts import (
    SecurityAnalysis,
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
    context = copy.deepcopy(context)
    if not prepared:
        context.update(task_contract=policy.TASK_CONTRACT, language=options["language"],
            available_paths=sorted(path for path in item.inventory if not file_policy.excluded(path, item.policy)))
        context = engine.redacted_context(item, context)
        # Small repositories carry their complete supporting evidence into independent assessment.
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
    instructions = engine.INSTRUCTIONS + {
        "analysis": "Audit the assigned file and report every evidenced security defect in that file. Supporting files are context.",
        "assessment": engine.ASSESSMENT_INSTRUCTIONS,
        "verification": "Verify the original security defect against both revisions. Absence from a scan never proves a fix. Fixed requires an actual change, original and current citations, and an explanation ruling out the original trigger.",
    }[task] + " " + policy.instructions(task)
    feedback = None
    source_ids = {}
    unreadable = set()
    for attempt in range(4):
        catalog = {}
        for revision, sources in sorted(context["sources"].items()):
            for path, source in sorted(sources.items()):
                reference = source_ids.setdefault((revision, path), f"source_{len(source_ids) + 1}")
                catalog[reference] = {"revision": revision, "path": path, "line_count": len(source.splitlines())}
        context["citation_sources"] = catalog
        evidence_schema = schema["$defs"]["SecurityEvidenceSelection"]["properties"]
        evidence_schema["source_id"]["enum"] = list(catalog) or ["unavailable"]
        supplied = engine.redacted_context(item, {**context, **({"validation_feedback": feedback} if feedback else {})})
        if len(json.dumps(supplied).encode()) > 1_500_000:
            raise ValueError("Security evidence exceeds the 1.5 MB context limit.")

        def validate(raw, supplied=supplied):
            value = selected.model_validate(raw, strict=True).model_dump()
            for path in value["required_paths"]:
                engine.relative_path(path)
            if task == "analysis":
                for index, proposal in enumerate(value["findings"]):
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
        result = await session.ask(stage, task_key, context=request_context, validate=validate, **request_options,
            token_budget=options.get({"analysis": "token_budget", "assessment": "assessment_token_budget",
                                      "verification": "verification_token_budget"}[task]),
            timeout=timeout)
        requested = result["required_paths"]
        if not requested:
            if task == "verification" and result["verdict"] == "still_present" and not any(
                citation["revision"] == "current" for citation in result["evidence"]
            ):
                result = {**result, "verdict": "uncertain",
                          "explanation": "The original defect was not confirmed in current code. " + result["explanation"]}
            if task == "assessment" and result["finding_id"] is not None:
                result["finding_id"] = history_ids[result["finding_id"]]
            return result, sorted(context["sources"]["current"]), not unreadable
        if attempt == 3:
            break
        unavailable = []
        for path in dict.fromkeys(requested):
            if path not in context["available_paths"] or path in context["sources"]["current"]:
                unavailable.append(path)
                continue
            if len(context["sources"]["current"]) >= 100:
                raise ValueError("Security source requests reached the 100-file evidence limit.")
            try:
                context["sources"]["current"][path] = item.source(path)
            except ValueError:
                unreadable.add(path)
                unavailable.append(path)
                continue
            unreadable.discard(path)
            if task == "verification":
                try:
                    context["sources"].setdefault("original", {})[path] = item.source(path, context["original_sha"])
                except ValueError:
                    pass
        feedback = {"kind": "evidence", "required_paths": unavailable,
                    "reason": "Use available_paths and complete sources already supplied. Missing evidence requires uncertainty."} if unavailable else None
    if task != "analysis":
        result.update(verdict="uncertain", required_paths=[],
                      explanation="Required security evidence remains unavailable after four evidence passes.")
    if task == "assessment":
        result["finding_id"] = None
    return result, sorted(context["sources"]["current"]), False
