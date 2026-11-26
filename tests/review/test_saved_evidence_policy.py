"""Saved evidence requests recover their policy without database or provider access."""

import asyncio
import copy
import hashlib
import json
import time
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from mortis_engine import assessments
from mortis_engine.history import encode
from mortis_engine.llm_http import InferenceError
from mortis_engine.review import engine, evidence, planning, service, source, verification, workflows
from mortis_engine.review.settings import RepoSettings
from mortis_engine.review.snapshots import Snapshot
from mortis_engine.schemas import Response, ToolCall, Usage
from mortis_engine.workflow import Session

from .git_helpers import commit
from .test_workflows import candidate, decision


class MemorySession(Session):
    """Use the actual request construction and cache contract, without admission or local state."""

    def __init__(self, callback):
        super().__init__({"options": {}}, {}, run_id="saved-evidence-fixture")
        self.callback = callback
        self.sent = []

    async def send(self, stage, key, request, **kwargs):
        digest = planning.fingerprint(request.model_dump())
        saved = self.records.get((stage, key))
        if saved:
            if saved[0] != digest:
                raise ValueError("The saved task has different immutable inputs.")
            if saved[1].get("consumption_unknown"):
                raise InferenceError("interrupted", "Saved consumption is unknown", consumption_unknown=True)
            return json.loads(saved[2])
        output = self.callback(stage, key, request)
        self.sent.append(request)
        value = kwargs["accept"](Response(output=output, usage=Usage(total_tokens=1)))
        self.records[stage, key] = (digest, {"status": "completed"}, encode(value))
        return value


def verification_result(*, requested=False):
    return {"verdict": "uncertain", "original_issue_present": True, "explanation": "Needs caller evidence",
            "change_explanation": "", "dismissal_recommended": False, "evidence": [],
            "required_sources": [{"revision": "current", "path": "tests/test_retry.py"}] if requested else []}


@pytest.mark.parametrize("policy", [None, planning.EVIDENCE_POLICY])
def test_unmarked_verification_recovers_completed_request_and_source_followup(repo, policy):
    base = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n",
                         "tests/test_retry.py": "assert inverse(0) == 0\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value if value else 0\n",
                         "tests/test_retry.py": "assert inverse(0) == 0  # guard\n"})
    original = {"head_sha": base, "findings": [{**candidate(base), "id": "finding-1"}]}
    options = {**RepoSettings().model_dump(), "_evidence_policy": policy}

    def interrupt(stage, key, request):
        assert stage == "verification"
        if key.endswith(":1"):
            raise InterruptedError("Stop after the completed original request")
        assert (verification.TEST_EVIDENCE_INSTRUCTIONS in request.instructions) == bool(policy)
        if policy is None:
            # Frozen from the pre-integration 35fbc99 verification prompt.
            assert hashlib.sha256(request.instructions.removesuffix(evidence.SELECTION_INSTRUCTIONS).encode()).hexdigest() == (
                "8ba5c6c85a7ff845c2bc9822af923afbbb99dea8cb22dfff99752e28ac356f01")
        return verification_result(requested=True)

    session = MemorySession(interrupt)
    with source.local_repository(repo) as (isolated, env):
        with pytest.raises(InterruptedError):
            asyncio.run(workflows.verify(isolated, env, original, head, options, session))
        saved = copy.deepcopy(session.records)
        sent_before = len(session.sent)

        def resume(stage, key, request):
            assert key.endswith(":1")
            text = request.messages[0].content
            assert ("current source: \"tests/test_retry.py\"" in text) == bool(policy)
            assert ("validation_feedback" in text) == (policy is None)
            return verification_result()

        session.callback = resume
        result = asyncio.run(workflows.verify(isolated, env, original, head,
            {**RepoSettings().model_dump(), "_evidence_policy": None, "_unmarked_verification_policy": True}, session))
    assert result["verifications"][0]["status"] == "completed"
    assert len(session.sent) == sent_before + 1
    assert all(session.records[key] == value for key, value in saved.items())


def test_unmarked_policy_uses_later_cached_test_finding_before_any_dispatch(repo):
    path = "tests/test_retry.py"
    base = commit(repo, {"app.py": "value = 1\n", path: "assert value == 1\n"})
    head = commit(repo, {"app.py": "value = 2\n", path: "assert value == 2\n"})
    saved_test = {**candidate(base), "id": "finding-test", "path": path}
    original = {"head_sha": base, "findings": [saved_test]}
    session = MemorySession(lambda *args: verification_result())
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.verify(isolated, env, original, head,
            {**RepoSettings().model_dump(), "_evidence_policy": planning.EVIDENCE_POLICY}, session))
        assert result["verifications"][0]["status"] == "completed"
        original["findings"].insert(0, {**candidate(base), "id": "uncached-finding"})

        def respond(stage, key, request):
            assert key.startswith("uncached-finding:")
            assert verification.TEST_EVIDENCE_INSTRUCTIONS in request.instructions
            return verification_result()

        session.callback = respond
        result = asyncio.run(workflows.verify(isolated, env, original, head,
            {**RepoSettings().model_dump(), "_evidence_policy": None, "_unmarked_verification_policy": True}, session))
    assert all(row["status"] == "completed" for row in result["verifications"])
    assert len(session.sent) == 2


