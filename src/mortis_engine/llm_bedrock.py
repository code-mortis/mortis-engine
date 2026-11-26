"""Bedrock Converse over pooled async HTTP; AWS credentials stay in the worker."""

import asyncio
import json
import math
import os
import re
import threading
import time
from functools import lru_cache
from urllib.parse import quote

import botocore.session
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError
from botocore.regions import EndpointResolver

from .llm_http import (
    DEFAULT_TIMEOUT,
    InferenceError,
    _bounded,
    _endpoint_error,
    _invalid,
    _usage,
    decode_response,
    encode_request,
    post_json,
)
from .schemas import Request, Response
from .security import safe_endpoint


@lru_cache(maxsize=32)
def _session(profile):
    session = botocore.session.Session(profile=profile or None)
    return session, threading.Lock()


def _prepare(connection, credentials, body):
    options = connection.get("options", {})
    session, lock = _session(options.get("aws_profile"))
    # Session setup is not thread-safe; cache refreshable credentials, never frozen keys.
    with lock:
        region = options.get("aws_region") or os.environ.get("AWS_REGION") or session.get_config_variable("region") or "us-east-1"
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", region):
            raise ValueError("Invalid AWS region")
        endpoint = EndpointResolver(session.get_data("endpoints")).construct_endpoint("bedrock-runtime", region)
        if not endpoint:
            raise ValueError("No Bedrock endpoint for the selected AWS region")
    try:
        base = safe_endpoint(connection.get("base_url") or f'https://{endpoint["hostname"]}')
    except ValueError as exc:
        raise _endpoint_error(exc) from exc
    model = connection["model"]
    if not isinstance(model, str) or not model or len(model) > 2048 or model in {".", ".."} or any(ord(char) < 32 for char in model):
        raise ValueError("Invalid Bedrock model identifier")
    url = f"{base}/model/{quote(model, safe='')}/converse"
    headers = httpx.Headers({"Content-Type": "application/json", "Accept": "application/json"})
    headers.update(credentials.get("extra_headers", {}))
    signing_headers = {"x-amz-date", "x-amz-security-token"}
    if any(name in headers for name in signing_headers | {"authorization"}):
        raise ValueError("Bedrock signing headers are managed by the worker")
    key = credentials.get("api_key") or os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
    if key:
        header = options.get("auth_header") or "Authorization"
        if header.lower() in signing_headers:
            raise ValueError("Bedrock signing headers are managed by the worker")
        headers[header] = f"Bearer {key}" if header.lower() == "authorization" else key
    else:
        if options.get("auth_header") and options["auth_header"].lower() != "authorization":
            raise ValueError("A custom Bedrock auth header requires an API key")
    if any(name in headers for name in ("host", "content-length", "transfer-encoding", "connection", "proxy-authorization")):
        raise ValueError("Bedrock headers cannot replace HTTP routing or framing")
    if any("\r" in name + value or "\n" in name + value for name, value in headers.items()):
        raise ValueError("Bedrock headers cannot contain line breaks")
    if not key:
        with lock:
            available = session.get_credentials()
            if available is None:
                raise InferenceError("authentication", "No AWS credentials are available to the review worker.", consumption_unknown=False)
            frozen = available.get_frozen_credentials()
        signed = AWSRequest(method="POST", url=url, data=body, headers=dict(headers))
        SigV4Auth(frozen, "bedrock", region).add_auth(signed)
        headers = httpx.Headers(dict(signed.headers))
    return url, headers


def encode_converse(connection, request):
    source = encode_request("anthropic", connection, request)
    source.pop("model")
    source.pop("stream")
    messages = source.pop("messages")
    for message in messages:
        converted = []
        for block in message["content"]:
            kind = block["type"]
            if kind == "text":
                converted.append({"text": block["text"]})
            elif kind == "tool_use":
                converted.append({"toolUse": {"toolUseId": block["id"], "name": block["name"], "input": block["input"]}})
            elif kind == "tool_result":
                converted.append({"toolResult": {"toolUseId": block["tool_use_id"], "content": [{"text": block["content"]}]}})
            elif kind == "thinking":
                reasoning = {"text": block["thinking"]}
                if "signature" in block:
                    reasoning["signature"] = block["signature"]
                converted.append({"reasoningContent": {"reasoningText": reasoning}})
            elif kind == "redacted_thinking":
                converted.append({"reasoningContent": {"redactedContent": block["data"]}})
            else:
                raise ValueError("Unsupported Bedrock replay block")
        message["content"] = converted
    body = {"messages": messages}
    if "system" in source:
        body["system"] = [{"text": source.pop("system")}]
    config = {target: source.pop(key) for key, target in {
        "max_tokens": "maxTokens", "temperature": "temperature", "top_p": "topP", "stop_sequences": "stopSequences",
    }.items() if key in source}
    if config:
        body["inferenceConfig"] = config
    if "tools" in source:
        tools = []
        for tool in source.pop("tools"):
            spec = {"name": tool["name"], "inputSchema": {"json": tool["input_schema"]}}
            if tool.get("description"):
                spec["description"] = tool["description"]
            if "strict" in tool:
                spec["strict"] = tool["strict"]
            tools.append({"toolSpec": spec})
        choice = source.pop("tool_choice")
        body["toolConfig"] = {"tools": tools, "toolChoice": {
            choice["type"]: {"name": choice["name"]} if choice["type"] == "tool" else {}}}
    output = source.get("output_config", {})
    if "format" in output:
        schema = output.pop("format")["schema"]
        body["outputConfig"] = {"textFormat": {"type": "json_schema", "structure": {
            "jsonSchema": {"name": "mortis_result", "schema": json.dumps(schema)}}}}
        if not output:
            source.pop("output_config")
    if source:
        body["additionalModelRequestFields"] = source
    return body


