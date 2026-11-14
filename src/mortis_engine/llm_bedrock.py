"""Bedrock InvokeModel over pooled async HTTP; AWS credentials stay in the worker."""

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
    InferenceError,
    _bounded,
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
    base = safe_endpoint(connection.get("base_url") or f'https://{endpoint["hostname"]}')
    model = connection["model"]
    if not isinstance(model, str) or not model or len(model) > 2048 or model in {".", ".."} or any(ord(char) < 32 for char in model):
        raise ValueError("Invalid Bedrock model identifier")
    url = f"{base}/model/{quote(model, safe='')}/invoke"
    headers = httpx.Headers({"Content-Type": "application/json", "Accept": "application/json"})
    headers.update(credentials.get("extra_headers", {}))
    key = credentials.get("api_key") or os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
    if key:
        header = options.get("auth_header") or "Authorization"
        headers[header] = f"Bearer {key}" if header.lower() == "authorization" else key
    else:
        if options.get("auth_header") and options["auth_header"].lower() != "authorization":
            raise ValueError("A custom Bedrock auth header requires an API key")
        if any(name in headers for name in ("authorization", "x-amz-date", "x-amz-security-token")):
            raise ValueError("Bedrock signing headers are managed by the worker")
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


async def send(connection: dict, credentials: dict, request: Request, *, client: httpx.AsyncClient,
               stopped=lambda: False, timeout: float | None = None) -> Response:
    if connection.get("kind", "api") != "api" or connection.get("protocol") != "anthropic-bedrock":
        raise ValueError("Bedrock inference requires a shared Bedrock connection")
    if stopped():
        raise InferenceError("cancelled", "Inference was cancelled before dispatch.", consumption_unknown=False)
    options = connection.get("options", {})
    limits = (float(options.get("timeout_sec", 120)), float(timeout) if timeout is not None else 900)
    if any(not math.isfinite(limit) or limit <= 0 for limit in limits):
        raise InferenceError("timeout", "The Bedrock deadline expired before dispatch.", consumption_unknown=False)
    timeout = min(limits)
    deadline = time.monotonic() + timeout
    body = encode_request("anthropic", connection, request)
    body.pop("model", None)
    body.pop("stream", None)
    body["anthropic_version"] = "bedrock-2023-05-31"
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
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise InferenceError("timeout", "The Bedrock request deadline expired before dispatch.", consumption_unknown=False)
    data, identity = await post_json(client, url, headers, payload, stopped=stopped, timeout=remaining,
                                     retry_codes=options.get("retry_codes"))
    return decode_response("anthropic", data, request, request_id=identity)
