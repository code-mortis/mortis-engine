"""Opt-in discovery shares accounting while keeping prior validated coverage."""

import json
import time
from contextlib import asynccontextmanager

import pytest
from pydantic import ValidationError

from mortis_engine import admission, gateway
from mortis_engine.history import History
from mortis_engine.llm_http import InferenceError
from mortis_engine.review import engine
from mortis_engine.review.settings import RepoSettings
from mortis_engine.schemas import Usage
from mortis_engine.workflow import Session

from .git_helpers import commit
from .test_execution import CONNECTION, OPTIONS, draft_finding, final, plan


def test_default_and_legacy_requests_are_unchanged_and_opt_in_is_strict(monkeypatch):
    value = plan()
    before, = engine.prepare_batches(value, CONNECTION, OPTIONS, {})
    after, = engine.prepare_batches(value, CONNECTION, {**OPTIONS, "second_pass": False}, {})
    assert before.request == after.request
    assert RepoSettings().second_pass is False
    with pytest.raises(ValidationError):
        RepoSettings(second_pass="true")
    calls = []

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        return final(request)

    monkeypatch.setattr(gateway, "send", send)
    status, raw, _ = engine.run(None, {}, value, CONNECTION, OPTIONS, {})
    assert status == "completed" and len(calls) == 1 and "second_pass" not in raw


def test_second_discovery_is_separate_skips_exact_repeats_and_reuses_saved_tasks(repo, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(repo.parent / (repo.name + "-state")))
    commit(repo, {"app.py": "value = 0\n"})
    calls = []

    async def send(connection, credentials, request, **kwargs):
        payload = json.loads(request.messages[0].content)
        calls.append(request)
        response = final(request)
        response.output["findings"] = [draft_finding(request)]
        if payload.get("discovery_pass") == 2:
            assert len(payload["previous_candidates"]) == 1
            assert "untrusted" in request.instructions and "additional candidates" in request.instructions
            response.output["findings"].append(draft_finding(request, content="Different defect"))
        return response

    monkeypatch.setattr(gateway, "send", send)
    options = {**OPTIONS, "second_pass": True}
    with History(repo) as history:
        run_id = history.begin("review", "fixture", {})
        session = Session(CONNECTION, {}, history=history, run_id=run_id)
        first = engine.run(None, {}, plan(), CONNECTION, options, {}, session=session)
        assert len(history.attempts(run_id)) == 2
    with History(repo) as history:
        restored = Session(CONNECTION, {}, history=history, run_id=run_id)
        resumed = engine.run(None, {}, plan(), CONNECTION, options, {}, session=restored)
    assert len(calls) == 2 and first == resumed
    assert calls[0].session_id != calls[1].session_id
    assert json.loads(calls[1].messages[0].content)["task_key"].endswith(":discovery:2")
    status, raw, candidates = first
    assert status == "completed" and len(candidates) == 2
    assert raw["second_pass"]["added_candidates"] == 1
    assert raw["second_pass"]["status"] == "completed"
    assert raw["summary"]["total_tokens"] == 20
    assert {row["stage"] for row in raw["usage"]} == {"analysis"}


@pytest.mark.parametrize("reason", ["budget", "usage_unknown", "deadline", "cancelled"])
def test_second_pass_cannot_reset_budget_unknown_usage_or_deadline(monkeypatch, reason):
    calls, stopped = [], [False]
    options = {**OPTIONS, "second_pass": True}
    batch, = engine.prepare_batches(plan(), CONNECTION, options, {})
    budget = gateway.reservation(CONNECTION, batch.request) + batch.request.max_output_tokens
    if reason == "budget":
        options["token_budget"] = budget

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        response = final(request, tokens=None if reason == "usage_unknown" else budget if reason == "budget" else 10)
        response.output["findings"] = [draft_finding(request)]
        return response

    def progress(event):
        if event.get("discovery_pass") == 2 or event.get("completed_units") != 1:
            return
        if reason == "deadline":
            monkeypatch.setattr(time, "monotonic", lambda: started + 3)
        if reason == "cancelled":
            stopped[0] = True

    monkeypatch.setattr(gateway, "send", send)
    session = Session(CONNECTION, {})
    started = time.monotonic()
    status, raw, candidates = engine.run(None, {}, plan(), CONNECTION, options, {}, session=session,
        deadline=started + 2 if reason == "deadline" else None, stopped=lambda: stopped[0], progress=progress)
    assert status == "partial" and len(calls) == len(candidates) == 1
    assert raw["manifest"]["coverage"]["completed"]
    assert raw["second_pass"]["status"] == "partial"
    assert raw["errors"][0]["message"] == "second_pass_" + reason
    assert len(session.records) == 1
    if reason == "usage_unknown":
        assert raw["summary"]["total_tokens"] is None
        assert raw["summary"]["reserved_tokens"] > 16384
        assert next(iter(session.records.values()))[1]["consumption_unknown"] is True