def test_unmarked_policy_preserves_independent_saved_results_after_mismatched_finding(repo):
    base = commit(repo, {"app.py": "value = 1\n"})
    head = commit(repo, {"app.py": "value = 2\n"})
    original = {"head_sha": base, "findings": [{**candidate(base), "id": identity}
                for identity in ("mismatched-finding", "valid-finding")]}
    session = MemorySession(lambda *args: verification_result())
    with source.local_repository(repo) as (isolated, env):
        asyncio.run(workflows.verify(isolated, env, original, head,
            {**RepoSettings().model_dump(), "_evidence_policy": planning.EVIDENCE_POLICY}, session))
        key = next(iter(session.records))
        _, row, value = session.records[key]
        session.records[key] = ("0" * 64, row, value)
        session.callback = lambda *args: pytest.fail("A settled request was replayed")
        result = asyncio.run(workflows.verify(isolated, env, original, head,
            {**RepoSettings().model_dump(), "_evidence_policy": None, "_unmarked_verification_policy": True}, session))
    assert [row["status"] for row in result["verifications"]] == ["failed", "completed"]


def test_unmarked_verification_retains_mixed_historical_policies_without_redispatch(repo):
    base = commit(repo, {"app.py": "value = 1\n", "tests/test_retry.py": "assert value == 1\n"})
    head = commit(repo, {"app.py": "value = 2\n", "tests/test_retry.py": "assert value == 2\n"})
    findings = [{**candidate(base), "id": identity} for identity in ("old-policy", "integrated-policy")]
    session = MemorySession(lambda *args: verification_result())
    with source.local_repository(repo) as (isolated, env):
        for finding, policy in zip(findings, (None, planning.EVIDENCE_POLICY), strict=True):
            result = asyncio.run(workflows.verify(isolated, env, {"head_sha": base, "findings": [finding]}, head,
                {**RepoSettings().model_dump(), "_evidence_policy": policy}, session))
            assert result["verifications"][0]["status"] == "completed"
        original_records = copy.deepcopy(session.records)
        session.callback = lambda *args: pytest.fail("A settled historical request was replayed")
        result = asyncio.run(workflows.verify(isolated, env, {"head_sha": base, "findings": findings}, head,
            {**RepoSettings().model_dump(), "_evidence_policy": None, "_unmarked_verification_policy": True}, session))
    assert [row["status"] for row in result["verifications"]] == ["completed", "completed"]
    assert session.records == original_records


def test_legacy_auto_resolve_reuses_original_verification_and_keeps_followup_policy(repo):
    base = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n",
                         "tests/test_retry.py": "assert inverse(0) == 0\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value if value else 0\n"})
    original = {"head_sha": base, "findings": [{**candidate(base), "id": "finding-1"}]}
    options = {**RepoSettings().model_dump(), "_evidence_policy": None, "auto_resolve": True}

    def respond(stage, key, request):
        if key.endswith(":0"):
            assert verification.TEST_EVIDENCE_INSTRUCTIONS not in request.instructions
            return verification_result(requested=True)
        raise InterruptedError("Stop after completed auto-resolve request")

    session = MemorySession(respond)
    raw = {"status": "completed", "base_sha": base, "head_sha": head, "findings": [], "coverage": {"selected": []}}
    with source.local_repository(repo) as (isolated, env):
        with pytest.raises(InterruptedError):
            asyncio.run(workflows.postprocess(isolated, env, raw, options, session, history=[original]))

        def resume(stage, key, request):
            assert key.endswith(":1")
            assert "validation_feedback" in request.messages[0].content
            assert "current source: \"tests/test_retry.py\"" not in request.messages[0].content
            return verification_result()

        session.callback = resume
        result = asyncio.run(workflows.postprocess(isolated, env, raw, options, session, history=[original]))
    assert result["verification"]["verifications"][0]["status"] == "completed"
    assert result["history"][0]["status"] == "open"
    assert len(session.sent) == 2


def test_unmarked_verification_preserves_unknown_attempt_without_replay(repo):
    base = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value if value else 0\n"})
    original = {"head_sha": base, "findings": [{**candidate(base), "id": "finding-1"}]}
    session = MemorySession(lambda *args: verification_result())
    with source.local_repository(repo) as (isolated, env):
        asyncio.run(workflows.verify(isolated, env, original, head,
                                    {**RepoSettings().model_dump(), "_evidence_policy": None}, session))
        key = next(iter(session.records))
        digest, _, _ = session.records[key]
        session.records[key] = (digest, {"status": "failed", "consumption_unknown": True}, None)
        session.callback = lambda *args: pytest.fail("Unknown request was replayed")
        result = asyncio.run(workflows.verify(isolated, env, original, head,
            {**RepoSettings().model_dump(), "_evidence_policy": None, "_unmarked_verification_policy": True}, session))
    assert result["verifications"][0]["status"] == "failed"
    assert session.records[key][1]["consumption_unknown"]


