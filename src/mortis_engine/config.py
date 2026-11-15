"""User connection profiles and credentials, independent of application services."""

import json
import os
import re
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path

import keyring
import tomli_w

SERVICE = "mortis-engine"
VAULT_BACKENDS = {
    "keyring.backends.macOS", "keyring.backends.Windows", "keyring.backends.SecretService",
    "keyring.backends.libsecret",
}


def config_path():
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    if not root.is_absolute():
        raise ValueError("XDG_CONFIG_HOME must be an absolute path")
    return root / "mortis" / "config.toml"


def load():
    try:
        with config_path().open("rb") as stream:
            data = tomllib.load(stream)
    except FileNotFoundError:
        return {"version": 1, "connections": {}}
    except tomllib.TOMLDecodeError as exc:
        raise ValueError("The Mortis configuration contains invalid TOML") from exc
    if data.get("version") != 1 or not isinstance(data.get("connections"), dict):
        raise ValueError("Expected Mortis configuration version 1 and a connections table")
    if any(not isinstance(value, dict) for value in data["connections"].values()):
        raise ValueError("Each saved connection must be a TOML table")
    for saved in data["connections"].values():
        if "options_json" in saved:
            if "options" in saved:
                raise ValueError("A profile must use options or options_json, not both")
            try:
                saved["options"] = json.loads(saved.pop("options_json"))
                if not isinstance(saved["options"], dict):
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise ValueError("The saved provider options must be a JSON object") from exc
    for name, saved in data["connections"].items():
        validate_profile(name, saved)
    return data


def profile(name):
    try:
        return load()["connections"][name]
    except KeyError as exc:
        raise ValueError(f"Connection {name!r} does not exist. Run mortis connect {name}.") from exc


def validate_profile(name, saved):
    allowed = {"provider", "protocol", "base_url", "model", "options", "credential_id", "api_key_env", "headers_env"}
    if set(saved) - allowed:
        raise ValueError("A saved profile contains unsupported fields")
    if "credential_id" in saved and (not isinstance(saved["credential_id"], str) or not saved["credential_id"]
                                      or len(saved["credential_id"]) > 200):
        raise ValueError("A saved credential ID must be nonempty text")
    if "api_key_env" in saved:
        environment_name(saved["api_key_env"])
    headers = saved.get("headers_env", {})
    if not isinstance(headers, dict) or any(not isinstance(key, str) or not key for key in headers):
        raise ValueError("Saved header references must map header names to environment variable names")
    for source in headers.values():
        environment_name(source)
    connection(name, saved)


def environment_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("Credential references must be environment variable names")


def environment(name):
    environment_name(name)
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Set environment variable {name} before testing this connection")
    return value


def vault():
    backend = keyring.get_keyring()
    if type(backend).__module__ not in VAULT_BACKENDS:
        raise ValueError("An OS credential vault is unavailable. Use --api-key-env and --headers-env.")
    return backend


def credentials(saved, *, api_key=True, headers=True):
    value = {"api_key": "", "extra_headers": {}}
    if (api_key or headers) and (identity := saved.get("credential_id")):
        raw = vault().get_password(SERVICE, identity)
        if raw is None:
            raise ValueError("The connection's stored credentials are missing. Run mortis connect again.")
        try:
            envelope = json.loads(raw)
            if (not isinstance(envelope, dict)
                    or envelope.get("destination") != list(credential_destination(saved))):
                raise ValueError("Stored credentials belong to a different provider destination. Run mortis connect again.")
            value = envelope.get("credentials")
            if (not isinstance(value, dict) or set(value) != {"api_key", "extra_headers"}
                    or not isinstance(value["api_key"], str) or not isinstance(value["extra_headers"], dict)
                    or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value["extra_headers"].items())):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ValueError("The stored credentials are invalid. Run mortis connect again.") from exc
    if not api_key:
        value["api_key"] = ""
    if not headers:
        value["extra_headers"] = {}
    if api_key and (source := saved.get("api_key_env")):
        value["api_key"] = environment(source)
    for header, source in (saved.get("headers_env", {}) if headers else {}).items():
        value["extra_headers"][header] = environment(source)
    return value


def connection(name, saved):
    from mortis_engine.schemas import ConnectionInput

    from .admission import connection_key

    value = ConnectionInput(name=name, **{key: saved[key] for key in (
        "provider", "protocol", "base_url", "model", "options"
    ) if key in saved}).model_dump(exclude={"api_key", "extra_headers", "name"})
    references = {key: saved[key] for key in ("credential_id", "api_key_env") if saved.get(key)}
    if saved.get("headers_env"):
        references["headers_env"] = {key.lower(): source for key, source in saved["headers_env"].items()}
    value["_admission_key"] = connection_key(value, references)
    return value


def credential_destination(value):
    options = value.get("options", {})
    return (value.get("provider"), value.get("protocol"), value.get("base_url", "").rstrip("/"),
            *(options.get(key) for key in ("aws_region", "aws_profile", "auth_header")))


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+b") as stream:
        if os.name == "nt":
            import msvcrt

            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def save(name, saved, expected):
    """Merge under a lock, rejecting edits made while the provider test ran."""
    path = config_path()
    with locked(path):
        data = load()
        if data["connections"].get(name) != expected:
            raise ValueError("This connection changed during testing. Run mortis connect again.")
        data["connections"][name] = saved
        # TOML has no null; provider JSON must retain nulls and nested values exactly.
        data["connections"] = {key: {**{k: v for k, v in item.items() if k != "options"},
                                     "options_json": json.dumps(item.get("options", {}), allow_nan=False)}
                               for key, item in data["connections"].items()}
        descriptor, temporary = tempfile.mkstemp(prefix="config-", suffix=".toml", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                tomli_w.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
