"""Pure report rendering checks; no history, credentials or provider calls."""

import copy
import json
from pathlib import Path

import pytest

from mortis_engine.reporting import markdown


def review_report():
    fixture = json.loads((Path(__file__).parent / "review/fixtures/partial_review.json").read_text())
    return {"schema_version": "mortis.review/v1", "run_id": "run-1", "status": "partial",
            "base_sha": "base-123", "head_sha": "head-456", "provider": "local", "model": "model-1",
            "findings": [{**fixture["findings"][0], "id": "finding-1", "status": "open",
                          "suggestion_code": "price = 10"}],
            "coverage": {**fixture["coverage"], "waived": [{"path": "later.py", "reason": "file limit"}]},
            "excluded_files": [{"path": ".env", "reason": "secret policy"}],
            "errors": [{"path": "pricing.py", "message": "analysis failed"}],
            "warnings": [{"message": "source truncated"}],
            "stage_errors": [{"stage": "assessment", "error": "deadline"}],
            "usage": {"total_tokens": None, "reserved_tokens": 200,
                      "requests": [{"stage": "analysis", "consumption_unknown": True, "reserved_tokens": 200}]}}


def test_partial_review_keeps_findings_coverage_accounting_and_legacy_timing():
    report = review_report()
    before = copy.deepcopy(report)
    rendered = markdown(report)
    for value in ("Mortis review", "partial", "run\\-1", "local", "model\\-1", "base\\-123", "head\\-456",
                  "pricing\\.py:1", "finding\\-1", "open", "Trigger", "Consequence", "Evidence",
                  "price = 0", "price = 10", "Failed", "Waived", "later\\.py", "file limit", ".env".replace(".", "\\."),
                  "secret policy", "analysis failed", "source truncated", "deadline", "unknown", "200",
                  "Consumption unknown", "unavailable \\(legacy report\\)"):
        assert value in rendered
    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")
    assert report == before


def test_security_preserves_grade_stages_dependency_and_redacted_secret_evidence():
    report = {"schema_version": "mortis.security/v1", "status": "partial", "grade": None,
              "commit": "commit-1", "elapsed_seconds": 1.25,
              "findings": [{"id": "secret-1", "severity": "critical", "title": "Exposed credential",
                            "path": "config.py", "start_line": 2, "end_line": 3, "kind": "secret",
                            "state": "accepted_risk", "outcome": "not_observed", "identity": "opaque-identity",
                            "locations": [{"path": "config.py", "start_line": 2, "end_line": 3}],
                            "explanation": "Already [redacted]", "remediation": "Rotate the credential.",
                            "verification": "Rotation unconfirmed", "rotation": None},
                           {"id": "dependency-1", "severity": "high", "path": "package-lock.json",
                            "kind": "dependency", "evidence": {"package": "demo", "installed_version": "1.0",
                            "fixed_version": "2.0", "references": ["https://example.test/advisory"]}}],
              "coverage": {"selected_files": 2, "completed_files": 1, "excluded": [{"path": ".env"}],
                           "units": [{"path": "config.py", "status": "failed"}]},
              "stages": {"trivy": {"status": "completed"}, "assessment": {"status": "partial"}},
              "scanner": {"version": "0.76.0", "database_fresh": False},
              "assessments": [{"result": {"verdict": "rejected", "explanation": "No reachable trigger"}}],
              "verifications": [{"finding_id": "secret-1", "verdict": "uncertain"}],
              "problems": ["incomplete analysis"]}
    rendered = markdown(report)
    for value in ("security scan", "Grade", "unavailable", "Elapsed seconds", "1\\.25", "accepted\\_risk",
                  "not\\_observed", "opaque\\-identity", "redacted", "Rotate the credential", "Rotation",
                  "Locations", "Installed version", "Fixed version", "rejected", "uncertain", "Database fresh",
                  "False", "incomplete analysis", "commit\\-1"):
        assert value in rendered
    assert "legacy report" not in rendered
    assert "https://" not in rendered


def test_summary_groups_diagram_and_fix_prompt_block_are_safe():
    report = review_report()
    malicious = '<script>alert(1)</script> [go](javascript:evil)\x1b[31m\n```mermaid\nevil'
    report["findings"][0].update(path=malicious, content=malicious, suggestion_code=malicious)
    report["findings"][0]["evidence"][0]["quote"] = "```\n</details>\n<script>evil</script>"
    report["summary"] = {"status": "completed", "limited_context": True, "result": {
        "narrative": malicious, "groups": [{"title": malicious, "description": malicious, "paths": [malicious]}],
        "diagram": {"nodes": [{"id": "A", "label": malicious[:120], "paths": ["pricing.py"]}], "edges": []}}}
    report["fix_prompts"] = malicious
    rendered = markdown(report)
    assert "&lt;script&gt;" in rendered
    assert r"\[go\]\(javascript:evil\)" in rendered
    assert rendered.count("[go](javascript:evil)") == 3  # Two fenced values and the encoded Mermaid label.
    assert "\x1b" not in rendered
    assert "#60;script#62;" in rendered
    assert "flowchart TD" in rendered
    assert "Limited context" in rendered
    assert rendered.index("## Change summary") < rendered.index("## Findings")
    assert "````text\n      ```\n      </details>\n      <script>evil</script>\n      ````" in rendered
    assert "## Fix prompts\n\n````text\n" in rendered


