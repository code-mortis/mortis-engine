import hashlib
import ipaddress
import os
import re
import socket
from urllib.parse import urlsplit


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


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
        raise ValueError("The provider address could not be found. Check the URL and local DNS.") from exc
    if not addresses:
        raise ValueError("The provider address could not be found. Check the URL and local DNS.")
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


def clean_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[redacted]")
    value = re.sub(r"(?i)(bearer\s+)[\w.~+/=\-]+", r"\1[redacted]", value)
    value = re.sub(r"cm_pat_[A-Za-z0-9_-]{43}", "[redacted]", value)
    value = re.sub(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+)\b", "[redacted]", value
    )
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)
