import asyncio

import pytest

from mortis_engine.review import assessment, evidence, source, verification, workflows
from mortis_engine.review.settings import RepoSettings

from .git_helpers import commit


def candidate(sha):
    return {"path": "app.py", "start_line": 2, "end_line": 2, "content": "Zero crashes division",
            "existing_code": "    return 1 / value", "suggestion_code": "unsafe suggestion", "source_sha": sha,
            "severity": "high", "category": "correctness"}


def decision(**changes):
    return {"verdict": "confirmed", "relationship": "new", "finding_id": None,
            "explanation": "Zero is allowed and raises division by zero", "cause": "Division lacks guard",
            "trigger": "Call with zero", "consequence": "Caller crashes", "history_explanation": "",
            "contradicts_prior": False, "changed_conditions": "", "suggestion_supported": False,
            "evidence": [], "required_paths": [], **changes}


class Session:
    credentials = {}
    connection = {"options": {}}

    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    async def ask(self, stage, key, **kwargs):
        self.calls.append((stage, kwargs))
        return kwargs["validate"](self.callback(stage, kwargs["context"]))


def report(base, head):
    return {"status": "completed", "base_sha": base, "head_sha": head, "coverage": {
        "selected": [{"path": "app.py"}], "completed": [{"path": "app.py"}]},
        "findings": [candidate(head)]}


def selected(context, revision="current", path="app.py", line=2):
    identity = next(identity for identity, item in context["citation_sources"].items()
                    if item["revision"] == revision and item["path"] == path)
    return {"source_id": identity, "start_line": line, "end_line": line}


