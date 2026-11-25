"""An unchanged cross-language caller travels through the public CLI and saved report."""

import asyncio
import json

import pytest

from mortis_engine import cli, gateway, reporting
from mortis_engine.review import affected_callers, source, summary, workflows
from mortis_engine.review.settings import RepoSettings
from mortis_engine.schemas import Response, Usage

from .git_helpers import commit, git
from .test_command import profile as profile
from .test_workflows import Session, decision, selected


@pytest.mark.parametrize("fallback", [False, True])
def test_deleted_script_from_preview_through_assessment(repo, profile, monkeypatch, capsys, fallback):
    script = "scripts/charge.sh"
    call = 'subprocess.run(["scripts/charge.sh"], check=True)  # \ufffd'
    app = "import subprocess\n" + (
        "try:\n    " + call + "\nexcept FileNotFoundError:\n    use_builtin_charge()\n" if fallback else call + "\n"
    )
    base = commit(repo, {script: "echo charge  # \ufffd\n", "payments.py": app})
    git(repo, "rm", script)
    git(repo, "commit", "-qm", "remove charge script")
    args = ["review", "--repo", str(repo), "--base", base, "--format", "json"]
    assert cli.main([*args, "--preview"]) == 0
    preview = json.loads(capsys.readouterr().out)
    site, = preview["affected_callers"]["sites"]
    assert site["path"] == "payments.py"
    assert preview["selected_files"] == []
    line = 3 if fallback else 2
    seen = []

    async def respond(connection, credentials, request, **kwargs):
        fields = request.output_schema["properties"]
        body = request.messages[0].content
        payload = json.loads(body.split("\n\n", 2)[1] if body.startswith("# ") else body)
        if "completed_units" in fields:
            unit, = payload["assigned_units"]
            assert unit["key"] == site["unit_key"]
            source = next(item for item in payload["citation_sources"] if item["path"] == "payments.py")
            seen.append("analysis")
            output = {"task_key": payload["task_key"], "completed_units": [unit["key"]], "findings": [{
                "unit_key": unit["key"], "path": "payments.py", "start_line": line, "end_line": line,
                "content": "The removed script is still invoked.", "severity": "high", "category": "correctness",
                "trigger": "Charge a payment.", "consequence": "Payment processing raises FileNotFoundError.",
                "evidence": [{"source_id": source["source_id"], "start_line": line, "end_line": line}]}]}
        elif "verdict" in fields:
            seen.append("assessment")
            assert payload["original_sha"] == base
            assert 'original source: "scripts/charge.sh"' in body
            assert "1 | echo charge  # \ufffd" in body
            assert 'current source: "payments.py"' in body
            assert all(f"{i} | {value}" in body for i, value in enumerate(app.splitlines(), 1))
            output = decision(verdict="rejected" if fallback else "confirmed",
                              evidence=[selected(payload, "original", script, 1),
                                        selected(payload, "current", "payments.py", line)])
        else:
            seen.append("summary")
            assert script in payload["paths"]
            assert payload["affected_callers"]["sites"][0]["path"] == "payments.py"
            output = {"narrative": "Removed the charge script and examined its payment caller.", "groups": []}
        return Response(output=output, usage=Usage(total_tokens=3))

    monkeypatch.setattr(gateway, "send", respond)
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert seen == ["analysis", "assessment", "summary"]
    assert result["status"] == result["workflow_status"] == "completed"
    assert result["coverage"]["completed"] == []
    assert result["affected_callers"]["sites"][0]["status"] == "completed"
    assert "Affected callers" in reporting.markdown(result)
    if fallback:
        assert result["findings"] == []
        assert result["candidates"][0]["result"]["verdict"] == "rejected"
    else:
        finding, = result["findings"]
        assert finding["inline_line"] is None
        assert finding["assessment"]["evidence"][0]["revision"] == "original"
    assert cli.main(["runs", result["run_id"], "--repo", str(repo)]) == 0
    assert json.loads(capsys.readouterr().out)["affected_callers"] == result["affected_callers"]


def test_discovery_limit_sets_preview_and_review_exit_code(repo, profile, monkeypatch, capsys):
    base = commit(repo, {"tool.sh": "exit 0\n", "run.py": 'command = "tool.sh"\n'})
    git(repo, "rm", "tool.sh")
    git(repo, "commit", "-qm", "delete")
    args = ["review", "--repo", str(repo), "--base", base, "--format", "json"]
    monkeypatch.setattr(affected_callers, "MAX_FILES", 0)
    monkeypatch.setattr(gateway, "send", lambda *a, **kw: pytest.fail("No caller units to dispatch"))
    for flags in (["--preview"], []):
        assert cli.main([*args, *flags]) == 2
        result = json.loads(capsys.readouterr().out)
        assert result["affected_callers"]["sites"] == []
        assert "scanned_files" in result["affected_callers"]["limits_reached"]
    assert result["status"] == result["workflow_status"] == "partial"
    assert "tool.sh" in result["summary"]["result"]["narrative"]


