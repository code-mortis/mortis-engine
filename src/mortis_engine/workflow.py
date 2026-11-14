"""Shared selected-provider dispatch for local review, verification and security workflows."""

import asyncio
import copy
import hashlib
import json
import time
from uuid import uuid4

from . import gateway, llm_http
from .history import encode
from .llm_http import InferenceError
from .review.context import sanitize
from .schemas import Message, Request, Response, Usage


def schema_for_transport(schema):
    schema = copy.deepcopy(schema)

    def strict(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            value.pop("default", None)
            for child in value.values():
                strict(child)
        elif isinstance(value, list):
            for child in value:
                strict(child)

    strict(schema)
    return schema


def evidence_bundle(context):
    import re

    parts = ["# Code Mortis verification evidence", json.dumps({
        key: value for key, value in context.items() if key not in {"sources", "diff"}
    }, indent=2)]
    blocks = [("Subsequent Git diff", context["diff"])]
    blocks.extend((f"{revision} source: {json.dumps(path)} (source line numbers before |)",
                   "\n".join(f"{number} | {line}" for number, line in enumerate(source.splitlines(), 1)))
                  for revision, sources in context["sources"].items() for path, source in sources.items())
    for title, source in blocks:
        fence = "`" * max(3, 1 + max((len(match) for match in re.findall(r"`+", source)), default=0))
        parts.extend([title, fence, source, fence])
    return "\n\n".join(parts)


class Session:
    def __init__(self, connection, credentials, *, history=None, run_id=None, deadline=None, scan_budget=None):
        self.connection = connection
        self.credentials = credentials
        self.history = history
        self.run_id = run_id or uuid4().hex
        self.deadline = deadline
        self.scan_budget = scan_budget
        self.stage_deadlines = {}
        self.client = None
        self.records = history.attempts(self.run_id) if history else {}
        for _, row, _ in self.records.values():
            if row["status"] == "running":
                row.update(status="failed", code="interrupted")
                history.settle(self.run_id, row)

    async def __aenter__(self):
        self.client = await gateway.http_client().__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()
        self.client = None

    def usage(self, stage=None):
        records = [item[1] for item in self.records.values() if stage is None or item[1]["stage"] == stage]
        charged = sum(self.charge(row) for row in records)
        return {"requests": records, "reserved_tokens": charged,
                "total_tokens": None if any(row["consumption_unknown"] for row in records) else charged,
                "budget_exceeded": any(row.get("code") == "budget" for row in records)}

    @staticmethod
    def charge(row):
        return row["reserved_tokens"] if row["consumption_unknown"] else row["usage"].get("total_tokens") or 0

    async def send(self, stage, key, request, *, client, timeout_at, token_budget, stopped, accept):
        for retry in range(3):
            task = key + (f":retry:{retry}" if retry else "")
            try:
                return await self._attempt(stage, task, request, client, timeout_at, token_budget, stopped, accept)
            except InferenceError as exc:
                delay = max(exc.retry_after or 0, 2 ** retry)
                if (not exc.retryable or exc.consumption_unknown or exc.usage.total_tokens not in {0, None}
                        or retry == 2 or time.monotonic() + delay >= timeout_at):
                    raise
                await llm_http._bounded(asyncio.sleep(delay), stopped, timeout_at - time.monotonic())
        raise AssertionError("Retry limit exhausted")

    async def _attempt(self, stage, key, request, client, until, budget, stopped, accept):
        digest = hashlib.sha256(encode(request.model_dump()).encode()).hexdigest()
        cached = self.records.get((stage, key))
        if cached:
            old_digest, row, value = cached
            if old_digest != digest:
                raise ValueError("The saved task has different immutable inputs.")
            if value is not None and row["status"] == "completed":
                return json.loads(value)
            raise InferenceError(row.get("code", "interrupted"), "The saved provider attempt did not complete.",
                                 consumption_unknown=row["consumption_unknown"], usage=Usage(**row["usage"]),
                                 retryable=row.get("retryable", False), retry_after=row.get("retry_after"))
        if stopped() or time.monotonic() >= until:
            raise InferenceError("deadline", "The stage deadline has expired.", consumption_unknown=False)
        amount = gateway.reservation(self.connection, request) + request.max_output_tokens
        rows = [row for _, row, _ in self.records.values() if row["stage"] == stage or
                stage.startswith("security_") and row["stage"].startswith("security_")]
        if any(row["consumption_unknown"] and row["status"] != "running" for row in rows):
            raise InferenceError("usage_unknown", "An earlier request has unknown token usage.", consumption_unknown=False)
        if (budget is not None and sum(self.charge(row) for row in rows if row["stage"] == stage) + amount > budget
                or self.scan_budget is not None and sum(self.charge(row) for row in rows) + amount > self.scan_budget):
            raise InferenceError("budget", "The token budget cannot admit this request.", consumption_unknown=False)
        # No awaits between admission, reservation and the durable dispatch marker.
        row = {"task_key": key, "stage": stage, "reserved_tokens": amount, "usage": {},
               "consumption_unknown": True, "status": "running"}
        if self.history:
            self.history.start_attempt(self.run_id, row, digest)
        self.records[stage, key] = (digest, row, None)
        result = None
        try:
            response = await gateway.send(self.connection, self.credentials, request, client=client,
                                          stopped=stopped, timeout=until - time.monotonic())
            row.update(usage=response.usage.model_dump(), consumption_unknown=response.usage.total_tokens is None)
            result = accept(response)
            row["status"] = "completed"
        except InferenceError as exc:
            row.update(status="failed", code=exc.code, usage=exc.usage.model_dump(),
                       retryable=exc.retryable, retry_after=exc.retry_after, consumption_unknown=exc.consumption_unknown)
            raise
        except BaseException:
            row.update(status="failed", code="interrupted")
            raise
        finally:
            if self.history:
                self.history.settle(self.run_id, row, result)
            self.records[stage, key] = (digest, row, encode(result) if result is not None else None)
        return result

    async def analysis(self, key, request, **kwargs):
        def accept(response):
            # The review state machine consumes only structured candidates and source requests.
            return sanitize(response.model_dump(include={"usage", "output", "tool_calls"}), self.credentials)
        result = await self.send("analysis", key, request, accept=accept, **kwargs)
        return Response.model_validate(result)

    async def ask(self, stage, key, *, instructions, context, schema, validate, effort="medium", token_budget=None,
                  timeout=None):
        supplied = sanitize(context, self.credentials)
        content = (evidence_bundle(supplied) if "sources" in supplied and "diff" in supplied
                   else json.dumps(supplied, ensure_ascii=False))
        session_id = hashlib.sha256(encode([self.run_id, stage, key]).encode()).hexdigest()
        request = Request(instructions=sanitize(instructions, self.credentials),
                          messages=[Message(role="user", content=content)], output_schema=schema_for_transport(schema),
                          effort=effort, max_output_tokens=16384, session_id=session_id)
        now = time.monotonic()
        stage_end = self.stage_deadlines.setdefault(stage, now + timeout if timeout else float("inf"))
        until = min(stage_end, self.deadline if self.deadline is not None and stage != "summary" else float("inf"),
                    now + self.connection.get("options", {}).get("timeout_sec", 300))

        def accept(response):
            try:
                if response.tool_calls:
                    raise ValueError("Unexpected tool call")
                return {"result": sanitize(validate(response.output), self.credentials)}
            except ValueError as exc:
                # Keep only bounded diagnostic fields, never a raw unvalidated model response.
                feedback = getattr(exc, "feedback", {}) or {}
                return {"invalid": True, "field": str(feedback.get("field", ""))[:256] if isinstance(feedback, dict) else ""}

        for attempt in range(2):
            task = key if attempt == 0 else key + ":repair"
            try:
                value = await self.send(stage, task, request, client=self.client, timeout_at=until,
                                        token_budget=token_budget, stopped=lambda: False, accept=accept)
            except InferenceError as exc:
                if attempt or exc.code != "malformed_response" or exc.consumption_unknown:
                    raise
                value = {"invalid": True}
            if "result" in value:
                return value["result"]
            if attempt == 0:
                repair = "Return the complete result conforming to the supplied schema, permitted paths and citation ranges."
                request = request.model_copy(update={"messages": [*request.messages, Message(role="user", content=repair
                    + (" Check field " + value["field"] if value.get("field") else ""))]})
        raise ValueError("The provider result failed the stage evidence contract after repair.")
