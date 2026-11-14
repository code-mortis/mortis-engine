import json
import re

import pytest

from mortis_engine import cli, config, gateway
from mortis_engine.schemas import Response, Usage

from .git_helpers import commit, git


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path.parent / ("config-" + tmp_path.name)))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path.parent / ("state-" + tmp_path.name)))
    monkeypatch.setenv("MORTIS_FIXTURE_TOKEN", "test-value")
    saved = {"provider": "opencode-go", "model": "saved-model", "protocol": "openai",
             "base_url": "https://opencode.ai/zen/go/v1", "api_key_env": "MORTIS_FIXTURE_TOKEN"}
    config.save("opencode-go", saved, None)
    return saved


def test_review_and_config_use_the_pinned_base_and_saved_profile(repo,
                                                               profile, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    ancestor = commit(repo, {"price.py": "price = 100\n"})
    git(repo, "branch", "feature")
    base = commit(repo, {"mortis.toml": '[review]\nlanguage="Spanish"\nmodel="override"\n'})
    git(repo, "checkout", "-q", "feature")
    head = commit(repo, {"price.py": "price = 0\n", ".env": "LOCAL_FIXTURE=example\n"})
    (repo / "price.py").write_text("uncommitted\n")
    seen = []

    async def send(connection, credentials, request, **kwargs):
        from .test_workflows import decision
        properties = request.output_schema["properties"]
        if "verdict" in properties:
            source_id = re.search(r'"(source_[a-f0-9]+)": \{\s*"revision": "current"', request.messages[0].content)[1]
            duplicate = "history_1" in request.messages[0].content
            return Response(output=decision(relationship="duplicate" if duplicate else "new",
                            finding_id="history_1" if duplicate else None, history_explanation="Same code",
                            evidence=[{"source_id": source_id, "start_line": 1, "end_line": 1}]),
                            usage=Usage(total_tokens=11))
        if "narrative" in properties:
            return Response(output={"narrative": "Actualiza el precio.", "groups": []}, usage=Usage(total_tokens=7))
        payload = json.loads(request.messages[0].content)
        unit, = payload["assigned_units"]
        assert payload["language"] == "Spanish" and unit["source"] == "price = 0\n"
        assert credentials["api_key"] == "test-value"
        seen.append((connection["provider"], connection["model"]))
        finding = {
            "unit_key": unit["key"], "path": "price.py", "start_line": 1, "end_line": 1,
            "severity": "high", "category": "correctness", "content": "El precio queda en cero.",
            "trigger": "Al pagar el producto", "consequence": "El comprador no paga el precio",
            "suggestion_code": "price = 100", "evidence": [{"source_id": payload["citation_sources"][0]["source_id"],
                                                            "start_line": 1, "end_line": 1}],
        }
        return Response(output={"task_key": payload["task_key"], "completed_units": [unit["key"]],
                                "findings": [finding]}, usage=Usage(total_tokens=33))

    monkeypatch.setattr(gateway, "send", send)
    command = ["review", "--base", "main", "--head", "HEAD"]
    assert cli.main([*command, "--format", "json"]) == 0
    out = capsys.readouterr()
    result = json.loads(out.out)
    assert "opencode-go" in out.err and "test-value" not in out.out + out.err
    assert (result["base_sha"], result["head_sha"], result["merge_base"]) == (base, head, ancestor)
    assert result["schema_version"] == "mortis.review/v1" and result["status"] == "completed"
    assert result["findings"][0]["evidence"][0]["quote"] == "price = 0"
    assert result["usage"]["total_tokens"] == 51
    assert result["candidates"][0]["result"]["verdict"] == "confirmed"
    assert [i["path"] for i in result["coverage"]["completed"]] == ["price.py"]
    assert cli.main(command) == 0
    text = capsys.readouterr().out
    assert "price.py:1-1" in text and "price = 0" in text
    assert "Excluded: 1." in text
    assert seen == [("opencode-go", "override")] * 2
    assert cli.main(["config", "show", "--base", "main"]) == 0
    assert json.loads(capsys.readouterr().out)["review"]["settings"]["model"] == "override"


def test_no_eligible_work_does_not_read_credentials_and_file_limit_is_visible(
    repo, profile, monkeypatch, capsys,
):
    monkeypatch.chdir(repo)
    monkeypatch.setattr(config, "credentials", lambda *_: pytest.fail("Unexpected credential read"))
    commit(repo, {"mortis.toml": "[review]\nmax_files = 1\n"})
    argv = ["review", "--base", "main", "--head", "HEAD", "--format", "json"]
    assert cli.main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "skipped" and result["usage"]["requests"] == []
    git(repo, "checkout", "-qb", "feature")
    commit(repo, {"a.py": "a = 1\n", "b.py": "b = 2\n"})
    monkeypatch.setattr(config, "credentials", lambda *_: {"api_key": "fixture"})
    async def summarize(connection, credentials, request, **kwargs):
        assert "narrative" in request.output_schema["properties"]
        return Response(output={"narrative": "Two changed files.", "groups": []}, usage=Usage(total_tokens=7))
    monkeypatch.setattr(gateway, "send", summarize)
    assert cli.main(argv) == 2
    result = json.loads(capsys.readouterr().out)
    assert len(result["coverage"]["waived"]) == 2 and result["usage"]["total_tokens"] == 7


def test_ambiguous_profile_requires_selection(repo, profile, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    commit(repo, {"app.py": "value = 3\n"})
    config.save("other", profile, None)
    command = ["review", "--base", "main"]
    assert cli.main(command) == 1
    assert "--connection" in capsys.readouterr().err
    assert cli.main([*command, "--connection", "opencode-go"]) == 0


@pytest.mark.parametrize("flags,mode", [([], "worktree"), (["--staged"], "staged"), (["--all"], "all")])
def test_standard_local_modes_and_saved_reports(repo, profile, monkeypatch, capsys, flags, mode):
    monkeypatch.chdir(repo)
    commit(repo, {"app.py": "value = 1\n", "README.md": "# Fixture\n"})
    (repo / "draft.py").write_text("new = 2\n")
    git(repo, "add", "draft.py")
    (repo / "draft.py").write_text("new = 3\n")
    seen = []

    async def send(connection, credentials, request, **kwargs):
        schema = request.output_schema["properties"]
        if "narrative" in schema:
            return Response(output={"narrative": "Fixture", "groups": []}, usage=Usage(total_tokens=2))
        payload = json.loads(request.messages[0].content)
        seen.extend((unit["path"], unit["source"]) for unit in payload["assigned_units"])
        assert request.effort == "high"
        if mode == "all":
            assert "introduced by this PR" not in request.instructions
        return Response(output={"task_key": payload["task_key"], "completed_units": [
            u["key"] for u in payload["assigned_units"]], "findings": []}, usage=Usage(total_tokens=5))

    monkeypatch.setattr(gateway, "send", send)
    assert cli.main(["review", *flags, "--effort", "high", "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == mode
    if mode == "all":
        assert dict(seen) == {"app.py": "value = 1\n", "README.md": "# Fixture\n"}
    else:
        assert dict(seen) == {"draft.py": "new = 2\n" if mode == "staged" else "new = 3\n"}
    assert cli.main(["runs", result["run_id"]]) == 0
    assert json.loads(capsys.readouterr().out) == result
    assert cli.main(["review", "--resume", result["run_id"], "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_standalone_commands_use_the_actual_engine(repo, profile, monkeypatch, capsys):
    from mortis_engine.security_scans import service as scans

    monkeypatch.chdir(repo)
    base = commit(repo, {"app.py": "value = 1\n", "README.md": "An accounting project.\n"})
    head = commit(repo, {"app.py": "value = 2\n"})

    async def send(connection, credentials, request, **kwargs):
        fields = request.output_schema["properties"]
        if "background" in fields:
            output = {"background": "An accounting project using Python."}
        elif "narrative" in fields:
            output = {"narrative": "Changed value", "groups": []}
        elif "required_paths" in fields:
            output = {"findings": [], "required_paths": []}
        else:
            data = json.loads(request.messages[0].content)
            output = {"task_key": data["task_key"], "completed_units": [u["key"] for u in data["assigned_units"]], "findings": []}
        return Response(output=output, usage=Usage(total_tokens=3))

    monkeypatch.setattr(gateway, "send", send)
    monkeypatch.setattr(scans, "trivy", lambda *a: {"findings": [], "targets": {}, "metadata": {"database_fresh": True}, "problems": []})
    assert cli.main(["review", "--base", base, "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    for command, args, schema in (("background", [], "mortis.background/v1"), ("verify", ["--run", report["run_id"]], "mortis.verification/v1"),
            ("summary", ["--run", report["run_id"]], "mortis.summary/v1"), ("scan", [], "mortis.security/v1")):
        assert cli.main([command, *args, "--format", "json"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "completed" and result["head_sha"] == head
        assert result["schema_version"] == schema


def test_scan_configuration_is_independent_and_inspectable(repo, profile, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    commit(repo, {"mortis.toml": '[review]\neffort="low"\n[scan]\neffort="high"\nconcurrency=5\nmodel="scan-model"\n'})
    assert cli.main(["config", "show"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["review"]["settings"]["effort"] == "low"
    assert result["scan"]["settings"]["concurrency"] == 5
    assert result["scan"]["settings"]["model"] == "scan-model"


@pytest.mark.parametrize("progress", [False, True])
@pytest.mark.parametrize("output_format", ["text", "json"])
def test_review_progress_is_opt_in_and_elapsed_is_always_on_stderr(
    repo, profile, monkeypatch, capsys, progress, output_format,
):
    monkeypatch.chdir(repo)
    commit(repo, {"app[red].py": "value = 1\n"})
    (repo / "app[red].py").write_text("value = 2\n")

    async def send(connection, credentials, request, **kwargs):
        if "narrative" in request.output_schema["properties"]:
            return Response(output={"narrative": "Changed value", "groups": []}, usage=Usage(total_tokens=2))
        from .test_execution import final
        return final(request)

    monkeypatch.setattr(gateway, "send", send)
    assert cli.main(["review", "--format", output_format, *(["--progress"] if progress else [])]) == 0
    out = capsys.readouterr()
    assert bool("Files in active requests:\n  app[red].py" in out.err) == progress
    assert bool("Files reviewed: 1/1    Failed: 0" in out.err) == progress
    assert bool("AI requests running: 1" in out.err) == progress
    assert bool("Checking findings and writing summary" in out.err) == progress
    assert "units complete" not in out.err
    assert re.search(r"Elapsed: \d+\.\d+s\n$", out.err)
    assert "Elapsed:" not in out.out and "\x1b" not in out.out + out.err
    if output_format == "json":
        assert json.loads(out.out)["status"] == "completed"


@pytest.mark.parametrize("error,code", [(ValueError("Fixture error"), 1), (KeyboardInterrupt(), 130)])
def test_failed_and_interrupted_reviews_still_print_elapsed(repo, profile, monkeypatch, capsys, error, code):
    from mortis_engine.cli import review

    monkeypatch.chdir(repo)
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(review, "review_local", fail)
    assert cli.main(["review", "--progress"]) == code
    out = capsys.readouterr()
    assert "Elapsed:" in out.err and not out.out


def test_live_progress_preserves_literal_paths_and_stdout(monkeypatch, capsys):
    from io import StringIO

    from rich.console import Console

    from mortis_engine.cli import review

    monkeypatch.setenv("TERM", "xterm-256color")
    stream = StringIO()
    console = Console(file=stream, force_terminal=True, width=120, color_system=None)
    monkeypatch.setattr(review, "Console", lambda **kwargs: console)
    with pytest.raises(KeyboardInterrupt), review.progress_display(True) as update:
        update({"phase": "analysis", "active_paths": ["app[red].py", "other\nfile\t.py\x1b[2J"],
                "selected_files": 2, "completed_files": 0, "failed_files": 0, "active_requests": 1})
        print('{"fixture": true}')
        update({"phase": "postprocess"})
        raise KeyboardInterrupt
    assert json.loads(capsys.readouterr().out) == {"fixture": True}
    output = stream.getvalue()
    assert "app[red].py" in output and "other\\nfile\\t.py" in output
    assert "\x1b[2J" not in output
    assert "Checking findings" in output and "\x1b[?25h" in output


@pytest.mark.parametrize("width", [48, 80, 120])
def test_compact_progress_keeps_file_rows_and_counts_readable(monkeypatch, width):
    from io import StringIO

    from rich.console import Console

    from mortis_engine.cli import review

    monkeypatch.setenv("TERM", "xterm-256color")
    console = Console(file=StringIO(), force_terminal=True, width=width, height=24, color_system=None,
                      record=True)
    monkeypatch.setattr(review, "Console", lambda **kwargs: console)
    paths = ["src/mortis_engine/cli/review.py", "src/mortis_engine/review/execution.py",
             "src/mortis_engine/review/service.py", *(f"other{i}.py" for i in range(65))]
    with review.progress_display(True) as update:
        update({"phase": "analysis", "active_paths": paths, "selected_files": 68,
                "completed_files": 12, "failed_files": 1, "active_requests": 2})
        frame = console.export_text()
        assert "Files reviewed: 12/68    Failed: 1" in frame
        assert "AI requests running: 2" in frame
        assert "Files in active requests:\n" in frame
        assert all(f"  {path}\n" in frame for path in paths[:3])
        assert "+65 more files" in frame and "other0.py" not in frame
        assert "Waiting for AI response" in frame and "units" not in frame
        update({"phase": "postprocess"})
        frame = console.export_text()
        assert "Files reviewed: 12/68    Failed: 1" in frame
        assert "Checking findings" in frame
        assert "Files in active requests" not in frame
