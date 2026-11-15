import json
import os
import stat

import pytest
from review.git_helpers import commit, git

from mortis_engine import cli, config, gateway
from mortis_engine.cli import reports
from mortis_engine.history import History
from mortis_engine.schemas import Response, Usage
from mortis_engine.security_scans import service as scans


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-qb", "main")
    commit(repo, {"app.py": "value = 1\n", "other.py": "other = 2\n"})
    return repo


@pytest.fixture
def provider(monkeypatch):
    calls = []
    config.save("fixture", {"provider": "openai", "protocol": "openai-responses", "model": "fixture",
                            "base_url": "https://api.openai.com/v1", "api_key_env": "MORTIS_REPORT_FIXTURE"}, None)
    monkeypatch.setenv("MORTIS_REPORT_FIXTURE", "synthetic-report-key")

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        fields = request.output_schema["properties"]
        if "narrative" in fields:
            output = {"narrative": "Updated app value.", "groups": []}
        elif "required_paths" in fields:
            output = {"findings": [], "required_paths": []}
        else:
            payload = json.loads(request.messages[0].content)
            output = {"task_key": payload["task_key"], "completed_units": [
                unit["key"] for unit in payload["assigned_units"]], "findings": []}
        return Response(output=output, usage=Usage(total_tokens=3))

    monkeypatch.setattr(gateway, "send", send)
    monkeypatch.setattr(scans, "trivy", lambda *a: {
        "findings": [], "targets": {}, "metadata": {"database_fresh": True}, "problems": []})
    return calls


@pytest.mark.parametrize("command", ["review", "scan"])
@pytest.mark.parametrize("partial", [False, True])
def test_command_exports_same_saved_result_once(repository, provider, monkeypatch, capsys, command, partial):
    monkeypatch.chdir(repository)
    (repository / "app.py").write_text("value = 3\n")
    (repository / "other.py").write_text("other = 4\n")
    flags = []
    if partial:
        if command == "review":
            original = gateway.send

            async def failed_summary(connection, credentials, request, **kwargs):
                if "narrative" in request.output_schema["properties"]:
                    raise ValueError("Fixture summary failed")
                return await original(connection, credentials, request, **kwargs)

            monkeypatch.setattr(gateway, "send", failed_summary)
        else:
            monkeypatch.setattr(scans, "trivy", lambda *a: {
                "findings": [], "targets": {}, "metadata": {"database_fresh": False},
                "problems": ["Vulnerability database is stale"]})
    markdown = repository / "artifacts" / "report.MARKDOWN"
    exported = repository / "artifacts" / "report.JSON"
    assert cli.main([command, *flags, "--format", "json", "--progress", "--output", str(markdown),
                     "--output", str(exported)]) == (2 if partial else 0)
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert json.loads(exported.read_text()) == result
    assert result["status"] == ("partial" if partial else "completed")
    assert result["elapsed_seconds"] >= 0
    assert "Elapsed:" in captured.err and "Elapsed:" not in captured.out
    assert f"Saved report: {str(markdown)!r}" in captured.err
    assert f"Saved report: {str(exported)!r}" in captured.err
    if partial:
        assert "partial" in markdown.read_text().lower()
    elif command == "review":
        assert "Updated app value" in markdown.read_text()
    assert markdown.stat().st_mode & 0o777 == 0o600
    assert exported.stat().st_mode & 0o777 == 0o600
    with History(repository) as history:
        assert history.report(result["run_id"]) == result
        assert len(history.list()) == 1
        original = history.row(result["run_id"])["report"]
    if command == "review":
        assert all(row["path"] not in {"artifacts/report.MARKDOWN", "artifacts/report.JSON"}
                   for row in result["coverage"]["selected"])
    count = len(provider)
    monkeypatch.setattr(config, "load", lambda: pytest.fail("Export loaded connection configuration"))
    monkeypatch.setattr(config, "credentials", lambda *_: pytest.fail("Export read credentials"))
    monkeypatch.setattr(gateway, "send", lambda *_: pytest.fail("Export dispatched AI"))
    again = repository / "copy.json"
    assert cli.main(["report", "--repo", str(repository), "--run", result["run_id"], "--output", str(again)]) == 0
    captured = capsys.readouterr()
    assert not captured.out and f"Saved report: {str(again)!r}" in captured.err
    assert len(provider) == count
    assert json.loads(again.read_text()) == result
    with History(repository) as history:
        assert history.row(result["run_id"])["report"] == original