def test_second_pass_keeps_independent_repair_rounds_and_first_partial_coverage(monkeypatch):
    calls = []

    async def send(connection, credentials, request, **kwargs):
        payload = json.loads(request.messages[0].content)
        calls.append(request)
        if payload["assigned_units"][0]["path"] == "app1.py":
            raise InferenceError("auth", "Fixture failure", consumption_unknown=False)
        response = final(request)
        second = payload.get("discovery_pass") == 2
        if second and len(request.messages) == 1:
            response.output["completed_units"] = []
        if second and len(request.messages) == 2:
            response.output["findings"] = [draft_finding(request)]
        return response

    monkeypatch.setattr(gateway, "send", send)
    status, raw, candidates = engine.run(None, {}, plan(2), CONNECTION, {**OPTIONS, "second_pass": True}, {})
    assert status == "partial" and len(candidates) == 1 and len(calls) == 4
    assert len(raw["manifest"]["coverage"]["completed"]) == len(raw["manifest"]["coverage"]["failed"]) == 1
    assert raw["second_pass"]["status"] == "completed"
    assert {unit["path"] for unit in raw["second_pass"]["units"]} == {"app0.py"}
    assert "validation_diagnostics" in json.loads(calls[-1].messages[-1].content)


@pytest.mark.parametrize("result_policy", [None, engine.DISCOVERY_RESULT_POLICY])
def test_second_pass_output_recovery_retains_unassigned_context_and_citations(monkeypatch, result_policy):
    @asynccontextmanager
    async def no_state_slot(*args, **kwargs):
        yield

    monkeypatch.setattr(admission, "slot", no_state_slot)
    value = plan(3)
    value.batches = [value.units]
    calls = []
    second_recoveries = []

    async def send(connection, credentials, request, **kwargs):
        payload = json.loads(request.messages[0].content)
        calls.append(request)
        assigned = payload["assigned_units"]
        second = payload.get("discovery_pass") == 2
        if len(assigned) > 1:
            if second:
                assert [unit["path"] for unit in payload["context_units"]] == ["app2.py"]
            raise InferenceError("output_limit", "Truncated", usage=Usage(total_tokens=10))
        if assigned[0]["path"] == "app2.py":
            raise InferenceError("auth", "Fixture failure", consumption_unknown=False)
        response = final(request)
        if second:
            second_recoveries.append(payload)
            context_paths = {unit["path"] for unit in payload["context_units"]}
            peer = "app1.py" if assigned[0]["path"] == "app0.py" else "app0.py"
            expected_context = {peer, "app2.py"} if result_policy else {peer}
            assert context_paths == expected_context
            supplied = assigned + payload["context_units"]
            assert len({unit["key"] for unit in supplied}) == len(supplied)
            original = {unit["key"]: unit for unit in json.loads(calls[0].messages[0].content)["assigned_units"]}
            assert all(unit == original[unit["key"]] for unit in supplied)
            assert {source["path"] for source in payload["citation_sources"]} == {"app0.py", "app1.py", "app2.py"}
            finding = draft_finding(request)
            local = next(source for source in payload["citation_sources"] if source["path"] == finding["path"])
            finding["evidence"][0]["source_id"] = local["source_id"]
            if result_policy:
                supporting = next(source for source in payload["citation_sources"] if source["path"] == "app2.py")
                finding["evidence"].append({"source_id": supporting["source_id"], "start_line": 3, "end_line": 3})
            response.output["findings"] = [finding]
        return response

    monkeypatch.setattr(gateway, "send", send)
    options = {**OPTIONS, "second_pass": True, "_discovery_result_policy": result_policy}
    status, raw, candidates = engine.run(None, {}, value, CONNECTION, options, {})
    assert status == "partial" and len(calls) == 7 and len(second_recoveries) == 2
    assert raw["second_pass"]["status"] == "completed"
    assert raw["second_pass"]["added_candidates"] == len(candidates) == 2
    assert {row["path"] for row in raw["manifest"]["coverage"]["failed"]} == {"app2.py"}
    assert {candidate["path"] for candidate in candidates} == {"app0.py", "app1.py"}
    if result_policy:
        assert all(candidate["evidence"][-1]["quote"] == "value = 2" for candidate in candidates)


def test_second_pass_unknown_dispatch_is_not_replayed_on_resume(monkeypatch):
    calls = []

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        if len(calls) == 2:
            raise InferenceError("interrupted", "Unknown dispatch", consumption_unknown=True)
        response = final(request)
        response.output["findings"] = [draft_finding(request)]
        return response

    monkeypatch.setattr(gateway, "send", send)
    session = Session(CONNECTION, {})
    options = {**OPTIONS, "second_pass": True}
    first = engine.run(None, {}, plan(), CONNECTION, options, {}, session=session)
    resumed = engine.run(None, {}, plan(), CONNECTION, options, {}, session=session)
    assert first == resumed and len(calls) == 2
    assert first[0] == "partial" and len(first[2]) == 1
    assert first[1]["summary"]["total_tokens"] is None
    assert session.usage()["reserved_tokens"] > 16384
