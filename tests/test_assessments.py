import asyncio
import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from mortis_engine import assessments, gateway, operations
from mortis_engine.history import History, encode
from mortis_engine.llm_http import InferenceError
from mortis_engine.review import source, workflows
from mortis_engine.review.context import source_id
from mortis_engine.review.service import effective_connection
from mortis_engine.review.settings import RepoSettings
from mortis_engine.schemas import Response, Usage
from mortis_engine.security_scans import service as security
from mortis_engine.workflow import Session

CONNECTION = {"kind": "api", "provider": "test", "protocol": "openai", "model": "fixture",
              "base_url": "https://example.com/v1", "options": {}}


def request_context(request):
    return json.JSONDecoder().raw_decode(request.messages[0].content.split("\n\n", 1)[1])[0]


@pytest.fixture(params=["review", "scan"])
def assessment_stage(request, local, monkeypatch):
    repo, _, _ = local
    paths = {name: f"{letter}.py" for name, letter in zip("LRCXDI", "abcdef", strict=True)}
    for name, path in paths.items():
        (repo / path).write_text(f"value = {name!r}\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "candidates")
    head = git(repo, "rev-parse", "HEAD")
    kind = request.param

    def response(inference_request):
        context = request_context(inference_request)

        def cite(path):
            identity = next(key for key, row in context["citation_sources"].items() if row["path"] == path)
            return {"source_id": identity, "start_line": 1, "end_line": 1}

        if "assigned_path" in context:
            path = context["assigned_path"]
            name = next((name for name, value in paths.items() if value == path), None)
            proposals = [] if name is None else [{"title": name, "path": path, "category": "authorization",
                "severity": "high", "explanation": "Fixture", "trigger": "Fixture", "impact": "Fixture",
                "remediation": "Fixture", "verification": "Fixture",
                "evidence": [cite(path), *([cite(paths["L"])] if name == "D" else [])]}]
            return None, {"findings": proposals, "required_paths": []}
        if kind == "review":
            name = context["finding"]["content"]
            output = {**decision(), "evidence": [cite(context["finding"]["path"])]}
        else:
            name = context["proposal"]["title"]
            output = {"verdict": "confirmed", "explanation": "Fixture", "finding_id": None,
                      "evidence": [cite(paths[name])], "required_paths": []}
        if name == "D" and context["history"]:
            output["finding_id"] = context["history"][0]["id"]
            if kind == "review":
                output.update(relationship="duplicate", history_explanation="Same fixture")
        return name, output

    async def run(session, progress, *, budget=None, continuous=True, max_output=None, concurrency=4):
        options = {"concurrency": concurrency, "assessment_token_budget": budget, "max_output_tokens": max_output}
        async with session:
            if kind == "scan":
                return await security.scan(repo, ref=head, session=session, options=options, scanner=lambda *args: {
                    "findings": [], "targets": {}, "problems": [], "metadata": {"database_fresh": True}},
                    progress=progress, continuous=continuous)
            snapshots = [{"path": paths["L" if name == "D" else name], "content": name, "start_line": 1,
                          "end_line": 1, "existing_code": f"value = {'L' if name == 'D' else name!r}",
                          "suggestion_code": ""} for name in paths]
            original = {"run_id": session.run_id, "base_sha": head, "head_sha": head, "status": "completed",
                        "findings": snapshots}
            with source.local_repository(repo, object_cache=session.history.objects) as (isolated, env):
                return await workflows.postprocess(isolated, env, original,
                    {**RepoSettings().model_dump(), **options, "review_mode": "all"}, session,
                    progress=progress, continuous=continuous)

    monkeypatch.setattr(gateway, "reservation",
                        lambda _, request: 0 if "Audit the assigned file" in request.instructions else 400)
    return repo, kind, run, response


@pytest.mark.parametrize("mode", ["complete", "budgeted", "interrupt", "legacy", "legacy-interrupt"])
def test_assessment_dispatch_and_resume(assessment_stage, monkeypatch, mode):
    repo, kind, run, response = assessment_stage
    legacy, interrupted = mode.startswith("legacy"), mode.endswith("interrupt")
    limits = {"budget": 5000, "max_output": 100} if mode == "budgeted" else {}

    async def exercise(history, run_id):
        started = {name: asyncio.Event() for name in "LRCXDI"}
        release = {name: asyncio.Event() for name in started}
        recorded = [asyncio.Event() for _ in range(7)]
        last_request = asyncio.Event()
        active, cancelled, sent = set(), set(), []
        peak = 0

        async def send(connection, credentials, request, **kwargs):
            nonlocal peak
            name, output = response(request)
            if name is not None:
                assert name not in sent
                sent.append(name)
                active.add(name)
                peak = max(peak, len(active))
                started[name].set()
                try:
                    await release[name].wait()
                except asyncio.CancelledError:
                    cancelled.add(name)
                    raise
                finally:
                    active.remove(name)
            return Response(output=output, usage=Usage(total_tokens=10))

        def progress(event):
            if event["phase"] == "assessment":
                recorded[event["assessed_candidates"]].set()
            if (event["phase"] == "connection_capacity" and event["active_requests"] == 1
                    and started["X"].is_set() and active == {"X"}):
                last_request.set()

        monkeypatch.setattr(gateway, "send", send)
        session = Session(CONNECTION, {}, history=history, run_id=run_id, progress=progress)
        async with asyncio.TaskGroup() as group:
            running = group.create_task(run(session, progress, continuous=not legacy, **limits))
            async with asyncio.timeout(10):
                for name in "LRCX":
                    await started[name].wait()
                release["R"].set()
                if legacy:
                    release["L"].set()
                    release["C"].set()
                    await last_request.wait()
                    assert not started["I"].is_set() and not recorded[1].is_set()
                    if interrupted:
                        running.cancel()
                    else:
                        release["X"].set()
                else:
                    await recorded[1].wait()
                    await started["I"].wait()
                    assert not started["D"].is_set() and active == set("LCXI")
                    release["L"].set()
                if not (legacy and interrupted):
                    await started["D"].wait()
                    if not legacy:
                        assert active == set("DCXI")
                    release["D"].set()
                    if interrupted:
                        await recorded[3].wait()
                        running.cancel()
                    else:
                        for event in release.values():
                            event.set()

        assert peak == 4 and not active
        if not interrupted:
            return running.result()
        with pytest.raises(asyncio.CancelledError):
            running.result()
        assert cancelled == ({"X"} if legacy else set("CXI"))
        assert history.row(run_id)["report"] is None
        usage = session.usage()
        assert usage["total_tokens"] is None
        assert sum(row["consumption_unknown"] for row in usage["requests"]) == len(cancelled)
        pinned = history.assessment_inputs(run_id)
        label = "content" if kind == "review" else "title"
        ids = {row["candidate"][label]: identity for identity, row in pinned.items()}
        if not legacy:
            assert ids["L"] in pinned[ids["D"]]["predecessors"]
        monkeypatch.setattr(gateway, "send", lambda *a, **k: pytest.fail("An assessment attempt was dispatched twice"))
        resumed = Session(CONNECTION, {}, history=history, run_id=run_id)
        result = await run(resumed, lambda event: None, continuous=not legacy)
        assert resumed.usage() == usage
        after = history.assessment_inputs(run_id)
        assert {identity: after[identity] for identity in pinned} == pinned
        return result

    with History(repo) as history:
        run_id = history.begin(kind, "main", {"triage_versions": {}})
        with history.claim(run_id):
            result = asyncio.run(exercise(history, run_id))
    candidates = result["candidates"]
    assert [row["position"] for row in candidates] == list(range(6))
    assert result["status"] == ("partial" if interrupted else "completed")
    if legacy and interrupted:
        assert [c["status"] for c in candidates] == ["completed"] * 3 + ["failed"] * 3
        assert len(result["findings"]) == 3
        return
    assert candidates[0]["result"] and candidates[4]["result"], [c.get("error") for c in candidates]
    assert candidates[0]["result"]["verdict"] == candidates[4]["result"]["verdict"] == "confirmed"
    shared_id = candidates[4]["result"]["finding_id"]
    assert shared_id and sum(row["id"] == shared_id for row in result["findings"]) == 1
    assert len(result["findings"]) == (2 if interrupted else 5)
    if kind == "scan":
        assert next(row["version"] for row in result["findings"] if row["id"] == shared_id) == 2
    else:
        assert candidates[0]["finding_id"] == shared_id


def test_assessment_dispatch_keeps_budget_reservations(assessment_stage, monkeypatch):
    repo, kind, run, response = assessment_stage

    async def exercise(session, budget):
        sent = []
        reserved = asyncio.Event()
        release = asyncio.Event()

        async def send(connection, credentials, request, **kwargs):
            name, output = response(request)
            if name is not None:
                sent.append(name)
                if len(sent) == 2:
                    reserved.set()
                await release.wait()
            return Response(output=output, usage=Usage(total_tokens=450 if name else 1))

        monkeypatch.setattr(gateway, "send", send)
        async with asyncio.TaskGroup() as group:
            running = group.create_task(run(session, lambda event: None, budget=budget, max_output=100))
            await asyncio.wait_for(reserved.wait(), 10)
            stage = "security_assessment" if kind == "scan" else "assessment"
            assert session.usage(stage)["reserved_tokens"] == 1000
            release.set()
        result = running.result()
        assert result["status"] == "partial"
        assert sent == ["L", "R"]
        assert sum(row["status"] == "failed" for row in result["candidates"]) == 4
        assert session.usage(stage)["total_tokens"] == 900

    with History(repo) as history:
        limits = [(1000, None), (None, 1007)] if kind == "scan" else [(1000, None)]
        for budget, scan_budget in limits:
            run_id = history.begin(kind, "main", {"triage_versions": {}})
            with history.claim(run_id):
                session = Session(CONNECTION, {}, history=history, run_id=run_id, scan_budget=scan_budget)
                asyncio.run(exercise(session, budget))


def test_assessment_retry_releases_slot_for_independent_work(assessment_stage, monkeypatch):
    repo, kind, run, response = assessment_stage
    sleep = asyncio.sleep

    async def exercise(session):
        running_other, backoff, replacement, release = (asyncio.Event() for _ in range(4))
        sent = []

        async def delay(seconds):
            if seconds == 31:
                backoff.set()
                await release.wait()
            else:
                await sleep(seconds)

        async def send(connection, credentials, request, **kwargs):
            name, output = response(request)
            if name is not None:
                sent.append(name)
            if name == "L" and sent.count("L") == 1:
                await running_other.wait()
                raise InferenceError("rate_limit", "Mock rejection", retryable=True, retry_after=31,
                                     consumption_unknown=False, usage=Usage(total_tokens=0))
            if name == "R":
                running_other.set()
                await release.wait()
            if name == "C":
                replacement.set()
                await release.wait()
            return Response(output=output, usage=Usage(total_tokens=10))

        monkeypatch.setattr(asyncio, "sleep", delay)
        monkeypatch.setattr(gateway, "send", send)
        async with asyncio.TaskGroup() as group:
            running = group.create_task(run(session, lambda event: None, concurrency=2))
            await asyncio.wait_for(backoff.wait(), 5)
            await asyncio.wait_for(replacement.wait(), 5)
            assert sent == ["L", "R", "C"]
            assert session.active_requests == 2
            release.set()
        assert sent.count("L") == 2 and running.result()["status"] == "completed"

    with History(repo) as history:
        run_id = history.begin(kind, "main", {"triage_versions": {}})
        with history.claim(run_id):
            asyncio.run(exercise(Session(CONNECTION, {}, history=history, run_id=run_id)))


@pytest.mark.parametrize("assessment_stage", ["scan"], indirect=True)
@pytest.mark.parametrize(("budget", "scan_budget", "cancel"), [(1000, None, False), (None, 1007, False),
                                                               (1000, None, True)])
def test_scan_assessment_waits_for_temporary_reservation(assessment_stage, monkeypatch, budget, scan_budget, cancel):
    repo, _, run, response = assessment_stage

    async def exercise(session):
        release_slow, checking_next, cheap_settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sent = []

        def reservation(_connection, request):
            candidate = request_context(request).get("proposal", {}).get("title")
            if candidate == "C":
                checking_next.set()
            return {None: 0, "R": 50, "X": 50}.get(candidate, 700)

        async def send(connection, credentials, request, **kwargs):
            name, output = response(request)
            if name is None:
                output["findings"] = [row for row in output["findings"] if row["title"] in "LRCX"]
            else:
                sent.append(name)
                if name == "L":
                    await release_slow.wait()
            return Response(output=output, usage=Usage(total_tokens=10 if name else 1))

        monkeypatch.setattr(gateway, "reservation", reservation)
        monkeypatch.setattr(gateway, "send", send)

        def progress(event):
            if event["phase"] == "assessment" and event["assessed_candidates"] == 2:
                cheap_settled.set()

        async with asyncio.TaskGroup() as group:
            running = group.create_task(run(session, progress, budget=budget, max_output=100, concurrency=2))
            await asyncio.wait_for(checking_next.wait(), 10)
            await asyncio.wait_for(cheap_settled.wait(), 10)
            assert session.active_requests == 1
            assert sent == ["L", "R", "X"]
            if cancel:
                running.cancel()
            else:
                release_slow.set()
        ledger = session.usage("security_assessment")
        if cancel:
            assert running.cancelled() and session.active_requests == 0
            assert sent == ["L", "R", "X"] and ledger["reserved_tokens"] == 820
            assert ledger["total_tokens"] is None and len(ledger["requests"]) == 3
        else:
            assert sent == ["L", "R", "X", "C"] and ledger["total_tokens"] == 40
            assert running.result()["status"] == "completed" and running.result()["grade"] == "D"

    with History(repo) as history:
        run_id = history.begin("scan", "main", {"triage_versions": {}})
        with history.claim(run_id):
            asyncio.run(exercise(Session(CONNECTION, {}, history=history, run_id=run_id, scan_budget=scan_budget)))


@pytest.mark.parametrize("assessment_stage", ["scan"], indirect=True)
def test_legacy_scan_replays_assessment_created_from_cached_analysis(assessment_stage, monkeypatch):
    repo, _, run, response = assessment_stage
    checked = []

    class Pause(Exception):
        pass

    async def send(connection, credentials, request, **kwargs):
        name, output = response(request)
        if name == "D":
            context = request_context(request)
            assert list(context["proposal"]) == sorted(context["proposal"])
            prior = context["history"][0]["assessment"]
            assert list(prior) == sorted(prior)
        checked.append(name)
        return Response(output=output, usage=Usage(total_tokens=10))

    def pause_after(count):
        def progress(event):
            if event["phase"] == "assessment" and event["assessed_candidates"] == count:
                raise Pause
        return progress

    monkeypatch.setattr(gateway, "send", send)
    with History(repo) as history:
        run_id = history.begin("scan", "main", {"triage_versions": {}})
        with history.claim(run_id):
            def session():
                return Session(CONNECTION, {}, history=history, run_id=run_id)

            # Simulate the old scanner which rendered values from SQLite in their saved key order.
            passthrough = SimpleNamespace(model_validate=lambda value, **kwargs: SimpleNamespace(model_dump=lambda: value))
            with monkeypatch.context() as old:
                old.setattr(security, "SecurityAnalysis", passthrough)
                old.setattr(security, "SecurityAssessment", passthrough)
                for assessed in (0, 4, 5):
                    with pytest.raises(Pause):
                        asyncio.run(run(session(), pause_after(assessed), continuous=False))
            assert sorted(name for name in checked if name) == sorted("LRCXDI")
            original_attempts = history.attempts(run_id)
            original_inputs = history.assessment_inputs(run_id)

            monkeypatch.setattr(gateway, "send", lambda *args, **kwargs: pytest.fail("Saved request was sent again"))
            result = asyncio.run(run(session(), lambda event: None, continuous=False))
            assert history.attempts(run_id) == original_attempts
            assert history.assessment_inputs(run_id) == original_inputs
    assert result["status"] == "completed"
    assert len(result["findings"]) == 5 and all(row["status"] == "completed" for row in result["candidates"])


def git(repo, *args):
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Test", "-c",
                           "user.email=test@localhost", "-c", "commit.gpgsign=false", *args],
                          cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "app.py").write_text("def inverse(value):\n    return value\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("def inverse(value):\n    return 1 / value\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "head")
    return repo, base, git(repo, "rev-parse", "HEAD")


