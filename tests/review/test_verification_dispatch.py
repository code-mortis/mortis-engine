"""Offline dispatch checks through real evidence validation and durable provider attempts."""

import asyncio
import copy
import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from mortis_engine import gateway, operations
from mortis_engine.history import History
from mortis_engine.llm_http import InferenceError
from mortis_engine.review import source, workflows
from mortis_engine.review.settings import RepoSettings
from mortis_engine.schemas import Response, Usage
from mortis_engine.security_scans import advisories
from mortis_engine.security_scans import service as security
from mortis_engine.workflow import Session

from .git_helpers import commit
from .test_workflows import candidate

CONNECTION = {"kind": "api", "provider": "test", "protocol": "openai", "model": "fixture",
              "base_url": "https://example.com/v1", "options": {}}


def request_context(request):
    return json.JSONDecoder().raw_decode(request.messages[0].content.split("\n\n", 1)[1])[0]


@pytest.fixture(params=["verify", "review", "scan"])
def verification_stage(request, repo, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path.parent / ("state-" + tmp_path.name)))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path.parent / ("config-" + tmp_path.name)))
    paths = [f"issue_{index}.py" for index in range(7)]
    base = commit(repo, {**dict.fromkeys(paths, "def inverse(value):\n    return 1 / value\n"),
                         "caller.py": "inverse(0)\n", "mortis.toml": "[review]\nconcurrency = 4\n"})
    head = commit(repo, dict.fromkeys(paths, "def inverse(value):\n    return 1 / value if value else 0\n"))
    findings = [{**candidate(base), "path": path, "id": f"issue-{index}", "status": "open"}
                for index, path in enumerate(paths)]
    kind = request.param
    if kind == "scan":
        findings = [{**finding, "kind": "code", "title": finding["id"], "fingerprint": finding["id"],
                     "state": "open", "version": 1, "evidence": []} for finding in findings]
    original = {"head_sha": base, "settings_sha": base, "findings": findings,
                "schema_version": "mortis.security/v1" if kind == "scan" else "mortis.review/v1"}

    def response(request, *, required=False):
        context = request_context(request)
        if "assigned_path" in context:
            return None, {"findings": [], "required_paths": []}
        index = int(context["finding"]["path"].removeprefix("issue_").removesuffix(".py"))
        citations = [{"source_id": identity, "start_line": 2, "end_line": 2}
                     for identity, row in context["citation_sources"].items()
                     if row["path"] == paths[index]]
        output = {"verdict": "fixed", "original_issue_present": True, "explanation": f"Fixed {index}",
                  "change_explanation": "Zero guard removes the original division trigger.", "evidence": citations}
        if kind == "scan":
            output["required_paths"] = ["caller.py"] if required else []
        else:
            output.update(dismissal_recommended=False, required_sources=[
                {"revision": "current", "path": "caller.py"}] if required else [])
        if required:
            output["verdict"] = "uncertain"
        return index, output

    async def run(session, *, scanner=None, **limits):
        options = {"concurrency": 4, **limits}
        async with session:
            if kind == "scan":
                return await security.scan(repo, ref=head, session=session, options=options, previous=original,
                    scanner=scanner or (lambda *args: {"findings": [], "targets": {}, "problems": [],
                                          "metadata": {"database_fresh": True}}))
            with source.local_repository(repo, object_cache=session.history.objects) as (isolated, env):
                options = {**RepoSettings().model_dump(), **options}
                if kind == "verify":
                    return await workflows.verify(isolated, env, original, head, options, session)
                return await workflows.postprocess(isolated, env,
                    {"base_sha": base, "head_sha": head, "status": "completed", "findings": [], "coverage": {}},
                    {**options, "auto_resolve": True}, session, history=[original])

    def rows(report):
        return report["verification"]["verifications"] if kind == "review" else report["verifications"]

    return repo, kind, run, response, rows, original, head


