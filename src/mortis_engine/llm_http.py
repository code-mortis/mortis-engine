"""Bounded direct HTTP protocols. No provider selection or automatic inference retry."""

import asyncio
import copy
import json
import math
import time
from contextlib import suppress
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from . import __version__
from .http_transport import EndpointPolicyError
from .schemas import Request, Response, ToolCall, Usage
from .security import clean_text, digest, safe_endpoint

MAX_RESPONSE_BYTES = 20_000_000
MAX_REQUEST_BYTES = 32_000_000
PROTECTED = {
    "model", "messages", "input", "instructions", "system", "tools", "tool_choice", "stream",
    "api_key", "max_tokens", "max_completion_tokens", "max_output_tokens", "response_format",
    "inferenceConfig", "additionalModelRequestFields", "toolConfig", "outputConfig",
    "store", "prompt_cache_key", "previous_response_id", "conversation", "background", "authorization", "extra_headers", "include",
}


class InferenceError(Exception):
    def __init__(self, code, message, *, usage=None, request_id=None, retryable=False,
                 consumption_unknown=None, status_code=None, retry_after=None):
        self.code = code
        self.usage = usage or Usage()
        self.request_id = request_id
        self.retryable = retryable
        self.consumption_unknown = self.usage.total_tokens is None if consumption_unknown is None else consumption_unknown
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(message)


def _invalid(message="The provider returned a malformed response.", **kwargs):
    return InferenceError("malformed_response", message, **kwargs)


