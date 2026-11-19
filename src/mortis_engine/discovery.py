"""Read the selected endpoint's model catalog using its connection credentials."""

import json
import time

import httpx

from .http_transport import PinnedHTTPTransport
from .llm_http import request_headers
from .security import safe_endpoint

MAX_MODELS = 5000
MAX_BYTES = 2_000_000


def model_entries(rows):
    if not isinstance(rows, list) or len(rows) > MAX_MODELS:
        raise ValueError("Invalid model list")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid model entry")
        identity = row.get("id")
        name = row.get("display_name") or row.get("name") or identity
        for field, limit in ((identity, 150), (name, 250)):
            if not isinstance(field, str) or not field.strip() or len(field) > limit or not field.isprintable():
                raise ValueError("The provider returned an invalid model identifier or display name")
        yield identity, name


def list_models(connection, credentials):
    protocol = connection["protocol"]
    if protocol == "anthropic-bedrock":
        raise ValueError("Model discovery is unavailable for Bedrock. Enter the model ID.")
    if not credentials.get("api_key"):
        raise ValueError("Set an API key to load models.")
    base = safe_endpoint(connection["base_url"])
    anthropic = protocol == "anthropic"
    url = base + ("/v1/models" if anthropic and not base.endswith("/v1") else "/models")
    headers = request_headers(protocol, connection.get("options", {}), credentials)
    params = {"limit": 1000} if anthropic else {}
    deadline = time.monotonic() + 20
    size, models, cursors = 0, {}, set()
    try:
        with httpx.Client(follow_redirects=False, trust_env=False, transport=PinnedHTTPTransport()) as client:
            for _ in range(20):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("Model discovery timed out")
                with client.stream("GET", url, headers=headers, params=params, timeout=min(10, remaining)) as response:
                    if response.status_code in {401, 403}:
                        raise ValueError("The provider denied model discovery. Check the credentials and permissions.")
                    if response.status_code in {404, 405, 501}:
                        raise ValueError("Model discovery is unavailable at this endpoint. Enter the model ID.")
                    if response.status_code != 200:
                        raise ValueError(f"Model discovery failed (HTTP {response.status_code}). Enter the model ID.")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES or time.monotonic() > deadline:
                            raise ValueError("Model discovery exceeded the size or time limit")
                        content.extend(chunk)
                data = json.loads(content)
                if not isinstance(data, dict):
                    raise ValueError("The provider returned an invalid model catalog")
                models.update(model_entries(data.get("data")))
                if len(models) > MAX_MODELS:
                    raise ValueError("Too many models")
                if not data.get("has_more"):
                    return list(models.items())
                cursor = data.get("last_id")
                if not isinstance(cursor, str) or not cursor or len(cursor) > 1000 or cursor in cursors:
                    raise ValueError("Invalid model cursor")
                cursors.add(cursor)
                params["after_id" if anthropic else "after"] = cursor
    except (httpx.HTTPError, json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise ValueError("Could not read the model catalog. Retry or enter the model ID.") from exc
    raise ValueError("Model discovery exceeded its page limit")