@pytest.mark.parametrize(("cancel", "budget"), [(False, None), (True, None), (False, 5000)])
def test_continuous_verification_order_cancellation_and_resume(verification_stage, monkeypatch, cancel, budget):
    repo, kind, run, response, rows, original, _ = verification_stage
    original_copy = copy.deepcopy(original)

    async def exercise(history, run_id):
        started = [asyncio.Event() for _ in range(7)]
        release = [asyncio.Event() for _ in started]
        active, cancelled, sent = set(), set(), []
        peak = 0

        async def send(connection, credentials, request, **kwargs):
            nonlocal peak
            index, output = response(request)
            if index is not None:
                assert index not in sent
                sent.append(index)
                active.add(index)
                peak = max(peak, len(active))
                assert len(active) <= 4
                if budget is not None:
                    stage = "security_verification" if kind == "scan" else "verification"
                    assert session.usage(stage)["reserved_tokens"] <= budget
                started[index].set()
                try:
                    await release[index].wait()
                except asyncio.CancelledError:
                    cancelled.add(index)
                    raise
                finally:
                    active.remove(index)
            return Response(output=output, usage=Usage(total_tokens=10))

        monkeypatch.setattr(gateway, "send", send)
        monkeypatch.setattr(gateway, "reservation", lambda *args: 400)
        session = Session(CONNECTION, {}, history=history, run_id=run_id)
        limits = {"verification_token_budget": budget, "max_output_tokens": 100} if budget else {}
        async with asyncio.TaskGroup() as group:
            running = group.create_task(run(session, **limits))
            async with asyncio.timeout(10):
                for index in range(4):
                    await started[index].wait()
                assert active == {0, 1, 2, 3}
                release[1].set()
                await started[4].wait()
                assert active == {0, 2, 3, 4}
                release[4].set()
                await started[5].wait()
                assert active == {0, 2, 3, 5}
                if cancel:
                    running.cancel()
                else:
                    for event in release:
                        event.set()
        assert peak == 4 and not active and session.active_requests == session.waiting_requests == 0
        if cancel:
            assert running.cancelled() and cancelled == {0, 2, 3, 5}
            assert not started[6].is_set() and history.row(run_id)["report"] is None
        else:
            assert not cancelled
            completed = running.result()
            assert completed["status"] == "completed"
        attempts = history.attempts(run_id)
        ledger = session.usage()
        if cancel:
            assert ledger["total_tokens"] is None
            assert sum(row["consumption_unknown"] for row in ledger["requests"]) == 4

        monkeypatch.setattr(gateway, "send", lambda *a, **k: pytest.fail("A saved or unknown attempt was sent again"))
        resumed = Session(CONNECTION, {}, history=history, run_id=run_id)
        result = await run(resumed, **limits)
        assert resumed.usage() == ledger and history.attempts(run_id) == attempts
        assert [row["finding_id"] for row in rows(result)] == [finding["id"] for finding in original["findings"]]
        assert [row["verdict"] for row in rows(result)] == [
            "fixed" if not cancel or index in {1, 4} else "uncertain" for index in range(7)]
        assert result["status"] == ("partial" if cancel else "completed")
        if not cancel:
            assert {key: value for key, value in result.items() if key != "generated_at"} == {
                key: value for key, value in completed.items() if key != "generated_at"}
        if kind == "scan":
            assert [row["id"] for row in result["findings"]] == [row["id"] for row in original["findings"]]
            assert all(row["state"] == ("open" if cancel else "resolved") for row in result["findings"])
        if kind == "review":
            assert [row["status"] for row in result["history"]] == [
                "resolved" if not cancel or index in {1, 4} else "open" for index in range(7)]

    with History(repo) as history:
        run_id = history.begin(kind, "main", {"triage_versions": {}})
        with history.claim(run_id):
            asyncio.run(exercise(history, run_id))
    assert original == original_copy


def test_verification_resume_finishes_saved_evidence_rounds(verification_stage, monkeypatch):
    repo, kind, run, response, rows, _, _ = verification_stage

    class Pause(BaseException):
        pass

    async def exercise(history, run_id):
        def pause(event):
            stage = "security_verification" if kind == "scan" else "verification"
            if event["phase"] == "connection_capacity" and event["active_requests"] == 0:
                if any(row["stage"] == stage and row["status"] == "completed"
                       for _, row, _ in history.attempts(run_id).values()):
                    raise Pause

        async def first(connection, credentials, request, **kwargs):
            _, output = response(request, required=True)
            return Response(output=output, usage=Usage(total_tokens=10))

        monkeypatch.setattr(gateway, "send", first)
        session = Session(CONNECTION, {}, history=history, run_id=run_id, progress=pause)
        with pytest.raises(Pause):
            await run(session, concurrency=1)
        saved = history.attempts(run_id)
        assert any(row["status"] == "completed" for _, row, _ in saved.values())
        sent = []

        async def followup(connection, credentials, request, **kwargs):
            context = request_context(request)
            index, output = response(request)
            if index == 0:
                assert any(row["path"] == "caller.py" and row["revision"] == "current"
                           for row in context["citation_sources"].values())
            sent.append(index)
            return Response(output=output, usage=Usage(total_tokens=10))

        monkeypatch.setattr(gateway, "send", followup)
        result = await run(Session(CONNECTION, {}, history=history, run_id=run_id))
        assert result["status"] == "completed"
        assert [row["verdict"] for row in rows(result)] == ["fixed"] * 7
        assert sent.count(0) == 1
        assert all(history.attempts(run_id)[key] == value for key, value in saved.items())

    with History(repo) as history:
        run_id = history.begin(kind, "main", {"triage_versions": {}})
        with history.claim(run_id):
            asyncio.run(exercise(history, run_id))


