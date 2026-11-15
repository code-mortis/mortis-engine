import asyncio
import copy

import pytest

from mortis_engine import gateway, operations
from mortis_engine.history import History
from mortis_engine.llm_http import InferenceError
from mortis_engine.review import assessment, source, workflows
from mortis_engine.review.context import source_id
from mortis_engine.review.service import effective_connection
from mortis_engine.review.settings import RepoSettings
from mortis_engine.schemas import Response, Usage
from mortis_engine.workflow import Session as WorkflowSession

from .git_helpers import commit
from .test_workflows import Session, candidate, decision, report, selected

SHA = "a" * 40
SOURCE = "def inverse(value):\n    return 1 / value\n"
CONNECTION = {"kind": "api", "provider": "test", "protocol": "openai", "model": "fixture",
              "base_url": "https://example.com/v1", "options": {}}


def context(text=SOURCE, **changes):
    return {"finding": {**candidate(SHA), **changes}, "history": [], "head_sha": SHA,
            "current_path": changes.get("path", "app.py"),
            "sources": {"current": {changes.get("path", "app.py"): text}}}


def assess(value):
    path = value["finding"]["path"]
    return assessment.validate_response(decision(suggestion_supported=True, evidence=[{
        "revision": "current", "path": path, "start_line": 2, "end_line": 2,
        "quote": value["sources"]["current"][path].splitlines()[1],
    }]), value)


@pytest.mark.parametrize("suggestion", ["    return (", "return 0", "    return await compute()", "    return '\0'"])
def test_invalid_python_patch_does_not_reject_confirmed_finding(suggestion):
    value = context(suggestion_code=suggestion)
    original = copy.deepcopy(value)
    result = assess(value)
    assert result["verdict"] == "confirmed"
    assert not result["suggestion_supported"]
    assert "invalid Python syntax" in result["explanation"]
    assert value == original


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("suggestion", ["    return 0", "    if not value:\n        return 0\n    return 1 / value\n"])
def test_valid_indented_replacement_preserves_next_line_boundary(newline, suggestion):
    text = (SOURCE + "\nraise AssertionError('Analyzed source must never execute')\n").replace("\n", newline)
    result = assess(context(text, suggestion_code=suggestion))
    assert result["verdict"] == "confirmed" and result["suggestion_supported"]


@pytest.mark.parametrize(("text", "changes"), [
    ("def inverse(value):\n    return (\n", {"existing_code": "    return ("}),
    (SOURCE, {"existing_code": "return 1 / value"}),
    (SOURCE, {"end_line": 3}),
    (SOURCE, {"start_line": 1}),
    (SOURCE, {"path": "app.js"}),
])
def test_original_invalid_or_ambiguous_replacement_preserves_assessor_decision(text, changes):
    result = assess(context(text, suggestion_code="    return (", **changes))
    assert result["verdict"] == "confirmed" and result["suggestion_supported"]


def test_stale_or_missing_current_source_is_not_syntax_checked():
    stale = context(source_sha="b" * 40)
    assert assess(stale)["suggestion_supported"]
    missing = context()
    missing["sources"]["current"].clear()
    assert not assessment._invalid_python_suggestion(missing)


def test_multiline_replacement_checks_the_whole_module():
    text = "def inverse(value):\n    if value:\n        return 1 / value\n    return 0\n"
    value = context(text, start_line=2, end_line=3, existing_code="    if value:\n        return 1 / value",
                    suggestion_code="    if value:\n    return 0")
    assert assessment._invalid_python_suggestion(value)
    value["finding"]["suggestion_code"] = "    return 1 / value if value else 0"
    assert not assessment._invalid_python_suggestion(value)


def test_exact_repeat_rechecks_a_previously_supported_invalid_suggestion():
    value = context(suggestion_code="    return (")
    previous = decision(suggestion_supported=True, evidence=[{
        "revision": "current", "path": "app.py", "start_line": 2, "end_line": 2,
        "quote": "    return 1 / value",
    }])
    previous.pop("required_paths")
    value["history"] = [{"id": "finding-old", "sha": SHA, "snapshot": copy.deepcopy(value["finding"]),
                         "assessment": previous}]
    original = copy.deepcopy(value)
    result = assessment.exact_repeat(value)
    assert result["verdict"] == "confirmed" and result["relationship"] == "duplicate"
    assert result["finding_id"] == "finding-old" and not result["suggestion_supported"]
    assert value == original


@pytest.mark.parametrize("failure_at", [1, 2])
def test_parser_resource_failure_does_not_claim_invalid_syntax(monkeypatch, failure_at):
    calls = []

    def compile_source(*args, **kwargs):
        calls.append(args)
        if len(calls) == failure_at:
            raise RecursionError("parser limit")

    monkeypatch.setattr(assessment, "compile", compile_source, raising=False)
    assert assess(context())["suggestion_supported"]