@pytest.mark.parametrize("integration", [False, True])
@pytest.mark.parametrize("entrypoint", ["retry", "resume"])
def test_saved_assessment_restores_policy_for_requested_tests(repo, integration, entrypoint):
    base = commit(repo, {"app.py": "def inverse(value):\n    return value\n",
                         "tests/test_retry.py": "assert inverse(0) == 0\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    options = {**RepoSettings().model_dump(), "_settings_sha": base}
    if integration:
        options["_assessment_attribution"] = "merge-base/v1"
    policy = planning.EVIDENCE_POLICY if integration else None
    proposal = candidate(head)
    session = MemorySession(lambda *args: decision(verdict="uncertain", required_paths=["tests/test_retry.py"]))
    with source.local_repository(repo) as (isolated, env):
        run = workflows._run({"base_sha": base, "head_sha": head},
            {**options, "_evidence_policy": policy, "_assessment_attribution": None}, session)
        context = workflows.assessment.build_context(isolated, env, env, run, proposal,
            {"findings": [], "prior_candidates": []}, lambda: False)
        value = {"options": options, "workflow_kind": "review", "candidate": proposal,
                 "task_key": planning.fingerprint(proposal),
                 "context": context, "base_sha": base, "head_sha": head}

        def respond(stage, key, request):
            if key.endswith(":0"):
                return decision(verdict="uncertain", required_paths=["tests/test_retry.py"])
            text = request.messages[0].content
            assert ("current source: \"tests/test_retry.py\"" in text) == integration
            assert ("validation_feedback" in text) != integration
            return decision(verdict="uncertain")

        session.callback = respond
        if entrypoint == "retry":
            result = asyncio.run(assessments._execute(repo, isolated, env, value, session))
        else:
            session.history = SimpleNamespace(assessment_input=lambda *args: value)
            run.config["settings"]["_evidence_policy"] = planning.EVIDENCE_POLICY
            result = asyncio.run(workflows._assess(isolated, env, run, proposal, {}, session,
                                                  candidate_id="saved-candidate"))
    assert result["verdict"] == "uncertain"
    assert "_evidence_policy" not in value["options"]


def test_legacy_analysis_reads_keep_default_test_exclusion(repo):
    base = commit(repo, {"app.py": "value = 1\n", "tests/test_retry.py": "assert value == 1\n"})
    head = commit(repo, {"app.py": "value = 2\n"})
    options = {**RepoSettings().model_dump(), "_evidence_policy": None}
    calls = [ToolCall(id="read-test", name="read_source", arguments={
        "path": "tests/test_retry.py", "start_line": 1, "end_line": 1})]
    with source.local_repository(repo) as (isolated, env):
        plan = planning.plan_review(isolated, env, base, head, base, options, lambda: False)
        for _ in range(2):
            result, sources = engine.read_sources(calls, repo=isolated, env=env, plan=plan, options=options,
                credentials={}, authorize=lambda: None, stopped=lambda: False, source_tool_policy="bounded-reads/v2")
            assert sources == []
            assert result["selections"][0]["reason"] == "excluded"


def test_saved_evidence_policy_rejects_unknown_version():
    with pytest.raises(ValueError, match="evidence policy"):
        planning.supports_test_evidence({"_evidence_policy": "unsupported/v99"})


@pytest.mark.parametrize("explicit_policy", [False, True])
def test_resumed_review_probes_unmarked_embedded_verification_only(repo, monkeypatch, explicit_policy):
    sha = commit(repo, {"app.py": "value = 1\n"})
    connection = {"provider": "fixture", "protocol": "openai", "model": "fixture", "options": {}}
    options = RepoSettings().model_dump()
    saved = {"effort": None, "graph": False, "history": [],
             "assessment_attribution": "merge-base/v1",
             "connection": planning.fingerprint(service.effective_connection(connection, options))}
    if explicit_policy:
        saved["evidence_policy"] = planning.EVIDENCE_POLICY

    class MemoryHistory:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def row(self, *args):
            return {"report": None, "started": time.time()}

        def claim(self, *args):
            return nullcontext()

        def previous_ids(self, *args):
            return []

        def attempts(self, *args):
            return {}

    @contextmanager
    def inputs(*args):
        with source.local_repository(repo) as (isolated, env):
            yield Snapshot(repo=isolated, env=env, mode="commits", base_sha=sha,
                           head_sha=sha, merge_base=sha, settings_sha=sha), saved

    def stop(repo, env, plan, connection, options, credentials, **kwargs):
        assert options.get("_unmarked_verification_policy", False) != explicit_policy
        assert options["_evidence_policy"] == planning.EVIDENCE_POLICY
        raise InterruptedError("Captured policy before inference")

    monkeypatch.setattr(service, "History", lambda *args: MemoryHistory())
    monkeypatch.setattr(service, "_inputs", inputs)
    monkeypatch.setattr(engine, "run", stop)
    with pytest.raises(InterruptedError, match="Captured policy"):
        service.review_local(repo, resume="saved-run", connection=connection, get_credentials=lambda: {})
