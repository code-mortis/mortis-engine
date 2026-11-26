"""Offline DNS and socket regressions; no credentials or network access."""

import asyncio
import socket
import ssl
import threading
import time
from contextlib import asynccontextmanager

import httpcore
import httpx
import pytest

from mortis_engine import discovery, gateway, http_transport, llm_http, security, workflow
from mortis_engine.http_transport import EndpointPolicyError, PinnedAsyncHTTPTransport, PinnedHTTPTransport
from mortis_engine.schemas import Message, Request
from mortis_engine.security_scans import advisories

PUBLIC = "8.8.8.8"


def dns_rows(*addresses):
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
            for ip in addresses]


@pytest.fixture(autouse=True)
def isolated_endpoint_policy(monkeypatch):
    monkeypatch.delenv("CM_ALLOW_PRIVATE_ENDPOINTS", raising=False)
    monkeypatch.delenv("CM_TRUSTED_ENDPOINT_HOSTS", raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: pytest.fail("Unexpected DNS lookup"))


@pytest.mark.parametrize("addresses", [(PUBLIC,), ("127.0.0.1", PUBLIC), ("100.64.0.1",)])
def test_private_endpoint_flag_never_enables_public_http(monkeypatch, addresses):
    monkeypatch.setenv("CM_ALLOW_PRIVATE_ENDPOINTS", "true")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: dns_rows(*addresses))
    with pytest.raises(ValueError, match="HTTPS"):
        security.safe_endpoint("http://provider.example/v1")


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "192.168.1.1", "::1", "fd00::1"])
def test_private_http_requires_opt_in_and_uses_http_port(monkeypatch, address):
    seen = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kw: seen.append(port) or dns_rows(address))
    with pytest.raises(ValueError, match="HTTPS"):
        security.safe_endpoint("http://provider.example/v1")
    monkeypatch.setenv("CM_ALLOW_PRIVATE_ENDPOINTS", "true")
    assert security.safe_endpoint("http://provider.example/v1/") == "http://provider.example/v1"
    assert seen == [80]


@pytest.mark.parametrize("address", ["169.254.169.254", "0.0.0.0", "224.0.0.1", "fe80::1", "::"])
def test_private_flag_preserves_protected_address_denial(monkeypatch, address):
    monkeypatch.setenv("CM_ALLOW_PRIVATE_ENDPOINTS", "true")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: dns_rows(address))
    with pytest.raises(ValueError, match="protected"):
        security.safe_endpoint("https://provider.example")


def test_explicit_host_trust_and_empty_dns(monkeypatch):
    monkeypatch.setenv("CM_TRUSTED_ENDPOINT_HOSTS", " provider.example ")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: dns_rows(PUBLIC))
    assert security.safe_endpoint("http://provider.example") == "http://provider.example"
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [])
    with pytest.raises(ValueError, match="could not be found"):
        security.safe_endpoint("https://provider.example")


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("address", [PUBLIC, "2001:4860:4860::8888"])
def test_transport_pins_dns_preserves_tls_host_and_pool_isolation(monkeypatch, asynchronous, address):
    lookups, sockets, tls, writes, streams = [], [], [], [], []

    def resolve(host, port, **kwargs):
        lookups.append(host)
        return dns_rows(address)

    def connected(host, port):
        # A second DNS lookup of the original hostname here would be vulnerable.
        assert host == address
        sockets.append((host, port))

    def secured(context, hostname):
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        tls.append(hostname)

    response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"

    class SyncStream(httpcore.MockStream):
        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            secured(ssl_context, server_hostname)
            return self

        def write(self, buffer, timeout=None):
            writes.append(buffer)

    class AsyncStream(httpcore.AsyncMockStream):
        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            secured(ssl_context, server_hostname)
            return self

        async def write(self, buffer, timeout=None):
            writes.append(buffer)

    def connect_sync(self, host, port, **kwargs):
        connected(host, port)
        stream = SyncStream([response, response])
        streams.append(stream)
        return stream

    async def connect_async(self, host, port, **kwargs):
        connected(host, port)
        stream = AsyncStream([response, response])
        streams.append(stream)
        return stream

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect_sync)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect_async)
    urls = ["https://one.example:8443/v1?after=next"] * 2 + ["https://two.example:8443/v1"]

    async def run():
        async with gateway.http_client() as client:
            for url in urls:
                result = await client.post(url, content=b"signed bytes", headers={"Authorization": "fixture-key"})
                assert result.json() == {} and str(result.request.url) == url

    if asynchronous:
        asyncio.run(run())
    else:
        with httpx.Client(transport=PinnedHTTPTransport(), trust_env=False) as client:
            for url in urls:
                result = client.post(url, content=b"signed bytes", headers={"Authorization": "fixture-key"})
                assert result.json() == {} and str(result.request.url) == url

    assert lookups == ["one.example", "one.example", "two.example"]
    assert sockets == [(address, 8443)] * 2
    assert tls == ["one.example", "two.example"]
    wire = b"".join(writes)
    assert wire.count(b"Host: one.example:8443\r\n") == 2
    assert wire.count(b"Host: two.example:8443\r\n") == 1
    assert wire.count(b"Authorization: fixture-key\r\n") == 3
    assert wire.count(b"signed bytes") == 3
    assert b"POST /v1?after=next HTTP/1.1" in wire
    assert all(stream._closed for stream in streams)