def _schema(schema):
    # Remote references must never turn schema validation into network access.
    def refs(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef"} and (not isinstance(item, str) or not item.startswith("#")):
                    raise ValueError("Inference schemas may contain only local references")
                refs(item)
        elif isinstance(value, list):
            for item in value:
                refs(item)
    refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError("Invalid inference JSON schema") from exc


def _strict_schema(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object" and (
            schema.get("additionalProperties") is not False
            or set(schema.get("properties", {})) != set(schema.get("required", []))
        ):
            return False
        return all(_strict_schema(item) for item in schema.values())
    if isinstance(schema, list):
        return all(_strict_schema(item) for item in schema)
    return True


def _extra_body(body, extra):
    if not isinstance(extra, dict):
        raise ValueError("extra_body must be a JSON object")
    def assign(parts, value):
        dotted = ".".join(parts)
        strict_flag = (len(parts) == 3 and parts[0] == "tools" and parts[1].isdigit() and parts[2] == "strict") or (
            len(parts) == 4 and parts[0] == "tools" and parts[1].isdigit() and parts[2:] == ["function", "strict"])
        protected = parts[0] in PROTECTED and not strict_flag
        protected |= dotted.startswith("text.format") or dotted.startswith("output_config.format")
        if parts[0].lower() in {"headers", "authentication"}:
            protected = True
        if strict_flag and type(value) is not bool:
            raise ValueError("Tool strict options must be boolean")
        target = body
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            if isinstance(target, list):
                if not part.isdigit() or int(part) >= len(target):
                    raise ValueError("extra_body references an invalid tool index")
                key = int(part)
            elif isinstance(target, dict):
                key = part
            else:
                raise ValueError("extra_body contains conflicting dotted properties")
            if last:
                existing = target.get(key) if isinstance(target, dict) else target[key]
                if protected and existing != value:
                    raise ValueError("extra_body cannot replace protected inference inputs, output limits or authentication")
                if dotted in {"reasoning.effort", "reasoning_effort", "output_config.effort"} and existing is not None and existing != value:
                    raise ValueError("extra_body conflicts with the explicitly selected inference effort")
                target[key] = copy.deepcopy(value)
            else:
                if isinstance(target, dict) and key not in target:
                    if protected or strict_flag:
                        raise ValueError("extra_body cannot replace protected inference inputs, output limits or authentication")
                    target[key] = {}
                target = target[key]

    def expand(parts, value):
        if isinstance(value, dict) and parts[0] in {"text", "output_config", "reasoning"}:
            for key, item in value.items():
                expand([*parts, key], item)
        else:
            assign(parts, value)

    for dotted, value in extra.items():
        if not isinstance(dotted, str) or not dotted or any(not part for part in dotted.split(".")):
            raise ValueError("extra_body requires nonempty property names")
        expand(dotted.split("."), value)
    if "n" in body and body["n"] != 1:
        raise ValueError("Inference supports one budgeted completion per request")
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "enabled" and isinstance(body.get("tool_choice"), dict) and body["tool_choice"].get("type") in {"any", "tool"}:
        raise ValueError("Manual extended thinking requires automatic tool choice")
    if isinstance(thinking, dict) and "budget_tokens" in thinking and (
        type(thinking["budget_tokens"]) is not int or thinking["budget_tokens"] <= 0
        or (body.get("max_tokens") and thinking["budget_tokens"] >= body["max_tokens"])
    ):
        raise ValueError("Thinking token budget must fit within the explicit output budget")
    return body


def encode_request(protocol: str, connection: dict, request: Request) -> dict:
    model = connection.get("model")
    if not isinstance(model, str) or not model or len(model) > (2048 if connection.get("protocol") == "anthropic-bedrock" else 150) or any(ord(c) < 32 for c in model):
        raise ValueError("Inference requires the exact explicitly selected model")
    for tool in request.tools:
        _schema(tool.parameters)
    if request.output_schema is not None:
        _schema(request.output_schema)
    body = {"model": model, "stream": False}
    if protocol == "openai":
        messages = [{"role": "system", "content": request.instructions}] if request.instructions else []
        for message in request.messages:
            row = {"role": message.role, "content": message.content}
            if message.provider_data:
                if set(message.provider_data) - {"reasoning_content", "reasoning_details"}:
                    raise ValueError("Invalid Chat reasoning replay fields")
                reasoning = message.provider_data.get("reasoning_content")
                details = message.provider_data.get("reasoning_details")
                if reasoning is not None and not isinstance(reasoning, str):
                    raise ValueError("Invalid Chat reasoning replay content")
                if details is not None and not isinstance(details, list):
                    raise ValueError("Invalid Chat reasoning replay details")
                row.update(copy.deepcopy(message.provider_data))
            if message.tool_call_id:
                row["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                row["tool_calls"] = [{"id": call.id, "type": "function", "function": {
                    "name": call.name, "arguments": json.dumps(call.arguments)}} for call in message.tool_calls]
            messages.append(row)
        extra = connection.get("options", {}).get("extra_body", {})
        aliases = {"max_tokens", "max_completion_tokens"} & set(extra)
        if len(aliases) > 1:
            raise ValueError("Configure only one protected Chat output budget alias")
        if aliases:
            budget_key = next(iter(aliases))
            if not request.max_output_tokens or type(extra[budget_key]) is not int or extra[budget_key] != request.max_output_tokens:
                raise ValueError("extra_body cannot replace the protected inference output budget")
        else:
            # Compatible providers commonly ignore the newer OpenAI-only budget field.
            budget_key = "max_completion_tokens" if urlsplit(connection.get("base_url", "")).hostname == "api.openai.com" else "max_tokens"
        body.update(messages=messages)
        if request.max_output_tokens:
            body[budget_key] = request.max_output_tokens
        if request.tools:
            body["tools"] = [{"type": "function", "function": {
                "name": tool.name, "description": tool.description, "parameters": tool.parameters,
                "strict": _strict_schema(tool.parameters)}} for tool in request.tools]
            body["tool_choice"] = request.tool_choice if request.tool_choice in {"auto", "required"} else {
                "type": "function", "function": {"name": request.tool_choice}}
        if request.output_schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "mortis_result", "schema": request.output_schema,
                "strict": _strict_schema(request.output_schema)}}
        if request.effort:
            body["reasoning_effort"] = request.effort
    elif protocol == "openai-responses":
        inputs = []
        for message in request.messages:
            if message.provider_data:
                inputs.extend(_replay(protocol, message, request))
                continue
            if message.role == "tool":
                inputs.append({"type": "function_call_output", "call_id": message.tool_call_id,
                               "output": message.content})
            else:
                if message.content:
                    inputs.append({"role": message.role, "content": message.content})
                inputs.extend({"type": "function_call", "call_id": call.id, "name": call.name,
                               "arguments": json.dumps(call.arguments)} for call in message.tool_calls)
        body.update(input=inputs, instructions=request.instructions,
                    max_output_tokens=request.max_output_tokens, store=False,
                    prompt_cache_key=digest(request.session_id))
        if request.tools:
            body["include"] = ["reasoning.encrypted_content"]
        if request.tools:
            body["tools"] = [{"type": "function", "name": tool.name, "description": tool.description,
                              "parameters": tool.parameters, "strict": _strict_schema(tool.parameters)}
                             for tool in request.tools]
            body["tool_choice"] = request.tool_choice if request.tool_choice in {"auto", "required"} else {
                "type": "function", "name": request.tool_choice}
        if request.output_schema is not None:
            body["text"] = {"format": {"type": "json_schema", "name": "mortis_result",
                                       "schema": request.output_schema,
                                       "strict": _strict_schema(request.output_schema)}}
        if request.effort:
            body["reasoning"] = {"effort": request.effort}
        if not request.max_output_tokens:
            body.pop("max_output_tokens")
    elif protocol == "anthropic":
        messages = []
        for message in request.messages:
            if message.role == "tool":
                role = "user"
                blocks = [{"type": "tool_result", "tool_use_id": message.tool_call_id,
                           "content": message.content}]
            else:
                role = message.role
                if message.provider_data:
                    blocks = _replay(protocol, message, request)
                else:
                    blocks = ([{"type": "text", "text": message.content}] if message.content else [])
                    blocks.extend({"type": "tool_use", "id": call.id, "name": call.name,
                                   "input": call.arguments} for call in message.tool_calls)
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(blocks)
            else:
                messages.append({"role": role, "content": blocks})
        body.update(messages=messages)
        if request.max_output_tokens:
            body["max_tokens"] = request.max_output_tokens
        if request.instructions:
            body["system"] = request.instructions
        if request.tools:
            body["tools"] = [{"name": tool.name, "description": tool.description,
                              "input_schema": tool.parameters} for tool in request.tools]
            body["tool_choice"] = {"type": "auto" if request.tool_choice == "auto" else "any"} if request.tool_choice in {"auto", "required"} else {
                "type": "tool", "name": request.tool_choice}
        if request.output_schema is not None:
            body["output_config"] = {"format": {"type": "json_schema", "schema": request.output_schema}}
        if request.effort:
            body.setdefault("output_config", {})["effort"] = request.effort
    else:
        raise ValueError("Unsupported direct HTTP inference protocol")
    return _extra_body(body, connection.get("options", {}).get("extra_body", {}))


