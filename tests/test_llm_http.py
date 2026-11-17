"""Offline protocol contracts; no provider credentials or real network calls."""

import asyncio
import json
import time

import httpx
import pytest
from pydantic import ValidationError

from mortis_engine import gateway as llm
from mortis_engine import llm_http
from mortis_engine.schemas import Message, Request, Tool, ToolCall

PROTOCOLS = ["openai", "openai-responses", "anthropic"]
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
          "required": ["ok"], "additionalProperties": False}


def connection(protocol="openai", **options):
    return {"kind": "api", "model": "exact-custom-model", "protocol": protocol,
            "base_url": "https://provider.example/v1", "options": options}


def request(**kwargs):
    return Request(**{"messages": [Message(role="user", content="Check the input")],
                      "max_output_tokens": 256, **kwargs})


def result(protocol, *, content='{"ok":true}', call=None, stop=None, usage=True):
    if protocol == "openai":
        message = {"role": "assistant", "content": content}
        if call:
            message["tool_calls"] = [{"id": call.id, "type": "function", "function": {
                "name": call.name, "arguments": json.dumps(call.arguments)}}]
        data = {"id": "request-fixture", "choices": [{"message": message,
                "finish_reason": stop or ("tool_calls" if call else "stop")} ]}
        tokens = {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12}
    elif protocol == "openai-responses":
        data = {"id": "request-fixture", "status": stop or "completed", "output": [
            {"type": "function_call", "call_id": call.id, "name": call.name,
             "arguments": json.dumps(call.arguments)} if call else {
                "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": content}]}]}
        tokens = {"input_tokens": 9, "output_tokens": 3, "total_tokens": 12}
    else:
        data = {"id": "request-fixture", "type": "message", "role": "assistant",
                "stop_reason": stop or ("tool_use" if call else "end_turn"), "content": [
                    {"type": "tool_use", "id": call.id, "name": call.name,
                     "input": call.arguments} if call else {"type": "text", "text": content}]}
        tokens = {"input_tokens": 9, "output_tokens": 3}
    if usage:
        data["usage"] = tokens
    return data


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_structured_response_and_exact_wire_model(protocol):
    task = request(output_schema=SCHEMA, instructions="trusted instructions", effort="high")
    body = llm_http.encode_request(protocol, connection(protocol), task)
    assert body["model"] == "exact-custom-model"
    assert body["stream"] is False
    assert body[{"openai": "max_tokens", "openai-responses": "max_output_tokens",
                 "anthropic": "max_tokens"}[protocol]] == 256
    assert ("prompt_cache_key" in body) is (protocol == "openai-responses")
    response = llm_http.decode_response(protocol, result(protocol), task)
    assert response.output == {"ok": True}
    assert response.usage.total_tokens == 12
    assert response.request_id == "request-fixture"
    assert response.finish_reason == "complete"


@pytest.mark.parametrize("session_id", ["stable-session", "a" * 200])
def test_responses_cache_affinity_survives_tool_rounds_and_separates_sessions(session_id):
    saved = connection("openai-responses")
    task = request(session_id=session_id, tools=[Tool(name="read_evidence", parameters=SCHEMA)])
    first = llm_http.encode_request("openai-responses", saved, task)
    call = ToolCall(id="call-1", name="read_evidence", arguments={"ok": True})
    response = llm_http.decode_response("openai-responses", result("openai-responses", call=call), task)
    followup = task.model_copy(update={"messages": [*task.messages,
        Message(role="assistant", content=response.content, tool_calls=response.tool_calls,
                provider_data=response.provider_data),
        Message(role="tool", tool_call_id=call.id, content='{"data":"bounded"}')]})
    assert len(first["prompt_cache_key"]) == 64
    assert llm_http.encode_request("openai-responses", saved, followup)["prompt_cache_key"] == first["prompt_cache_key"]
    other = task.model_copy(update={"session_id": session_id[:-1] + "b"})
    assert llm_http.encode_request("openai-responses", saved, other)["prompt_cache_key"] != first["prompt_cache_key"]
    assert llm.reservation(saved, task) == llm.reservation(saved, other)
    encoded_size = len(json.dumps(first, ensure_ascii=False, separators=(",", ":")).encode())
    assert llm.reservation(saved, task) == encoded_size + 1024 + 64 * (len(task.messages) + len(task.tools))


