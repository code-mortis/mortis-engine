import httpx
import pytest

from mortis_engine import discovery


def test_discovery_pages_use_configured_endpoint_and_credentials(monkeypatch):
    seen = []

    def respond(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(200, json={"data": [{"id": "first"}], "has_more": True, "last_id": "next"})
        return httpx.Response(200, json={"data": [{"id": "second", "display_name": "Second"}]})

    monkeypatch.setattr(discovery, "PinnedHTTPTransport", lambda: httpx.MockTransport(respond))
    monkeypatch.setattr(discovery, "safe_endpoint", lambda url: url)
    assert discovery.list_models(
        {"protocol": "anthropic", "base_url": "https://provider.example", "options": {"auth_header": "X-Key"}},
        {"api_key": "test-key", "extra_headers": {"X-Team": "demo"}},
    ) == [("first", "first"), ("second", "Second")]
    assert all(r.url.path == "/v1/models" and r.headers["X-Key"] == "test-key" and r.headers["X-Team"] == "demo" for r in seen)
    assert seen[1].url.params["after_id"] == "next"


def test_discovery_only_uses_supported_protocols_and_headers(monkeypatch):
    with pytest.raises(ValueError, match="Bedrock"):
        discovery.list_models({"protocol": "anthropic-bedrock"}, {})
    monkeypatch.setattr(discovery, "safe_endpoint", lambda url: url)
    with pytest.raises(ValueError, match="routing"):
        discovery.list_models({"protocol": "openai", "base_url": "https://provider.example"},
                              {"api_key": "test-key", "extra_headers": {"Host": "other.example"}})