def decode_converse(data, request, identity):
    raw = data.get("usage")
    usage = {target: raw[key] for key, target in {
        "inputTokens": "input_tokens", "outputTokens": "output_tokens", "totalTokens": "total_tokens",
        "cacheReadInputTokens": "cache_read_input_tokens", "cacheWriteInputTokens": "cache_creation_input_tokens",
    }.items() if key in raw} if isinstance(raw, dict) else {}
    normalized = {"usage": usage, "stop_reason": data.get("stopReason"), "content": []}
    try:
        message = data["output"]["message"]
        if message["role"] != "assistant" or not isinstance(message["content"], list):
            raise ValueError()
        for block in message["content"]:
            if len(block) != 1:
                raise ValueError()
            if "text" in block:
                value = {"type": "text", "text": block["text"]}
            elif "toolUse" in block:
                tool = block["toolUse"]
                value = {"type": "tool_use", "id": tool["toolUseId"], "name": tool["name"], "input": tool["input"]}
            elif "reasoningContent" in block:
                reasoning = block["reasoningContent"]
                if not isinstance(reasoning, dict) or len(reasoning) != 1:
                    raise ValueError()
                if "reasoningText" in reasoning:
                    text = reasoning["reasoningText"]
                    if not isinstance(text["text"], str) or ("signature" in text and not isinstance(text["signature"], str)):
                        raise ValueError()
                    value = {"type": "thinking", "thinking": text["text"]}
                    if "signature" in text:
                        value["signature"] = text["signature"]
                else:
                    if not isinstance(reasoning["redactedContent"], str):
                        raise ValueError()
                    value = {"type": "redacted_thinking", "data": reasoning["redactedContent"]}
            else:
                raise ValueError()
            normalized["content"].append(value)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise _invalid(usage=_usage("anthropic", normalized), request_id=identity) from exc
    return decode_response("anthropic", normalized, request, request_id=identity)


async def send(connection: dict, credentials: dict, request: Request, *, client: httpx.AsyncClient,
               stopped=lambda: False, timeout=DEFAULT_TIMEOUT) -> Response:
    if connection.get("kind", "api") != "api" or connection.get("protocol") != "anthropic-bedrock":
        raise ValueError("Bedrock inference requires a shared Bedrock connection")
    if stopped():
        raise InferenceError("cancelled", "Inference was cancelled before dispatch.", consumption_unknown=False)
    options = connection.get("options", {})
    timeout = float(options.get("timeout_sec", 120)) if timeout is DEFAULT_TIMEOUT else timeout
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        raise InferenceError("timeout", "The Bedrock deadline expired before dispatch.", consumption_unknown=False)
    deadline = time.monotonic() + timeout if timeout is not None else None
    body = encode_converse(connection, request)
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    # Credential providers may perform I/O; only preparation runs in a thread, never inference.
    try:
        url, headers = await _bounded(asyncio.to_thread(_prepare, connection, credentials, payload), stopped, timeout)
    except InferenceError as exc:
        exc.consumption_unknown = False
        raise
    except (BotoCoreError, ValueError, TypeError, OSError) as exc:
        raise InferenceError("authentication", "Could not prepare Bedrock authentication. Check the worker AWS credentials, profile, region and endpoint.", consumption_unknown=False) from exc
    if stopped():
        raise InferenceError("cancelled", "Inference was cancelled before dispatch.", consumption_unknown=False)
    remaining = deadline - time.monotonic() if deadline is not None else None
    if remaining is not None and remaining <= 0:
        raise InferenceError("timeout", "The Bedrock request deadline expired before dispatch.", consumption_unknown=False)
    data, identity = await post_json(client, url, headers, payload, stopped=stopped, timeout=remaining,
                                     retry_codes=options.get("retry_codes"))
    return decode_converse(data, request, identity)