@pytest.mark.parametrize(("coverage", "caller_fields", "incomplete"), [
    ({}, {"sites": []}, False),
    ({"coverage": {}}, {"sites": []}, False),
    ({"coverage": {"selected": []}}, {}, True),
])
def test_caller_summary_handles_missing_coverage_or_sites(repo, coverage, caller_fields, incomplete):
    base = commit(repo, {"retired.py": "pass\n"})
    git(repo, "rm", "retired.py")
    head = commit(repo, {})
    report = {"base_sha": base, "head_sha": head, **coverage,
              "affected_callers": {"triggers": [{"path": "retired.py"}], "limits_reached": [], **caller_fields}}
    session = Session(None)
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.generate_summary(isolated, env, report, {}, session))
    assert result["status"] == "completed"
    assert result["limited_context"] is incomplete
    expected = "Caller discovery was incomplete." if incomplete else "No matching caller sites were found."
    assert result["result"]["narrative"] == f"Removed paths: retired.py. {expected}"
    assert session.calls == []


@pytest.mark.parametrize("unit_key", [None, "assigned"])
def test_local_summary_rechecks_pinned_caller_scope(repo, unit_key):
    base = commit(repo, {"retired.py": "# retired\n", "moved.py": "# moved\n", ".env": "example\n",
                         "caller.py": "import retired\n", "ignored.py": "import retired\n"})
    git(repo, "rm", "retired.py", ".env")
    git(repo, "mv", "moved.py", "private.py")
    head = commit(repo, {})
    triggers = [{"path": path, "new_path": new, "status": "renamed" if new else "deleted"}
                for path, new in [("retired.py", None), ("ghost.py", None), (".env", None), ("moved.py", "private.py")]]
    sites = [{"path": path, "unit_key": unit_key, "status": "completed", "start_line": 1, "end_line": 1}
             for path in ["caller.py", "ignored.py"]]
    report = {"base_sha": base, "head_sha": head, "affected_callers": {"triggers": triggers, "sites": sites}}
    options = {**RepoSettings().model_dump(), "exclusions": "private.py,ignored.py", "summary_token_budget": 1}
    session = Session(None)
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.generate_summary(isolated, env, report, options, session))
    assert result["result"]["narrative"] == "Removed paths: retired.py. Examined 1 of 1 planned caller ranges."
    assert session.calls == []


def test_missing_caller_sites_preserves_changed_file_summary(repo):
    base = commit(repo, {"app.py": "value = 1\n", "old.py": "pass\n"})
    git(repo, "rm", "old.py")
    head = commit(repo, {"app.py": "value = 2\n"})
    report = {"base_sha": base, "head_sha": head, "coverage": {"selected": [{"path": "app.py"}]},
              "affected_callers": {"triggers": [{"path": "old.py", "new_path": None, "status": "deleted"}]}}
    session = Session(lambda stage, context: {"narrative": "Updated app.py and removed old.py.", "groups": []})
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows.generate_summary(isolated, env, report, RepoSettings().model_dump(), session))
    assert result["status"] == "completed" and result["limited_context"]
    assert "+value = 2" in session.calls[0][1]["context"]["diff"]


def test_summary_clips_caller_details_before_changing_primary_diff(repo):
    base = commit(repo, {"old.py": "# retired source only\n", "primary.py": "answer = 0\n",
                         "caller.py": "import old\n" * 256})
    git(repo, "rm", "old.py")
    head = commit(repo, {"primary.py": "answer = 1\n"})
    report = {"base_sha": base, "head_sha": head, "coverage": {"selected": [{"path": "primary.py"}]}}
    session = Session(None)
    session.connection = {"options": {"prompt_token_ceiling": 6500}}
    run = workflows._run(report, RepoSettings().model_dump(), session)
    sites = [{"path": "caller.py", "start_line": i, "end_line": i, "status": "completed"} for i in range(1, 257)]
    scope = {"triggers": [{"path": "old.py", "new_path": None, "status": "deleted"}], "sites": sites,
             "limits_reached": []}
    with source.local_repository(repo) as (isolated, env):
        primary = summary.build_context(isolated, env, run, lambda: False)
        result = summary.build_context(isolated, env, run, lambda: False, affected=scope)
    assert result["diff"] == primary["diff"]
    assert "# retired source" not in result["diff"]
    assert result["limited_context"]
    assert 0 < len(result["affected_callers"]["sites"]) < len(sites)
    assert result["affected_callers"]["triggers"] == scope["triggers"]