@pytest.mark.parametrize("verification_stage", ["verify"], indirect=True)
def test_standalone_verify_entry_point_replays_saved_report(verification_stage, monkeypatch):
    repo, _, _, response, _, original, head = verification_stage
    sent = []

    async def send(connection, credentials, request, **kwargs):
        index, output = response(request)
        sent.append(index)
        return Response(output=output, usage=Usage(total_tokens=10))

    monkeypatch.setattr(gateway, "send", send)

    with History(repo) as history:
        parent = history.begin("review", "main", {})
        history.finish(parent, original)
    result = operations.run(repo, kind="verify", connection=CONNECTION, get_credentials=lambda: {}, finding_run=parent)
    assert result["schema_version"] == "mortis.verification/v1" and result["head_sha"] == head
    assert result["review_run_id"] == parent and result["status"] == "completed"
    assert len(sent) == 7 and result["usage"]["total_tokens"] == 70
    assert operations.run(repo, kind="verify", connection=CONNECTION,
                          get_credentials=lambda: pytest.fail("Saved report needs no credentials"),
                          resume=result["run_id"]) == result
    assert len(sent) == 7


@pytest.mark.parametrize("max_output", [100, 0])
def test_verification_keeps_finite_budget_accounting(verification_stage, monkeypatch, max_output):
    repo, kind, run, response, rows, _, _ = verification_stage
    sent = []

    async def send(connection, credentials, request, **kwargs):
        index, output = response(request)
        if index is not None:
            sent.append(index)
            assert session.usage("security_verification" if kind == "scan" else "verification")["reserved_tokens"] <= 1000
        await asyncio.sleep(0)
        return Response(output=output, usage=Usage(total_tokens=10))

    monkeypatch.setattr(gateway, "reservation", lambda *args: 400)
    monkeypatch.setattr(gateway, "send", send)
    with History(repo) as history:
        run_id = history.begin(kind, "main", {"triage_versions": {}})
        with history.claim(run_id):
            session = Session(CONNECTION, {}, history=history, run_id=run_id)
            result = asyncio.run(run(session, verification_token_budget=1000, max_output_tokens=max_output))
    assert result["status"] == "completed"
    assert [row["verdict"] for row in rows(result)] == ["fixed"] * 7
    assert [row["finding_id"] for row in rows(result)] == [f"issue-{index}" for index in range(7)]
    assert sorted(sent) == list(range(7))
    assert session.usage("security_verification" if kind == "scan" else "verification")["total_tokens"] == 70


def test_verification_preserves_individual_failure_and_redaction(verification_stage, monkeypatch):
    repo, kind, run, response, rows, _, _ = verification_stage

    async def send(connection, credentials, request, **kwargs):
        index, output = response(request)
        if index == 3:
            raise InferenceError("fixture", "Unavailable fixture-private-token", consumption_unknown=False,
                                 usage=Usage(total_tokens=5))
        return Response(output=output, usage=Usage(total_tokens=10))

    monkeypatch.setattr(gateway, "send", send)

    def scanner(item, *args):
        item.secret_values.append("fixture-private-token")
        return {"findings": [], "targets": {}, "problems": [], "metadata": {"database_fresh": True}}

    with History(repo) as history:
        run_id = history.begin(kind, "main", {"triage_versions": {}})
        with history.claim(run_id):
            result = asyncio.run(run(Session(CONNECTION, {"api_key": "fixture-private-token"},
                                             history=history, run_id=run_id), scanner=scanner))
    assert result["status"] == "partial"
    assert [row["verdict"] for row in rows(result)] == ["uncertain" if index == 3 else "fixed" for index in range(7)]
    assert "fixture-private-token" not in json.dumps(result)


