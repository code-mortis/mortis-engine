"""Bounded local execution, conservative token admission and validated outcomes."""

import asyncio
import json
import time
from uuid import uuid4

from mortis_engine import gateway, llm_http
from mortis_engine.llm_http import InferenceError

from . import engine
from .errors import ReviewStopped
from .planning import fingerprint


def execute(repo, env, plan, prepared, credentials, *, stopped=lambda: False, progress=lambda value: None,
            deadline=None):
    async def run():
        outcomes, usage = {}, []
        admission = asyncio.Lock()
        options = prepared[0].options if prepared else {}
        budget = options.get("token_budget")
        seconds = options.get("timeout_seconds")
        run_deadline = min(deadline if deadline is not None else float("inf"),
                           time.monotonic() + seconds if seconds else float("inf"))
        run_id = str(uuid4())
        held = 0

        def cancelled():
            return stopped() or time.monotonic() >= run_deadline

        def fail(batch, code):
            for unit in batch.units:
                if outcomes.get(unit.key, {}).get("status") != "completed":
                    outcomes[unit.key] = {"status": "failed", "error": code}

        async def attempt(batch, request, client, until):
            nonlocal held
            if cancelled():
                raise ReviewStopped("Review cancelled or its deadline expired")
            amount = gateway.reservation(batch.connection, request) + request.max_output_tokens
            async with admission:
                if budget is not None and held + amount > budget:
                    raise InferenceError("budget", "Analysis token budget cannot admit this request",
                                         consumption_unknown=False)
                held += amount
            record = {"task_key": batch.key, "reserved_tokens": amount, "usage": {},
                      "consumption_unknown": True, "status": "running"}
            usage.append(record)
            try:
                response = await gateway.send(batch.connection, credentials, request, client=client,
                    stopped=cancelled, timeout=until - time.monotonic())
            except InferenceError as exc:
                tokens = exc.usage.total_tokens
                record.update(status="failed", code=exc.code, usage=exc.usage.model_dump(),
                              consumption_unknown=exc.consumption_unknown)
                async with admission:
                    if tokens is not None and not exc.consumption_unknown:
                        held += tokens - amount
                    elif not exc.consumption_unknown:
                        held -= amount
                raise
            except BaseException:
                record.update(status="failed", code="interrupted")
                raise
            tokens = response.usage.total_tokens
            record.update(status="completed", usage=response.usage.model_dump(), consumption_unknown=tokens is None)
            async with admission:
                if tokens is not None:
                    held += tokens - amount
            return response

        async def send(batch, request, client):
            until = min(run_deadline, time.monotonic() + batch.connection.get("options", {}).get("timeout_sec", 120))
            for index in range(3):
                try:
                    return await attempt(batch, request, client, until)
                except InferenceError as exc:
                    known_zero = not exc.consumption_unknown and exc.usage.total_tokens in {None, 0}
                    delay = max(exc.retry_after or 0, 2 ** index)
                    if not (exc.retryable and known_zero and index < 2 and time.monotonic() + delay < until):
                        raise
                    await llm_http._bounded(asyncio.sleep(delay), cancelled, until - time.monotonic())
            raise AssertionError("Bounded retry loop exhausted")

        async def batch_run(batch, client, *, recovered=False, session_id=None):
            if plan.limit_reason or batch.error:
                fail(batch, batch.error or "file_limit")
                return
            session_id = session_id or fingerprint([run_id, batch.key])
            request = batch.request.model_copy(update={"session_id": session_id})
            sources, supporting, notices = list(batch.sources), [], []
            rounds, repairs = 0, 0
            candidate_draft = None
            candidate_diagnostics = None
            while True:
                try:
                    response = await send(batch, request, client)
                except InferenceError as exc:
                    if (exc.code == "output_limit" and exc.usage.total_tokens is not None
                            and not exc.consumption_unknown and not recovered):
                        for child in engine._recovery_batches(batch, request, sources, batch.key):
                            await batch_run(child, client, recovered=True, session_id=session_id)
                        return
                    code = ("cancelled" if stopped() else "deadline") if cancelled() else exc.code
                    fail(batch, code)
                    return
                except ReviewStopped:
                    raise
                except ValueError:
                    fail(batch, "required_context_limit")
                    return
                try:
                    if response.tool_calls:
                        if recovered or rounds >= engine.ROUNDS[batch.options["effort"]]:
                            fail(batch, "source_round_limit")
                            return
                        result, additional = await asyncio.to_thread(engine.read_sources, response.tool_calls,
                            repo=repo, env=env, plan=plan, options=batch.options, credentials=credentials,
                            authorize=lambda: None, stopped=cancelled)
                        rounds += 1
                        supporting.extend(additional)
                        sources.extend(additional)
                        notices.extend(result["selections"])
                        feedback = None
                    else:
                        result = engine.validate_result(response.output, batch, sources, credentials, cancelled)
                        outcomes.update(engine._completed_outcomes(result, batch))
                        return
                except ReviewStopped:
                    raise
                except ValueError as exc:
                    if recovered or (not response.tool_calls and repairs >= 1) or rounds >= engine.ROUNDS[batch.options["effort"]]:
                        fail(batch, "invalid_result")
                        return
                    feedback = engine._validation_feedback(exc)
                    rounds += 1
                    if not response.tool_calls:
                        repairs += 1
                if response.usage.total_tokens is None:
                    fail(batch, "usage_unknown")
                    return
                if feedback is not None and not response.tool_calls:
                    candidate_diagnostics = feedback
                    try:
                        candidate_draft = engine.context.sanitize(response.output, credentials, cancelled)
                    except ReviewStopped:
                        raise
                    except ValueError:
                        fail(batch, "repair_context_unavailable")
                        return
                    encoded = json.dumps(candidate_draft, ensure_ascii=False, allow_nan=False).encode()
                    if len(encoded) > engine.MAX_REPAIR_DRAFT_BYTES:
                        fail(batch, "repair_context_limit")
                        return
                request = engine._next_request(batch, supporting, notices,
                    remaining_source_rounds=engine.ROUNDS[batch.options["effort"]] - rounds,
                    allow_source_reads=rounds < engine.ROUNDS[batch.options["effort"]],
                    repair=feedback is not None, validation=feedback or candidate_diagnostics, candidate_draft=candidate_draft)
                request = request.model_copy(update={"session_id": session_id})

        async with gateway.http_client() as client:
            queue = asyncio.Queue()
            for batch in prepared:
                queue.put_nowait(batch)

            async def worker():
                while not queue.empty():
                    batch = queue.get_nowait()
                    try:
                        if cancelled():
                            fail(batch, "cancelled" if stopped() else "deadline")
                        else:
                            try:
                                await batch_run(batch, client)
                            except ReviewStopped:
                                fail(batch, "cancelled" if stopped() else "deadline")
                            except TimeoutError:
                                fail(batch, "deadline")
                            except (ValueError, OSError):
                                fail(batch, "source_unavailable")
                        progress({"phase": "analysis", "completed_units": sum(
                            value["status"] == "completed" for value in outcomes.values()),
                            "selected_units": len(plan.units)})
                    finally:
                        queue.task_done()

            workers = [asyncio.create_task(worker()) for _ in range(min(options.get("concurrency", 2), len(prepared)))]
            try:
                await asyncio.gather(*workers)
            finally:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
        tokens = None if any(item["consumption_unknown"] for item in usage) else sum(
            item["usage"].get("total_tokens") or 0 for item in usage)
        status, report, comments = engine._report(plan, prepared, outcomes, tokens=tokens)
        report["usage"] = usage
        report["summary"]["reserved_tokens"] = held
        return status, report, comments

    return asyncio.run(run())