def test_coverage_and_candidates_are_compact_but_keep_incomplete_results():
    report = review_report()
    report["coverage"] = {"inventory": {f"file{index}.py": {"hash": "unneeded-inventory-hash"} for index in range(68)},
                          "selected_files": 68, "completed_files": 67,
                          "units": [{"task": "analysis:file3.py", "status": "failed"}]}
    report["candidates"] = [{"id": "candidate-1", "snapshot": {
        "path": "file3.py", "content": "duplicate-snapshot-content"}, "status": "failed", "error": "deadline"},
        {"id": "candidate-2", "snapshot": {"path": "file4.py"}, "status": "completed",
         "result": {"verdict": "rejected", "explanation": "caller validates"}}]
    rendered = markdown(report)
    assert "68" in rendered and "67" in rendered
    assert "analysis:file3\\.py" in rendered
    assert "candidate\\-1" in rendered and "candidate\\-2" in rendered
    assert "deadline" in rendered and "rejected" in rendered and "caller validates" in rendered
    assert "unneeded-inventory-hash" not in rendered
    assert "duplicate-snapshot-content" not in rendered
    assert "file67" not in rendered


def test_retained_findings_preserve_observation_and_decisions_separately():
    report = review_report()
    report["findings"] = []
    report["history"] = [{"id": "older-finding", "status": "open", "observation": "not_observed",
                          "snapshot": {"path": "old.py", "start_line": 4, "severity": "high", "content": "Retained defect"},
                          "verification": {"status": "failed", "verdict": "uncertain", "error": "verification timed out"}},
                         {"id": "resolved-finding", "status": "resolved", "observation": "not_observed"},
                         {"id": "dismissed-finding", "status": "dismissed", "observation": "observed"}]
    rendered = markdown(report)
    for value in ("No findings recorded. See coverage and errors for analysis limitations.", "Retained findings",
                  "older\\-finding", "old\\.py", "open", "not\\_observed", "uncertain", "verification timed out",
                  "resolved\\-finding", "resolved", "dismissed\\-finding", "dismissed"):
        assert value in rendered
    assert "fixed" not in rendered


@pytest.mark.parametrize("diagram", [
    "```mermaid\nclick A malicious",
    {"nodes": [{"id": "A", "label": "Node", "paths": ["outside.py"]}], "edges": []},
    {"nodes": [{"id": "A", "label": "Node", "paths": ["pricing.py"]}],
     "edges": [{"source": "A", "target": "missing"}]},
    {"nodes": [{"id": "A", "label": "Node", "paths": ["pricing.py"]}] * 2, "edges": []},
])
def test_invalid_saved_diagram_is_not_rendered(diagram):
    report = review_report()
    report["summary"] = {"result": {"narrative": "Change summary", "groups": [], "diagram": diagram}}
    rendered = markdown(report)
    assert "Diagram unavailable" in rendered
    assert "```mermaid" not in rendered


@pytest.mark.parametrize("affected", [None, ["untrusted.py"]])
@pytest.mark.parametrize(("path", "renders_diagram"), [("pricing.py", True), ("untrusted.py", False)])
def test_malformed_caller_scope_does_not_break_diagram_export(affected, path, renders_diagram):
    report = review_report()
    report["affected_callers"] = affected
    report["summary"] = {"result": {"narrative": "Change summary", "diagram": {
        "nodes": [{"id": "A", "label": "Node", "paths": [path]}], "edges": []}}}
    rendered = markdown(report)
    assert "Change summary" in rendered and "## Findings" in rendered
    assert ("```mermaid" in rendered) == renders_diagram
    assert ("Diagram unavailable" in rendered) != renders_diagram


@pytest.mark.parametrize("schema", [None, "mortis.summary/v1", "mortis.review/v2"])
def test_unsupported_report_contract_fails_explicitly(schema):
    with pytest.raises(ValueError, match="mortis.review/v1 and mortis.security/v1 only"):
        markdown({"schema_version": schema})


@pytest.mark.parametrize("schema", ["mortis.review/v1", "mortis.security/v1"])
@pytest.mark.parametrize("update", [
    {"coverage": None}, {"coverage": {"selected": None}}, {"coverage": {"inventory": None}},
    {"coverage": {"failed": [None]}}, {"coverage": {"excluded": ["private-source-value"]}},
    {"findings": None}, {"findings": "private-source-value"}, {"findings": [None]},
    {"findings": [{"title": 1}]}, {"summary": "private-source-value"},
    {"summary": {"result": "private-source-value"}}, {"summary": {"result": {"groups": None}}},
    {"summary": {"result": {"groups": [None]}}}, {"summary": {"result": {"groups": [{"paths": None}]}}},
    {"units": [None]}, {"units": [{"status": []}]}, {"history": [None]},
    {"history": [{"snapshot": None}]}, {"history": [{"snapshot": "path"}]},
    {"candidates": [None]}, {"assessments": [{"result": "verdict"}]}, {"usage": "private-source-value"},
])
def test_malformed_saved_report_shapes_raise_safe_value_error(schema, update):
    report = {"schema_version": schema, **update}
    before = copy.deepcopy(report)
    with pytest.raises(ValueError, match="Invalid saved report structure") as error:
        markdown(report)
    assert "private-source-value" not in str(error.value)
    assert report == before


@pytest.mark.parametrize("report", [None, [], "private-source-value"])
def test_non_object_report_raises_safe_value_error(report):
    with pytest.raises(ValueError, match="Invalid saved report structure"):
        markdown(report)