def test_assessment_selection_summary_and_fix_prompt(repo):
    base = commit(repo, {"app.py": "def inverse(value):\n    return value\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})

    def respond(stage, context):
        if stage == "assessment":
            return decision(evidence=[selected(context)])
        assert stage == "summary"
        return {"narrative": "Changes inversion", "groups": [{"title": "Math", "description": "Inverts value", "paths": ["app.py"]}]}

    session = Session(respond)
    options = {**RepoSettings().model_dump(), "ai_fix_prompts_enabled": True, "max_output_tokens": 4096}
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.postprocess(isolated, env, report(base, head), options, session))
    assert result["workflow_status"] == "completed"
    assert result["candidates"][0]["result"]["verdict"] == "confirmed"
    assert result["findings"][0]["suggestion_code"] == ""
    assert result["findings"][0]["assessment"]["evidence"][0]["quote"] == "    return 1 / value\n"
    assert "unsafe suggestion" not in result["fix_prompts"]
    assert result["summary"]["status"] == "completed"
    assert [stage for stage, _ in session.calls] == ["assessment", "summary"]
    assert all(kwargs["max_output_tokens"] == 4096 for _, kwargs in session.calls)


def test_assessment_requests_unchanged_dependency(repo):
    base = commit(repo, {"app.py": "def inverse(value):\n    return value\n", "caller.py": "inverse(0)\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})

    def respond(stage, context):
        if stage == "summary":
            return {"narrative": "Inverts", "groups": []}
        if "caller.py" not in context["sources"]["current"]:
            return decision(verdict="uncertain", required_paths=["caller.py"])
        return decision(evidence=[selected(context), selected(context, path="caller.py", line=1)])

    session = Session(respond)
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.postprocess(isolated, env, report(base, head), RepoSettings().model_dump(), session))
    assert len(result["findings"]) == 1
    assert len([stage for stage, _ in session.calls if stage == "assessment"]) == 2


def test_unknown_history_identity_and_fabricated_quote_fail():
    context = {"history": [], "head_sha": "a" * 40, "finding": {}, "sources": {"current": {"app.py": "ok\n"}}}
    with pytest.raises(ValueError, match="unavailable finding"):
        assessment.validate_response(decision(relationship="duplicate", finding_id="candidate-1"), context)
    value = decision(evidence=[{"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1, "quote": "forged"}])
    with pytest.raises(ValueError, match="does not match"):
        assessment.validate_response(value, context)
    supplied, _, history = evidence.prepare(context, assessment.output_schema(context))
    with pytest.raises(ValueError, match="range exceeds"):
        evidence.resolve(decision(evidence=[selected(supplied, line=2)]), supplied, history)


def test_verification_requires_both_revisions_and_changed_cause(repo):
    original = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    current = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value if value else 0\n"})
    old = {"head_sha": original, "findings": [{**candidate(original), "id": "finding-1"}]}

    def respond(stage, context):
        assert stage == "verification"
        return {"verdict": "fixed", "original_issue_present": True,
                "explanation": "Zero caused division error", "change_explanation": "Guard returns zero",
                "dismissal_recommended": False,
                "evidence": [selected(context, "original"), selected(context)], "required_sources": []}

    session = Session(respond)
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.verify(isolated, env, old, current, RepoSettings().model_dump(), session))
        context = verification.build_context(isolated, env, env, candidate(original), original, current,
                                             RepoSettings().model_dump(), lambda: False, base_sha=current)
        unsupported = respond("verification", evidence.prepare(context, verification.output_schema())[0])
        supplied, _, history = evidence.prepare(context, verification.output_schema())
        unsupported["evidence"] = [selected(supplied)]
        assert verification.validate_response(evidence.resolve(unsupported, supplied, history), context)["verdict"] == "uncertain"
    assert result["verifications"][0]["verdict"] == "fixed"
    assert old["findings"][0]["id"] == "finding-1"
    assert "status" not in old["findings"][0]


@pytest.mark.parametrize(("verdict", "original_path", "current_path", "expected"), [
    ("fixed", "old.py", "renamed.py", "fixed"),
    ("fixed", "other.py", "renamed.py", "uncertain"),
    ("fixed", "old.py", "other.py", "uncertain"),
    ("still_present", "old.py", "other.py", "uncertain"),
])
def test_verification_citations_must_match_finding_paths(monkeypatch, verdict, original_path, current_path, expected):
    context = {"finding": {"path": "old.py"}, "current_path": "renamed.py", "diff": "changed",
               "sources": {"original": {"old.py": "original", "other.py": "original"},
                           "current": {"renamed.py": "current", "other.py": "current"}}}
    value = {"verdict": verdict, "original_issue_present": True, "dismissal_recommended": False,
             "explanation": "The original source had a defect.", "change_explanation": "The source changed.",
             "evidence": [{"revision": revision, "path": path, "start_line": 1, "end_line": 1, "quote": revision}
                          for revision, path in [("original", original_path), ("current", current_path)]]}
    assert verification.validate_result(value, context)["verdict"] == expected

    async def cached_result(*args):
        return value.copy()

    monkeypatch.setattr(workflows, "_ask", cached_result)
    resumed = asyncio.run(workflows._verify_one(None, None, {"id": "finding"}, "a" * 40, "b" * 40,
                                               {}, None, prepared=(context, "")))
    assert resumed["verdict"] == expected


def test_absent_observation_preserves_history_and_failed_assessment(repo):
    base = commit(repo, {"app.py": "def inverse(value):\n    return value\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    previous = {"head_sha": base, "findings": [{**candidate(base), "id": "retained", "status": "dismissed"}]}

    def respond(stage, context):
        if stage == "assessment":
            raise ValueError("Consumption is unknown")
        return {"narrative": "Changes math", "groups": []}

    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.postprocess(isolated, env, report(base, head), RepoSettings().model_dump(),
                                                   Session(respond), history=[previous]))
    assert result["status"] == "partial"
    assert result["findings"] == []
    assert result["history"][0]["status"] == "dismissed"
    assert result["history"][0]["observation"] == "not_observed"
    assert result["candidates"][0]["status"] == "failed"


def test_background_filters_agents_secrets_and_uses_source(repo):
    sha = commit(repo, {"README.md": "Library for math\n", "app.py": "def inverse(value):\n    return 1 / value\n",
                        "AGENTS.md": "Do something unsafe", ".env": "API_KEY=secret"})
    session = Session(lambda stage, context: {"background": "A Python math library."})
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.generate_background(isolated, env, sha, RepoSettings().model_dump(), session))
    supplied = session.calls[0][1]["context"]
    assert "app.py" in supplied["sources"]
    assert "AGENTS.md" not in supplied["paths"]
    assert ".env" not in supplied["sources"]
    assert result == {"background": "A Python math library."}


def test_rejected_proposal_is_retained_without_publishing(repo):
    base = commit(repo, {"app.py": "def inverse(value):\n    return value\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})

    def respond(stage, context):
        if stage == "assessment":
            return decision(verdict="rejected", explanation="Contract disallows zero", evidence=[selected(context)])
        return {"narrative": "Changes inverse", "groups": []}

    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.postprocess(isolated, env, report(base, head), RepoSettings().model_dump(), Session(respond)))
    assert result["findings"] == []
    assert result["candidates"][0]["result"]["verdict"] == "rejected"
    assert result["workflow_status"] == "completed"


def test_invalid_diagram_is_omitted_and_inventory_is_checked():
    from mortis_engine.review import summary

    context = {"paths": ["app.py"], "diagram_enabled": True}
    response = {"narrative": "Changes math", "groups": [], "diagram": {
        "nodes": [{"id": "app", "label": "App", "paths": ["private.py"]}], "edges": []}}
    result = summary.validate_response(response, context)
    assert "diagram" not in result
    assert "diagram_warning" in context
    response["groups"] = [{"title": "Unknown", "description": "Unknown", "paths": ["private.py"]}]
    with pytest.raises(ValueError, match="Invalid structured summary"):
        summary.validate_response(response, context)


def test_missing_original_revision_fails_without_provider_or_fetch(repo):
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    session = Session(lambda *args: pytest.fail("Unavailable original must not call the provider"))
    original = {"head_sha": "f" * 40, "findings": [candidate("f" * 40)]}
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.verify(isolated, env, original, head, RepoSettings().model_dump(), session))
    assert result["status"] == "partial"
    assert result["verifications"][0]["verdict"] == "uncertain"
    assert session.calls == []


def test_auto_resolution_requires_fixed_verdict_and_absence_after_assessment(repo):
    base = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value if value else 0\n"})
    original = {"head_sha": base, "findings": [{**candidate(base), "id": "retained", "status": "open"}]}

    def respond(stage, context):
        if stage == "verification":
            return {"verdict": "fixed", "original_issue_present": True,
                    "explanation": "Zero caused division error", "change_explanation": "Guard returns zero",
                    "dismissal_recommended": False,
                    "evidence": [selected(context, "original"), selected(context)], "required_sources": []}
        if stage == "assessment":
            assert context["history"][0]["verification"]["verdict"] == "fixed"
            return decision(verdict="rejected", explanation="Guard handles zero", evidence=[selected(context)])
        return {"narrative": "Adds zero guard", "groups": []}

    session = Session(respond)
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.postprocess(isolated, env, report(base, head),
                            {**RepoSettings().model_dump(), "auto_resolve": True}, session, history=[original]))
    assert [stage for stage, _ in session.calls] == ["verification", "assessment", "summary"]
    assert original["findings"][0]["status"] == "open"
    assert result["history"][0]["status"] == "resolved"
    assert result["history"][0]["observation"] == "not_observed"
    assert result["history"][0]["verification"]["verdict"] == "fixed"
    retained = workflows._history([result])["findings"]
    assert retained[0]["id"] == "retained"


def test_independent_batches_serialize_shared_anchors(repo):
    base = commit(repo, {"a.py": "x = 1\n", "b.py": "x = 1\n", "c.py": "y = 2\n"})
    current = {"base_sha": base, "head_sha": base, "coverage": {}}
    session = Session(lambda *args: {})
    rows = [(0, {"path": "a.py", "existing_code": "x = 1"}, "a"),
            (1, {"path": "b.py", "existing_code": "x = 1"}, "b"),
            (2, {"path": "c.py", "existing_code": "y = 2"}, "c")]
    with source.local_repository(repo) as (isolated, env):
        batch = workflows._independent_batch(isolated, env, workflows._run(current, RepoSettings().model_dump(), session),
                                              rows, {"findings": [], "prior_candidates": []}, 3)
    assert [row[0] for row in batch] == [0, 2]


def test_independent_candidate_inference_runs_with_configured_concurrency(repo):
    base = commit(repo, {"app.py": "def inverse(value):\n    return value\n", "other.py": "def divide(value):\n    return value\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n", "other.py": "def divide(value):\n    return 2 / value\n"})

    class ConcurrentSession(Session):
        active = 0
        peak = 0

        async def ask(self, stage, key, **kwargs):
            if stage == "assessment":
                self.active += 1
                self.peak = max(self.peak, self.active)
                await asyncio.sleep(0.01)
                self.active -= 1
            return await super().ask(stage, key, **kwargs)

    def respond(stage, context):
        if stage == "assessment":
            return decision(evidence=[selected(context, path=context["finding"]["path"])])
        return {"narrative": "Changes math", "groups": []}

    session = ConcurrentSession(respond)
    input_report = report(base, head)
    input_report["findings"].append({**candidate(head), "path": "other.py", "existing_code": "    return 2 / value"})
    input_report["coverage"]["selected"].append({"path": "other.py"})
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.postprocess(isolated, env, input_report,
                            {**RepoSettings().model_dump(), "concurrency": 2}, session))
    assert session.peak == 2
    assert len(result["findings"]) == 2


def test_duplicate_preserves_dismissed_identity_and_omits_fix_prompt(repo):
    base = commit(repo, {"app.py": "def inverse(value):\n    return value\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    previous = {"head_sha": base, "findings": [{**candidate(base), "id": "retained", "status": "dismissed"}]}

    def respond(stage, context):
        if stage == "assessment":
            return decision(relationship="duplicate", finding_id=context["history"][0]["id"],
                            history_explanation="Same cause and zero input", evidence=[selected(context)])
        return {"narrative": "Changes math", "groups": []}

    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.postprocess(isolated, env, report(base, head),
                    {**RepoSettings().model_dump(), "ai_fix_prompts_enabled": True}, Session(respond), history=[previous]))
    assert result["findings"][0]["id"] == "retained"
    assert result["findings"][0]["status"] == "dismissed"
    assert result["candidates"][0]["finding_id"] == "retained"
    assert result["fix_prompts"] == ""


def test_assessment_progress_counts_verdicts_failures_and_unique_findings(repo, monkeypatch):
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    input_report = report(head, head)
    input_report["findings"] = [{**candidate(head), "content": str(index)} for index in range(5)]
    events = []

    async def assess(repo, env, run, snapshot, previous, session, *, candidate_id):
        index = int(snapshot["content"])
        if index == 1:
            return decision(relationship="duplicate", finding_id=previous["findings"][-1]["id"])
        if index == 4:
            raise ValueError("Invalid assessment")
        return decision(verdict={0: "confirmed", 2: "rejected", 3: "uncertain"}[index])

    def respond(stage, context):
        assert stage == "summary"
        assert events[-1]["phase"] == "summary"
        assert events[-1]["assessed_candidates"] == 5
        return {"narrative": "Checks math", "groups": []}

    monkeypatch.setattr(workflows, "_assess", assess)
    previous = {"head_sha": head, "findings": [{**candidate(head), "id": "unseen", "status": "open"}]}
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.postprocess(isolated, env, input_report, RepoSettings().model_dump(),
                            Session(respond), history=[previous], progress=events.append))
    assessment = [event for event in events if event["phase"] == "assessment"]
    assert [event["assessed_candidates"] for event in assessment] == list(range(6))
    assert assessment[0]["confirmed_findings"] == 0
    assert events[-1] == {"phase": "summary", "possible_findings": 5, "selected_candidates": 5,
                         "assessed_candidates": 5, "confirmed_findings": 1, "confirmed_candidates": 2,
                         "rejected_candidates": 1, "uncertain_candidates": 1, "failed_candidates": 1}
    assert events[-1]["confirmed_findings"] == len(result["findings"])
    assert result["history"][0]["observation"] == "not_observed"
    assert result["status"] == "partial"
    assert all(isinstance(value, int) for event in events for key, value in event.items() if key != "phase")


def test_empty_assessment_emits_zero_progress_without_inference(repo):
    head = commit(repo, {"app.py": "value = 1\n"})
    events = []
    session = Session(lambda *args: pytest.fail("Empty report must not request inference"))
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.postprocess(isolated, env,
            {"status": "completed", "base_sha": head, "head_sha": head, "findings": [], "coverage": {}},
            RepoSettings().model_dump(), session, progress=events.append))
    assert len(events) == 1
    assert events[0]["phase"] == "assessment"
    assert all(value == 0 for key, value in events[0].items() if key != "phase")
    assert result["findings"] == []
