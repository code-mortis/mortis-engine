import asyncio
import json

import pytest

from mortis_engine import gateway, operations
from mortis_engine.history import History
from mortis_engine.llm_http import InferenceError
from mortis_engine.schemas import Response, Usage
from mortis_engine.workflow import Session


@pytest.fixture
def local(tmp_path, monkeypatch):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return repo


CONNECTION = {"kind": "api", "provider": "test", "protocol": "openai", "model": "fake", "options": {}}
SCHEMA = {"type": "object", "properties": {"value": {"type": "string"}}}


def ask(session, key="one", **extra):
    return session.ask("verification", key, instructions="Check supplied source", context={"body": "fixture"},
                       schema=SCHEMA, validate=lambda v: {"value": v["value"]}, **extra)


def test_durable_replay_reuses_sanitized_result_under_exclusive_owner(local, monkeypatch):
    calls = []

    async def send(*args, **kwargs):
        calls.append(1)
        return Response(output={"value": "fixture-sensitive-token"}, usage=Usage(total_tokens=12))

    monkeypatch.setattr(gateway, "send", send)
    credentials = {"api_key": "fixture-sensitive-token"}
    with History(local) as history:
        run_id = history.begin("verify", "HEAD", {})
        with history.claim(run_id):
            stored = asyncio.run(ask(Session(CONNECTION, credentials, history=history, run_id=run_id)))
            with pytest.raises(ValueError, match="owns"):
                with history.claim(run_id):
                    pass
    with History(local) as history, history.claim(run_id):
        session = Session(CONNECTION, credentials, history=history, run_id=run_id)
        assert asyncio.run(ask(session)) == stored == {"value": "[redacted]"}
        assert len(calls) == 1 and session.usage()["total_tokens"] == 12
        assert "fixture-sensitive-token" not in str(history.attempts(run_id))


def test_dead_attempt_keeps_reservation_and_never_resends(local, monkeypatch):
    monkeypatch.setattr(gateway, "send", lambda *a, **kw: pytest.fail("Unknown dispatch repeated"))
    with History(local) as history:
        run_id = history.begin("verify", "HEAD", {})
        history.start_attempt(run_id, {"task_key": "lost", "stage": "verification", "reserved_tokens": 20000,
                              "status": "running", "usage": {}, "consumption_unknown": True}, "hash")
        session = Session(CONNECTION, {}, history=history, run_id=run_id)
        with pytest.raises(InferenceError, match="unknown"):
            asyncio.run(ask(session, "later"))
        assert session.usage()["total_tokens"] is None and session.usage()["reserved_tokens"] == 20000


def test_stage_budget_and_repair_need_known_tokens(monkeypatch):
    sent = []

    async def send(*a, **kw):
        sent.append(1)
        return Response(output={"value": "first"}, usage=Usage(total_tokens=20000))

    monkeypatch.setattr(gateway, "send", send)
    session = Session(CONNECTION, {})
    asyncio.run(ask(session, token_budget=30000))
    with pytest.raises(InferenceError, match="budget"):
        asyncio.run(ask(session, "second", token_budget=30000))
    assert sent == [1]


def test_secret_rotation_checks_retained_finding_before_acknowledging():
    report = {"findings": [{"id": "old", "kind": "secret", "identity": "digest"}]}
    operations.acknowledge_rotations(report, ["old"])
    assert report["findings"][0]["rotation"] == {"identity": "digest"}
    with pytest.raises(ValueError, match="retained secret"):
        operations.acknowledge_rotations(json.loads(json.dumps(report)), ["missing"])


def test_uncapped_output_reserves_remaining_budget_and_serializes_concurrent_requests(monkeypatch):
    sent, active, maximum = [], 0, 0

    async def send(connection, credentials, request, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        sent.append(request)
        assert gateway.reservation(connection, request) + request.max_output_tokens <= 10000 - 100 * (len(sent) - 1)
        await asyncio.sleep(0)
        active -= 1
        return Response(output={"value": "ok"}, usage=Usage(total_tokens=100))

    monkeypatch.setattr(gateway, "send", send)
    session = Session(CONNECTION, {}, scan_budget=10000)

    async def run(offset):
        return await asyncio.gather(*(ask(session, str(i + offset), max_output_tokens=0) for i in range(2)))

    assert asyncio.run(run(0)) == [{"value": "ok"}] * 2
    # Reviews run analysis and postprocessing on separate loops using the same session.
    assert asyncio.run(run(2)) == [{"value": "ok"}] * 2
    assert maximum == 1 and sent[0].max_output_tokens > sent[1].max_output_tokens > 0
    assert session.usage()["total_tokens"] == 400


def test_uncapped_output_replay_and_unknown_usage_still_block_new_dispatch(local, monkeypatch):
    calls = []

    async def send(connection, credentials, request, **kwargs):
        calls.append(request.max_output_tokens)
        return Response(output={"value": "ok"}, usage=Usage())

    monkeypatch.setattr(gateway, "send", send)
    with History(local) as history:
        run_id = history.begin("verify", "HEAD", {})
        session = Session(CONNECTION, {}, history=history, run_id=run_id)
        assert asyncio.run(ask(session, max_output_tokens=0)) == {"value": "ok"}
        assert session.usage()["requests"][0]["output_unbounded"] is True
    with History(local) as history:
        session = Session(CONNECTION, {}, history=history, run_id=run_id)
        assert asyncio.run(ask(session, max_output_tokens=0)) == {"value": "ok"}
        with pytest.raises(InferenceError, match="unknown token usage"):
            asyncio.run(ask(session, "second", max_output_tokens=0))
        assert calls == [0] and session.usage()["total_tokens"] is None


def test_uncapped_request_cannot_dispatch_after_total_budget_is_consumed(monkeypatch):
    sent = []

    async def send(*args, **kwargs):
        sent.append(1)
        return Response(output={"value": "ok"}, usage=Usage(total_tokens=10000))

    monkeypatch.setattr(gateway, "send", send)
    session = Session(CONNECTION, {})
    asyncio.run(ask(session, max_output_tokens=0, token_budget=10000))
    with pytest.raises(InferenceError, match="budget"):
        asyncio.run(ask(session, "second", max_output_tokens=0, token_budget=10000))
    assert sent == [1]
