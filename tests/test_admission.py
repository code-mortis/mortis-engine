"""Offline process/transport admission checks with isolated local state."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack

import httpx
import pytest

from mortis_engine import admission, config, gateway, llm_bedrock, llm_http
from mortis_engine.cli.review import progress_display
from mortis_engine.history import History
from mortis_engine.llm_http import InferenceError
from mortis_engine.schemas import Message, Request, Response, Usage
from mortis_engine.workflow import Session

CONNECTION = {"kind": "api", "provider": "test", "protocol": "openai", "model": "mock",
              "base_url": "https://provider.invalid/v1", "options": {}}
REQUEST = Request(messages=[Message(role="user", content="fixture")], max_output_tokens=1,
                  session_id="fixture")


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


def call(session, key="one", *, timeout=5, stopped=lambda: False, budget=None, client=None):
    return session.send("analysis", key, REQUEST, client=client, timeout_at=time.monotonic() + timeout,
                        token_budget=budget, stopped=stopped, accept=lambda response: {"ok": True})


def occupy(connection, stack, count=admission.CAPACITY):
    directory = admission.state_directory(connection)
    for _ in range(count):
        descriptor = admission.try_slot(directory)
        assert descriptor is not None
        stack.callback(os.close, descriptor)
    return directory


def test_profile_aliases_models_and_normalized_destinations_share_nonsecret_key():
    saved = {"provider": "test", "protocol": "openai", "base_url": "https://PROVIDER.invalid:443/v1/",
             "model": "first", "api_key_env": "MORTIS_TEST_KEY", "headers_env": {"X-Auth": "MORTIS_TEST_HEADER"}}
    first = config.connection("first", saved)
    alias = config.connection("alias", {**saved, "model": "second", "base_url": "https://provider.invalid/v1",
                                         "headers_env": {"x-auth": "MORTIS_TEST_HEADER"}})
    assert first["_admission_key"] == alias["_admission_key"]
    other = config.connection("different-account", {**saved, "api_key_env": "OTHER_TEST_KEY"})
    assert first["_admission_key"] != other["_admission_key"]
    secret = "fixture-secret-never-persisted"
    assert admission.connection_key(CONNECTION) == admission.connection_key(
        {**CONNECTION, "api_key": secret, "extra_headers": {"Authorization": secret}, "model": "another"})
    directory = admission.state_directory({**CONNECTION, "api_key": secret})
    with ExitStack() as stack:
        occupy(CONNECTION, stack)
    names = str(list(directory.rglob("*")))
    assert secret not in names and hashlib.sha256(secret.encode()).hexdigest() not in names
    assert all(path.read_bytes() in {b"", b"0"} for path in directory.iterdir())
    assert admission.connection_key({**CONNECTION, "protocol": "anthropic", "base_url": "https://provider.invalid"}) == admission.connection_key(
        {**CONNECTION, "protocol": "anthropic", "base_url": "https://provider.invalid/v1/"})


def test_separate_groups_can_dispatch_while_another_group_is_full(monkeypatch):
    sent = []

    async def send(*args, **kwargs):
        sent.append(True)
        return Response(usage=Usage(total_tokens=1))

    monkeypatch.setattr(gateway, "send", send)
    with ExitStack() as stack:
        occupy(CONNECTION, stack)
        session = Session({**CONNECTION, "base_url": "https://other.invalid/v1"}, {})
        assert asyncio.run(call(session)) == {"ok": True}
    assert sent == [True]


@pytest.mark.parametrize("kind", ["deadline", "stopped", "cancelled"])
def test_waiting_never_reserves_or_dispatches_and_reports_capacity(kind, monkeypatch):
    monkeypatch.setattr(gateway, "send", lambda *args, **kwargs: pytest.fail("Waiting call dispatched"))
    events = []
    session = Session(CONNECTION, {}, progress=events.append)

    async def run():
        stopped = False
        task = asyncio.create_task(call(session, timeout=0.08 if kind == "deadline" else 5,
                                        stopped=lambda: stopped))
        while not session.waiting_requests:
            await asyncio.sleep(0)
        assert session.active_requests == 0 and not session.records
        if kind == "cancelled":
            task.cancel()
        elif kind == "stopped":
            stopped = True
        with pytest.raises(asyncio.CancelledError if kind == "cancelled" else InferenceError) as error:
            await task
        if kind != "cancelled":
            assert error.value.code == ("cancelled" if kind == "stopped" else "deadline")
            assert not error.value.consumption_unknown

    with ExitStack() as stack:
        occupy(CONNECTION, stack)
        asyncio.run(run())
    assert not session.records and session.usage()["reserved_tokens"] == 0
    assert events[0] == {"phase": "connection_capacity", "active_requests": 0, "waiting_requests": 1}
    assert events[-1] == {"phase": "connection_capacity", "active_requests": 0, "waiting_requests": 0}


def test_cached_results_need_no_slot_and_waiting_rechecks_budget(monkeypatch):
    async def send(*args, **kwargs):
        return Response(usage=Usage(total_tokens=1))

    monkeypatch.setattr(gateway, "send", send)
    session = Session(CONNECTION, {})
    assert asyncio.run(call(session)) == {"ok": True}
    with ExitStack() as stack:
        directory = occupy(CONNECTION, stack)
        assert asyncio.run(call(session, timeout=0.01)) == {"ok": True}

        async def run():
            task = asyncio.create_task(call(session, "later", budget=5000))
            while not session.waiting_requests:
                await asyncio.sleep(0)
            # Another completed request consumes the budget while this request is queued.
            session.records["analysis", "one"][1]["usage"]["total_tokens"] = 5000
            stack.close()
            with pytest.raises(InferenceError) as error:
                await task
            assert error.value.code == "budget" and not error.value.consumption_unknown

        asyncio.run(run())
        descriptor = admission.try_slot(directory)
        assert descriptor is not None
        os.close(descriptor)
    assert len(session.records) == 1


@pytest.mark.parametrize("protocol", ["openai", "anthropic-bedrock"])
def test_repeated_cancellation_holds_slot_until_transport_cleanup_and_retains_unknown_usage(protocol, monkeypatch):
    connection = {**CONNECTION, "protocol": protocol}
    monkeypatch.setattr(llm_http, "safe_endpoint", lambda value: value)
    monkeypatch.setattr(llm_bedrock, "_prepare", lambda *args: ("https://provider.invalid/model/mock/invoke", {}))

    async def run():
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                started.set()
                await asyncio.Event().wait()
                yield b""

            async def aclose(self):
                cleaning.set()
                await release.wait()

        with ExitStack() as stack:
            directory = occupy(connection, stack, admission.CAPACITY - 1)
            session = Session(connection, {})
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, stream=Stream()))) as client:
                task = asyncio.create_task(call(session, client=client))
                await asyncio.wait_for(started.wait(), 2)
                task.cancel()
                await asyncio.wait_for(cleaning.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert admission.try_slot(directory) is None
                assert session.active_requests == 1
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            descriptor = admission.try_slot(directory)
            assert descriptor is not None
            os.close(descriptor)
            assert session.active_requests == 0
            assert session.usage()["total_tokens"] is None
            assert session.usage()["reserved_tokens"] > 0
            assert session.usage()["requests"][0]["code"] == "interrupted"

    asyncio.run(run())


def test_retry_backoff_does_not_hold_capacity(monkeypatch):
    async def send(*args, **kwargs):
        raise InferenceError("rate_limit", "mock rejection", retryable=True, consumption_unknown=False,
                             usage=Usage(total_tokens=0))

    monkeypatch.setattr(gateway, "send", send)
    session = Session(CONNECTION, {})

    async def run():
        task = asyncio.create_task(call(session))
        while not session.records or session.active_requests:
            await asyncio.sleep(0)
        assert not task.done()
        with ExitStack() as stack:
            occupy(CONNECTION, stack)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(session.records) == 1 and session.usage()["reserved_tokens"] == 0

    asyncio.run(run())


WORKER = '''
import asyncio, json, sys, time
from pathlib import Path
from mortis_engine import gateway
from mortis_engine.history import History
from mortis_engine.schemas import Message, Request, Response, Usage
from mortis_engine.workflow import Session
connection = json.loads(sys.argv[2])
events = []
async def send(*args, **kwargs):
    events.append([time.monotonic_ns(), 1])
    await asyncio.sleep(0.2)
    events.append([time.monotonic_ns(), -1])
    return Response(usage=Usage(total_tokens=1))
gateway.send = send
async def run(session):
    request = Request(messages=[Message(role="user", content="fixture")], max_output_tokens=1)
    await asyncio.gather(*(session.send("analysis", str(i), request, client=None,
        timeout_at=time.monotonic()+10, token_budget=None, stopped=lambda:False,
        accept=lambda response:{"ok":True}) for i in range(8)))
with History(sys.argv[1]) as history:
    run_id = history.begin("review", "fixture", {})
    session = Session(connection, {}, history=history, run_id=run_id)
    while not Path(sys.argv[3]).exists():
        time.sleep(0.01)
    asyncio.run(run(session))
print(json.dumps(events))
'''


def test_two_repository_processes_share_eight_request_slots(tmp_path):
    processes = []
    barrier = tmp_path / "start"
    try:
        for index in range(2):
            repo = tmp_path / f"repository-{index}"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            connection = {**CONNECTION, "model": f"alias-{index}"}
            processes.append(subprocess.Popen([sys.executable, "-c", WORKER, str(repo),
                                               json.dumps(connection), str(barrier)],
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        barrier.touch()
        events = []
        for process in processes:
            output, errors = process.communicate(timeout=20)
            assert process.returncode == 0, errors
            events.extend(json.loads(output))
        active, peak = 0, 0
        for _, change in sorted(events):
            active += change
            peak = max(peak, active)
        assert active == 0 and peak == admission.CAPACITY
        assert len(events) == 32
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()


def test_killed_owner_releases_slots_without_releasing_unknown_reservation(tmp_path):
    repo = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    code = '''
import asyncio, json, sys
from mortis_engine import gateway
from mortis_engine.history import History
from mortis_engine.schemas import Message, Request
from mortis_engine.workflow import Session
async def send(*args, **kwargs):
    print("dispatched", flush=True)
    await asyncio.Event().wait()
gateway.send = send
async def run(session):
    await session.send("analysis", "interrupted", Request(messages=[Message(role="user", content="fixture")],
        max_output_tokens=1, session_id="fixture"), client=None, timeout_at=float("inf"), token_budget=None,
        stopped=lambda:False, accept=lambda response:{})
with History(sys.argv[1]) as history:
    run_id = history.begin("review", "fixture", {})
    print(run_id, flush=True)
    asyncio.run(run(Session(json.loads(sys.argv[2]), {}, history=history, run_id=run_id)))
'''
    process = subprocess.Popen([sys.executable, "-c", code, str(repo), json.dumps(CONNECTION)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        run_id = process.stdout.readline().strip()
        assert process.stdout.readline().strip() == "dispatched"
        process.kill()
        process.communicate(timeout=5)
        with ExitStack() as stack:
            occupy(CONNECTION, stack)
        with History(repo) as history:
            session = Session(CONNECTION, {}, history=history, run_id=run_id)
            assert session.usage()["total_tokens"] is None
            assert session.usage()["reserved_tokens"] > 0
            assert session.usage()["requests"][0]["code"] == "interrupted"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions and symlinks")
@pytest.mark.parametrize("kind", ["public", "symlink", "hardlink"])
def test_slot_files_reject_insecure_state(tmp_path, kind):
    directory = admission.state_directory(CONNECTION)
    slot = directory / "0.lock"
    target = tmp_path / "target"
    target.touch(mode=0o600)
    if kind == "symlink":
        slot.symlink_to(target)
    elif kind == "hardlink":
        slot.hardlink_to(target)
    else:
        slot.touch(mode=0o644)
    with pytest.raises(ValueError, match="private regular"):
        admission.try_slot(directory)


def test_capacity_progress_preserves_active_paths_and_waiting_state(capsys):
    with progress_display(True) as progress:
        progress({"phase": "analysis", "selected_files": 2, "completed_files": 0, "failed_files": 0,
                  "active_requests": 1, "active_paths": ["active.py"]})
        capsys.readouterr()
        progress({"phase": "connection_capacity", "active_requests": 1, "waiting_requests": 1})
        output = capsys.readouterr().err
        assert "1 active, 1 waiting" in output and "active.py" in output
        assert "Waiting for connection capacity" in output
        progress({"phase": "connection_capacity", "active_requests": 0, "waiting_requests": 0})
        assert "0 active, 0 waiting" in capsys.readouterr().err
