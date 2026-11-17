"""Direct connection probes, with synthetic provider HTTP."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from mortis_engine import gateway as llm
from mortis_engine import llm_http
from mortis_engine.schemas import Response, ToolCall, Usage


def configuration(protocol="openai-responses"):
    return {"id": "connection", "kind": "api", "provider": "opencode", "model": "exact-model",
            "protocol": protocol, "base_url": "https://opencode.ai/zen/go/v1",
            "options": {"timeout_sec": 120}}


@pytest.mark.parametrize("extra,expected", [
    ({"thinking": {"type": "enabled", "budget_tokens": 4096}}, 5120),
    ({"thinking.type": "enabled", "thinking.budget_tokens": 4096}, 5120),
    ({"thinking": {"type": "enabled", "budget_tokens": 4096}, "max_tokens": 8192}, 8192),
])
def test_probe_preserves_configured_reasoning_and_output_budgets(monkeypatch, extra, expected):
    monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: url)
    connection, calls = configuration("anthropic"), []
    connection["options"]["extra_body"] = extra

    async def handler(wire):
        if wire.method == "GET":
            return httpx.Response(200, json={"id": "exact-model", "max_tokens": 128000})
        body = json.loads(wire.content)
        assert body["max_tokens"] == expected and body["thinking"]["budget_tokens"] == 4096
        calls.append(body)
        return httpx.Response(200, json=probe_payload("anthropic", len(calls)))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await llm.check_connection(connection, {}, client=client)

    asyncio.run(run())
    assert len(calls) == 2


def probe_payload(protocol, turn, *, incorrect=None):
    tool = {"id": "call-probe", "name": "mortis_probe", "arguments": {"value": 17}}
    content = "MORTIS_PROBE_OK" if turn == 2 else "Call the synthetic probe"
    call = turn == 1
    if incorrect == "no_tool":
        call = False
    elif incorrect == "wrong_ack":
        content = "It appears connected"
    elif incorrect == "extra_tool":
        call = True
    if protocol == "openai-responses":
        output = [{"type": "function_call", "call_id": tool["id"], "name": tool["name"],
                   "arguments": json.dumps(tool["arguments"])}] if call else [{
                       "type": "message", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": content}]}]
        if turn == 1 and call:
            output.insert(0, {"type": "reasoning", "id": "reasoning-probe", "summary": [],
                              "encrypted_content": "opaque-reasoning-state"})
        return {"id": f"response-{turn}", "status": "completed", "output": output,
                "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}}
    if protocol == "openai":
        message = {"role": "assistant", "content": content, "reasoning_content": "reasoning-state"}
        if call:
            message["tool_calls"] = [{"id": tool["id"], "type": "function", "function": {
                "name": tool["name"], "arguments": json.dumps(tool["arguments"])}}]
        return {"id": f"response-{turn}", "choices": [{"message": message,
            "finish_reason": "tool_calls" if call else "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}
    blocks = [{"type": "thinking", "thinking": "reasoning-state", "signature": "opaque-signature"},
              {"type": "tool_use", "id": tool["id"], "name": tool["name"], "input": tool["arguments"]}] if call else [{
                  "type": "text", "text": content}]
    return {"id": f"response-{turn}", "type": "message", "role": "assistant", "content": blocks,
            "stop_reason": "tool_use" if call else "end_turn",
            "usage": {"input_tokens": 20, "output_tokens": 10}}


@pytest.mark.parametrize("protocol", ["openai", "openai-responses", "anthropic"])
def test_probe_accepts_auto_only_tools_and_replays_provider_state(monkeypatch, protocol):
    calls, sends = [], []
    monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: url)
    original = llm.send

    async def sent(connection, credentials, request, **kwargs):
        sends.append((kwargs["client"], request.session_id, kwargs["timeout"]))
        return await original(connection, credentials, request, **kwargs)

    async def handler(wire):
        if wire.method == "GET":
            return httpx.Response(200, json={"id": "exact-model", "max_tokens": 128000})
        body = json.loads(wire.content)
        assert body["model"] == "exact-model"
        assert body["tool_choice"] == ({"type": "auto"} if protocol == "anthropic" else "auto")
        assert wire.headers["x-opencode-session"] == sends[-1][1]
        calls.append(body)
        if len(calls) == 2:
            if protocol == "openai-responses":
                assert body["input"][1:3] == probe_payload(protocol, 1)["output"]
                assert any(row.get("type") == "function_call_output" and row["output"] == '{"result":"MORTIS_PROBE_OK"}' for row in body["input"])
            elif protocol == "openai":
                assert body["messages"][1]["reasoning_content"] == "reasoning-state"
                assert body["messages"][2]["tool_call_id"] == "call-probe"
            else:
                assert body["messages"][1]["content"] == probe_payload(protocol, 1)["content"]
                assert body["messages"][2]["content"][0]["tool_use_id"] == "call-probe"
        return httpx.Response(200, json=probe_payload(protocol, len(calls)))

    monkeypatch.setattr(llm, "send", sent)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            first, second = await llm.check_connection(configuration(protocol), {"api_key": "synthetic-key"}, client=client)
            assert first.usage.total_tokens == second.usage.total_tokens == 30
            assert second.content == "MORTIS_PROBE_OK"

    asyncio.run(run())
    assert len(calls) == 2
    assert sends[0][0] is sends[1][0]
    assert sends[0][1] == sends[1][1]
    assert 0 < sends[1][2] <= sends[0][2] <= 120


@pytest.mark.parametrize("incorrect,expected_calls", [("no_tool", 1), ("wrong_ack", 2), ("extra_tool", 2)])
def test_probe_rejects_reachability_text_and_unconfirmed_tool_result(monkeypatch, incorrect, expected_calls):
    calls = []
    monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: url)

    async def handler(wire):
        if wire.method == "GET":
            return httpx.Response(200, json={"id": "exact-model", "max_tokens": 128000})
        calls.append(wire)
        turn = len(calls)
        wrong = incorrect if turn == (1 if incorrect == "no_tool" else 2) else None
        return httpx.Response(200, json=probe_payload("openai-responses", turn, incorrect=wrong))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(llm_http.InferenceError) as caught:
                await llm.check_connection(configuration(), {}, client=client)
            assert caught.value.code == "tool_round_trip"
            assert caught.value.usage.total_tokens == 30

    asyncio.run(run())
    assert len(calls) == expected_calls


def test_probe_roundtrips_share_one_wall_deadline(monkeypatch):
    timestamp, seen = [100.0], []
    monkeypatch.setattr(llm, "time", SimpleNamespace(monotonic=lambda: timestamp[0]))

    async def sent(connection, credentials, request, **kwargs):
        seen.append(kwargs["timeout"])
        timestamp[0] += 80
        if len(seen) == 1:
            return Response(tool_calls=[ToolCall(id="probe-call", name="mortis_probe", arguments={"value": 17})],
                            finish_reason="tool_calls", usage=Usage(total_tokens=12))
        return Response(content="MORTIS_PROBE_OK", usage=Usage(total_tokens=12))

    monkeypatch.setattr(llm, "send", sent)
    asyncio.run(llm.check_connection(configuration(), {}, client=object()))
    assert seen == [120, 40]
