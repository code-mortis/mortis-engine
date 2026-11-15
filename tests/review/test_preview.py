"""The local preview is the execution planner, without connection or state access."""

import json

import pytest

from mortis_engine import cli, config, gateway
from mortis_engine.review import engine, planning, preview_local, service

from .git_helpers import commit, git


@pytest.mark.parametrize("mode", ["worktree", "staged", "commits", "all"])
@pytest.mark.parametrize("ceiling", [None, 10000])
def test_preview_matches_real_execution_plan(repo, tmp_path, monkeypatch, mode, ceiling):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    base = commit(repo, {"app.py": "value = 1\n", "deleted.py": "old = 1\n"})
    text = "".join(f"value_{i} = {i}\n" for i in range(300))
    commit(repo, {"app.py": text, "README.md": "Instructions\n", "skip_test.py": "assert True\n"})
    git(repo, "rm", "deleted.py")
    git(repo, "commit", "-qm", "remove")
    (repo / "app.py").write_text(text + "staged = True\n")
    git(repo, "add", "app.py")
    (repo / "app.py").write_text(text + "unstaged = True\n")
    (repo / "draft.py").write_text("draft = True\n")
    kwargs = {"mode": mode, "base": base if mode == "commits" else None}
    result = preview_local(repo, **kwargs, prompt_token_ceiling=ceiling)
    observed = []

    def capture(_repo, _env, plan, *args, **kwargs):
        observed.append(plan)
        raise InterruptedError("Captured real execution plan")

    monkeypatch.setattr(engine, "run", capture)
    connection = {"provider": "custom", "protocol": "openai", "model": "fixture", "options": {}}
    if ceiling is not None:
        connection["options"]["prompt_token_ceiling"] = ceiling
    with pytest.raises(InterruptedError, match="Captured"):
        service.review_local(repo, **kwargs, connection=connection, get_credentials=lambda: {})
    plan, = observed
    assert result["schema_version"] == "mortis.review-preview/v1"
    assert (result["base_sha"], result["head_sha"], result["merge_base"]) == (plan.base_sha, plan.head_sha, plan.merge_base)
    assert [(row["path"], row["error"], row["unit_keys"]) for row in result["selected_files"]] == [
        (row.path, row.error, [unit.key for unit in row.units]) for row in plan.selected]
    assert [(row["key"], row["start_line"], row["end_line"]) for row in result["units"]] == [
        (unit.key, unit.start_line, unit.end_line) for unit in plan.units]
    assert [row["unit_keys"] for row in result["batches"]] == [[unit.key for unit in batch] for batch in plan.batches]
    assert result["totals"]["source_bytes"] == sum(len(unit.source.encode()) for unit in plan.units)
    assert result["totals"]["diff_bytes"] == sum(len(unit.patch.encode()) for unit in plan.units)
    assert result["totals"]["assigned_unit_json_bytes"] == sum(planning._unit_bytes(unit) for unit in plan.units)
    assert result["totals"]["planned_batches"] == len(plan.batches)
    assert all(row["source_only"] == (mode == "all") for row in result["units"])
    assert (result["bounds"]["max_unit_bytes"] == 1024) == (ceiling is not None)