def decision(verdict="confirmed"):
    return {"verdict": verdict, "relationship": "new", "finding_id": None,
            "explanation": "Zero is allowed and raises division by zero", "cause": "Division lacks guard",
            "trigger": "Call with zero", "consequence": "Caller crashes", "history_explanation": "",
            "contradicts_prior": False, "changed_conditions": "", "suggestion_supported": False,
            "evidence": [{"source_id": source_id("current", "app.py"), "start_line": 2, "end_line": 2}],
            "required_paths": []}


def test_duplicate_review_candidates_have_distinct_durable_attempts(local, monkeypatch):
    repo, base, head = local
    candidate = {"path": "app.py", "start_line": 2, "end_line": 2, "content": "Zero crashes division",
                 "existing_code": "    return 1 / value", "source_sha": head}
    options = {**RepoSettings().model_dump(), "_settings_sha": base,
               "_assessment_task_key_policy": workflows.ASSESSMENT_TASK_KEY_POLICY}
    prior_counts = []

    async def send(connection, credentials, request, **kwargs):
        prior_counts.append(len(request_context(request)["prior_candidates"]))
        return Response(output={**decision("rejected"), "required_base_paths": []}, usage=Usage(total_tokens=7))

    monkeypatch.setattr(gateway, "send", send)
    with History(repo) as history, source.local_repository(repo, object_cache=history.objects) as (isolated, env):
        run_id = history.begin("review", "main", {"triage_versions": {}})
        report = {"run_id": run_id, "head_sha": head, "base_sha": base, "status": "completed",
                  "findings": [candidate, candidate.copy()]}

        def review():
            session = Session(effective_connection(CONNECTION, options), {}, history=history, run_id=run_id)
            return asyncio.run(workflows.postprocess(isolated, env, report, options, session))

        result = review()
        assert result["status"] == "completed"
        assert [row["result"]["verdict"] for row in result["candidates"]] == ["rejected", "rejected"]
        keys = [row["id"] for row in result["candidates"]]
        assert set(history.attempts(run_id)) == {("assessment", f"{key}:0") for key in keys}
        assert [history.assessment_input(run_id, key)["task_key"] for key in keys] == keys

        monkeypatch.setattr(gateway, "send", lambda *args, **kwargs: pytest.fail("Saved request was sent again"))
        assert review() == result

    assert prior_counts == [0, 1]