def _replay(protocol, message, request):
    key = "anthropic_content" if protocol == "anthropic" else "responses_output"
    if set(message.provider_data) != {key}:
        raise ValueError("Invalid provider reasoning replay fields")
    blocks = message.provider_data[key]
    payload = {"content": blocks, "stop_reason": "tool_use" if message.tool_calls else "end_turn"} if protocol == "anthropic" else {
        "output": blocks, "status": "completed"}
    try:
        check = decode_response(protocol, payload, request.model_copy(update={"tool_choice": "auto", "output_schema": None}))
    except InferenceError as exc:
        raise ValueError("Invalid provider reasoning replay content") from exc
    if check.content != message.content or check.tool_calls != message.tool_calls:
        raise ValueError("Provider replay content must match the validated assistant message")
    return copy.deepcopy(blocks)


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _usage(protocol, data):
    raw = data.get("usage")
    if not isinstance(raw, dict):
        return Usage()
    def nested(name, key):
        value = raw.get(name)
        return _count(value.get(key)) if isinstance(value, dict) else None
    if protocol == "openai":
        incoming, outgoing = _count(raw.get("prompt_tokens")), _count(raw.get("completion_tokens"))
        cached = nested("prompt_tokens_details", "cached_tokens")
        reasoning = nested("completion_tokens_details", "reasoning_tokens")
        written = None
    else:
        incoming, outgoing = _count(raw.get("input_tokens")), _count(raw.get("output_tokens"))
        cached = _count(raw.get("cache_read_input_tokens")) if protocol == "anthropic" else nested("input_tokens_details", "cached_tokens")
        written = _count(raw.get("cache_creation_input_tokens")) if protocol == "anthropic" else None
        reasoning = nested("output_tokens_details", "reasoning_tokens")
        if protocol == "anthropic" and incoming is not None:
            # Anthropic input_tokens excludes both cache buckets; include them in billable totals.
            if any(key in raw and _count(raw[key]) is None for key in ("cache_read_input_tokens", "cache_creation_input_tokens")):
                incoming = None
            else:
                incoming = incoming + (cached or 0) + (written or 0)
    total = incoming + outgoing if incoming is not None and outgoing is not None else None
    reported = _count(raw.get("total_tokens"))
    if reported is not None and total is not None and reported != total:
        return Usage(cache_read_tokens=cached, cache_write_tokens=written, reasoning_tokens=reasoning)
    return Usage(input_tokens=incoming, output_tokens=outgoing, total_tokens=total,
                 cache_read_tokens=cached, cache_write_tokens=written, reasoning_tokens=reasoning)


