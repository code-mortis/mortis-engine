"""Eight OS-locked request slots per local connection, shared across repositories."""

import asyncio
import errno
import hashlib
import os
import stat
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .history import encode, private_directory
from .llm_http import InferenceError

CAPACITY = 8


def connection_key(connection, references=None):
    """Hash only the destination and nonsecret references, never credential values."""
    options = connection.get("options", {})
    parsed = urlsplit(connection.get("base_url", ""))
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Provider URLs cannot contain credentials, query parameters, or fragments")
    host = (parsed.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    if port and port != {"https": 443, "http": 80}.get(parsed.scheme.lower()):
        host += f":{port}"
    destination = urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip("/"), "", ""))
    protocol = connection.get("protocol", "").lower()
    if protocol == "anthropic" and not destination.endswith("/v1"):
        destination += "/v1"
    auth_header = options.get("auth_header") or ("x-api-key" if protocol == "anthropic" else "authorization")
    region, profile = "", ""
    if protocol == "anthropic-bedrock":
        region = options.get("aws_region") or os.environ.get("AWS_REGION", "")
        profile = options.get("aws_profile") or os.environ.get("AWS_PROFILE") or os.environ.get("AWS_DEFAULT_PROFILE", "")
    value = {"provider": connection.get("provider", "").lower(), "protocol": protocol,
             "destination": destination, "auth_header": auth_header.lower(),
             "aws_region": region, "aws_profile": profile,
             "references": references or {}}
    return hashlib.sha256(encode(value).encode()).hexdigest()


def state_directory(connection):
    root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    if not root.is_absolute():
        raise ValueError("XDG_STATE_HOME must be an absolute path.")
    key = connection.get("_admission_key") or connection_key(connection)
    if not isinstance(key, str) or len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
        raise ValueError("Invalid internal connection admission key.")
    return private_directory(private_directory(private_directory(root / "mortis") / "admission") / key)


def try_slot(directory):
    for number in range(CAPACITY):
        path = directory / f"{number}.lock"
        if path.is_symlink():
            raise ValueError("Mortis admission requires private regular slot files.")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_nlink != 1
                    or hasattr(os, "getuid") and info.st_uid != os.getuid()):
                raise ValueError("Mortis admission requires private regular slot files.")
            if os.name == "nt":
                import msvcrt

                if not info.st_size:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
        except BaseException:
            os.close(fd)
            raise
        else:
            return fd
    return None


@asynccontextmanager
async def slot(connection, *, stopped, until, waiting=lambda: None):
    directory = None
    notified = False
    descriptor = None
    try:
        while True:
            if stopped():
                raise InferenceError("cancelled", "Inference was cancelled while waiting for connection capacity.", consumption_unknown=False)
            remaining = until - time.monotonic()
            if remaining <= 0:
                raise InferenceError("deadline", "The stage deadline expired while waiting for connection capacity.", consumption_unknown=False)
            directory = directory or state_directory(connection)
            descriptor = try_slot(directory)
            if descriptor is not None:
                break
            if not notified:
                notified = True
                waiting()
            await asyncio.sleep(min(0.05, remaining))
        # Recheck after the filesystem operation and before any reservation or dispatch.
        if stopped() or time.monotonic() >= until:
            raise InferenceError("cancelled" if stopped() else "deadline", "Inference stopped before dispatch.", consumption_unknown=False)
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