def failed_review(local, monkeypatch, *, unknown=False, task_key_policy=workflows.ASSESSMENT_TASK_KEY_POLICY):
    repo, base, head = local
    snapshot = {"path": "app.py", "start_line": 2, "end_line": 2, "content": "Zero crashes division",
                "existing_code": "    return 1 / value", "suggestion_code": "unsafe suggestion", "source_sha": head,
                "severity": "high", "category": "correctness"}
    options = {**RepoSettings().model_dump(), "_settings_sha": base, "_assessment_task_key_policy": task_key_policy}

    async def fail(*args, **kwargs):
        raise InferenceError("fixture", "Original failure", consumption_unknown=unknown, usage=Usage(total_tokens=None if unknown else 7))

    monkeypatch.setattr(gateway, "send", fail)
    with History(repo) as history, source.local_repository(repo, object_cache=history.objects) as (isolated, env):
        run_id = history.begin("review", "main", {"triage_versions": {}, "profile": "fixture"})
        session = Session(effective_connection(CONNECTION, options), {}, history=history, run_id=run_id)
        report = {"schema_version": "mortis.review/v1", "run_id": run_id, "head_sha": head, "base_sha": base,
                  "settings_sha": base, "settings": options, "status": "partial", "findings": [],
                  "coverage": {"selected": [{"path": "app.py"}], "completed": [{"path": "app.py"}]}}
        with pytest.raises(InferenceError):
            asyncio.run(workflows._assess(isolated, env, workflows._run(report, options, session), snapshot,
                                         {"findings": [], "prior_candidates": []}, session, candidate_id="candidate-fixture"))
        report["candidates"] = [{"id": "candidate-fixture", "snapshot": snapshot, "status": "failed",
                                 "error": "Original failure", "result": None}]
        report["usage"] = session.usage()
        history.finish(run_id, report)
    return run_id


