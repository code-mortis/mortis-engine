"""Safe, offline Markdown presentation of versioned review and security reports."""

from collections import Counter

from mortis_engine.review.summary import diagram_section, fenced, text
from mortis_engine.review.workflow_contracts import SummaryDiagram
from mortis_engine.security import clean_text


def _text(value):
    # Prevent bare URL autolinks as well as the Markdown/HTML escaped by summary.text.
    return text(value).replace("://", "&#58;//")


def _block(value):
    return fenced(clean_text(str(value)))


def _details(value, depth=0):
    """Keep nested evidence and accounting fields without trusting their markup."""
    indent = "  " * depth
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            label = _text(str(key).replace("_", " ").capitalize())
            if isinstance(item, (dict, list)):
                lines.append(f"{indent}- **{label}:**" + (" none" if not item else ""))
                lines.extend(_details(item, depth + 1))
            elif key in {"quote", "existing_code", "suggestion_code", "fix_prompts"} and item:
                lines.extend([f"{indent}- **{label}:**", ""])
                lines.extend(indent + "  " + line for line in _block(item).splitlines())
                lines.append("")
            else:
                lines.append(f"{indent}- **{label}:** {_text('unavailable' if item is None else item)}")
        return lines
    if isinstance(value, list):
        lines = []
        for index, item in enumerate(value, 1):
            if isinstance(item, (dict, list)):
                lines.append(f"{indent}- **{index}.**")
                lines.extend(_details(item, depth + 1))
            else:
                lines.append(f"{indent}- {_text(item)}")
        return lines
    return [f"{indent}- {_text(value)}"]


def _section(lines, title, value):
    lines.extend(["", f"## {title}", ""])
    lines.extend(_details(value) if value else ["None recorded."])


def _summary(lines, summary, coverage):
    lines.extend(["", "## Change summary", ""])
    result = summary.get("result") or {}
    lines.extend(_details({key: value for key, value in summary.items() if key != "result"}))
    if result.get("narrative"):
        lines.extend(["", _text(result["narrative"])])
    for group in result.get("groups", []):
        lines.extend(["", "### " + _text(group.get("title", "Change group")), "",
                      _text(group.get("description", "")), ""])
        lines.extend("- " + _text(path) for path in group.get("paths", []))
    if diagram := result.get("diagram"):
        try:
            validated = SummaryDiagram.model_validate(diagram)
            ids = {node.id for node in validated.nodes}
            allowed = {row["path"] for row in coverage.get("selected", [])}
            if (len(ids) != len(validated.nodes)
                    or any(edge.source not in ids or edge.target not in ids for edge in validated.edges)
                    or any(path not in allowed for node in validated.nodes for path in node.paths)):
                raise ValueError("Invalid diagram references")
            safe = validated.model_dump()
            for node in safe["nodes"]:
                node["label"] = clean_text(node["label"])
            lines.extend(["", diagram_section(safe)])
        except (ValueError, TypeError, KeyError):
            lines.extend(["", "Diagram unavailable: invalid structure or file references."])


def _candidate(row):
    source = row.get("snapshot") or row.get("proposal") or {}
    result = row.get("result") or {}
    return {**{key: row[key] for key in ("id", "status", "error") if key in row},
            **{key: source[key] for key in ("path", "title", "start_line", "end_line") if key in source},
            **{key: result[key] for key in ("verdict", "finding_id", "relationship", "explanation") if key in result}}


def markdown(report) -> str:
    """Render a saved report without changing it or consulting state/providers."""
    try:
        return _markdown(report)
    except (AttributeError, TypeError, KeyError, RecursionError):
        raise ValueError("Invalid saved report structure; Markdown export is unavailable.") from None