@pytest.mark.parametrize("command", ["review", "scan"])
@pytest.mark.parametrize("invalid", ["extension", "duplicate", "ancestor", "descendant", "existing", "directory", "symlink", "parent", "unwritable"])
def test_destinations_fail_before_profile_or_run(repository, tmp_path, monkeypatch, capsys, command, invalid):
    path = tmp_path / "report.json"
    values = [str(path)]
    if invalid == "extension":
        values = [str(tmp_path / "report.txt")]
    elif invalid == "duplicate":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        values.append(str(alias / "report.json"))
    elif invalid in {"ancestor", "descendant"}:
        values.append(str(path / "nested.md"))
        if invalid == "ancestor":
            values.reverse()
    elif invalid == "existing":
        path.write_text("preserve original")
    elif invalid == "directory":
        path.mkdir()
    elif invalid == "symlink":
        path.symlink_to(tmp_path / "absent")
    elif invalid == "parent":
        parent = tmp_path / "file"
        parent.write_text("original source")
        values = [str(parent / "report.json")]
    else:
        monkeypatch.setattr(reports.os, "access", lambda *args: False)
    monkeypatch.setattr(config, "load", lambda: pytest.fail("Invalid output loaded a profile"))
    argv = [command, "--repo", str(repository)]
    for value in values:
        argv.extend(["--output", value])
    assert cli.main(argv) == 1
    assert "Error:" in capsys.readouterr().err
    if invalid == "existing":
        assert path.read_text() == "preserve original"
    assert not (tmp_path / "state").exists()


def test_report_requires_output_and_rejects_unfinished_or_other_workflow(repository, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["report", "--run", "anything"])
    assert exc.value.code == 2
    capsys.readouterr()
    with History(repository) as history:
        unfinished = history.begin("review", "scope", {})
        other = history.begin("summary", "scope", {})
        history.finish(other, {"schema_version": "mortis.summary/v1", "run_id": other})
    for run_id, expected in ((unfinished, "no final report"), (other, "Only saved review and security")):
        path = tmp_path / (run_id + ".json")
        assert cli.main(["report", "--repo", str(repository), "--run", run_id, "--output", str(path)]) == 1
        assert expected in capsys.readouterr().err
        assert not path.exists()


def test_legacy_report_export_preserves_original_without_timing(repository, tmp_path, monkeypatch, capsys):
    with History(repository) as history:
        run_id = history.begin("review", "scope", {})
        result = {"schema_version": "mortis.review/v1", "run_id": run_id, "status": "partial",
                  "coverage": {"selected": [], "completed": [], "failed": [], "waived": []}, "findings": []}
        history.finish(run_id, result)
    monkeypatch.setattr(config, "load", lambda: pytest.fail("Legacy export loaded profiles"))
    path = tmp_path / "legacy.json"
    assert cli.main(["report", "--repo", str(repository), "--run", run_id, "--output", str(path)]) == 0
    assert json.loads(path.read_text()) == result
    assert not capsys.readouterr().out


@pytest.mark.parametrize("command", ["review", "scan"])
def test_output_failure_preserves_saved_run_and_reports_recovery(repository, provider, tmp_path, monkeypatch, capsys, command):
    path = tmp_path / "new-directory" / "report.json"

    original = os.link

    def fail(source, target):
        if ".mortis-report-" in str(source):
            raise OSError("disk full fixture")
        return original(source, target)

    monkeypatch.setattr(reports.os, "link", fail)
    assert cli.main([command, "--repo", str(repository), *(["--all"] if command == "review" else []),
                     "--output", str(path), "--format", "json"]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "disk full fixture" in captured.err and "mortis report --run" in captured.err
    assert "Elapsed:" in captured.err
    with History(repository) as history:
        run_id = history.list()[0]["id"]
        assert history.report(run_id)["status"] == "completed"
    assert run_id in captured.err
    assert not path.exists()
    assert not list(path.parent.iterdir())


def test_waived_review_exports_with_original_exit_status(repository, provider, tmp_path, capsys):
    path = tmp_path / "waived.md"
    assert cli.main(["review", "--repo", str(repository), "--all", "--max-files", "1",
                     "--output", str(path), "--format", "json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "skipped" and len(result["coverage"]["waived"]) == 2
    assert path.is_file()


def test_racing_target_is_never_truncated_and_partial_files_are_reported(tmp_path, monkeypatch):
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    paths = reports.destinations([str(first), str(second)])
    result = {"schema_version": "mortis.review/v1", "run_id": "saved-run"}
    original = os.link

    def raced(source, target):
        if target == second:
            second.write_text("concurrent content")
        original(source, target)

    monkeypatch.setattr(reports.os, "link", raced)
    with pytest.raises(ValueError, match="Saved run: saved-run") as exc:
        reports.write_outputs(result, paths)
    assert str(first) in str(exc.value) and "Files written:" in str(exc.value)
    assert json.loads(first.read_text()) == result
    assert second.read_text() == "concurrent content"
    assert not list(tmp_path.glob(".mortis-report-*"))
    assert stat.S_IMODE(first.stat().st_mode) == 0o600


def test_render_failure_creates_no_output(tmp_path, monkeypatch):
    paths = reports.destinations([str(tmp_path / "report.json"), str(tmp_path / "nested" / "report.md")])

    def fail(result):
        raise ValueError("Unsupported saved content")

    monkeypatch.setattr(reports, "markdown", fail)
    with pytest.raises(ValueError, match="Saved run: saved-run"):
        reports.write_outputs({"schema_version": "mortis.review/v1", "run_id": "saved-run"}, paths)
    assert list(tmp_path.iterdir()) == []
