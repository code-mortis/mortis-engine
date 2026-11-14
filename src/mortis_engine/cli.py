"""Command-line connection setup for the standalone Mortis package."""

import argparse
import copy
import getpass
import json
import sys
import warnings
from importlib.resources import files
from uuid import uuid4

from keyring.errors import KeyringError
from pydantic import ValidationError

from mortis_engine import __version__, config, gateway
from mortis_engine.llm_http import InferenceError
from mortis_engine.schemas import ConnectionInput
from mortis_engine.security import clean_text

PROTOCOLS = ("openai", "openai-responses", "anthropic", "anthropic-bedrock")
OPTIONS = "timeout_sec, prompt_token_ceiling, retry_codes, extra_body, auth_header, aws_region, aws_profile"


def object_json(value):
    try:
        result = json.loads(value)
    except ValueError as exc:
        raise ValueError("Enter a valid JSON object") from exc
    if not isinstance(result, dict):
        raise ValueError("Enter a JSON object")
    return result


def ask(label, default=""):
    value = input(f"{label}" + (f" [{default}]" if default else "") + ": ").strip()
    return value or default


def masked(label):
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(f"{label}: ")
        except getpass.GetPassWarning as exc:
            raise ValueError("This terminal cannot hide input. Use environment variable references.") from exc


def providers():
    return json.loads(files("mortis_engine").joinpath("providers.json").read_text(encoding="utf-8"))