@pytest.mark.parametrize("cache_key", ["explicit-operator-affinity", None])
def test_responses_cache_affinity_preserves_explicit_override(cache_key):
    saved = connection("openai-responses", extra_body={"prompt_cache_key": cache_key})
    assert llm_http.encode_request("openai-responses", saved, request())["prompt_cache_key"] == cache_key


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_tool_round_trip_and_validation(protocol):
    tool = Tool(name="read_evidence", description="Read bounded evidence", parameters=SCHEMA)
    task = request(tools=[tool], tool_choice=tool.name)
    call = ToolCall(id="call-1", name=tool.name, arguments={"ok": True})
    response = llm_http.decode_response(protocol, result(protocol, content=None, call=call), task)
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls == [call]
    followup = task.model_copy(update={"messages": [*task.messages,
        Message(role="assistant", tool_calls=response.tool_calls),
        Message(role="tool", tool_call_id=call.id, content='{"data":"bounded"}') ]})
    body = llm_http.encode_request(protocol, connection(protocol), followup)
    assert "call-1" in json.dumps(body)
    assert "bounded" in json.dumps(body)
    invalid = call.model_copy(update={"arguments": {"ok": "incorrect"}})
    with pytest.raises(llm_http.InferenceError, match="malformed") as caught:
        llm_http.decode_response(protocol, result(protocol, call=invalid), task)
    assert caught.value.usage.total_tokens == 12
    unknown = call.model_copy(update={"name": "execute_shell"})
    with pytest.raises(llm_http.InferenceError):
        llm_http.decode_response(protocol, result(protocol, call=unknown), task)


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("content", ['{"ok":"wrong type"}', '{"ok":true', '{"ok":true,"ok":false}', '{"ok":NaN}'])
def test_malformed_structured_output_retains_usage(protocol, content):
    with pytest.raises(llm_http.InferenceError) as caught:
        llm_http.decode_response(protocol, result(protocol, content=content), request(output_schema=SCHEMA))
    assert caught.value.code == "malformed_response"
    assert caught.value.usage.total_tokens == 12
    assert not caught.value.consumption_unknown


@pytest.mark.parametrize("protocol,stop,code", [("openai", "length", "output_limit"),
    ("openai-responses", "incomplete", "incomplete"), ("anthropic", "max_tokens", "output_limit"),
    ("anthropic", "pause_turn", "incomplete")])
def test_incomplete_output_is_never_accepted(protocol, stop, code):
    with pytest.raises(llm_http.InferenceError) as caught:
        llm_http.decode_response(protocol, result(protocol, stop=stop), request())
    assert caught.value.code == code
    assert caught.value.usage.total_tokens == 12


@pytest.mark.parametrize("reason,code", [("max_output_tokens", "output_limit"), ("content_filter", "incomplete"),
                                        ("PRIVATE_PROVIDER_TEXT", "incomplete")])
def test_responses_incomplete_reason_is_allowlisted(reason, code):
    payload = result("openai-responses", stop="incomplete")
    payload["incomplete_details"] = {"reason": reason}
    with pytest.raises(llm_http.InferenceError) as caught:
        llm_http.decode_response("openai-responses", payload, request())
    assert caught.value.code == code and caught.value.usage.total_tokens == 12
    assert "PRIVATE_PROVIDER_TEXT" not in str(caught.value)


def test_truncated_tool_arguments_keep_output_limit_and_usage_diagnostics():
    payload = result("openai", stop="length")
    payload["choices"][0]["message"]["tool_calls"] = [{"function": {"arguments": '{"path":'}}]
    with pytest.raises(llm_http.InferenceError) as caught:
        llm_http.decode_response("openai", payload, request())
    assert caught.value.code == "output_limit"
    assert caught.value.usage.total_tokens == 12 and caught.value.request_id == "request-fixture"


def test_chat_null_tool_calls_preserves_structured_output_and_usage():
    payload = result("openai")
    payload["choices"][0]["message"]["tool_calls"] = None
    response = llm_http.decode_response("openai", payload, request(output_schema=SCHEMA))
    assert response.tool_calls == [] and response.output == {"ok": True}
    assert response.usage.total_tokens == 12 and response.request_id == "request-fixture"