def test_preview_never_reads_profiles_credentials_or_state_or_mutates_repository(repo, tmp_path, monkeypatch):
    commit(repo, {"app.py": "value = 1\n", "skip_test.py": "assert True\n"})
    (repo / "app.py").write_text("value = 2\n")
    (repo / ".env").write_text("EXAMPLE=not-a-secret\n")
    git(repo, "add", ".env")
    state, configuration = tmp_path / "private-state", tmp_path / "private-config"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(configuration))

    def forbidden(*args, **kwargs):
        pytest.fail("Preview accessed connection, inference or history")

    monkeypatch.setattr(config, "load", forbidden)
    monkeypatch.setattr(config, "credentials", forbidden)
    monkeypatch.setattr(gateway, "send", forbidden)
    monkeypatch.setattr(service, "History", forbidden)
    before = {p.relative_to(repo): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    result = preview_local(repo)
    after = {p.relative_to(repo): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    assert before == after
    assert not state.exists() and not configuration.exists()
    assert result["totals"]["selected_files"] == 1
    assert {row["path"]: row["reason"] for row in result["excluded_files"]}[".env"] == "mandatory_exclusion"
    assert "value = 2" not in json.dumps(result)
    assert "EXAMPLE" not in json.dumps(result)


def test_preview_reasons_limits_and_admission_errors(repo):
    options = '[review]\nmax_files=1\nexclusions="custom.py"\n[review.rules]\nexclude=["rule.py"]\n'
    commit(repo, {"mortis.toml": options, "app.py": "a = 1\n", "other.py": "b = 1\n",
                  "custom.py": "excluded = 1\n", "rule.py": "excluded = 2\n",
                  "skip_test.py": "assert True\n", "binary.py": "\0\n", "notes.unknown": "text\n"})
    limited = preview_local(repo, mode="all")
    assert limited["limit_reason"] and not limited["units"] and not limited["batches"]
    assert all(row["source_bytes"] is None for row in limited["selected_files"])
    reasons = {row["path"]: row["reason"] for row in limited["excluded_files"]}
    assert reasons["custom.py"] == "configured_exclusion"
    assert reasons["rule.py"] == "rule_exclusion"
    assert reasons["skip_test.py"] == "default_test"
    assert reasons["binary.py"] == "binary"
    assert reasons["notes.unknown"] == "unsupported_source"
    unlimited = preview_local(repo, mode="all", overrides={"max_files": 0})
    assert not unlimited["limit_reason"] and unlimited["units"]
    (repo / "app.py").write_text("x" * 5000)
    invalid = preview_local(repo, prompt_token_ceiling=4096)
    assert invalid["errors"] and "line exceeds" in invalid["errors"][0]["message"]
    assert not invalid["units"]


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_cli_preview_does_not_select_a_profile(repo, monkeypatch, capsys, output_format):
    monkeypatch.chdir(repo)
    commit(repo, {"app.py": "value = 1\n", "skip_test.py": "assert True\n"})
    monkeypatch.setattr(config, "load", lambda: pytest.fail("Preview selected profile"))
    assert cli.main(["review", "--all", "--preview", "--format", output_format]) == 0
    out = capsys.readouterr()
    assert not out.err
    if output_format == "json":
        result = json.loads(out.out)
        assert result["schema_version"] == "mortis.review-preview/v1"
        assert "run_id" not in result and "usage" not in result
    else:
        for text in ("Review scope preview", "Selected: app.py", "default_test", "Planned batches:", "Assigned source:"):
            assert text in out.out


@pytest.mark.parametrize("flag", ["--connection=example", "--resume=example", "--graph", "--output=preview.json", "--progress", "--concurrency=2", "--second-pass"])
def test_cli_preview_rejects_runtime_options_before_connection_loading(repo, monkeypatch, capsys, flag):
    monkeypatch.setattr(config, "load", lambda: pytest.fail("Invalid preview read a profile"))
    assert cli.main(["review", "--preview", flag]) == 1
    assert "--preview" in capsys.readouterr().err


@pytest.mark.parametrize("ceiling", [True, 4095, 2000001, "10000"])
def test_invalid_preview_sizing_never_opens_repository(ceiling):
    with pytest.raises(ValueError, match="prompt_token_ceiling"):
        preview_local("/missing/repository", prompt_token_ceiling=ceiling)


def test_preview_file_limit_cli_returns_partial_and_sizing_requires_preview(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    commit(repo, {"app.py": "a = 1\n", "other.py": "b = 1\n"})
    monkeypatch.setattr(config, "load", lambda: pytest.fail("Invalid preview selected profile"))
    assert cli.main(["review", "--all", "--preview", "--max-files", "1", "--format", "json"]) == 2
    assert json.loads(capsys.readouterr().out)["limit_reason"]
    assert cli.main(["review", "--prompt-token-ceiling", "10000"]) == 1
    assert "requires --preview" in capsys.readouterr().err
