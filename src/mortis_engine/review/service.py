"""Public entry point shared by the CLI and other local callers."""

import time
from collections.abc import Callable
from pathlib import Path

from . import engine, planning, settings, source


def review_commits(
    repository: str | Path, *, base: str, head: str, connection: dict,
    get_credentials: Callable[[], dict], on_plan: Callable[[int, str], None] | None = None,
) -> dict:
    """Review pinned commits. Resolve credentials only if there is work to dispatch."""
    started = time.monotonic()
    with source.local_repository(repository) as (repo, env):
        base_sha = source.resolve_commit(repo, env, base)
        head_sha = source.resolve_commit(repo, env, head)
        ancestor = source.merge_base(repo, env, base_sha, head_sha)
        options = settings.load(repo, env, base_sha)
        deadline = started + options["timeout_seconds"] if options["timeout_seconds"] else None

        def check_deadline():
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("The review reached its configured time limit.")
            return False

        check_deadline()
        connection = {**connection, "model": options["model"] or connection["model"],
                      "options": {"timeout_sec": 300, **connection.get("options", {})}}
        ceiling = connection["options"].get("prompt_token_ceiling")
        # The transport checks the final encoded request after the planner splits source.
        unit_bytes = max(1024, min(49152, (ceiling - 8192) // 2)) if ceiling is not None else 49152
        plan = planning.plan_review(repo, env, base_sha, head_sha, ancestor, options, check_deadline,
                                    max_unit_bytes=unit_bytes)
        if on_plan is not None and plan.batches:
            on_plan(len(plan.selected), connection["model"])
        check_deadline()
        credentials = get_credentials() if plan.batches else {}
        status, report, findings = engine.run(repo, env, plan, connection, options, credentials,
                                              deadline=deadline)

    return {
        "schema_version": "mortis.review/v1",
        "status": status,
        "base_sha": base_sha, "head_sha": head_sha, "merge_base": ancestor,
        "provider": connection["provider"], "model": connection["model"],
        "findings": findings,
        "coverage": report["manifest"]["coverage"],
        "units": [{k: v for k, v in unit.items() if k != "comments"} for unit in report["units"]],
        "excluded_files": report["excluded_files"],
        "errors": report["errors"], "warnings": report["warnings"],
        "usage": {**{k: v for k, v in report["summary"].items() if k != "findings"}, "requests": report["usage"]},
    }