@pytest.mark.parametrize("tool_calls", [False, 0, "", {}, [None], [{"type": "function"}]])
def test_chat_non_null_malformed_tool_calls_are_rejected(tool_calls):
    payload = result("openai")
    payload["choices"][0]["message"]["tool_calls"] = tool_calls
    with pytest.raises(llm_http.InferenceError) as caught:
        llm_http.decode_response("openai", payload, request())
    assert caught.value.code == "malformed_response" and caught.value.usage.total_tokens == 12


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_refusal_and_missing_usage(protocol):
    payload = result(protocol, usage=False)
    response = llm_http.decode_response(protocol, payload, request())
    assert response.usage.total_tokens is None
    if protocol == "openai":
        payload["choices"][0]["message"]["refusal"] = "Policy refusal"
    elif protocol == "openai-responses":
        payload["output"][0]["content"] = [{"type": "refusal", "refusal": "Policy refusal"}]
    else:
        payload["stop_reason"] = "refusal"
    with pytest.raises(llm_http.InferenceError) as caught:
        llm_http.decode_response(protocol, payload, request())
    assert caught.value.code == "refusal"


def test_unknown_usage_is_conservative_and_cache_tokens_are_accounted():
    payload = result("anthropic")
    payload["usage"].update(cache_read_input_tokens=20, cache_creation_input_tokens=10)
    response = llm_http.decode_response("anthropic", payload, request())
    assert response.usage.total_tokens == 42
    assert response.usage.cache_read_tokens == 20
    payload = result("openai")
    payload["usage"]["prompt_tokens"] = True
    assert llm_http.decode_response("openai", payload, request()).usage.total_tokens is None
    payload = result("openai")
    payload["usage"]["total_tokens"] = 1
    assert llm_http.decode_response("openai", payload, request()).usage.total_tokens is None


@pytest.mark.parametrize("key", ["model", "messages.0.content", "tools.0.function.parameters",
    "max_tokens", "max_completion_tokens", "max_output_tokens", "response_format.json_schema",
    "instructions", "system", "output_config.format", "stream", "api_key", "background"])
def test_extra_body_cannot_override_protected_inputs(key):
    with pytest.raises(ValueError, match="protected"):
        llm_http.encode_request("openai", connection(extra_body={key: "unsafe"}), request())


def test_dotted_options_do_not_mutate_connection():
    saved = connection(extra_body={"thinking.type": "enabled", "temperature": 0.2})
    encoded = llm_http.encode_request("openai", saved, request())
    assert encoded["thinking"] == {"type": "enabled"}
    assert saved["options"]["extra_body"] == {"thinking.type": "enabled", "temperature": 0.2}


@pytest.mark.parametrize("base_url,key", [("https://api.openai.com/v1", "max_completion_tokens"),
    ("https://api.deepseek.com", "max_tokens"), ("https://opencode.ai/zen/go/v1", "max_tokens"),
    ("https://custom.example/v1", "max_tokens")])
def test_chat_budget_alias_is_deterministic(base_url, key):
    body = llm_http.encode_request("openai", {**connection(), "base_url": base_url}, request())
    assert body[key] == 256
    assert len({"max_tokens", "max_completion_tokens"} & set(body)) == 1
    other = "max_completion_tokens" if key == "max_tokens" else "max_tokens"
    body = llm_http.encode_request("openai", {**connection(extra_body={other: 256}), "base_url": base_url}, request())
    assert body[other] == 256 and key not in body


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_unlimited_output_omits_wire_cap_and_cannot_be_overridden(protocol):
    task = request(max_output_tokens=0)
    body = llm_http.encode_request(protocol, connection(protocol), task)
    assert not {"max_tokens", "max_completion_tokens", "max_output_tokens"} & body.keys()
    key = "max_tokens" if protocol == "openai" else "max_output_tokens"
    with pytest.raises(ValueError, match="protected"):
        llm_http.encode_request(protocol, connection(protocol, extra_body={key: 16384}), task)




@pytest.mark.parametrize("protocol,key", [("openai", "tools.0.function.strict"),
                                           ("openai-responses", "tools.0.strict"),
                                           ("anthropic", "tools.0.strict")])