@pytest.mark.parametrize("verdict", ["confirmed", "rejected", "uncertain"])
def test_retry_review_exact_input_idempotency_and_immutable_origin(local, monkeypatch, verdict):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    with History(repo) as history:
        original = history.row(parent)["report"]
        plan = history.row(parent)["plan"]
        value = history.assessment_inputs(parent)["candidate-fixture"]
    assert value["schema_version"] == "mortis.assessment-input/v1"
    assert assessments.list_assessments(repo, run_id=parent)["candidates"][0]["retry_eligible"]
    monkeypatch.setattr(History, "assessment_inputs", lambda *args: pytest.fail("Retry loaded every candidate"))
    sent = []

    async def send(connection, credentials, request, **kwargs):
        sent.append(request)
        assert "adjudicate" in request.instructions.lower()
        return Response(output=decision(verdict), usage=Usage(total_tokens=11))

    monkeypatch.setattr(gateway, "send", send)
    (repo / "app.py").write_text("the current worktree must never be evidence")
    result = operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture",
                                          connection=CONNECTION, get_credentials=lambda: {})
    assert result["schema_version"] == "mortis.assessment/v1"
    assert result["status"] == "completed" and result["result"]["verdict"] == verdict
    assert result["usage"]["total_tokens"] == 11 and result["lineage_usage"]["total_tokens"] == 18
    assert bool(result["finding"]) == (verdict == "confirmed")
    again = operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture",
                                       connection=CONNECTION, get_credentials=lambda: pytest.fail("credentials read"))
    assert again == result and len(sent) == 1
    with History(repo) as history:
        assert history.row(parent)["report"] == original and history.row(parent)["plan"] == plan
        assert history.report(result["run_id"]) == result
    current = operations.list_findings(repo, run_id=parent)["findings"]
    assert len(current) == (1 if verdict == "confirmed" else 0)
    if current:
        assert current[0]["id"] == result["finding"]["id"] and current[0]["decision"] == "open"