@pytest.mark.parametrize(("suggestion", "supported"), [("    return (", False), ("    return 0", True)])
def test_initial_assessment_strips_only_invalid_suggestion(repo, suggestion, supported):
    base = commit(repo, {"app.py": "def inverse(value):\n    return value\n"})
    head = commit(repo, {"app.py": SOURCE})
    original = report(base, head)
    original["findings"][0]["suggestion_code"] = suggestion
    before = copy.deepcopy(original)

    def respond(stage, supplied):
        if stage == "assessment":
            return decision(suggestion_supported=True, evidence=[selected(supplied)])
        return {"narrative": "Changes inversion", "groups": []}

    session = Session(respond)
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.postprocess(isolated, env, original, RepoSettings().model_dump(), session))
    assert result["workflow_status"] == "completed"
    assert len(result["findings"]) == 1
    assert result["findings"][0]["assessment"]["verdict"] == "confirmed"
    assert result["findings"][0]["assessment"]["suggestion_supported"] is supported
    assert result["findings"][0]["suggestion_code"] == (suggestion if supported else "")
    assert result["coverage"] == original["coverage"] and original == before
    assert (repo / "app.py").read_text() == SOURCE
    assert [stage for stage, _ in session.calls] == ["assessment", "summary"]


def test_saved_targeted_retry_uses_pinned_source_and_retains_origin(repo, tmp_path_factory, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("suggestion-state")))
    base = commit(repo, {"app.py": "def inverse(value):\n    return value\n"})
    head = commit(repo, {"app.py": SOURCE})
    snapshot = {**candidate(head), "suggestion_code": "    return ("}
    options = {**RepoSettings().model_dump(), "_settings_sha": base}

    async def fail(*args, **kwargs):
        raise InferenceError("fixture", "Known failure", consumption_unknown=False, usage=Usage(total_tokens=7))

    monkeypatch.setattr(gateway, "send", fail)
    with History(repo) as history, source.local_repository(repo, object_cache=history.objects) as (isolated, env):
        parent = history.begin("review", "main", {"triage_versions": {}, "profile": "fixture"})
        session = WorkflowSession(effective_connection(CONNECTION, options), {}, history=history, run_id=parent)
        original = {**report(base, head), "schema_version": "mortis.review/v1", "run_id": parent,
                    "settings_sha": base, "settings": options, "status": "partial", "findings": []}
        with pytest.raises(InferenceError):
            asyncio.run(workflows._assess(isolated, env, workflows._run(original, options, session), snapshot,
                {"findings": [], "prior_candidates": []}, session, candidate_id="candidate-syntax"))
        original["candidates"] = [{"id": "candidate-syntax", "snapshot": snapshot, "status": "failed",
                                   "result": None, "error": "Known failure"}]
        original["usage"] = session.usage()
        history.finish(parent, original)
        saved_report, saved_plan = history.row(parent)["report"], history.row(parent)["plan"]
        saved_input = history.assessment_inputs(parent)["candidate-syntax"]

    calls = []

    async def succeed(connection, credentials, request, **kwargs):
        calls.append(request)
        return Response(output=decision(suggestion_supported=True, evidence=[{
            "source_id": source_id("current", "app.py"),
            "start_line": 2, "end_line": 2,
        }]), usage=Usage(total_tokens=11))

    monkeypatch.setattr(gateway, "send", succeed)
    worktree = "the current worktree is invalid Python and must not be evidence"
    (repo / "app.py").write_text(worktree)
    result = operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-syntax",
                                        connection=CONNECTION, get_credentials=lambda: {})
    assert result["status"] == "completed" and result["result"]["verdict"] == "confirmed"
    assert not result["result"]["suggestion_supported"] and result["finding"]["suggestion_code"] == ""
    assert result["usage"]["total_tokens"] == 11 and result["lineage_usage"]["total_tokens"] == 18
    again = operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-syntax",
                                       connection=CONNECTION, get_credentials=lambda: pytest.fail("credentials read"))
    assert again == result and len(calls) == 1
    assert (repo / "app.py").read_text() == worktree
    with History(repo) as history:
        assert history.row(parent)["report"] == saved_report and history.row(parent)["plan"] == saved_plan
        assert history.assessment_inputs(parent)["candidate-syntax"] == saved_input
        assert history.report(result["run_id"]) == result
    assert operations.list_findings(repo, run_id=parent)["findings"][0]["suggestion_code"] == ""
