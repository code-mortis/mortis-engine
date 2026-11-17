import asyncio
import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta

import botocore.session
import httpx
import pytest
from botocore.credentials import Credentials, RefreshableCredentials
from botocore.exceptions import ProfileNotFound

from mortis_engine import llm_bedrock as bedrock
from mortis_engine.llm_http import InferenceError
from mortis_engine.schemas import Message, Request, Tool


@pytest.fixture(autouse=True)
def isolated_aws(monkeypatch, tmp_path):
    # No test may consult ambient AWS credentials, metadata, or configured credential processes.
    for name in tuple(os.environ):
        if name.startswith("AWS_"):
            monkeypatch.delenv(name)
    empty = tmp_path / "empty-aws-config"
    empty.write_text("")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(empty))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(empty))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setattr(bedrock, "safe_endpoint", lambda url: url.rstrip("/"))
    original = bedrock._session
    original.cache_clear()
    yield
    original.cache_clear()


def connection(**kwargs):
    return {"kind": "api", "protocol": "anthropic-bedrock", "provider": "bedrock", "base_url": "",
            "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0", "options": {}, **kwargs}


def request(**kwargs):
    return Request(messages=[Message(role="user", content="Check café")], **{"max_output_tokens": 64, **kwargs})


def response(**kwargs):
    blocks = kwargs.pop("content", [{"type": "text", "text": "Done"}])
    content = [{"text": block["text"]} if block["type"] == "text" else {"toolUse": {
        "toolUseId": block["id"], "name": block["name"], "input": block["input"]}} for block in blocks]
    return {"output": {"message": {"role": "assistant", "content": content}},
            "stopReason": kwargs.pop("stop_reason", "end_turn"),
            "usage": {"inputTokens": 7, "outputTokens": 2, "totalTokens": 9}, **kwargs}


def execute(handler, config=None, credentials=None, query=None, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await bedrock.send(config or connection(), credentials or {"api_key": "bedrock-key"},
                                      query or request(), client=client, **kwargs)
    return asyncio.run(run())


def test_bearer_tools_schema_options_and_selected_model(monkeypatch):
    def no_credentials(self):
        raise AssertionError("An explicit API key must not consult AWS credentials")
    monkeypatch.setattr(botocore.session.Session, "get_credentials", no_credentials)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
              "additionalProperties": False}
    tool = Tool(name="read_source", parameters={"type": "object", "properties": {}})
    def handler(req):
        assert req.url.host == "bedrock-runtime.eu-west-1.amazonaws.com"
        assert req.url.raw_path == b"/model/us.anthropic.claude-sonnet-4-5-20250929-v1%3A0/converse"
        assert req.headers["authorization"] == "Bearer bedrock-key"
        assert req.headers["x-tenant"] == "tenant-secret"
        body = json.loads(req.content)
        assert "model" not in body and "stream" not in body
        assert "anthropic_version" not in body
        assert body["inferenceConfig"]["maxTokens"] == 64
        assert body["messages"][0]["content"]
        assert body["inferenceConfig"]["temperature"] == 0.2
        assert json.loads(body["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"]) == schema
        assert body["additionalModelRequestFields"]["output_config"]["effort"] == "high"
        assert body["toolConfig"]["tools"][0]["toolSpec"]["name"] == "read_source"
        return httpx.Response(200, json=response(content=[{"type": "text", "text": '{"ok":true}'}]),
                              headers={"x-amzn-requestid": "aws-request-id"})
    result = execute(handler, connection(options={"aws_region": "eu-west-1", "extra_body": {"temperature": 0.2}}),
                     {"api_key": "bedrock-key", "extra_headers": {"x-tenant": "tenant-secret"}},
                     request(tools=[tool], output_schema=schema, effort="high"))
    assert result.output == {"ok": True}
    assert result.request_id == "aws-request-id"
    assert result.usage.input_tokens == 7 and result.usage.output_tokens == 2


def test_environment_bearer_and_custom_header(monkeypatch):
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "environment-key")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    def handler(req):
        assert req.headers["x-worker-key"] == "environment-key"
        assert "authorization" not in req.headers
        assert req.url.host == "bedrock-runtime.us-west-2.amazonaws.com"
        return httpx.Response(200, json=response())
    execute(handler, connection(options={"auth_header": "X-Worker-Key"}), {"api_key": ""})