def test_unknown_original_usage_and_changed_connection_block_before_credentials(local, monkeypatch):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch, unknown=True)
    row = operations.list_assessments(repo, run_id=parent)["candidates"][0]
    assert not row["retry_eligible"] and "Unknown" in row["blocked_reason"]
    with pytest.raises(ValueError, match="Unknown"):
        operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                    get_credentials=lambda: pytest.fail("credentials read"))
    parent = failed_review(local, monkeypatch)
    with pytest.raises(ValueError, match="connection changed"):
        operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture",
                                    connection={**CONNECTION, "model": "changed"},
                                    get_credentials=lambda: pytest.fail("credentials read"))


@pytest.mark.parametrize("failure", ["redaction", "identifier", "credentials"])
def test_retry_preflight_failure_does_not_create_child_run(local, monkeypatch, failure):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    with History(repo) as history:
        original = dict(history.row(parent))
    monkeypatch.setattr(gateway, "send", lambda *args, **kwargs: pytest.fail("Dispatched withheld retry"))

    def get_credentials():
        if failure == "credentials":
            raise ValueError("Fixture credentials unavailable")
        return {"api_key": "app.py" if failure == "identifier" else "return 1 / value"}

    error = {"redaction": "safe redaction", "identifier": "structural identifier", "credentials": "credentials unavailable"}
    for _ in range(2):
        with pytest.raises(ValueError, match=error[failure]):
            operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture",
                                        connection=CONNECTION, get_credentials=get_credentials)
        with History(repo) as history:
            assert assessments._children(history, parent, "candidate-fixture") == []
            assert dict(history.row(parent)) == original
        assert operations.list_assessments(repo, run_id=parent)["candidates"][0]["retry_eligible"]


