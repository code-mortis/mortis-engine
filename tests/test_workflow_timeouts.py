"""Operation deadlines override connection defaults without losing cancellation."""

import asyncio

import httpx
import pytest

from mortis_engine import gateway, llm_bedrock, llm_http
from mortis_engine.schemas import Message, Request


@pytest.mark.parametrize("protocol", ["openai", "openai-responses", "anthropic", "anthropic-bedrock"])
@pytest.mark.parametrize("timeout", [None, 600, "omitted"])
def test_explicit_operation_timeout_reaches_transport(monkeypatch, protocol, timeout):
    adapter = llm_bedrock if protocol == "anthropic-bedrock" else llm_http
    connection = {"kind": "api", "protocol": protocol, "model": "test-model",
                  "base_url": "https://provider.example/v1", "options": {"timeout_sec": 5}}
    request = Request(messages=[Message(role="user", content="test")], max_output_tokens=128)
    observed = []

    async def post(*args, **kwargs):
        observed.append(kwargs["timeout"])
        return {"max_tokens": 128000}, "test-request"

    monkeypatch.setattr(adapter, "post_json", post)
    monkeypatch.setattr(adapter, "decode_converse" if protocol == "anthropic-bedrock" else "decode_response", lambda *args, **kwargs: "response")
    monkeypatch.setattr(llm_http, "safe_endpoint", lambda value: value)
    if protocol == "anthropic-bedrock":
        monkeypatch.setattr(adapter, "_prepare", lambda *args: ("https://bedrock.example", {}))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail("Network call"))) as client:
            kwargs = {} if timeout == "omitted" else {"timeout": timeout}
            assert await gateway.send(connection, {}, request, client=client, **kwargs) == "response"

    asyncio.run(run())
    if timeout is None:
        assert observed == [None] * (2 if protocol == "anthropic" else 1)
    else:
        expected = 5 if timeout == "omitted" else timeout
        assert expected - 1 < observed[0] <= expected


def test_unlimited_wait_still_cancels_and_drains():
    stopped, drained = [False], []

    async def task():
        try:
            stopped[0] = True
            await asyncio.Event().wait()
        finally:
            drained.append(True)

    async def run():
        with pytest.raises(llm_http.InferenceError, match="cancelled"):
            await llm_http._bounded(task(), lambda: stopped[0], None)

    asyncio.run(run())
    assert drained == [True]


def test_unlimited_http_request_has_no_httpx_deadline():
    async def handler(request):
        assert all(value is None for value in request.extensions["timeout"].values())
        return httpx.Response(200, json={"ok": True})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result, _ = await llm_http.post_json(client, "https://provider.example", {}, {}, timeout=None)
            assert result == {"ok": True}

    asyncio.run(run())
