"""Dispatch to the selected provider and validate the same two-round probe as Code Mortis."""

import asyncio
import json
import time

import httpx

from . import llm_http
from .llm_http import InferenceError
from .schemas import Message, Request, Tool


def http_client():
    """One pool per run/event loop, shared across its concurrent tasks and tool rounds."""
    return httpx.AsyncClient(trust_env=False, follow_redirects=False,
                             limits=httpx.Limits(max_connections=8, max_keepalive_connections=8))


def reservation(connection, request):
    protocol = connection["protocol"]
    body = llm_http.encode_request("anthropic" if protocol == "anthropic-bedrock" else protocol,
                                   connection, request)
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > llm_http.MAX_REQUEST_BYTES:
        raise ValueError("Inference request exceeds the byte limit")
    # A conservative byte estimate plus framing; actual provider usage settles the ledger.
    tokens = len(encoded) + 1024 + 64 * (len(request.messages) + len(request.tools))
    ceiling = connection.get("options", {}).get("prompt_token_ceiling")
    if ceiling is not None and tokens > ceiling:
        raise ValueError("Inference request exceeds the connection prompt token ceiling")
    return tokens


async def send(connection, credentials, request, *, client, stopped=lambda: False, timeout=None):
    """Single attempt, exact connection/model, no implicit protocol or provider fallback."""
    if connection.get("kind") != "api":
        raise ValueError("Inference requires a shared API connection")
    if connection.get("protocol") == "anthropic-bedrock":
        from . import llm_bedrock

        adapter = llm_bedrock
    else:
        adapter = llm_http
    reservation(connection, request)
    return await adapter.send(connection, credentials, request, client=client, stopped=stopped, timeout=timeout)


async def check_connection(connection, credentials, *, client, stopped=lambda: False):
    """Two synthetic requests; no repository content or arbitrary tool execution."""
    deadline = time.monotonic() + connection.get("options", {}).get("timeout_sec", 120)
    extra = connection.get("options", {}).get("extra_body", {})
    thinking = extra.get("thinking", {})
    reasoning_budget = extra.get("thinking.budget_tokens", thinking.get("budget_tokens", 0) if isinstance(thinking, dict) else 0)
    if type(reasoning_budget) is not int or reasoning_budget < 0:
        raise ValueError("Connection thinking budget must be a nonnegative whole number")
    output_limit = next((extra[key] for key in ("max_tokens", "max_completion_tokens", "max_output_tokens") if key in extra),
                        max(2048, reasoning_budget + 1024))
    request = Request(messages=[Message(role="user", content="Call mortis_probe with value 17.")],
                      tools=[Tool(name="mortis_probe", parameters={"type": "object", "properties": {
                          "value": {"type": "integer", "const": 17}}, "required": ["value"],
                          "additionalProperties": False})], max_output_tokens=output_limit)
    first = await send(connection, credentials, request, client=client, stopped=stopped,
                       timeout=deadline - time.monotonic())
    if len(first.tool_calls) != 1 or first.tool_calls[0].arguments != {"value": 17}:
        raise InferenceError("tool_round_trip", "The selected model did not complete the tool probe.", usage=first.usage)
    call = first.tool_calls[0]
    followup = request.model_copy(update={"tool_choice": "auto", "messages": [*request.messages,
        Message(role="assistant", content=first.content, tool_calls=first.tool_calls, provider_data=first.provider_data),
        Message(role="tool", tool_call_id=call.id, content='{"result":"MORTIS_PROBE_OK"}'),
        Message(role="user", content="Reply with exactly MORTIS_PROBE_OK and do not call tools.")]})
    second = await send(connection, credentials, followup, client=client, stopped=stopped,
                        timeout=deadline - time.monotonic())
    if second.tool_calls or second.content.strip() != "MORTIS_PROBE_OK":
        raise InferenceError("tool_round_trip", "The selected model did not confirm the tool result.", usage=second.usage)
    return first, second


def test_connection(connection, credentials, *, stopped=lambda: False):
    async def run():
        async with http_client() as client:
            return await check_connection(connection, credentials, client=client, stopped=stopped)

    asyncio.run(run())
    return "Connected. Model completed a tool-call round trip."
