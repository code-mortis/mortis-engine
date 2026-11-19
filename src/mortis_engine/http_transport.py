"""Pin HTTP connections to validated addresses, retaining Host and TLS identity."""

import asyncio
import threading
import time

import httpx

from .security import endpoint_addresses


class EndpointPolicyError(ValueError):
    pass


def addresses(request):
    try:
        return endpoint_addresses(str(request.url.copy_with(query=None, fragment=None)))
    except ValueError as exc:
        raise EndpointPolicyError(str(exc)) from exc


def pinned_request(request, address, started):
    timeouts = dict(request.extensions.get("timeout", {}))
    if timeouts.get("connect") is not None:
        timeouts["connect"] -= time.monotonic() - started
        if timeouts["connect"] <= 0:
            raise httpx.ConnectTimeout("Endpoint resolution or connection exceeded its deadline")
    return httpx.Request(request.method, request.url.copy_with(host=address),
        headers=request.headers, stream=request.stream,
        extensions={**request.extensions, "timeout": timeouts, "sni_hostname": request.url.host})


class PinnedHTTPTransport(httpx.BaseTransport):
    def __init__(self):
        self._transports = {}
        self._lock = threading.Lock()

    def handle_request(self, request):
        started = time.monotonic()
        resolved = addresses(request)
        origin = (request.url.scheme, request.url.host, request.url.port)
        with self._lock:
            # Separate origins must never reuse a TLS session merely because their IPs match.
            if origin not in self._transports:
                self._transports[origin] = httpx.HTTPTransport(trust_env=False)
            transport = self._transports[origin]
        for index, address in enumerate(resolved):
            try:
                return transport.handle_request(pinned_request(request, address, started))
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if index == len(resolved) - 1:
                    raise

    def close(self):
        for transport in self._transports.values():
            transport.close()


class PinnedAsyncHTTPTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self._transports = {}

    async def handle_async_request(self, request):
        started = time.monotonic()
        try:
            resolved = await asyncio.wait_for(asyncio.to_thread(addresses, request),
                                              request.extensions.get("timeout", {}).get("connect"))
        except TimeoutError as exc:
            raise httpx.ConnectTimeout("Endpoint resolution exceeded its deadline") from exc
        origin = (request.url.scheme, request.url.host, request.url.port)
        if origin not in self._transports:
            self._transports[origin] = httpx.AsyncHTTPTransport(trust_env=False,
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=8))
        transport = self._transports[origin]
        for index, address in enumerate(resolved):
            try:
                return await transport.handle_async_request(pinned_request(request, address, started))
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if index == len(resolved) - 1:
                    raise

    async def aclose(self):
        for transport in self._transports.values():
            await transport.aclose()