def connect(args):
    if args.api_key_env is not None and args.ambient_auth:
        raise ValueError("Choose --api-key-env or --ambient-auth for this connection")
    if args.list_providers:
        for item in providers():
            print(f"{item['name']}  {item['displayName']}  ({item['protocol']})")
        return
    interactive = sys.stdin.isatty() and not args.non_interactive
    if args.advanced and (not interactive or args.test or args.list_models):
        raise ValueError("--advanced requires interactive connection setup. For scripts use --options and --headers-env.")
    name = args.name or (ask("Connection name", "default") if interactive else "default")
    if not name.strip() or len(name) > 100 or any(ord(c) < 32 for c in name):
        raise ValueError("Connection names must have 1–100 printable characters")
    previous = config.load()["connections"].get(name)
    if args.test or args.list_models:
        if any(value is not None for value in (args.provider, args.protocol, args.base_url, args.model,
                                              args.api_key_env, args.headers_env, args.options)) or args.ambient_auth:
            raise ValueError("Use --test or --list-models alone with a saved connection")
        saved = config.profile(name)
        secrets = config.credentials(saved)
        if args.list_models:
            for identifier, label in discover(name, saved, secrets):
                print(f"{identifier}  {label}")
        else:
            check(name, saved, secrets)
        return
    old = copy.deepcopy(previous or {})
    preset_names = {item["name"]: item for item in providers()}
    provider = args.provider
    if provider is None and (previous or name in preset_names):
        provider = old.get("provider") or name
    if provider is None:
        if interactive:
            print("Providers: " + ", ".join(preset_names))
            provider = ask("Provider", old.get("provider", "openai"))
        else:
            provider = old.get("provider", "openai")
    if provider not in preset_names:
        raise ValueError("Choose a listed provider or use custom. See mortis connect --list-providers.")
    preset = preset_names[provider]
    same_provider = provider == old.get("provider")
    protocol = args.protocol or (old.get("protocol") if same_provider else None) or preset["protocol"]
    base_url = args.base_url
    if base_url is None:
        base_url = (old.get("base_url") if same_provider else None) or preset["baseUrl"]
    model = args.model if args.model is not None else old.get("model", "") if same_provider else ""
    if interactive:
        if args.protocol is None and (args.advanced or provider == "custom"):
            protocol = ask("API protocol (" + ", ".join(PROTOCOLS) + ")", protocol)
        if args.base_url is None and protocol != "anthropic-bedrock" and (args.advanced or not base_url):
            base_url = ask("API base URL", base_url)
        if args.model is None:
            model = ask("Model (enter ? to list available models)", model)
    if protocol not in PROTOCOLS:
        raise ValueError("Choose one of the four supported API protocols")
    if not model.strip():
        raise ValueError("Choose a model with --model or use an interactive terminal")
    options = object_json(args.options) if args.options is not None else copy.deepcopy(old.get("options", {})) if same_provider else {}
    if args.advanced and args.options is None and ask("Configure advanced options? (y/N)", "n").lower() == "y":
        print("Allowed options: " + OPTIONS)
        options = object_json(ask("Provider options JSON", json.dumps(options)))
    saved = {"provider": provider, "protocol": protocol, "base_url": base_url, "model": model.strip(),
             "options": options}
    reuse = bool(previous) and config.credential_destination(saved) == config.credential_destination(old)
    secrets = {"api_key": "", "extra_headers": {}}
    retain_key = reuse
    retain_headers = reuse
    api_key_env = old.get("api_key_env") if reuse else None
    headers_env = copy.deepcopy(old.get("headers_env", {})) if reuse else {}
    if args.api_key_env:
        api_key_env = args.api_key_env
        retain_key = False
    if args.headers_env is not None:
        headers_env = object_json(args.headers_env)
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in headers_env.items()):
            raise ValueError("Header environment references must be a JSON object of names to variable names")
        retain_headers = False
    if args.ambient_auth:
        if protocol != "anthropic-bedrock":
            raise ValueError("--ambient-auth requires the Bedrock protocol")
        api_key_env, retain_key = None, False
    if interactive and args.api_key_env is None and not args.ambient_auth:
        hint = "input hidden"
        if retain_key:
            hint += "; Enter keeps current authentication"
        elif protocol == "anthropic-bedrock":
            hint += "; Enter uses AWS credentials"
        value = masked(f"API key ({hint})")
        if value:
            secrets["api_key"] = value
            api_key_env, retain_key = None, False
        elif not retain_key and protocol != "anthropic-bedrock":
            raise ValueError("An API key is required. Run connect again or use --api-key-env VARIABLE.")
    if args.advanced and args.headers_env is None and ask("Configure extra headers? (y/N)", "n").lower() == "y":
        method = ask("Header storage (keyring/env)", "keyring")
        if method == "env":
            entered = ask("Header to environment variable JSON (Enter to keep, {} to clear)")
            if entered:
                headers_env = object_json(entered)
                retain_headers = False
        elif method == "keyring":
            entered = masked("Extra headers JSON (Enter to keep, {} to clear)")
            if entered.strip():
                secrets["extra_headers"] = object_json(entered)
                headers_env = {}
                retain_headers = False
        else:
            raise ValueError("Choose keyring or env header storage")
    if reuse:
        previous_credentials = config.credentials(old, api_key=retain_key, headers=retain_headers)
        if retain_key:
            secrets["api_key"] = previous_credentials["api_key"]
        if retain_headers:
            secrets["extra_headers"] = previous_credentials["extra_headers"]
    if api_key_env:
        secrets["api_key"] = config.environment(api_key_env)
    for header, source in headers_env.items():
        secrets["extra_headers"][header] = config.environment(source)
    if not secrets["api_key"] and protocol != "anthropic-bedrock":
        raise ValueError("Provide --api-key-env or enter an API key. Local endpoints may use a placeholder.")
    if previous and not reuse and not args.api_key_env and not args.ambient_auth and not interactive:
        raise ValueError("The credential destination changed. Explicitly choose --api-key-env or --ambient-auth.")
    if api_key_env:
        saved["api_key_env"] = api_key_env
    if headers_env:
        saved["headers_env"] = headers_env
    stored = {"api_key": "" if api_key_env else secrets["api_key"],
              "extra_headers": {key: value for key, value in secrets["extra_headers"].items() if key not in headers_env}}
    credential_id = None
    backend = config.vault() if stored["api_key"] or stored["extra_headers"] else None
    if saved["model"] == "?" and interactive:
        # Discovery follows credential selection so draft endpoints never inherit another profile's key.
        candidate = {**saved, "model": old.get("model") or "model-discovery"}
        ConnectionInput(name=name, **saved_connection(candidate), **secrets)
        try:
            rows = discover(name, candidate, secrets)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            rows = []
        for index, (identifier, label) in enumerate(rows, start=1):
            print(f"{index}. {identifier}  {label}")
        selected = ask("Model ID or listed number")
        if selected.isdecimal() and 1 <= int(selected) <= len(rows):
            selected = rows[int(selected) - 1][0]
        saved["model"] = selected
    check(name, saved, secrets)
    if stored["api_key"] or stored["extra_headers"]:
        credential_id = uuid4().hex
        backend.set_password(config.SERVICE, credential_id, json.dumps({
            "destination": list(config.credential_destination(saved)), "credentials": stored,
        }))
        saved["credential_id"] = credential_id
    try:
        config.save(name, saved, previous)
    except BaseException:
        if credential_id:
            try:
                backend.delete_password(config.SERVICE, credential_id)
            except KeyringError:
                pass
        raise
    if old_id := old.get("credential_id"):
        try:
            config.vault().delete_password(config.SERVICE, old_id)
        except (ValueError, KeyringError):
            print("Connection saved. Its previous vault entry could not be removed.", file=sys.stderr)
    print(f"Saved connection {name!r} in {config.config_path()}")


