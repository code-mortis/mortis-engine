import asyncio
import json

import pytest

from mortis_engine import gateway
from mortis_engine.llm_http import InferenceError
from mortis_engine.review import engine, planning
from mortis_engine.schemas import Response, Usage

CONNECTION = {"kind": "api", "provider": "custom", "protocol": "openai", "model": "fixture", "options": {}}
OPTIONS = {"language": "Spanish", "effort": "medium", "review_sensitivity": "balanced", "concurrency": 1}


def plan(count=1, same_file=False):
    units = [planning.ReviewUnit(f"unit-{i}", "app.py" if same_file else f"app{i}.py", None, "M", "b" * 40,
        i + 1, i + 1, f"value = {i}\n", f"@@ -1 +{i + 1} @@\n-old\n+value = {i}\n", "Check defects", "c" * 64)
        for i in range(count)]
    return planning.ReviewPlan("a" * 40, "b" * 40, "a" * 40, "d" * 64, "e" * 64,
        {"layers": [{"rules": [], "include": [], "exclude": []}]},
        [planning.SelectedFile(path, None, "M", "b" * 40, units=[u for u in units if u.path == path])
         for path in sorted({u.path for u in units})], [], units, [[u] for u in units])


def final(request, *, tokens=10):
    schema = request.output_schema["properties"]
    return Response(output={"task_key": schema["task_key"]["enum"][0],
        "completed_units": schema["completed_units"]["items"]["enum"], "findings": []}, usage=Usage(total_tokens=tokens))


def test_only_validated_results_count_and_partial_file_stays_incomplete(monkeypatch):
    calls = []

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        if len(calls) == 1:
            return final(request)
        raise InferenceError("auth", "Provider rejected authentication", consumption_unknown=False)

    monkeypatch.setattr(gateway, "send", send)
    state, report, comments = engine.run(None, {}, plan(2, True), CONNECTION, OPTIONS, {})
    assert state == "partial" and comments == []
    assert len(report["manifest"]["coverage"]["completed"]) == 0
    assert len(report["manifest"]["coverage"]["failed"]) == 1
    assert report["summary"]["total_tokens"] == 10