def test_existing_tool_strict_options_and_reasoning_defaults(protocol, key):
    task = request(tools=[Tool(name="evidence", parameters=SCHEMA)])
    saved = connection(protocol, extra_body={key: False, "reasoning.effort": "high", "service_tier": "flex"})
    body = llm_http.encode_request(protocol, saved, task)
    tool = body["tools"][0]
    assert (tool["function"] if protocol == "openai" else tool)["strict"] is False
    assert body["reasoning"]["effort"] == "high"
    assert body["service_tier"] == "flex"
    assert saved["options"]["extra_body"][key] is False
    if protocol == "openai-responses":
        with pytest.raises(ValueError, match="explicitly selected"):
            llm_http.encode_request(protocol, saved, request(tools=task.tools, effort="low"))


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("key", ["tools.0.strict", "tools.0.function.strict"])
def test_tool_strict_options_cannot_create_missing_tools(protocol, key):
    with pytest.raises(ValueError):
        llm_http.encode_request(protocol, connection(protocol, extra_body={key: False}), request())


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("key,value", [("tools.1.strict", False), ("tools.0.strict", 0)])
def test_tool_strict_options_require_existing_index_and_boolean(protocol, key, value):
    task = request(tools=[Tool(name="evidence", parameters=SCHEMA)])
    with pytest.raises(ValueError):
        llm_http.encode_request(protocol, connection(protocol, extra_body={key: value}), task)