@pytest.mark.parametrize("protocol", ["openai", "openai-responses", "anthropic"])
@pytest.mark.parametrize("code", ["endpoint_policy", "endpoint_dns"])
def test_inference_rechecks_dns_at_dispatch_and_accounts_for_denial(monkeypatch, protocol, code):
    lookups = iter([True, False])

    def resolve(*args, **kwargs):
        if next(lookups):
            return dns_rows(PUBLIC)
        if code == "endpoint_dns":
            raise socket.gaierror(socket.EAI_AGAIN, "fixture DNS failure")
        return dns_rows("169.254.169.254")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", lambda *a, **kw: pytest.fail("Socket opened"))

    async def run():
        async with gateway.http_client() as client:
            with pytest.raises(llm_http.InferenceError) as error:
                await gateway.send({"kind": "api", "protocol": protocol, "base_url": "https://provider.example/v1",
                    "model": "fixture", "options": {}}, {"api_key": "fixture-key"},
                    Request(messages=[Message(role="user", content="fixture")], max_output_tokens=256), client=client)
        assert error.value.code == code and not error.value.consumption_unknown
        assert error.value.retryable == (code == "endpoint_dns")

    asyncio.run(run())


@pytest.mark.parametrize("code", ["endpoint_dns", "endpoint_policy", "transport"])
def test_only_a_dispatched_failure_blocks_the_next_analysis_task(monkeypatch, code):
    lookups, posts = [], []

    @asynccontextmanager
    async def admit(*args, **kwargs):
        yield

    def resolve(*args, **kwargs):
        lookups.append(None)
        if len(lookups) == 1:
            if code == "endpoint_dns":
                raise socket.gaierror(socket.EAI_AGAIN, "fixture DNS failure")
            if code == "endpoint_policy":
                return dns_rows("169.254.169.254")
        return dns_rows(PUBLIC)

    def respond(wire):
        posts.append(wire.method)
        if code == "transport":
            raise httpx.ReadError("fixture connection lost after dispatch", request=wire)
        return httpx.Response(200, json={"choices": [{"message": {"content": "complete"}, "finish_reason": "stop"}],
                                        "usage": {"prompt_tokens": 2, "completion_tokens": 1}})

    monkeypatch.setattr(workflow.admission, "slot", admit)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    async def run():
        connection = {"kind": "api", "protocol": "openai", "base_url": "https://provider.example/v1", "model": "fixture"}
        session = workflow.Session(connection, {})
        request = Request(messages=[Message(role="user", content="fixture")], max_output_tokens=256)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            async def task(key):
                # Leave less than the retry backoff to settle the first attempt independently.
                return await session.send("analysis", key, request, client=client, timeout_at=time.monotonic() + 0.9,
                                          token_budget=None, stopped=lambda: False, accept=lambda value: value.content)

            with pytest.raises(llm_http.InferenceError) as error:
                await task("first")
            assert error.value.code == code
            first = session.usage()["requests"][0]
            assert first["code"] == code
            assert first["consumption_unknown"] == (code == "transport")
            if code == "transport":
                with pytest.raises(llm_http.InferenceError) as next_error:
                    await task("second")
                assert next_error.value.code == "usage_unknown"
                assert len(lookups) == 1 and session.usage()["total_tokens"] is None
                assert session.usage()["reserved_tokens"] == first["reserved_tokens"] > 0
            else:
                assert posts == [] and session.usage()["reserved_tokens"] == 0
                assert await task("second") == "complete"
                assert len(lookups) == 2 and session.usage()["total_tokens"] == 3
        assert posts == ["POST"]

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["second_dns", "after_dns", "custom_transport"])
@pytest.mark.parametrize("code", ["timeout", "cancelled"])
def test_only_stopping_during_pinned_dns_has_known_usage(monkeypatch, phase, code):
    release, reached = threading.Event(), threading.Event()
    lookups, dispatches = [], []

    def resolve(*args, **kwargs):
        lookups.append(None)
        if phase == "second_dns" and len(lookups) == 2:
            reached.set()
            release.wait(3)
        return dns_rows(PUBLIC)

    async def respond(wire):
        dispatches.append(wire.method)
        assert not http_transport.awaiting_endpoint_resolution(wire)
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(respond))

    async def run():
        client = (httpx.AsyncClient(transport=httpx.MockTransport(respond))
                  if phase == "custom_transport" else gateway.http_client())
        async with client:
            if phase == "second_dns":
                build = client.build_request

                def build_request(*args, **kwargs):
                    wire = build(*args, **kwargs)
                    # Force the outer deadline to win over the pinned connect deadline.
                    wire.extensions["timeout"]["connect"] = 2
                    return wire

                monkeypatch.setattr(client, "build_request", build_request)
            connection = {"kind": "api", "protocol": "openai", "base_url": "https://provider.example/v1",
                          "model": "fixture"}
            request = Request(messages=[Message(role="user", content="fixture")], max_output_tokens=256)
            try:
                with pytest.raises(llm_http.InferenceError) as error:
                    await gateway.send(connection, {}, request, client=client, timeout=0.25,
                                       stopped=lambda: code == "cancelled" and reached.is_set())
                assert error.value.code == code
                assert error.value.consumption_unknown == (phase != "second_dns")
                assert not error.value.retryable
            finally:
                release.set()  # Let asyncio.run join the mocked resolver thread.

    asyncio.run(run())
    assert len(lookups) == (1 if phase == "custom_transport" else 2)
    assert dispatches == ([] if phase == "second_dns" else ["POST"])