def test_sigv4_signs_exact_bytes_with_refreshable_credentials(monkeypatch):
    monkeypatch.setattr("botocore.auth.get_current_datetime", lambda: datetime(2026, 11, 7, tzinfo=UTC))
    sdk = botocore.session.Session()
    calls = []
    class Refreshing:
        def get_frozen_credentials(self):
            calls.append(True)
            return Credentials("AKIDEXAMPLE", "signing-secret", f"token-{len(calls)}").get_frozen_credentials()
    monkeypatch.setattr(sdk, "get_credentials", lambda: Refreshing())
    monkeypatch.setattr(bedrock, "_session", lambda profile: (sdk, threading.Lock()))
    seen = []
    def handler(req):
        seen.append(req)
        assert req.headers["x-amz-date"] == "20261107T000000Z"
        assert req.headers["x-amz-security-token"] == f"token-{len(seen)}"
        assert "Credential=AKIDEXAMPLE/20261107/us-east-1/bedrock/aws4_request" in req.headers["authorization"]
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        names = req.headers["authorization"].split("SignedHeaders=", 1)[1].split(",", 1)[0].split(";")
        headers = {name: req.headers[name] for name in names if name != "host"}
        signed = AWSRequest(method="POST", url=str(req.url), data=req.content, headers=headers)
        SigV4Auth(Credentials("AKIDEXAMPLE", "signing-secret", f"token-{len(seen)}"), "bedrock", "us-east-1").add_auth(signed)
        assert signed.headers["Authorization"] == req.headers["authorization"]
        assert b"caf\xc3\xa9" in req.content
        return httpx.Response(200, json=response())
    execute(handler, credentials={"api_key": ""})
    execute(handler, credentials={"api_key": ""})
    assert len(calls) == 2


def test_signed_arn_request_matches_sdk_without_network(monkeypatch):
    monkeypatch.setattr("botocore.auth.get_current_datetime", lambda: datetime(2026, 11, 7, tzinfo=UTC))
    sdk = botocore.session.Session()
    sdk.set_credentials("AKIDEXAMPLE", "signing-secret", "token")
    monkeypatch.setattr(bedrock, "_session", lambda profile: (sdk, threading.Lock()))
    model = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.example:0"
    captured = []
    class Captured(Exception):
        pass
    def before_send(request, **kwargs):
        captured.append(request)
        raise Captured()
    def handler(req):
        client = sdk.create_client("bedrock-runtime", region_name="us-east-1")
        try:
            def same_bytes(request, **kwargs):
                request.data = req.content
                request.headers.add_header("Accept", "application/json")
            client.meta.events.register("before-sign.bedrock-runtime.Converse", same_bytes)
            client.meta.events.register("before-send.bedrock-runtime.Converse", before_send)
            with pytest.raises(Captured):
                client.converse(modelId=model, **json.loads(req.content))
        finally:
            client.close()
        assert captured[0].url == str(req.url)
        assert captured[0].headers["Authorization"].decode() == req.headers["authorization"]
        return httpx.Response(200, json=response())
    execute(handler, connection(model=model), {"api_key": ""})


@pytest.mark.parametrize(("region", "hostname"), [("cn-north-1", "bedrock-runtime.cn-north-1.amazonaws.com.cn"),
                                                     ("us-gov-west-1", "bedrock-runtime.us-gov-west-1.amazonaws.com")])
def test_partition_endpoints_and_arn_escaping(region, hostname):
    model = f"arn:aws:bedrock:{region}:123456789012:inference-profile/us.anthropic.example:0"
    def handler(req):
        assert req.url.host == hostname
        assert b"%2Finference" not in req.url.raw_path
        assert b"inference-profile%2Fus.anthropic.example%3A0/converse" in req.url.raw_path
        assert req.url.path.endswith("/converse")
        return httpx.Response(200, json=response())
    execute(handler, connection(model=model, options={"aws_region": region}))


def test_missing_credentials_profile_error_and_endpoint_gate(monkeypatch):
    def fail_dispatch(req):
        raise AssertionError("Invalid credentials must never dispatch")
    monkeypatch.setattr(botocore.session.Session, "get_credentials", lambda self: None)
    with pytest.raises(InferenceError) as missing:
        execute(fail_dispatch, credentials={"api_key": ""})
    assert missing.value.code == "authentication" and not missing.value.consumption_unknown
    with pytest.raises(InferenceError) as profile:
        execute(fail_dispatch, connection(options={"aws_profile": "does-not-exist"}), {"api_key": ""})
    assert profile.value.code == "authentication" and isinstance(profile.value.__cause__, ProfileNotFound)
    def denied(url):
        raise ValueError("protected network")
    monkeypatch.setattr(bedrock, "safe_endpoint", denied)
    with pytest.raises(InferenceError) as blocked:
        execute(fail_dispatch)
    assert not blocked.value.consumption_unknown


@pytest.mark.parametrize("headers", [{"Host": "evil.example"}, {"Content-Length": "0"}, {"Transfer-Encoding": "chunked"},
                                     {"Authorization": "secret"}, {"X-Amz-Date": "bad"}, {"X-Amz-Security-Token": "bad"}, {"Connection": "close"},
                                     {"Proxy-Authorization": "proxy-secret"}, {"X-Extra": "value\nInjected: bad"}])