def test_anthropic_effort_default_and_thinking_budget():
    body = llm_http.encode_request("anthropic", connection("anthropic", extra_body={
        "output_config": {"effort": "high"}, "thinking": {"type": "enabled", "budget_tokens": 128}}), request())
    assert body["output_config"]["effort"] == "high"
    with pytest.raises(ValueError, match="output budget"):
        llm_http.encode_request("anthropic", connection("anthropic", extra_body={
            "thinking.budget_tokens": 256}), request())


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_thinking_and_reasoning_state_survives_tool_roundtrip(protocol):
    task = request(tools=[Tool(name="evidence", parameters=SCHEMA)])
    call = ToolCall(id="call-1", name="evidence", arguments={"ok": True})
    payload = result(protocol, content="Check evidence", call=call)
    if protocol == "openai":
        payload["choices"][0]["message"]["reasoning_content"] = "provider reasoning"
    elif protocol == "openai-responses":
        payload["output"].insert(0, {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque-signature"})
    else:
        payload["content"].insert(0, {"type": "thinking", "thinking": "provider reasoning", "signature": "opaque-signature"})
        payload["content"].append({"type": "redacted_thinking", "data": "redacted-opaque"})
    response = llm_http.decode_response(protocol, payload, task)
    assistant = Message(role="assistant", content=response.content, tool_calls=response.tool_calls,
                        provider_data=response.provider_data)
    followup = Request(messages=[*task.messages, assistant,
        Message(role="tool", tool_call_id=call.id, content="bounded evidence")], tools=task.tools, max_output_tokens=256)
    body = llm_http.encode_request(protocol, connection(protocol), followup)
    if protocol == "openai":
        assert body["messages"][1]["reasoning_content"] == "provider reasoning"
    elif protocol == "openai-responses":
        assert body["include"] == ["reasoning.encrypted_content"]
        assert body["input"][1:3] == payload["output"]
    else:
        assert body["messages"][1]["content"] == payload["content"]
    if protocol != "openai":
        forged = assistant.model_copy(update={"content": "different content"})
        with pytest.raises(ValueError, match="match the validated"):
            llm_http.encode_request(protocol, connection(protocol), followup.model_copy(update={
                "messages": [task.messages[0], forged, followup.messages[-1]]}))


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_forced_tools_cannot_return_plain_text(protocol):
    task = request(tools=[Tool(name="evidence", parameters=SCHEMA)], tool_choice="required")
    with pytest.raises(llm_http.InferenceError, match="malformed"):
        llm_http.decode_response(protocol, result(protocol), task)


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_large_workflow_allowance_is_not_capped_by_transport(protocol):
    task = request(max_output_tokens=5_000_000)
    body = llm_http.encode_request(protocol, connection(protocol), task)
    key = "max_output_tokens" if protocol == "openai-responses" else "max_tokens"
    assert body[key] == 5_000_000


def test_invalid_schema_and_tool_history_rejected():
    with pytest.raises(ValueError, match="local references"):
        llm_http.encode_request("openai", connection(), request(output_schema={"$ref": "https://other/schema"}))
    with pytest.raises(ValidationError, match="pending tool call"):
        Request(messages=[Message(role="tool", tool_call_id="unknown", content="data")], max_output_tokens=10)
    with pytest.raises(ValidationError):
        request(max_output_tokens=True)


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_send_custom_auth_options_and_headers(monkeypatch, protocol):
    monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: url)
    observed = []
    async def handler(wire):
        if wire.method == "GET":
            return httpx.Response(200, json={"id": "exact-custom-model", "max_tokens": 128000})
        observed.append(wire)
        return httpx.Response(200, json=result(protocol), headers={"x-request-id": "wire-request"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            response = await llm_http.send(connection(protocol, auth_header="X-Custom-Key", extra_body={"temperature": 0}),
                {"api_key": "secret", "extra_headers": {"X-Account": "account"}}, request(), client=client)
            assert response.request_id == "wire-request"
    asyncio.run(run())
    wire, = observed
    assert wire.headers["x-custom-key"] == "secret"
    assert wire.headers["x-account"] == "account"
    assert json.loads(wire.content)["temperature"] == 0
    assert wire.url.path == "/v1" + {"openai": "/chat/completions", "openai-responses": "/responses", "anthropic": "/messages"}[protocol]


def test_go_session_is_stable_and_cannot_be_overridden(monkeypatch):
    monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: url)
    seen = []
    async def handler(wire):
        seen.append(wire.headers)
        return httpx.Response(200, json=result("openai"))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            config = {**connection(), "base_url": "https://opencode.ai/zen/go/v1"}
            task = request(session_id="stable-session")
            for _ in range(2):
                await llm_http.send(config, {"api_key": "key", "extra_headers": {
                    "X-OpenCode-Session": "forged", "user-agent": "forged"}}, task, client=client)
    asyncio.run(run())
    assert all(headers["x-opencode-session"] == "stable-session" for headers in seen)
    assert all(headers["user-agent"] == "code-mortis/0.1.0" for headers in seen)


@pytest.mark.parametrize("status,code,unknown,retry", [(401, "authentication", False, False),
    (403, "authentication", False, False), (429, "rate_limit", False, True),
    (500, "provider_http_500", True, False), (503, "provider_http_503", True, False),
    (307, "redirect", False, False)])
def test_errors_are_bounded_sanitized_and_never_retried(status, code, unknown, retry):
    calls = []
    async def handler(wire):
        calls.append(wire)
        return httpx.Response(status, text="leaked-secret", headers={"location": "https://other.example"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            with pytest.raises(llm_http.InferenceError) as caught:
                await llm_http.post_json(client, "https://provider.example/v1", {}, {}, timeout=1)
            error = caught.value
            assert error.code == code
            assert error.consumption_unknown is unknown
            assert error.retryable is retry
            assert "leaked-secret" not in str(error)
    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize("payload,headers,expected", [(b'data: {"partial":true}\n', {"content-type": "text/event-stream"}, "partial_stream"),
    (b'{"partial":', {}, "malformed_response"), (b'{"a":1,"a":2}', {}, "malformed_response"),
    (b'[]', {}, "malformed_response")])
def test_partial_streams_and_malformed_http(payload, headers, expected):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda wire: httpx.Response(200, content=payload, headers=headers))) as client:
            with pytest.raises(llm_http.InferenceError) as caught:
                await llm_http.post_json(client, "https://provider.example/v1", {}, {}, timeout=1)
            assert caught.value.code == expected
    asyncio.run(run())


def test_cancel_and_deadline_interrupt_pending_io():
    cancelled = []
    async def handler(wire):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
    async def run(timeout, stop):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(llm_http.InferenceError) as caught:
                await llm_http.post_json(client, "https://provider.example/v1", {}, {}, timeout=timeout, stopped=stop)
            return caught.value
    start = time.monotonic()
    error = asyncio.run(run(0.04, lambda: False))
    assert error.code == "timeout" and error.consumption_unknown
    assert time.monotonic() - start < 1
    start = time.monotonic()
    error = asyncio.run(run(10, lambda: time.monotonic() - start > 0.02))
    assert error.code == "cancelled" and error.consumption_unknown
    assert len(cancelled) == 2
    error = asyncio.run(run(10, lambda: True))
    assert error.code == "cancelled" and not error.consumption_unknown


def test_bounded_response_and_exact_signed_bytes(monkeypatch):
    monkeypatch.setattr(llm_http, "MAX_RESPONSE_BYTES", 10)
    body = b'{"signed": "exact whitespace"}'
    async def handler(wire):
        assert wire.content == body
        return httpx.Response(200, content=b"x" * 11)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(llm_http.InferenceError) as caught:
                await llm_http.post_json(client, "https://provider.example/v1", {}, body)
            assert caught.value.code == "response_limit"
    asyncio.run(run())


@pytest.mark.parametrize("status,body,configured,code,retry", [
    (429, {"error": {"code": "insufficient_quota"}}, None, "quota", False),
    (429, {"error": {"type": "rate_limit_error"}}, [], "rate_limit", False),
    (400, {"error": {}}, [400], "provider_error", True),
    (500, {"error": {}}, [500], "provider_http_500", False),
])
def test_quota_and_configured_retry_codes(status, body, configured, code, retry):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda wire: httpx.Response(
            status, json=body, headers={"retry-after": "2"}))) as client:
            with pytest.raises(llm_http.InferenceError) as caught:
                await llm_http.post_json(client, "https://provider.example/v1", {}, {}, retry_codes=configured)
            assert caught.value.code == code
            assert caught.value.retryable is retry
            assert caught.value.retry_after == 2
    asyncio.run(run())


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("options,status,provider_code,code,retry,unknown", [
    ({}, 429, "rate_limit_error", "rate_limit", True, False),
    ({"retry_codes": None}, 429, "rate_limit_error", "rate_limit", True, False),
    ({"retry_codes": []}, 429, "rate_limit_error", "rate_limit", False, False),
    ({"retry_codes": [429]}, 429, "rate_limit_error", "rate_limit", True, False),
    ({"retry_codes": [400]}, 429, "rate_limit_error", "rate_limit", False, False),
    ({"retry_codes": None}, 429, "insufficient_quota", "quota", False, False),
    ({"retry_codes": [429]}, 429, "insufficient_quota", "quota", False, False),
    ({"retry_codes": [401]}, 401, "invalid_api_key", "authentication", False, False),
    ({"retry_codes": [403]}, 403, "permission_denied", "authentication", False, False),
    ({"retry_codes": [400]}, 400, "bad_request", "provider_error", True, False),
    ({"retry_codes": [503]}, 503, "unavailable", "provider_http_503", False, True),
])
def test_send_preserves_transport_retry_policy(monkeypatch, protocol, options, status, provider_code, code, retry, unknown):
    monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: url)
    calls = []
    async def handler(wire):
        if wire.method == "GET":
            return httpx.Response(200, json={"id": "exact-custom-model", "max_tokens": 128000})
        calls.append(wire)
        return httpx.Response(status, json={"error": {"code": provider_code}},
                              headers={"retry-after": "2", "x-request-id": "retry-fixture"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            with pytest.raises(llm_http.InferenceError) as caught:
                await llm_http.send(connection(protocol, **options), {}, request(), client=client)
            error = caught.value
            assert error.code == code and error.status_code == status
            assert error.retryable is retry and error.consumption_unknown is unknown
            assert error.retry_after == 2 and error.request_id == "retry-fixture"
    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize("value,expected", [("nan", None), ("inf", None), ("-1", 0),
                                           ("100000000000", 900), ("nonsense", None)])
def test_retry_after_is_bounded(value, expected):
    assert llm_http._retry_after(value) == expected


@pytest.mark.parametrize("identity", ["x" * 251, "contains\nnewline", "contains\ttab", "", None])
def test_request_identity_always_fits_the_durable_ledger(identity):
    payload = result("openai")
    payload["id"] = identity
    assert llm_http.decode_response("openai", payload, request()).request_id is None


def test_endpoint_and_routing_validation_precede_dispatch(monkeypatch):
    called = []
    async def handler(wire):
        called.append(wire)
        return httpx.Response(200, json=result("openai"))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: (_ for _ in ()).throw(ValueError("protected endpoint")))
            with pytest.raises(ValueError, match="protected endpoint"):
                await llm_http.send(connection(), {"api_key": "secret"}, request(), client=client)
            monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: url)
            with pytest.raises(ValueError, match="routing"):
                await llm_http.send(connection(), {"api_key": "secret", "extra_headers": {
                    "Host": "attacker.example"}}, request(), client=client)
    asyncio.run(run())
    assert not called