def _json(text):
    def reject_constant(_):
        raise ValueError("Nonfinite JSON number")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON property")
            result[key] = value
        return result
    return json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique)


def decode_response(protocol: str, data: dict, request: Request, request_id=None) -> Response:
    if not isinstance(data, dict):
        raise _invalid()
    usage = _usage(protocol, data)
    request_id = _request_id(request_id or data.get("id"))
    common = {"usage": usage, "request_id": request_id}
    texts, calls, stop, provider_data = [], [], None, {}
    try:
        if data.get("error"):
            raise InferenceError("provider_error", "The provider returned an inference error.", **common)
        if protocol == "openai":
            choices = data["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError()
            choice, = choices
            message, stop = choice["message"], choice["finish_reason"]
            if message.get("refusal"):
                raise InferenceError("refusal", "The provider refused this inference request.", **common)
            if stop == "length":
                raise InferenceError("output_limit", "The provider exhausted the response token allowance.", **common)
            content = message.get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise ValueError()
                texts.append(content)
            tool_calls = message.get("tool_calls")
            if tool_calls is None:
                tool_calls = []
            if not isinstance(tool_calls, list):
                raise ValueError()
            for call in tool_calls:
                if call.get("type") != "function":
                    raise ValueError()
                calls.append(ToolCall(id=call["id"], name=call["function"]["name"],
                                      arguments=_json(call["function"]["arguments"])))
            for key in ("reasoning_content", "reasoning_details"):
                if key in message and message[key] is not None:
                    if not isinstance(message[key], str if key == "reasoning_content" else list):
                        raise ValueError()
                    provider_data[key] = copy.deepcopy(message[key])
            complete, tool_stop = {"stop"}, "tool_calls"
        elif protocol == "openai-responses":
            stop = data["status"]
            if stop != "completed":
                if (stop == "incomplete" and isinstance(data.get("incomplete_details"), dict)
                        and data["incomplete_details"].get("reason") == "max_output_tokens"):
                    raise InferenceError("output_limit", "The provider exhausted the response token allowance.", **common)
                raise InferenceError("incomplete", "The provider did not complete this inference response.", **common)
            if not isinstance(data["output"], list):
                raise ValueError()
            for item in data["output"]:
                if item.get("status") not in {None, "completed"}:
                    raise InferenceError("incomplete", "The provider returned an incomplete output item.", **common)
                if item["type"] == "message":
                    for block in item["content"]:
                        if block["type"] == "refusal":
                            raise InferenceError("refusal", "The provider refused this inference request.", **common)
                        if block["type"] != "output_text" or not isinstance(block["text"], str):
                            raise ValueError()
                        texts.append(block["text"])
                elif item["type"] == "function_call":
                    calls.append(ToolCall(id=item["call_id"], name=item["name"], arguments=_json(item["arguments"])))
                elif item["type"] != "reasoning":
                    raise ValueError()
            provider_data["responses_output"] = copy.deepcopy(data["output"])
            complete, tool_stop = {"completed"}, "completed"
        elif protocol == "anthropic":
            stop = data["stop_reason"]
            if stop == "max_tokens":
                raise InferenceError("output_limit", "The provider exhausted the response token allowance.", **common)
            if not isinstance(data["content"], list):
                raise ValueError()
            for block in data["content"]:
                if block["type"] == "text":
                    if not isinstance(block["text"], str):
                        raise ValueError()
                    texts.append(block["text"])
                elif block["type"] == "tool_use":
                    calls.append(ToolCall(id=block["id"], name=block["name"], arguments=block["input"]))
                elif block["type"] not in {"thinking", "redacted_thinking"}:
                    raise ValueError()
            provider_data["anthropic_content"] = copy.deepcopy(data["content"])
            complete, tool_stop = {"end_turn", "stop_sequence"}, "tool_use"
        else:
            raise ValueError()
        if stop in {"refusal", "content_filter"}:
            raise InferenceError("refusal", "The provider refused this inference request.", **common)
        if stop not in complete | {tool_stop}:
            raise InferenceError("incomplete", "The provider stopped before completing the response.", **common)
        if (calls and stop != tool_stop) or (not calls and stop == tool_stop and tool_stop not in complete):
            raise ValueError()
        if request.tool_choice != "auto" and not calls:
            raise ValueError()
        if request.tool_choice not in {"auto", "required"} and any(call.name != request.tool_choice for call in calls):
            raise ValueError()
        tools = {tool.name: tool for tool in request.tools}
        seen = set()
        for call in calls:
            if call.name not in tools or call.id in seen:
                raise ValueError()
            seen.add(call.id)
            Draft202012Validator(tools[call.name].parameters).validate(call.arguments)
        content, output = "".join(texts), None
        if not content and not calls:
            raise ValueError()
        if request.output_schema is not None and not calls:
            output = _json(content)
            Draft202012Validator(request.output_schema).validate(output)
        return Response(content=content, tool_calls=calls, usage=usage, request_id=request_id,
                        finish_reason="tool_calls" if calls else "complete", output=output,
                        provider_data=provider_data)
    except InferenceError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, ValidationError, RecursionError) as exc:
        raise _invalid(**common) from exc


