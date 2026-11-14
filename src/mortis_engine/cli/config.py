"""Inspect saved profiles and the settings at a given Git revision."""

import json

from mortis_engine import config
from mortis_engine.review import settings, source

from .connect import saved_connection


def show(args):
    connections = {args.name: config.profile(args.name)} if args.name else config.load()["connections"]
    safe = {}
    for name, saved in connections.items():
        safe[name] = saved_connection(saved)
        safe[name]["authentication"] = "OS credential vault" if saved.get("credential_id") else "AWS" if saved.get("protocol") == "anthropic-bedrock" and not saved.get("api_key_env") else "environment"
        safe[name]["api_key_env"] = saved.get("api_key_env")
        safe[name]["headers_env"] = saved.get("headers_env", {})
    output = {"path": str(config.config_path()), "connections": safe}
    base = args.base or "HEAD"
    if args.base or args.repo or source.find_repository(".") is not None:
        with source.local_repository(args.repo or ".") as (repo, env):
            sha = source.resolve_commit(repo, env, base)
            options = settings.load(repo, env, sha)
            scan = settings.load_scan(repo, env, sha)
        if not options["model"] and len(connections) == 1:
            options["model"] = next(iter(connections.values()))["model"]
        output["review"] = {"base_sha": sha, "settings": options}
        if not scan["model"] and len(connections) == 1:
            scan["model"] = next(iter(connections.values()))["model"]
        output["scan"] = {"sha": sha, "settings": scan}
    print(json.dumps(output, indent=2))


def add_parser(commands):
    configuration = commands.add_parser("config", help="Inspect saved connection configuration")
    inspect = configuration.add_subparsers(dest="config_command", required=True).add_parser("show", help="Show configuration with credentials omitted")
    inspect.add_argument("name", nargs="?")
    inspect.add_argument("--base", help="Show repository settings at this commit (default: HEAD)")
    inspect.add_argument("--repo", help="Local repository directory")
    inspect.set_defaults(run=show)
