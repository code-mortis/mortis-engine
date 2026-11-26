"""Source selections resolve to quotes from supplied immutable source only."""

import copy

from pydantic import Field

from mortis_engine.schemas import StrictModel

from . import source_lines
from .context import source_id
from .verification import ResultValidationError

SELECTION_INSTRUCTIONS = (
    " Select evidence using only source_id, start_line and end_line from citation_sources. "
    "Mortis constructs quotes, paths and revisions. Select finding_id only from supplied "
    "history references; never copy an ID from prior_candidates."
)


class Selection(StrictModel):
    source_id: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


def prepare(context, schema):
    supplied, schema = copy.deepcopy(context), copy.deepcopy(schema)
    supplied["task_contract"] = "review-task/v2"
    supplied["citation_sources"] = {
        source_id(revision, path): {"revision": revision, "path": path, "line_count": len(source_lines.split(text))}
        for revision, sources in sorted(context["sources"].items()) for path, text in sorted(sources.items())
    }
    definition = Selection.model_json_schema()
    definition["properties"]["source_id"]["enum"] = list(supplied["citation_sources"]) or ["unavailable"]
    schema.setdefault("$defs", {})["SelectedEvidence"] = definition
    schema["properties"]["evidence"]["items"] = {"$ref": "#/$defs/SelectedEvidence"}
    history = {f"history_{i + 1}": item["id"] for i, item in enumerate(context.get("history", []))}
    if "history" in supplied:
        supplied["history"] = [{**item, "id": ref} for ref, item in zip(history, supplied["history"], strict=True)]
        schema["properties"]["finding_id"] = {"anyOf": [
            {"type": "string", "enum": list(history)}, {"type": "null"}
        ]} if history else {"type": "null"}
    return supplied, schema, history


def resolve(raw, supplied, history):
    if not isinstance(raw, dict) or not isinstance(raw.get("evidence"), list):
        raise ResultValidationError("Evidence selections must be a list.", {"kind": "citation_selection"})
    result, citations = dict(raw), []
    for index, item in enumerate(raw["evidence"]):
        selection = Selection.model_validate(item, strict=True)
        source = supplied["citation_sources"].get(selection.source_id)
        if source is None:
            raise ResultValidationError("Source identity was not supplied.", {"kind": "citation_selection", "field": f"evidence[{index}]"})
        text = supplied["sources"][source["revision"]][source["path"]]
        lines = source_lines.split(text, keepends=True)
        if not 1 <= selection.start_line <= selection.end_line <= len(lines):
            raise ResultValidationError("Evidence range exceeds supplied source.", {"kind": "citation_selection", "field": f"evidence[{index}]"})
        quote = "".join(lines[selection.start_line - 1:selection.end_line])
        if not quote.strip() or len(quote) > 20000:
            raise ResultValidationError("Selected evidence is empty or exceeds 20000 characters.", {"kind": "citation_selection"})
        citations.append({"revision": source["revision"], "path": source["path"],
                          "start_line": selection.start_line, "end_line": selection.end_line, "quote": quote})
    result["evidence"] = citations
    if result.get("finding_id") is not None:
        if result["finding_id"] not in history:
            raise ResultValidationError("Select an established history reference.", {"kind": "history_selection"})
        result["finding_id"] = history[result["finding_id"]]
    return result