@pytest.mark.parametrize("operation", ["discovery", "advisory"])
@pytest.mark.parametrize("blocked", ["endpoint_policy", "endpoint_dns"])
def test_sync_callers_recheck_dns_before_sending(monkeypatch, operation, blocked):
    lookups = iter([True, False])

    def resolve(*args, **kwargs):
        if next(lookups):
            return dns_rows(PUBLIC)
        if blocked == "endpoint_dns":
            raise socket.gaierror(socket.EAI_AGAIN, "fixture DNS failure")
        return dns_rows("127.0.0.1")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", lambda *a, **kw: pytest.fail("Socket opened"))
    with pytest.raises(security.EndpointLookupError if blocked == "endpoint_dns" else EndpointPolicyError) as error:
        if operation == "discovery":
            discovery.list_models({"protocol": "openai", "base_url": "https://provider.example"},
                                  {"api_key": "fixture-key"})
        else:
            advisories.fetch("CVE-2026-12345")
    assert isinstance(error.value, ValueError)


@pytest.mark.parametrize("operation", ["discovery", "advisory", "transport"])
def test_sync_dns_timeout_prevents_dispatch(monkeypatch, operation):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def resolve(*args, **kwargs):
        started.set()
        release.wait(2)
        finished.set()
        return dns_rows(PUBLIC)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", lambda *a, **kw: pytest.fail("Socket opened"))
    for module in (discovery, advisories):
        monkeypatch.setattr(module, "resolve_sync", lambda call, _timeout: http_transport.resolve_sync(call, 0.1))

    try:
        if operation == "discovery":
            with pytest.raises(ValueError, match="Could not read the model catalog") as error:
                discovery.list_models({"protocol": "openai", "base_url": "https://provider.example"},
                                      {"api_key": "fixture-key"})
            assert isinstance(error.value.__cause__, httpx.ConnectTimeout)
        elif operation == "advisory":
            with pytest.raises(httpx.ConnectTimeout, match="Endpoint resolution"):
                advisories.fetch("CVE-2026-12345")
        else:
            with httpx.Client(transport=PinnedHTTPTransport(), timeout=0.1) as client:
                with pytest.raises(httpx.ConnectTimeout, match="Endpoint resolution"):
                    client.get("https://provider.example")
        assert started.is_set() and not finished.is_set()
    finally:
        release.set()
        finished.wait(2)


def test_sync_dns_timeout_keeps_stalled_workers_bounded(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(http_transport, "_DNS_SLOTS", slots)
    release = threading.Event()

    try:
        with pytest.raises(httpx.ConnectTimeout):
            http_transport.resolve_sync(lambda: release.wait(2), 0.1)
        with pytest.raises(httpx.ConnectTimeout):
            http_transport.resolve_sync(lambda: pytest.fail("Started an extra DNS worker"), 0.1)
    finally:
        release.set()
        assert slots.acquire(timeout=2)
        slots.release()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadError, httpx.WriteError])
def test_transport_only_tries_another_address_before_dispatch(monkeypatch, asynchronous, failure):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: dns_rows(PUBLIC, "1.1.1.1"))
    seen = []

    def respond(request):
        seen.append(request.url.host)
        if len(seen) == 1:
            raise failure("fixture failure")
        return httpx.Response(200, content=b"{}")

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kw: httpx.MockTransport(respond))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(respond))

    async def run():
        async with httpx.AsyncClient(transport=PinnedAsyncHTTPTransport(), trust_env=False) as client:
            await client.post("https://provider.example", content=b"fixture")

    def execute():
        if asynchronous:
            asyncio.run(run())
        else:
            with httpx.Client(transport=PinnedHTTPTransport(), trust_env=False) as client:
                client.post("https://provider.example", content=b"fixture")

    if failure is httpx.ConnectError:
        execute()
        assert seen == [PUBLIC, "1.1.1.1"]
    else:
        with pytest.raises(failure):
            execute()
        assert seen == [PUBLIC]