def _markdown(report) -> str:
    schema = report.get("schema_version")
    if schema not in {"mortis.review/v1", "mortis.security/v1"}:
        raise ValueError("Markdown reports support mortis.review/v1 and mortis.security/v1 only.")
    security = schema == "mortis.security/v1"
    lines = ["# Mortis " + ("security scan" if security else "review"), ""]
    metadata = {"schema": schema, "status": report.get("status", "unavailable"),
                "run_id": report.get("run_id", "unavailable"),
                "provider": report.get("provider", "unavailable"), "model": report.get("model", "unavailable")}
    metadata.update({key: report[key] for key in (
        "repository", "mode", "ref", "revision", "base_sha", "merge_base", "head_sha", "commit",
        "settings_sha", "generated_at", "workflow_status") if key in report})
    metadata["elapsed_seconds"] = report.get("elapsed_seconds", "unavailable (legacy report)")
    if security:
        metadata.update(grade=report.get("grade") or "unavailable", rating_policy=report.get("rating_policy", "unavailable"))
    lines.extend(_details(metadata))
    if comparison := report.get("comparison"):
        _section(lines, "Review comparison", comparison)
    coverage = report.get("coverage", {})
    if report.get("summary"):
        _summary(lines, report["summary"], coverage)

    findings = report.get("findings", [])
    lines.extend(["", f"## Findings ({len(findings)})", ""])
    if not findings:
        lines.append("No findings recorded. See coverage and errors for analysis limitations.")
    for index, finding in enumerate(findings, 1):
        location = str(finding.get("path", "unavailable"))
        if finding.get("start_line") is not None:
            location += ":" + str(finding["start_line"])
            if finding.get("end_line") != finding["start_line"] and finding.get("end_line") is not None:
                location += "-" + str(finding["end_line"])
        heading = f"{index}. [{finding.get('severity', 'unknown')}] {location}"
        if finding.get("title"):
            heading += " - " + finding["title"]
        lines.extend(["", "### " + _text(heading), ""])
        lines.extend(_details({key: value for key, value in finding.items()
                               if key not in {"severity", "path", "start_line", "end_line", "title"}}))

    counts = {key + "_files": len(coverage[key]) for key in ("selected", "completed", "failed", "reused", "waived")
              if key in coverage}
    counts.update({key: coverage[key] for key in ("selected_files", "completed_files") if key in coverage})
    if "inventory" in coverage:
        counts["inventory_files"] = len(coverage["inventory"])
    for key in ("failed", "waived", "excluded"):
        if key in coverage:
            counts[key] = [{name: value for name, value in row.items() if name != "item_id"}
                           for row in coverage[key]]
    units = report.get("units", coverage.get("units", []))
    if units:
        counts["units"] = dict(Counter(row.get("status", "unavailable") for row in units))
        counts["incomplete_units"] = [{key: value for key, value in row.items() if key not in {"key", "comments"}}
                                      for row in units if row.get("status") not in {"completed", "reused"}]
    _section(lines, "Coverage", counts)
    if "excluded_files" in report:
        _section(lines, "Excluded files", report["excluded_files"])
    if "history" in report:
        retained = [{**{key: row[key] for key in (
            "id", "status", "observation", "sha", "reasons", "verification", "triage") if key in row},
            **{key: row.get("snapshot", {})[key] for key in ("path", "start_line", "end_line", "severity", "content")
               if key in row.get("snapshot", {})}} for row in report["history"]]
        _section(lines, "Retained findings", retained)
    for key, title in (("candidates", "Candidate assessments"), ("assessments", "Assessments"),
                       ("verification", "Verification"), ("verifications", "Verifications"),
                       ("stages", "Stages"), ("scanner", "Scanner"), ("code_graph", "Code graph"),
                       ("errors", "Errors"), ("warnings", "Warnings"),
                       ("stage_errors", "Stage errors"), ("problems", "Problems")):
        if key in report:
            value = [_candidate(row) for row in report[key]] if key in {"candidates", "assessments"} else report[key]
            _section(lines, title, value)
    usage = report.get("usage") or {}
    _section(lines, "Token usage", {"total_tokens": usage.get("total_tokens")
                                   if usage.get("total_tokens") is not None else "unknown",
                                   **{key: value for key, value in usage.items() if key != "total_tokens"}})
    if report.get("fix_prompts"):
        lines.extend(["", "## Fix prompts", "", _block(report["fix_prompts"])])
    return "\n".join(lines).rstrip() + "\n"