def _request_id(value):
    return clean_text(value) if isinstance(value, str) and 0 < len(value) <= 250 and value.isprintable() else None


def _retry_after(value):
    if not value:
        return None
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return min(900.0, max(0.0, seconds)) if math.isfinite(seconds) else None


async def _drain(task):
    """Finish owned cleanup even when its caller is cancelled repeatedly."""
    interrupted = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done():
                raise
            interrupted = True
    if interrupted:
        raise asyncio.CancelledError()
    return result


DEFAULT_TIMEOUT = object()


async def _bounded(awaitable, stopped, timeout):
    if stopped():
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise InferenceError("cancelled", "Inference was cancelled before dispatch.", consumption_unknown=False)
    task = asyncio.create_task(awaitable)
    deadline = time.monotonic() + timeout if timeout is not None else None
    try:
        while True:
            if stopped():
                raise InferenceError("cancelled", "Inference was cancelled.")
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise InferenceError("timeout", "The inference request exceeded its deadline.")
            done, _ = await asyncio.wait({task}, timeout=min(0.1, remaining) if remaining is not None else 0.1)
            if done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await _drain(task)


async def post_json(client: httpx.AsyncClient, url: str, headers, body: dict | bytes,
                    *, stopped=lambda: False, timeout: float | None = 120, retry_codes=None, method="POST"):
    """Use exact bytes for signed requests; return (decoded object, safe request ID)."""
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        raise InferenceError("timeout", "The inference deadline expired before dispatch.", consumption_unknown=False)
    content = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
    if len(content) > MAX_REQUEST_BYTES:
        raise ValueError("Inference request exceeded its transport size limit")

    async def perform():
        try:
            wire = client.build_request(method, url, headers=headers, content=content, timeout=timeout)
            # Provider cookies are not credentials and must not cross task/run boundaries.
            if "cookie" not in httpx.Headers(headers):
                wire.headers.pop("cookie", None)
            response = await client.send(wire, stream=True, follow_redirects=False, auth=None)
            try:
                identity = _request_id(response.headers.get("x-request-id") or response.headers.get("request-id")
                                       or response.headers.get("x-amzn-requestid"))
                if 300 <= response.status_code < 400:
                    raise InferenceError("redirect", "The provider redirected inference; credentials were not forwarded.",
                                         request_id=identity, status_code=response.status_code, consumption_unknown=False)
                if response.status_code < 400 and "text/event-stream" in response.headers.get("content-type", "").lower():
                    raise InferenceError("partial_stream", "The provider returned an unexpected event stream; no partial result was accepted.", request_id=identity)
                size, chunks = 0, []
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise InferenceError("response_limit", "The provider response exceeded the transport size limit.", request_id=identity)
                    chunks.append(chunk)
                if response.status_code >= 400:
                    status = response.status_code
                    code = "authentication" if status in {401, 403} else "rate_limit" if status == 429 else "provider_error"
                    if 500 <= status < 600:
                        code = f"provider_http_{status}"
                    try:
                        error_body = _json(b"".join(chunks))
                        error = error_body.get("error", {}) if isinstance(error_body, dict) else {}
                        error_type = error.get("code") or error.get("type") if isinstance(error, dict) else None
                        if error_type in {"insufficient_quota", "billing_hard_limit_reached", "credit_balance_too_low"}:
                            code = "quota"
                    except (ValueError, UnicodeError, RecursionError):
                        pass
                    known_rejection = status in {400, 401, 403, 404, 405, 413, 415, 422, 429}
                    raise InferenceError(code, {"authentication": "The provider denied the connection credentials.",
                                               "rate_limit": "The provider rate limit was reached.",
                                               "quota": "The provider quota or credit balance was exhausted."}.get(
                                                   code, "The provider rejected the inference request."),
                                         request_id=identity, status_code=status,
                                         retryable=known_rejection and status in (retry_codes if retry_codes is not None else [429]) and code not in {"quota", "authentication"},
                                         retry_after=_retry_after(response.headers.get("retry-after")),
                                         consumption_unknown=not known_rejection)
                try:
                    data = _json(b"".join(chunks))
                    if not isinstance(data, dict):
                        raise ValueError()
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise _invalid(request_id=identity) from exc
                return data, identity
            finally:
                await response.aclose()
        except EndpointPolicyError as exc:
            raise InferenceError("endpoint_policy", "The selected inference endpoint failed address validation.",
                                 consumption_unknown=False) from exc
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise InferenceError("connection", "Could not connect to the selected inference endpoint.",
                                 retryable=True, consumption_unknown=False) from exc
        except httpx.TimeoutException as exc:
            raise InferenceError("timeout", "The inference request timed out; consumption is unknown.") from exc
        except httpx.HTTPError as exc:
            raise InferenceError("transport", "The inference connection failed; consumption is unknown.") from exc
    return await _bounded(perform(), stopped, timeout)