@pytest.mark.parametrize("same_file", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_progress_tracks_concurrent_files_before_dispatch_and_clears_them(monkeypatch, same_file, failed):
    events, calls, capacity = [], [], []
    both_started = asyncio.Event()

    async def send(connection, credentials, request, **kwargs):
        units = json.loads(request.messages[0].content)["assigned_units"]
        assert units[0]["path"] in events[-1]["active_paths"]
        calls.append(request)
        if len(calls) == 1:
            await both_started.wait()
        else:
            both_started.set()
            if failed:
                raise InferenceError("auth", "Rejected", consumption_unknown=False)
        return final(request)

    monkeypatch.setattr(gateway, "send", send)
    def progress(value):
        (events if value["phase"] == "analysis" else capacity).append(value)

    state, report, _ = engine.run(None, {}, plan(2, same_file), CONNECTION, {**OPTIONS, "concurrency": 2}, {},
                                  progress=progress)
    assert state == ("partial" if failed else "completed")
    assert events[0]["completed_units"] == 0
    assert events[1]["active_paths"] == (["app.py"] if same_file else ["app0.py", "app1.py"])
    assert max(event["active_requests"] for event in capacity) == 2
    assert events[1]["completed_files"] == 0
    assert events[1]["selected_files"] == (1 if same_file else 2)
    assert len(events[2]["active_paths"]) == 1
    assert events[-1] == {"phase": "analysis", "active_paths": [], "selected_units": 2,
                          "possible_findings": 0,
                          "completed_units": 1 if failed else 2, "active_requests": 0, "waiting_requests": 0,
                          "selected_files": 1 if same_file else 2, "failed_files": int(failed),
                          "completed_files": (0 if failed else 1) if same_file else (1 if failed else 2)}
    assert sum(unit["status"] == "completed" for unit in report["units"]) == events[-1]["completed_units"]


def test_invalid_coverage_has_one_repair_and_never_completes(monkeypatch):
    calls = []

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        response = final(request)
        response.output["completed_units"] = []
        return response

    monkeypatch.setattr(gateway, "send", send)
    state, report, _ = engine.run(None, {}, plan(), CONNECTION, OPTIONS, {})
    assert state == "failed" and len(calls) == 2
    assert report["errors"][0]["message"] == "invalid_result"
    assert "validation_diagnostics" in json.loads(calls[1].messages[-1].content)


@pytest.mark.parametrize(("first_usage", "expected"), [(10, "completed"), (650, "partial")])
def test_analysis_waits_for_temporary_reservation_and_respects_settled_usage(monkeypatch, first_usage, expected):
    checking_second = asyncio.Event()
    sent = []

    def position(request):
        return json.loads(request.messages[0].content)["assigned_units"][0]["key"]

    def reserve(connection, request):
        if position(request) == "unit-0":
            return 700
        checking_second.set()
        return 300

    async def send(connection, credentials, request, **kwargs):
        sent.append(position(request))
        if sent[-1] == "unit-0":
            await asyncio.wait_for(checking_second.wait(), 5)
            assert sent == ["unit-0"]
            return final(request, tokens=first_usage)
        return final(request)

    monkeypatch.setattr(gateway, "send", send)
    monkeypatch.setattr(gateway, "reservation", reserve)
    options = {**OPTIONS, "token_budget": 1000, "max_output_tokens": 100, "concurrency": 2}
    state, report, _ = engine.run(None, {}, plan(2), CONNECTION, options, {})
    assert state == expected
    assert sent == (["unit-0", "unit-1"] if expected == "completed" else ["unit-0"])
    assert report["summary"]["budget_exceeded"] == (expected == "partial")
    assert report["summary"]["reserved_tokens"] == first_usage + 10 * (len(sent) - 1)


def test_unknown_usage_retains_reservation_and_stops_repair(monkeypatch):
    calls = []

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        response = final(request, tokens=None)
        response.output["completed_units"] = []
        return response

    monkeypatch.setattr(gateway, "send", send)
    state, report, _ = engine.run(None, {}, plan(), CONNECTION, OPTIONS, {})
    assert state == "failed" and len(calls) == 1
    assert report["summary"]["total_tokens"] is None
    assert report["summary"]["reserved_tokens"] > 16384
    assert report["errors"][0]["message"] == "usage_unknown"


def test_known_output_limit_recovers_once_and_accounts_original_request(monkeypatch):
    calls = []

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        if len(calls) == 1:
            raise InferenceError("output_limit", "Output truncated", usage=Usage(total_tokens=50))
        return final(request)

    monkeypatch.setattr(gateway, "send", send)
    state, report, _ = engine.run(None, {}, plan(), CONNECTION, OPTIONS, {})
    assert state == "completed" and len(calls) == 2
    assert calls[1].max_output_tokens == 32768 and not calls[1].tools
    assert report["summary"]["total_tokens"] == 60


def test_cancelled_review_does_not_dispatch(monkeypatch):
    async def send(*args, **kwargs):
        pytest.fail("Cancelled review dispatched inference")

    monkeypatch.setattr(gateway, "send", send)
    state, report, _ = engine.run(None, {}, plan(), CONNECTION, OPTIONS, {}, stopped=lambda: True)
    assert state == "failed" and report["summary"]["total_tokens"] == 0
    assert report["units"][0]["error"] == "cancelled"


def test_redaction_preserves_line_identity_and_rejects_secrets_in_paths():
    from mortis_engine.review import context

    credentials = {"api_key": "secret\nkey"}
    assert context.sanitize("first\nsecret\nkey\nlast", credentials).splitlines() == [
        "first", "[redacted]", "[redacted]", "last"]
    with pytest.raises(ValueError, match="structural"):
        context.sanitize({"path": "secret\nkey"}, credentials)


def test_nested_structural_values_with_credentials_are_withheld():
    from types import SimpleNamespace

    from mortis_engine.review import context
    from mortis_engine.security_scans.engine import redacted_context

    secret = "fixture-credential"
    nested = [{"entry": {"name": secret + ".py"}}]
    item = SimpleNamespace(redact=lambda text: text.replace(secret, "[redacted]"))
    for sanitize in (lambda value: context.sanitize(value, {"api_key": secret}),
                     lambda value: redacted_context(item, value)):
        with pytest.raises(ValueError, match="identifier"):
            sanitize({"paths": nested})
        assert sanitize({"source": nested}) == {"source": [{"entry": {"name": "[redacted].py"}}]}


def test_recovery_interruption_preserves_completed_units(monkeypatch):
    calls = []
    value = plan(2, same_file=True)
    value.batches = [value.units]

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        if len(calls) == 1:
            raise InferenceError("output_limit", "Output truncated", usage=Usage(total_tokens=50))
        if len(calls) == 2:
            return final(request)
        raise TimeoutError

    monkeypatch.setattr(gateway, "send", send)
    state, report, _ = engine.run(None, {}, value, CONNECTION, OPTIONS, {})
    assert state == "partial"
    assert [unit["status"] for unit in report["units"]] == ["completed", "failed"]
    assert report["units"][1]["error"] == "deadline"
    assert len({request.session_id for request in calls}) == 1


def test_recovery_units_share_analysis_slots_and_refill(monkeypatch):
    value = plan(5)
    value.batches = [value.units]
    gates = {unit.key: asyncio.Event() for unit in value.units}
    active, sessions = set(), set()
    replaced = []

    async def send(connection, credentials, request, **kwargs):
        sessions.add(request.session_id)
        units = json.loads(request.messages[0].content)["assigned_units"]
        if len(units) > 1:
            raise InferenceError("output_limit", "Output truncated", usage=Usage(total_tokens=50))
        key = units[0]["key"]
        active.add(key)
        try:
            assert len(active) <= 4
            if len(active) == 4 and key == "unit-3":
                gates["unit-1"].set()
            if key == "unit-4":
                replaced.append(active.copy())
                for gate in gates.values():
                    gate.set()
            await asyncio.wait_for(gates[key].wait(), 5)
        finally:
            active.remove(key)
        return final(request)

    monkeypatch.setattr(gateway, "send", send)
    monkeypatch.setattr(gateway, "reservation", lambda *args: 100)
    options = {**OPTIONS, "concurrency": 4, "token_budget": 1000, "max_output_tokens": 100}
    state, report, _ = engine.run(None, {}, value, CONNECTION, options, {})
    assert state == "completed" and report["summary"]["total_tokens"] == 100
    assert replaced == [{"unit-0", "unit-2", "unit-3", "unit-4"}]
    assert len(sessions) == 1


@pytest.mark.parametrize("limit,expected", [(None, [16384, 32768, 32768]), (4096, [4096] * 3), (0, [0] * 3)])
def test_output_recovery_keeps_explicit_limit_and_legacy_default(monkeypatch, limit, expected):
    sent = []
    value = plan(2, same_file=True)
    value.batches = [value.units]

    async def send(connection, credentials, request, **kwargs):
        sent.append(request.max_output_tokens)
        if len(sent) == 1:
            raise InferenceError("output_limit", "Provider output limit", usage=Usage(total_tokens=50))
        return final(request)

    monkeypatch.setattr(gateway, "send", send)
    state, _, _ = engine.run(None, {}, value, CONNECTION, {**OPTIONS, "max_output_tokens": limit}, {})
    assert state == "completed" and sent == expected


def test_absolute_deadline_prevents_dispatch(monkeypatch):
    import time

    async def send(*args, **kwargs):
        pytest.fail("Expired review dispatched inference")

    monkeypatch.setattr(gateway, "send", send)
    state, report, _ = engine.run(None, {}, plan(), CONNECTION, OPTIONS, {}, deadline=time.monotonic() - 1)
    assert state == "failed" and report["summary"]["total_tokens"] == 0
    assert report["errors"][0]["message"] == "deadline"


def test_sessions_are_fresh_per_run_and_stable_during_repair(monkeypatch):
    sessions = []

    async def send(connection, credentials, request, **kwargs):
        sessions.append(request.session_id)
        response = final(request)
        if len(sessions) % 2:
            response.output["completed_units"] = []
        return response

    monkeypatch.setattr(gateway, "send", send)
    for _ in range(2):
        assert engine.run(None, {}, plan(), CONNECTION, OPTIONS, {})[0] == "completed"
    assert sessions[0] == sessions[1] and sessions[2] == sessions[3] and sessions[0] != sessions[2]


@pytest.mark.parametrize("unknown,tokens,expected", [(False, 0, 3), (False, None, 3), (True, None, 1), (True, 0, 1), (False, 1, 1)])
def test_retries_are_bounded_and_require_known_zero_consumption(monkeypatch, unknown, tokens, expected):
    from mortis_engine import llm_http

    calls, delays = [], []

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        raise InferenceError("rate_limit", "Provider rate limited", retryable=True,
                             usage=Usage(total_tokens=tokens), consumption_unknown=unknown)

    async def bounded(awaitable, stopped, timeout):
        delays.append(timeout)
        awaitable.close()

    monkeypatch.setattr(gateway, "send", send)
    monkeypatch.setattr(llm_http, "_bounded", bounded)
    state, report, _ = engine.run(None, {}, plan(), CONNECTION, OPTIONS, {})
    assert state == "failed" and len(calls) == expected and len(delays) == expected - 1
    assert len(report["usage"]) == expected
    if unknown:
        assert report["summary"]["reserved_tokens"] >= 16384
    assert report["summary"]["total_tokens"] == (None if unknown else (tokens or 0) * expected)


def test_invalid_source_calls_use_remaining_source_rounds(monkeypatch):
    from mortis_engine.schemas import ToolCall

    calls = []

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        if len(calls) <= 2:
            return Response(tool_calls=[ToolCall(id=str(len(calls)), name="wrong", arguments={})],
                            usage=Usage(total_tokens=10))
        return final(request)

    monkeypatch.setattr(gateway, "send", send)
    state, report, _ = engine.run(None, {}, plan(), CONNECTION, OPTIONS, {})
    assert state == "completed" and len(calls) == 3
    assert report["summary"]["total_tokens"] == 30


def test_historical_redaction_metadata_is_rejected_before_dispatch(monkeypatch):
    async def send(*args, **kwargs):
        pytest.fail("Unsupported redaction metadata dispatched inference")

    monkeypatch.setattr(gateway, "send", send)
    with pytest.raises(ValueError, match="Historical secret metadata"):
        engine.run(None, {}, plan(), CONNECTION, OPTIONS, {"redaction_rules": [{"identity": "secret"}]})


def test_preparation_timeout_returns_every_unit_with_zero_usage(monkeypatch):
    def prepare(*args, **kwargs):
        raise TimeoutError("Preparation expired")

    monkeypatch.setattr(engine, "prepare_batches", prepare)
    state, report, comments = engine.run(None, {}, plan(2), CONNECTION, OPTIONS, {})
    assert state == "failed" and comments == []
    assert len(report["manifest"]["coverage"]["failed"]) == 2
    assert len(report["units"]) == 2
    assert all(unit["error"] == "deadline" for unit in report["units"])
    assert report["usage"] == [] and report["summary"]["total_tokens"] == 0
    assert report["summary"]["reserved_tokens"] == 0


def test_unknown_consumption_with_positive_usage_never_recovers_output_limit(monkeypatch):
    calls = []

    async def send(*args, **kwargs):
        calls.append(1)
        raise InferenceError("output_limit", "Truncated but total consumption unconfirmed",
                             usage=Usage(total_tokens=5), consumption_unknown=True)

    monkeypatch.setattr(gateway, "send", send)
    state, report, _ = engine.run(None, {}, plan(), CONNECTION, OPTIONS, {})
    assert state == "failed" and len(calls) == 1
    assert report["summary"]["total_tokens"] is None
    assert report["summary"]["reserved_tokens"] > 16384


@pytest.mark.parametrize("first_completed", [False, True])
@pytest.mark.parametrize("reason", ["deadline", "cancelled"])
def test_inflight_stop_returns_coverage_and_correct_reason(monkeypatch, first_completed, reason):
    import time

    from mortis_engine.review import execution

    clock = [time.monotonic()]
    monkeypatch.setattr(execution.time, "monotonic", lambda: clock[0])
    stopped = False
    calls = []

    async def send(connection, credentials, request, **kwargs):
        nonlocal stopped
        calls.append(1)
        if first_completed and len(calls) == 1:
            return final(request)
        if reason == "deadline":
            clock[0] += 3
        else:
            stopped = True
        raise InferenceError("cancelled", "Transport stopped", consumption_unknown=True)

    monkeypatch.setattr(gateway, "send", send)
    state, report, _ = engine.run(None, {}, plan(2), CONNECTION, OPTIONS, {},
        stopped=lambda: stopped, deadline=clock[0] + (2 if reason == "deadline" else 100))
    assert state == ("partial" if first_completed else "failed")
    assert len(report["manifest"]["coverage"]["completed"]) == int(first_completed)
    failed = [unit for unit in report["units"] if unit["status"] == "failed"]
    assert all(unit["error"] == reason for unit in failed)
    assert report["summary"]["total_tokens"] is None


def draft_finding(request, *, invalid_citation=False, content="Incorrect value"):
    payload = json.loads(request.messages[0].content)
    unit = payload["assigned_units"][0]
    return {"unit_key": unit["key"], "path": unit["path"], "content": content,
            "start_line": unit["start_line"], "end_line": unit["start_line"],
            "category": "correctness", "severity": "medium", "suggestion_code": "value = 1",
            "trigger": "Caller reads value", "consequence": "Incorrect response",
            "evidence": [{"source_id": "unavailable" if invalid_citation else payload["citation_sources"][0]["source_id"],
                          "start_line": unit["start_line"], "end_line": unit["start_line"]}]}


def test_possible_findings_update_while_other_analysis_is_active(monkeypatch):
    events = []
    first_finished = asyncio.Event()

    def progress(event):
        if event["phase"] != "analysis":
            return
        events.append(event)
        if event["possible_findings"] == 1:
            first_finished.set()

    async def send(connection, credentials, request, **kwargs):
        assigned = json.loads(request.messages[0].content)["assigned_units"][0]
        if assigned["path"] == "app1.py":
            await first_finished.wait()
            assert events[-1]["completed_files"] == 1
            assert events[-1]["active_requests"] == 1
            raise InferenceError("auth", "Rejected", consumption_unknown=False)
        response = final(request)
        response.output["findings"] = [draft_finding(request)]
        return response

    monkeypatch.setattr(gateway, "send", send)
    state, _, findings = engine.run(None, {}, plan(2), CONNECTION, {**OPTIONS, "concurrency": 2}, {}, progress=progress)
    assert state == "partial"
    assert events[-1]["possible_findings"] == len(findings) == 1
    assert all("Incorrect value" not in json.dumps(event) for event in events)


def test_recovery_possible_findings_include_validated_partial_units(monkeypatch):
    calls, events, capacity = [], [], []
    value = plan(2, same_file=True)
    value.batches = [value.units]

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        if len(calls) == 1:
            raise InferenceError("output_limit", "Truncated", usage=Usage(total_tokens=10))
        if len(calls) == 3:
            assert events[-1]["possible_findings"] == 1
            assert events[-1]["completed_files"] == 0
            assert capacity[-1]["active_requests"] == 1
            raise TimeoutError
        response = final(request)
        response.output["findings"] = [draft_finding(request)]
        return response

    monkeypatch.setattr(gateway, "send", send)
    state, _, findings = engine.run(None, {}, value, CONNECTION, OPTIONS, {},
        progress=lambda v: (events if v["phase"] == "analysis" else capacity).append(v))
    assert state == "partial"
    assert events[-1]["possible_findings"] == len(findings) == 1


def test_repair_reconsiders_valid_and_invalid_candidates_from_prior_draft(monkeypatch):
    calls, events = [], []

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        response = final(request)
        if len(calls) == 1:
            response.output["findings"] = [draft_finding(request), draft_finding(request, invalid_citation=True,
                                                                                  content="Second concrete defect")]
        else:
            assert events[-1]["possible_findings"] == 0
            followup = json.loads(request.messages[-1].content)
            draft = followup.get("previous_result_draft")
            if draft:
                assert followup["validation_diagnostics"]["issues"][0]["code"] == "unavailable_citation"
                draft["findings"][1]["evidence"][0]["source_id"] = draft["findings"][0]["evidence"][0]["source_id"]
                response.output = draft
        return response

    monkeypatch.setattr(gateway, "send", send)
    state, report, findings = engine.run(None, {}, plan(), CONNECTION, OPTIONS, {},
        progress=lambda v: events.append(v) if v["phase"] == "analysis" else None)
    assert state == "completed" and len(calls) == 2
    assert len(findings) == report["summary"]["findings"] == 2
    assert calls[0].messages[0] == calls[1].messages[0]
    assert calls[0].output_schema == calls[1].output_schema
    assert {finding["content"] for finding in findings} == {"Incorrect value", "Second concrete defect"}
    assert all(finding["evidence"][0]["quote"] == "value = 0" for finding in findings)
    assert events[-1]["possible_findings"] == len(findings)


def test_invalid_candidate_credentials_are_redacted_before_repair(monkeypatch):
    calls = []
    credentials = {"api_key": "private-fixture-token", "extra_headers": {"X-Token": "header-fixture-token"}}

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        response = final(request)
        if len(calls) == 1:
            response.output["findings"] = [draft_finding(request, invalid_citation=True,
                content="private-fixture-token header-fixture-token")]
        else:
            content = request.messages[-1].content
            assert "private-fixture-token" not in content and "header-fixture-token" not in content
            draft = json.loads(content)["previous_result_draft"]
            assert draft["findings"][0]["content"] == "[redacted] [redacted]"
            assert "unvalidated" in json.loads(content)["candidate_revision_instructions"]
            # The model may discard an unsupported draft. Its old allegations are never automatically accepted.
        return response

    monkeypatch.setattr(gateway, "send", send)
    state, report, findings = engine.run(None, {}, plan(), CONNECTION, OPTIONS, credentials)
    assert state == "completed" and len(calls) == 2 and findings == []
    assert "private-fixture-token" not in json.dumps(report)


def test_oversized_candidate_draft_withholds_repair_without_claiming_clean(monkeypatch):
    calls = []

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        response = final(request)
        response.output["findings"] = [draft_finding(request, content="x" * engine.MAX_REPAIR_DRAFT_BYTES)]
        return response

    monkeypatch.setattr(gateway, "send", send)
    state, report, findings = engine.run(None, {}, plan(), CONNECTION, OPTIONS, {})
    assert state == "failed" and len(calls) == 1 and findings == []
    assert report["errors"][0]["message"] == "repair_context_limit"
    assert report["summary"]["total_tokens"] == 10


def test_unknown_usage_candidate_draft_never_dispatches_repair(monkeypatch):
    calls = []

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        response = final(request, tokens=None)
        response.output["findings"] = [draft_finding(request, invalid_citation=True)]
        return response

    monkeypatch.setattr(gateway, "send", send)
    state, report, _ = engine.run(None, {}, plan(), CONNECTION, OPTIONS, {})
    assert state == "failed" and len(calls) == 1
    assert report["errors"][0]["message"] == "usage_unknown"


def test_candidate_draft_survives_source_round_after_repair(monkeypatch):
    from mortis_engine.schemas import ToolCall

    calls = []

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        response = final(request)
        if len(calls) == 1:
            response.output["findings"] = [draft_finding(request, invalid_citation=True)]
        elif len(calls) == 2:
            assert "previous_result_draft" in json.loads(request.messages[-1].content)
            response = Response(tool_calls=[ToolCall(id="read", name="read_source", arguments={
                "path": "app0.py", "start_line": 1, "end_line": 1})], usage=Usage(total_tokens=10))
        else:
            followup = json.loads(request.messages[-1].content)
            assert followup["validation_diagnostics"]["issues"][0]["code"] == "unavailable_citation"
            draft = followup["previous_result_draft"]
            draft["findings"][0]["evidence"][0]["source_id"] = json.loads(request.messages[0].content)[
                "citation_sources"][0]["source_id"]
            response.output = draft
        return response

    monkeypatch.setattr(gateway, "send", send)
    monkeypatch.setattr(engine, "read_sources", lambda *args, **kwargs: ({"kind": "expansion", "selections": []}, []))
    state, report, findings = engine.run(None, {}, plan(), CONNECTION, OPTIONS, {})
    assert state == "completed" and len(calls) == 3 and len(findings) == 1
    assert report["summary"]["total_tokens"] == 30