def test_unknown_retry_lineage_cannot_be_bypassed_by_a_new_child(local, monkeypatch):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    with History(repo) as history:
        child = history.begin("assessment", "main", {"parent_run_id": parent, "candidate_id": "candidate-fixture"})
        history.start_attempt(child, {"stage": "assessment", "task_key": "lost", "reserved_tokens": 200,
                              "status": "running", "usage": {}, "consumption_unknown": True}, "input")
        history.finish(child, {"schema_version": "mortis.assessment/v1", "status": "failed"})
    with pytest.raises(ValueError, match="Unknown"):
        operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                    get_credentials=lambda: pytest.fail("credentials read"))


@pytest.mark.parametrize("task_key_policy", [None, workflows.ASSESSMENT_TASK_KEY_POLICY])
def test_retry_resumes_cached_assessment_without_dispatching_twice(local, monkeypatch, task_key_policy):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch, task_key_policy=task_key_policy)
    sent = []

    async def send(*args, **kwargs):
        sent.append(1)
        return Response(output=decision(), usage=Usage(total_tokens=11))

    monkeypatch.setattr(gateway, "send", send)
    real_finding = assessments._finding
    monkeypatch.setattr(assessments, "_finding", lambda *args: (_ for _ in ()).throw(RuntimeError("interrupted after validation")))
    with pytest.raises(RuntimeError, match="interrupted"):
        operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                    get_credentials=lambda: {})
    with History(repo) as history:
        child = assessments._children(history, parent, "candidate-fixture")[0]["id"]
        pinned = history.assessment_input(parent, "candidate-fixture")
        key = "candidate-fixture" if task_key_policy else workflows.planning.fingerprint(pinned["candidate"])
        assert pinned["task_key"] == key
        assert list(history.attempts(child)) == [("assessment", f"{key}:0")]
    monkeypatch.setattr(assessments, "_finding", real_finding)
    result = operations.retry_assessment(repo, resume=child, connection=CONNECTION, get_credentials=lambda: {})
    assert result["status"] == "completed" and result["run_id"] == child and sent == [1]