@pytest.mark.parametrize("verification_stage", ["scan"], indirect=True)
@pytest.mark.parametrize("total_budget", [False, True])
def test_scan_verification_waits_for_temporary_reservations(verification_stage, monkeypatch, total_budget):
    repo, _, run, response, _, _, _ = verification_stage

    async def exercise(history, run_id):
        release, waiting, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sent = []

        def reserve(connection, request):
            index, _ = response(request)
            if index == 2:
                waiting.set()
            return {None: 0, 0: 650, 4: 100}.get(index, 200)

        async def send(connection, credentials, request, **kwargs):
            index, output = response(request)
            if index is not None:
                sent.append(index)
                assert session.usage("security_verification")["reserved_tokens"] <= 1000
                if index == 0:
                    await release.wait()
            return Response(output=output, usage=Usage(total_tokens=10 if index is not None else 1))

        monkeypatch.setattr(gateway, "reservation", reserve)
        monkeypatch.setattr(gateway, "send", send)

        def progress(event):
            if (event["phase"] == "connection_capacity" and event["active_requests"] == 1
                    and waiting.is_set() and sent == [0, 4]):
                settled.set()

        session = Session(CONNECTION, {}, history=history, run_id=run_id, scan_budget=1009 if total_budget else None,
                          progress=progress)
        async with asyncio.TaskGroup() as group:
            running = group.create_task(run(session, max_output_tokens=100,
                verification_token_budget=None if total_budget else 1000))
            await asyncio.wait_for(waiting.wait(), 10)
            await asyncio.wait_for(settled.wait(), 10)
            assert sent == [0, 4] and session.active_requests == 1
            release.set()
        assert running.result()["status"] == "completed"
        assert sorted(sent) == list(range(7))
        assert session.usage("security_verification")["total_tokens"] == 70

    with History(repo) as history:
        run_id = history.begin("scan", "main", {"triage_versions": {}})
        with history.claim(run_id):
            asyncio.run(exercise(history, run_id))


@pytest.mark.parametrize("verification_stage", ["scan"], indirect=True)
@pytest.mark.parametrize("cancel", [False, True])
def test_scan_deterministic_checks_keep_order_cache_and_snapshot(verification_stage, monkeypatch, cancel):
    repo, _, run, response, _, original, _ = verification_stage
    retained = original["findings"]
    dependencies = [{**retained[index], "kind": "dependency", "rule_id": "CVE-2026-12345", "package": "pkg",
                     "package_type": "npm", "installed_version": "1.0.0", "fixed_version": ""} for index in range(2)]
    original["findings"] = [*dependencies, *retained[2:]]
    checks, fetched = [], []
    actual_fix = security.deterministic_fix

    async def exercise(session):
        loop = asyncio.get_running_loop()
        checking, models_ready = asyncio.Event(), asyncio.Event()
        release_check = threading.Event()
        release_models = asyncio.Event()
        active = set()
        snapshot_files = []

        def fix(item, old, result, **kwargs):
            checks.append(old["id"])
            snapshot_files.append(item.files)
            assert item.files.is_dir()
            verdict = actual_fix(item, old, result, **kwargs)
            assert item.files.is_dir()
            return verdict

        def fetch(rule):
            fetched.append(rule)
            loop.call_soon_threadsafe(checking.set)
            assert release_check.wait(10)
            return [{"ghsa_id": "GHSA-abcd-1234-efgh", "cve_id": rule, "type": "reviewed", "withdrawn_at": None,
                     "html_url": "https://github.com/advisories/GHSA-abcd-1234-efgh",
                     "updated_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                     "vulnerabilities": [{"package": {"ecosystem": "npm", "name": "pkg"},
                                          "vulnerable_version_range": "<= 1.0.0"}]}]

        async def send(connection, credentials, request, **kwargs):
            index, output = response(request)
            if index is not None:
                active.add(index)
                if active == {2, 3, 4}:
                    models_ready.set()
                try:
                    await release_models.wait()
                finally:
                    active.remove(index)
            return Response(output=output, usage=Usage(total_tokens=10))

        def scanner(*args):
            return {"findings": [], "problems": [], "metadata": {"database_fresh": True}, "targets": {
                old["path"]: {"packages": [{"name": "pkg", "version": "1.1.0"}]} for old in dependencies}}

        monkeypatch.setattr(security, "deterministic_fix", fix)
        monkeypatch.setattr(advisories, "fetch", fetch)
        monkeypatch.setattr(gateway, "send", send)
        async with asyncio.TaskGroup() as group:
            running = group.create_task(run(session, scanner=scanner))
            try:
                async with asyncio.timeout(10):
                    await checking.wait()
                    await models_ready.wait()
                assert checks == ["issue-0"] and active == {2, 3, 4}
                if cancel:
                    running.cancel()
                    running.cancel()
            finally:
                release_check.set()
                release_models.set()
        assert not active and all(not path.exists() for path in snapshot_files)
        assert fetched == ["CVE-2026-12345"]
        if cancel:
            assert running.cancelled() and checks == ["issue-0"]
        else:
            assert checks == ["issue-0", "issue-1"]
            report = running.result()
            assert report["status"] == "completed"
            assert [row["finding_id"] for row in report["verifications"]] == [row["id"] for row in original["findings"]]
            assert all(row["verdict"] == "fixed" for row in report["verifications"])

    with History(repo) as history:
        run_id = history.begin("scan", "main", {"triage_versions": {}})
        with history.claim(run_id):
            asyncio.run(exercise(Session(CONNECTION, {}, history=history, run_id=run_id)))
