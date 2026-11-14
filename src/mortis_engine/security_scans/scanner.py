"""Run trusted Trivy with credential-free configuration and offline source analysis."""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from mortis_engine.security import digest

from .engine import normalize_trivy

VERSION = "0.76.0"


def state_directory():
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "mortis"
    if not root.is_absolute():
        raise ValueError("XDG_STATE_HOME must be an absolute directory.")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077
            or hasattr(os, "geteuid") and info.st_uid != os.geteuid()):
        raise ValueError("Mortis security state must be an owned private directory (chmod 700).")
    return root


def identity_key(*, create=True):
    root = state_directory()
    path = root / "security.key"
    # Publish a fully written key atomically so concurrent scans always read the same identity.
    if not path.exists() and create:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".security-key-") as candidate:
            candidate.write(os.urandom(32))
            candidate.flush()
            os.fsync(candidate.fileno())
            try:
                os.link(candidate.name, path)
            except FileExistsError:
                pass
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                    or hasattr(os, "geteuid") and info.st_uid != os.geteuid()):
                raise ValueError("Mortis security identity key must be an owned private regular file (chmod 600).")
            value = stream.read(33)
    except OSError as exc:
        raise ValueError("Mortis security identity key cannot be read safely.") from exc
    if len(value) != 32:
        raise ValueError("Mortis security identity key is invalid.")
    return value


def command(args, root, env, timeout, stopped):
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        child = subprocess.Popen(args, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=errors, start_new_session=True)
        deadline = time.monotonic() + timeout
        try:
            while child.poll() is None:
                if stopped():
                    raise ValueError("Security scan cancelled.")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Trivy reached its time limit.")
                if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > 64_000_000:
                    raise ValueError("Trivy output exceeds the 64 MB scanner limit.")
                try:
                    child.wait(timeout=0.05)
                except subprocess.TimeoutExpired:
                    pass
            if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > 64_000_000:
                raise ValueError("Trivy output exceeds the 64 MB scanner limit.")
            output.seek(0)
            errors.seek(0)
            return child.returncode, output.read(64_000_001).decode("utf-8"), errors.read(64_000_001).decode("utf-8")
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


def offline_prefix():
    if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
        return ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny network*)"]
    if sys.platform == "linux":
        return [sys.executable, str(Path(__file__).with_name("sandbox.py"))]
    raise ValueError("Offline Trivy isolation requires macOS sandbox-exec or Linux seccomp.")


def trivy(item, timeout=300, stopped=lambda: False):
    binary = shutil.which("trivy", path=os.pathsep.join(
        part for part in os.environ.get("PATH", os.defpath).split(os.pathsep) if Path(part).is_absolute()))
    if binary is None:
        raise ValueError(f"Security scanning requires Trivy {VERSION}. Install Trivy and add it to PATH.")
    original = getattr(item, "repository", None)
    if original is not None and Path(binary).resolve().is_relative_to(original):
        raise ValueError("Trivy must be installed outside the repository being scanned.")
    cache = state_directory() / "trivy"
    cache.mkdir(mode=0o700, exist_ok=True)
    if cache.is_symlink() or not cache.is_dir() or cache.stat().st_mode & 0o077:
        raise ValueError("Trivy cache must be a private directory (chmod 700).")
    env = {"PATH": os.pathsep.join(part for part in os.environ.get("PATH", os.defpath).split(os.pathsep)
                                  if Path(part).is_absolute()), "HOME": str(item.root), "LC_ALL": "C",
           "TRIVY_DISABLE_TELEMETRY": "true", "TRIVY_SKIP_VERSION_CHECK": "true"}
    code, version, _ = command([binary, "--version"], item.root, env, 10, stopped)
    if code or not re.search(rf"\b{re.escape(VERSION)}\b", version):
        raise ValueError(f"Security scanning requires Trivy {VERSION}.")
    overrides = []
    for path in item.inventory:
        target = item.files / path
        original = target.read_text(encoding="utf-8")
        inert = re.sub(r"\b(?:trivy|tfsec):ignore", "cmort:ignore", original)
        if inert != original:
            target.write_text(inert, encoding="utf-8")
            item.inventory[path]["scanner_hash"] = digest(inert)
            overrides.append(path)
    trusted, ignores, secrets = (item.root / name for name in ("config.yaml", "ignore", "secrets.yaml"))
    trusted.write_text("{}\n")
    ignores.write_text("")
    secrets.write_text("skip-patterns: []\n")
    args = [binary, "fs", "--cache-dir", str(cache), "--config", str(trusted), "--disable-telemetry",
            "--skip-version-check", "--ignorefile", str(ignores), "--secret-config", str(secrets)]
    update, _, _ = command([*args, "--download-db-only"], item.root, env, min(timeout, 300), stopped)
    metadata = {}
    try:
        metadata = json.loads((cache / "db/metadata.json").read_text())
        updated = datetime.fromisoformat(metadata["UpdatedAt"].replace("Z", "+00:00"))
        fresh = updated.utcoffset() is not None and 0 <= (datetime.now(UTC) - updated).total_seconds() <= 86400
    except (OSError, ValueError, KeyError, TypeError):
        fresh = False
    empty = item.root / "check-update"
    empty.mkdir()
    command([*args, "--scanners", "misconfig", "--skip-db-update", "--offline-scan", str(empty)],
            item.root, env, min(timeout, 300), stopped)
    scanners = "vuln,misconfig,secret" if fresh else "misconfig,secret"
    code, output, errors = command([*offline_prefix(), *args, "--scanners", scanners, "--format", "json",
        "--offline-scan", "--skip-check-update", "--skip-db-update", "--skip-java-db-update", "--list-all-pkgs",
        "--include-dev-deps", "--include-non-failures", "--parallel", "2", str(item.files)],
        item.root, env, timeout, stopped)
    if code:
        raise ValueError("Trivy could not complete secret and configuration scanning. AI analysis was withheld.")
    try:
        data = json.loads(output)
        if not isinstance(data, dict) or data.get("SchemaVersion") != 2 or not isinstance(data.get("Results", []), list):
            raise ValueError("Unsupported Trivy report schema.")
        result = normalize_trivy(item, data)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError("Trivy returned an unsupported security report. AI analysis was withheld.") from exc
    result["metadata"] = {"version": VERSION, "database": metadata, "database_fresh": fresh,
        "database_update_succeeded": update == 0, "scanners": scanners.split(","), "offline": True,
        "network_isolation": "OS sandbox", "inline_suppressions_disabled": overrides,
        "secret_policy": "all built-in rules; no repository configuration"}
    if not fresh:
        result["problems"].append("Vulnerability database is missing or older than 24 hours.")
    if re.search(r"\b(?:WARN|ERROR|FATAL)\b", errors):
        result["problems"].append("Trivy reported a warning or analysis error; complete coverage is unestablished.")
    return result