def test_provider_set_cookie_cannot_become_implicit_credentials():
    seen = []

    async def handler(wire):
        seen.append(wire.headers.get("cookie"))
        return httpx.Response(200, json={"ok": True}, headers={"set-cookie": "provider-session=private; Path=/"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), cookies={"preexisting": "private"}) as client:
            for headers in ({}, {}, {"Cookie": "explicit-admin-configured=value"}):
                await llm_http.post_json(client, "https://provider.example/v1", headers, {})

    asyncio.run(run())
    assert seen == [None, None, "explicit-admin-configured=value"]


def test_unexpected_success_stream_is_closed_without_reading():
    closed = []

    class UnfinishedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("Unexpected event stream was consumed")
            yield b"unreachable"

        async def aclose(self):
            closed.append(True)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda wire: httpx.Response(
            200, stream=UnfinishedStream(), headers={"content-type": "text/event-stream"}))) as client:
            with pytest.raises(llm_http.InferenceError) as caught:
                await llm_http.post_json(client, "https://provider.example/v1", {}, {})
            assert caught.value.code == "partial_stream"

    asyncio.run(run())
    assert closed == [True]


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1, 0])
def test_invalid_request_timeout_never_dispatches(monkeypatch, timeout):
    called = []
    monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: called.append(url) or url)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda wire: pytest.fail("HTTP dispatched"))) as client:
            with pytest.raises(llm_http.InferenceError) as caught:
                await llm_http.send(connection(), {}, request(), client=client, timeout=timeout)
            assert caught.value.code == "timeout" and not caught.value.consumption_unknown

    asyncio.run(run())
    assert not called