def discover(name, saved, secrets):
    from mortis_engine.discovery import list_models

    try:
        ConnectionInput(name=name, **saved_connection(saved), **secrets)
        return list_models(config.connection(name, saved), secrets)
    except ValueError as exc:
        raise ValueError(clean_text(str(exc), (secrets["api_key"], *secrets["extra_headers"].values()))) from exc


def check(name, saved, secrets):
    try:
        ConnectionInput(name=name, **saved_connection(saved), **secrets)
        print(f"Testing connection {name!r} ({saved['provider']} / {saved['model']})…")
        result = gateway.test_connection(config.connection(name, saved), secrets)
    except InferenceError as exc:
        raise ValueError(f"Connection {name!r} failed ({exc.code}): {clean_text(str(exc), (secrets['api_key'], *secrets['extra_headers'].values()))}") from exc
    print(result)


def saved_connection(saved):
    return {key: saved[key] for key in ("provider", "protocol", "base_url", "model", "options") if key in saved}


def show(args):
    connections = {args.name: config.profile(args.name)} if args.name else config.load()["connections"]
    safe = {}
    for name, saved in connections.items():
        safe[name] = saved_connection(saved)
        safe[name]["authentication"] = "OS credential vault" if saved.get("credential_id") else "AWS" if saved.get("protocol") == "anthropic-bedrock" and not saved.get("api_key_env") else "environment"
        safe[name]["api_key_env"] = saved.get("api_key_env")
        safe[name]["headers_env"] = saved.get("headers_env", {})
    print(json.dumps({"path": str(config.config_path()), "connections": safe}, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mortis", description="Configure Mortis Engine's AI connections.")
    parser.add_argument("--version", action="version", version=f"mortis {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("connect", help="Create or update and test an AI connection")
    setup.add_argument("name", nargs="?", help="Profile name; a new profile named after a provider uses its preset")
    setup.add_argument("--provider", help="Provider preset, including custom")
    setup.add_argument("--protocol", choices=PROTOCOLS)
    setup.add_argument("--base-url", help="Provider API URL")
    setup.add_argument("--model", help="Provider model identifier")
    setup.add_argument("--api-key-env", metavar="VARIABLE", help="Read the API key from this environment variable")
    setup.add_argument("--headers-env", metavar="JSON", help='Header environment references, e.g. {"X-Token":"TOKEN"}')
    setup.add_argument("--ambient-auth", action="store_true", help="Use ambient AWS credentials with Bedrock")
    setup.add_argument("--options", metavar="JSON", help="Provider options object: " + OPTIONS)
    setup.add_argument("--advanced", action="store_true", help="Interactively edit protocol, endpoint, provider options and extra headers")
    setup.add_argument("--non-interactive", action="store_true", help="Use supplied flags and saved values without prompts")
    inspection = setup.add_mutually_exclusive_group()
    inspection.add_argument("--test", action="store_true", help="Test an existing saved connection")
    inspection.add_argument("--list-models", action="store_true", help="List models for an existing saved connection")
    setup.add_argument("--list-providers", action="store_true", help="Show the provider presets")
    setup.set_defaults(run=connect)
    configuration = commands.add_parser("config", help="Inspect saved connection configuration")
    inspect = configuration.add_subparsers(dest="config_command", required=True).add_parser("show", help="Show configuration with credentials omitted")
    inspect.add_argument("name", nargs="?")
    inspect.set_defaults(run=show)
    args = parser.parse_args(argv)
    try:
        args.run(args)
        return 0
    except ValidationError as exc:
        errors = [".".join(map(str, error["loc"])) + ": " + error["msg"] for error in exc.errors(include_input=False)]
        print("Invalid connection configuration. " + "; ".join(errors), file=sys.stderr)
    except KeyringError:
        print("The OS credential vault could not be accessed. Use environment variable references or unlock the vault.", file=sys.stderr)
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("Connection setup cancelled.", file=sys.stderr)
        return 130
    return 1