def test_signing_and_framing_headers_are_protected(headers):
    with pytest.raises(InferenceError) as failure:
        execute(lambda req: pytest.fail("Unsafe request dispatched"), credentials={"api_key": "", "extra_headers": headers})
    assert not failure.value.consumption_unknown


@pytest.mark.parametrize(("status", "payload"), [(401, {"message": "private-provider-diagnostic"}),
                                                     (403, {"message": "private-provider-diagnostic"}),
                                                     (429, {"message": "private-provider-diagnostic"}),
                                                     (503, {"message": "private-provider-diagnostic"})])
def test_provider_failures_do_not_retry_or_leak(status, payload):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, json=payload, headers={"x-amzn-requestid": "failure-id"})
    with pytest.raises(InferenceError) as failure:
        execute(handler)
    assert failure.value.status_code == status and len(calls) == 1
    assert failure.value.request_id == "failure-id"
    assert "private-provider-diagnostic" not in str(failure.value)


@pytest.mark.parametrize("payload", [response(stop_reason="refusal"), response(stop_reason="max_tokens"),
                                    response(content=[]), {"unexpected": "not a message"}])
def test_refusal_truncation_and_malformed_responses_fail(payload):
    with pytest.raises(InferenceError):
        execute(lambda req: httpx.Response(200, json=payload))


def test_known_usage_and_request_identity_survive_refusal():
    with pytest.raises(InferenceError) as failure:
        execute(lambda req: httpx.Response(200, json=response(stop_reason="refusal"),
                                          headers={"x-amzn-requestid": "aws-refusal-id"}))
    assert failure.value.code == "refusal"
    assert failure.value.usage.input_tokens == 7 and failure.value.usage.output_tokens == 2
    assert failure.value.request_id == "aws-refusal-id"