def request_headers(protocol, options, credentials):
    key = credentials.get("api_key", "")
    auth_header = options.get("auth_header") or ("x-api-key" if protocol == "anthropic" else "Authorization")
    headers = httpx.Headers({"Content-Type": "application/json", "Accept": "application/json"})
    if key:
        headers[auth_header] = f"Bearer {key}" if auth_header.lower() == "authorization" else key
    if protocol == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
    headers.update(credentials.get("extra_headers", {}))
    if any(name in headers for name in ("host", "content-length", "transfer-encoding", "connection", "proxy-authorization")):
        raise ValueError("Extra inference headers cannot alter HTTP routing or framing")
    if any("\r" in name + value or "\n" in name + value for name, value in headers.items()):
        raise ValueError("Inference headers cannot contain line breaks")
    return headers


async def send(connection: dict, credentials: dict, request: Request, *, client: httpx.AsyncClient,
               stopped=lambda: False, timeout=DEFAULT_TIMEOUT) -> Response:
    if connection.get("kind", "api") != "api":
        raise ValueError("Inference requires a shared API connection")
    protocol = connection.get("protocol")
    body = encode_request(protocol, connection, request)
    options = connection.get("options", {})
    duration = float(options.get("timeout_sec", 120)) if timeout is DEFAULT_TIMEOUT else timeout
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise InferenceError("timeout", "The inference deadline expired before dispatch.", consumption_unknown=False)
    started = time.monotonic()
    try:
        base = await _bounded(asyncio.to_thread(safe_endpoint, connection.get("base_url", "")), stopped, duration)
    except InferenceError as exc:
        exc.consumption_unknown = False
        raise
    suffix = {"openai": "/chat/completions", "openai-responses": "/responses", "anthropic": "/messages"}[protocol]
    if protocol == "anthropic" and not base.endswith("/v1"):
        base += "/v1"
    headers = request_headers(protocol, options, credentials)
    if protocol == "anthropic" and not request.max_output_tokens:
        # Messages requires a positive allowance; discover it only when none was supplied.
        try:
            model, identity = await post_json(client, base + "/models/" + quote(connection["model"], safe=""),
                headers, b"", method="GET", stopped=stopped,
                timeout=duration - (time.monotonic() - started) if duration is not None else None,
                retry_codes=options.get("retry_codes"))
            capacity = model.get("max_tokens")
            if type(capacity) is not int or capacity <= 0:
                raise InferenceError("model_capacity", "The selected Anthropic endpoint did not report the model's output capacity.",
                                     request_id=identity, consumption_unknown=False)
            body = encode_request(protocol, connection, request.model_copy(update={"max_output_tokens": capacity}))
        except InferenceError as exc:
            exc.consumption_unknown = False
            raise
    endpoint = urlsplit(base)
    if endpoint.hostname == "opencode.ai" and endpoint.path.rstrip("/") == "/zen/go/v1":
        headers["User-Agent"] = f"code-mortis/{__version__}"
        headers["x-opencode-session"] = request.session_id
    data, identity = await post_json(client, base + suffix, headers, body, stopped=stopped,
                                     timeout=duration - (time.monotonic() - started) if duration is not None else None, retry_codes=options.get("retry_codes"))
    return decode_response(protocol, data, request, identity)