def test_dns_preflight_timeout_is_known_not_consumed(monkeypatch):
    def blocked_lookup(url):
        time.sleep(0.03)
        return url

    monkeypatch.setattr(llm_http, "safe_endpoint", blocked_lookup)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda wire: pytest.fail("HTTP dispatched"))) as client:
            with pytest.raises(llm_http.InferenceError) as caught:
                await llm_http.send(connection(), {}, request(), client=client, timeout=0.005)
            assert caught.value.code == "timeout" and not caught.value.consumption_unknown

    asyncio.run(run())


@pytest.mark.parametrize("allowance,expected", [(0, 128000), (5000, 5000), (5000000, 5000000)])
def test_anthropic_discovers_capacity_only_without_an_explicit_limit(monkeypatch, allowance, expected):
    monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: url)
    seen = []

    def handler(wire):
        seen.append(wire)
        assert wire.headers["x-api-key"] == "selected-key"
        assert "cookie" not in wire.headers
        assert all(value is None for value in wire.extensions["timeout"].values())
        if wire.method == "GET":
            assert wire.url.path == "/v1/models/exact-custom-model"
            if allowance:
                return httpx.Response(404)
            return httpx.Response(200, json={"id": "resolved-selected-alias", "max_tokens": 128000})
        assert wire.url.path == "/v1/messages"
        assert json.loads(wire.content)["max_tokens"] == expected
        assert json.loads(wire.content)["model"] == "exact-custom-model"
        return httpx.Response(200, json=result("anthropic"))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), cookies={"ambient": "private"}) as client:
            response = await llm.send(connection("anthropic"), {"api_key": "selected-key"},
                                      request(max_output_tokens=allowance), client=client, timeout=None)
            assert response.usage.total_tokens == 12
    asyncio.run(run())
    assert len(seen) == (1 if allowance else 2)


@pytest.mark.parametrize("capacity", [None, 0, -1, True, "128000"])
def test_anthropic_missing_capacity_never_dispatches_or_invents_a_cap(monkeypatch, capacity):
    monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: url)
    def handler(wire):
        assert wire.method == "GET"
        return httpx.Response(200, json={"max_tokens": capacity})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(InferenceError) as error:
                await llm.send(connection("anthropic"), {}, request(max_output_tokens=0), client=client, timeout=None)
            assert error.value.code == "model_capacity" and not error.value.consumption_unknown
    from mortis_engine.llm_http import InferenceError
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["timeout", "cancelled", "redirect", "unauthorized"])
def test_anthropic_metadata_failure_has_no_inference_consumption(monkeypatch, failure):
    monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: url)
    stopped = [False]
    async def handler(wire):
        assert wire.method == "GET" and wire.url.host == "provider.example"
        if failure in {"timeout", "cancelled"}:
            stopped[0] = failure == "cancelled"
            await asyncio.sleep(1)
        return httpx.Response(302 if failure == "redirect" else 401, headers={"location": "https://other.example"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            with pytest.raises(llm_http.InferenceError) as error:
                await llm.send(connection("anthropic"), {"api_key": "private-key"}, request(max_output_tokens=0),
                               client=client, stopped=lambda: stopped[0], timeout=0.02 if failure == "timeout" else None)
            assert not error.value.consumption_unknown
            assert "private-key" not in str(error.value)
    asyncio.run(run())