def test_session_cache_retains_sdk_refreshable_credentials(monkeypatch):
    first, lock = bedrock._session(None)
    assert bedrock._session(None) == (first, lock)
    assert bedrock._session("other-profile")[0] is not first
    observed = []
    def refresh():
        observed.append(True)
        return {"access_key": "AKIDEXAMPLE", "secret_key": "signing-secret", "token": "renewed-token",
                "expiry_time": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
    available = RefreshableCredentials.create_from_metadata(
        metadata={"access_key": "AKIDEXAMPLE", "secret_key": "signing-secret", "token": "expired-token",
                  "expiry_time": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
        refresh_using=refresh, method="test-only")
    monkeypatch.setattr(first, "get_credentials", lambda: available)
    def handler(req):
        assert req.headers["x-amz-security-token"] == "renewed-token"
        return httpx.Response(200, json=response())
    execute(handler, credentials={"api_key": ""})
    execute(handler, credentials={"api_key": ""})
    assert len(observed) == 1


def test_missing_usage_stays_unknown():
    result = execute(lambda req: httpx.Response(200, json=response(usage={})))
    assert result.usage.input_tokens is None and result.usage.output_tokens is None


def test_tool_response_is_normalized():
    tool = Tool(name="read_source", parameters={"type": "object", "properties": {"path": {"type": "string"}},
                                               "required": ["path"]})
    result = execute(lambda req: httpx.Response(200, json=response(stop_reason="tool_use", content=[
        {"type": "tool_use", "id": "tool-1", "name": "read_source", "input": {"path": "a.py"}}
    ])), query=request(tools=[tool]))
    assert result.tool_calls[0].arguments == {"path": "a.py"} and result.finish_reason == "tool_calls"


def test_preparation_cancellation_and_timeout_never_dispatch(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(bedrock, "_prepare", lambda *args: release.wait(0.2))
    ticks = []
    def stop():
        ticks.append(True)
        if len(ticks) > 1:
            release.set()
            return True
        return False
    with pytest.raises(InferenceError) as cancelled:
        execute(lambda req: pytest.fail("Cancelled request dispatched"), stopped=stop)
    assert cancelled.value.code == "cancelled" and not cancelled.value.consumption_unknown
    release.clear()
    with pytest.raises(InferenceError) as timeout:
        execute(lambda req: pytest.fail("Expired request dispatched"), timeout=0.01)
    assert timeout.value.code == "timeout" and not timeout.value.consumption_unknown


def test_inflight_cancellation_closes_async_transport():
    state = {"stopped": False, "closed": False}
    async def handler(req):
        state["stopped"] = True
        try:
            await asyncio.sleep(5)
        finally:
            state["closed"] = True
    started = time.monotonic()
    with pytest.raises(InferenceError) as cancelled:
        execute(handler, stopped=lambda: state["stopped"])
    assert cancelled.value.code == "cancelled" and cancelled.value.consumption_unknown
    assert state["closed"] and time.monotonic() - started < 1


@pytest.mark.parametrize(("status", "codes", "retryable", "unknown"), [
    (429, [], False, False), (422, [422], True, False), (503, [503], False, True),
])
def test_configured_retry_eligibility_is_preserved(status, codes, retryable, unknown):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, json={"message": "rejected"})
    with pytest.raises(InferenceError) as failure:
        execute(handler, connection(options={"retry_codes": codes}))
    assert failure.value.retryable is retryable and failure.value.consumption_unknown is unknown
    assert len(calls) == 1


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("source", ["caller", "connection"])
def test_invalid_timeout_never_prepares_credentials(duration, source, monkeypatch):
    monkeypatch.setattr(bedrock, "_prepare", lambda *args: pytest.fail("Invalid timeout prepared credentials"))
    options, arguments = ({"timeout_sec": duration}, {}) if source == "connection" else ({}, {"timeout": duration})
    with pytest.raises(InferenceError) as failure:
        execute(lambda req: pytest.fail("Invalid timeout dispatched"), connection(options=options), **arguments)
    assert failure.value.code == "timeout" and not failure.value.consumption_unknown


@pytest.mark.parametrize("header", ["Host", "Connection", "Proxy-Authorization", "X-Invalid\nHeader"])
def test_custom_auth_header_cannot_change_routing_or_inject_headers(header):
    with pytest.raises(InferenceError) as failure:
        execute(lambda req: pytest.fail("Unsafe auth header dispatched"), connection(options={"auth_header": header}))
    assert not failure.value.consumption_unknown


@pytest.mark.parametrize("allowance", [0, 5000, 5000000])
def test_converse_uses_only_the_requested_allowance_and_preserves_thinking(allowance):
    from mortis_engine.schemas import Message
    calls = []
    output_tokens = min(allowance, 20000) if allowance else 20000
    tool = Tool(name="read_source", parameters={"type": "object"})
    task = request(max_output_tokens=allowance, tools=[tool])
    def handler(wire):
        body = json.loads(wire.content)
        calls.append(body)
        assert body.get("inferenceConfig", {}).get("maxTokens", 0) == allowance
        assert "max_tokens" not in body.get("additionalModelRequestFields", {})
        if len(calls) == 1:
            return httpx.Response(200, json={"stopReason": "tool_use", "usage": {
                "inputTokens": 7, "outputTokens": output_tokens, "totalTokens": 117 + output_tokens,
                "cacheReadInputTokens": 100, "cacheWriteInputTokens": 10}, "output": {"message": {
                    "role": "assistant", "content": [
                        {"reasoningContent": {"reasoningText": {"text": "inspect", "signature": "opaque"}}},
                        {"reasoningContent": {"redactedContent": "ZW5jcnlwdGVk"}},
                        {"toolUse": {"toolUseId": "read-1", "name": "read_source", "input": {}}}]}}})
        assert body["messages"][1]["content"][0]["reasoningContent"]["reasoningText"]["signature"] == "opaque"
        assert body["messages"][1]["content"][1]["reasoningContent"]["redactedContent"] == "ZW5jcnlwdGVk"
        assert body["messages"][2]["content"] == [{"toolResult": {"toolUseId": "read-1", "content": [{"text": "source"}]}}]
        return httpx.Response(200, json=response())
    first = execute(handler, query=task, timeout=None)
    assert first.usage.total_tokens == 117 + output_tokens
    task = task.model_copy(update={"messages": [*task.messages,
        Message(role="assistant", content=first.content, tool_calls=first.tool_calls, provider_data=first.provider_data),
        Message(role="tool", tool_call_id="read-1", content="source")]})
    assert execute(handler, query=task, timeout=None).content == "Done"


@pytest.mark.parametrize("extra", [{"inferenceConfig.maxTokens": 10}, {"additionalModelRequestFields": {"max_tokens": 10}}])
def test_converse_cannot_hide_a_connection_output_override(extra):
    with pytest.raises(ValueError, match="protected"):
        bedrock.encode_converse(connection(options={"extra_body": extra}), request(max_output_tokens=0))


def test_converse_accepts_optional_reasoning_signature():
    data = response()
    data["output"]["message"]["content"].insert(0, {"reasoningContent": {"reasoningText": {"text": "inspect"}}})
    result = execute(lambda wire: httpx.Response(200, json=data), query=request(max_output_tokens=0))
    assert result.content == "Done"
    assert result.provider_data["anthropic_content"][0] == {"type": "thinking", "thinking": "inspect"}
