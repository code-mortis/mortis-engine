import hashlib
import ipaddress
import json
import os
import re
import socket
from collections.abc import Iterable
from heapq import merge
from urllib.parse import urlsplit


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class EndpointLookupError(ValueError):
    def __init__(self, *, retryable=False):
        super().__init__("The provider address could not be found. Check the URL and local DNS.")
        self.retryable = retryable


def safe_endpoint(url: str) -> str:
    endpoint_addresses(url)
    return url.rstrip("/")


def endpoint_addresses(url: str) -> list[str]:
    """Resolve and validate the addresses that an HTTP transport may actually dial."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("Enter a complete provider URL starting with https:// or http://.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Provider URLs cannot contain credentials, query parameters, or fragments")
    allow_private = os.environ.get("CM_ALLOW_PRIVATE_ENDPOINTS", "").strip().lower() in {"1", "true", "yes", "on"}
    trusted = {h.strip().lower() for h in os.environ.get("CM_TRUSTED_ENDPOINT_HOSTS", "").split(",") if h.strip()}
    explicitly_trusted = parsed.hostname.lower() in trusted
    if parsed.scheme != "https" and not (allow_private or explicitly_trusted):
        raise ValueError("Use an HTTPS provider URL or explicitly trust the local provider through CM_TRUSTED_ENDPOINT_HOSTS.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
                                       type=socket.SOCK_STREAM)
    except OSError as exc:
        raise EndpointLookupError(retryable=isinstance(exc, socket.gaierror) and exc.errno == socket.EAI_AGAIN) from exc
    if not addresses:
        raise EndpointLookupError()
    resolved = list(dict.fromkeys(entry[4][0] for entry in addresses))
    for value in resolved:
        address = ipaddress.ip_address(value)
        if explicitly_trusted:
            continue
        if address.is_link_local or address.is_unspecified or address.is_multicast:
            raise ValueError("This provider URL points to a protected system or local-network address. Choose a different provider URL.")
        if not address.is_global and not allow_private:
            raise ValueError("Private-network provider addresses require explicit trust. Set CM_TRUSTED_ENDPOINT_HOSTS for your local provider.")
        if parsed.scheme != "https" and not (address.is_private or address.is_loopback):
            raise ValueError("Public provider addresses require HTTPS or explicit trust through CM_TRUSTED_ENDPOINT_HOSTS.")
    return resolved


_BEARER_TOKEN = re.compile(r"(?i)bearer\s+([\w.~+/=\-]+)")
_PREFIXED_TOKEN = re.compile(
    r"cm_pat_[A-Za-z0-9_-]{43}|\b(?:gh[pour]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+)\b"
)
# Findings can be serialized before sanitization, with an escaped newline or tab before the token.
_INSTALLATION_TOKEN = re.compile(r"(?:\b|(?<=\\[nrt]))ghs_[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){0,2}")


def _literal_spans(text, lines, separator):
    literal = separator.join(lines)
    start = text.find(literal)
    while start >= 0:
        end = start + len(literal)
        for line in lines:
            yield start, start + len(line)
            start += len(line) + len(separator)
        start = text.find(literal, end)


def clean_text(value: str, secrets: Iterable[str] = (), *, preserve_lines: bool = False) -> str:
    # Match the original text so a shorter credential cannot split an installation token
    # and masking a token cannot split a longer stored credential.
    spans = [
        (match.span(1) for match in _BEARER_TOKEN.finditer(value)),
        (match.span() for match in _PREFIXED_TOKEN.finditer(value)),
        (match.span() for match in _INSTALLATION_TOKEN.finditer(value)),
    ]
    for secret in set(filter(None, secrets)):
        lines = secret.split("\n") if preserve_lines else [secret]
        spans.append(_literal_spans(value, lines, "\n"))
        if preserve_lines:
            escaped = [json.dumps(line)[1:-1] for line in lines]
            if escaped != lines or len(lines) > 1:
                spans.append(_literal_spans(value, escaped, r"\n"))

    parts = []
    end = 0
    for start, stop in merge(*spans):
        if start >= end:
            parts.extend((value[end:start], "[redacted]"))
        end = max(end, stop)
    parts.append(value[end:])
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", "".join(parts))