def test_candidate_claim_and_stale_triage_block_replay(local, monkeypatch):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    claim = hashlib.sha256(encode([parent, "candidate-fixture"]).encode()).hexdigest()[:32]
    with History(repo) as history, history.claim(claim):
        with pytest.raises(ValueError, match="owns"):
            operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                        get_credentials=lambda: pytest.fail("credentials read"))
    with History(repo) as history, history.db:
        history.db.execute("INSERT INTO triage_events VALUES ('review','main','other',1,?,'open','dismissed','reason',1)", (parent,))
    with pytest.raises(ValueError, match="decisions changed"):
        operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                    get_credentials=lambda: pytest.fail("credentials read"))


def test_failed_retry_preserves_cost_and_allows_new_known_attempt(local, monkeypatch):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    result = operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                        get_credentials=lambda: {})
    assert result["status"] == "failed" and result["usage"]["total_tokens"] == 7
    assert result["lineage_usage"]["total_tokens"] == 14
    assert operations.list_assessments(repo, run_id=parent)["candidates"][0]["retry_eligible"]


@pytest.mark.parametrize("dispatched", [False, True])
def test_retry_oserror_finishes_child_and_preserves_unknown_usage(local, monkeypatch, dispatched):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    with History(repo) as history:
        original = dict(history.row(parent))

    async def fail(*args, **kwargs):
        raise OSError("Cannot read fixture-private-token")

    monkeypatch.setattr(gateway if dispatched else assessments, "send" if dispatched else "_execute", fail)
    result = operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture",
                                        connection=CONNECTION, get_credentials=lambda: {"api_key": "fixture-private-token"})
    assert result["status"] == "failed" and result["result"] is None and result["finding"] is None
    assert "Cannot read" in result["error"] and "fixture-private-token" not in result["error"]
    with History(repo) as history:
        assert history.report(result["run_id"]) == result
        assert dict(history.row(parent)) == original
    candidate = operations.list_assessments(repo, run_id=parent)["candidates"][0]
    assert candidate["retry_eligible"] is not dispatched
    if dispatched:
        assert result["usage"]["total_tokens"] is None and result["usage"]["reserved_tokens"] > 0
        assert "Unknown token consumption" in candidate["blocked_reason"]
        with pytest.raises(ValueError, match="Unknown token consumption"):
            operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                        get_credentials=lambda: pytest.fail("credentials read"))
    else:
        assert result["usage"]["total_tokens"] == 0
        async def succeed(*args, **kwargs):
            return decision("rejected")
        monkeypatch.setattr(assessments, "_execute", succeed)
        again = operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture",
                                           connection=CONNECTION, get_credentials=lambda: {})
        assert again["status"] == "completed" and again["run_id"] != result["run_id"]


def test_decision_conflict_retains_validated_retry_result_without_publishing(local, monkeypatch):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    with History(repo) as history:
        other = history.begin("review", "main", {})
        history.finish(other, {"findings": [{"id": "other", "status": "open"}]})

    async def send(*args, **kwargs):
        operations.triage(repo, run_id=other, finding_id="other", state="dismissed", version=0, reason="Changed")
        return Response(output=decision(), usage=Usage(total_tokens=11))

    monkeypatch.setattr(gateway, "send", send)
    result = operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture",
                                         connection=CONNECTION, get_credentials=lambda: {})
    assert result["status"] == "failed" and result["finding"] is None
    assert result["result"]["verdict"] == "confirmed" and "decisions changed" in result["error"]
    with History(repo) as history:
        assert history.report(result["run_id"]) == result
    assert all(f["id"] == "other" for f in operations.list_findings(repo, run_id=parent)["findings"])


def test_legacy_failed_candidate_has_explicit_blocker(local):
    repo, _, head = local
    with History(repo) as history:
        parent = history.begin("review", "main", {})
        history.finish(parent, {"head_sha": head, "findings": [], "candidates": [{"id": "legacy", "snapshot": {"path": "app.py"},
                                                                               "status": "failed", "error": "failed"}]})
    assert "predates" in operations.list_assessments(repo, run_id=parent)["candidates"][0]["blocked_reason"]


def test_scan_failed_candidate_retries_without_scanner_or_analysis(local, monkeypatch):
    repo, _, head = local
    calls = []

    async def initial(connection, credentials, request, **kwargs):
        if "Audit the assigned file" in request.instructions:
            proposal = {"title": "Authorization bypass", "category": "authorization", "severity": "high", "path": "app.py",
                        "explanation": "Missing guard", "trigger": "Zero", "impact": "Crash", "remediation": "Guard input",
                        "verification": "Inspect callers", "evidence": [{"source_id": "source_1", "start_line": 1, "end_line": 2}]}
            return Response(output={"findings": [proposal], "required_paths": []}, usage=Usage(total_tokens=5))
        raise InferenceError("fixture", "Known failed assessment", consumption_unknown=False, usage=Usage(total_tokens=7))

    monkeypatch.setattr(gateway, "send", initial)
    options = security.ScanOptions().model_dump()
    with History(repo) as history:
        parent = history.begin("scan", "main", {"triage_versions": {}, "profile": "fixture"})
        session = Session(effective_connection(CONNECTION, options), {}, history=history, run_id=parent)
        result = asyncio.run(security.scan(repo, ref=head, session=session, scanner=lambda *args: {
            "findings": [], "targets": {}, "problems": [], "metadata": {"database_fresh": True}}))
        result["run_id"] = parent
        history.finish(parent, result)
        original = history.row(parent)["report"]
    candidate = operations.list_assessments(repo, run_id=parent)["candidates"][0]
    assert candidate["status"] == "failed" and candidate["retry_eligible"]

    async def retry(connection, credentials, request, **kwargs):
        calls.append(request.instructions)
        assert "Audit the assigned file" not in request.instructions
        return Response(output={"verdict": "confirmed", "finding_id": None, "explanation": "The supplied code confirms the issue",
            "evidence": [{"source_id": "source_1", "start_line": 1, "end_line": 2}], "required_paths": []}, usage=Usage(total_tokens=13))

    monkeypatch.setattr(gateway, "send", retry)
    monkeypatch.setattr(security, "scan", lambda *args, **kwargs: pytest.fail("Repeated scan"))
    retried = operations.retry_assessment(repo, run_id=parent, candidate_id=candidate["candidate_id"],
                                         connection=CONNECTION, get_credentials=lambda: {})
    assert retried["status"] == "completed" and retried["finding"]["kind"] == "code" and len(calls) == 1
    with History(repo) as history:
        assert history.row(parent)["report"] == original


def test_retention_excludes_secret_binary_and_user_excluded_blobs(local, monkeypatch):
    repo, base, _ = local
    (repo / ".env").write_text("TOKEN=fixture-secret-never-retain\n")
    (repo / "private.key").write_text("fixture-key-never-retain\n")
    (repo / "data.bin").write_bytes(b"binary\x00fixture")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "excluded fixtures")
    head = git(repo, "rev-parse", "HEAD")
    parent = failed_review((repo, base, head), monkeypatch)
    with History(repo) as history:
        value = history.assessment_inputs(parent)["candidate-fixture"]
        assert "retry_blocked_reason" not in value
        check_env = {"PATH": "/usr/bin:/bin", "GIT_OBJECT_DIRECTORY": str(history.objects)}
        for path in (".env", "private.key", "data.bin"):
            blob = git(repo, "rev-parse", head + ":" + path)
            assert blob not in value["retained_objects"]
            with pytest.raises(ValueError):
                source.git(repo, check_env, "cat-file", "-e", blob)


def test_scan_grade_counts_accepted_risk_after_decision_overlay(local, monkeypatch):
    repo, _, head = local
    scope = git(repo, "symbolic-ref", "HEAD")
    with History(repo) as history, history.db:
        history.db.execute("INSERT INTO triage_events VALUES ('scan',?,'risk',1,'origin','open','accepted_risk','temporary',1)", (scope,))

    async def scan(*args, **kwargs):
        return {"schema_version": "mortis.security/v1", "status": "completed", "grade": "A", "commit": head,
                "findings": [{"id": "risk", "kind": "code", "severity": "high", "state": "resolved"}]}

    monkeypatch.setattr(security, "scan", scan)
    result = operations.run(repo, kind="scan", connection=CONNECTION, get_credentials=lambda: {})
    assert result["findings"][0]["state"] == "accepted_risk" and result["grade"] == "D"


def test_missing_retained_object_blocks_listing_and_execution(local, monkeypatch):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    with History(repo) as history, history.db:
        value = history.assessment_inputs(parent)["candidate-fixture"]
        value["retained_objects"].append("0" * 40)
        history.db.execute("UPDATE assessment_inputs SET input=? WHERE run=?", (encode(value), parent))
    row = operations.list_assessments(repo, run_id=parent)["candidates"][0]
    assert not row["retry_eligible"] and "evidence is unavailable" in row["blocked_reason"]
    with pytest.raises(ValueError, match="evidence is unavailable"):
        operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                    get_credentials=lambda: pytest.fail("credentials read"))
