import json

import pytest

from mortis_engine import cli, config, gateway
from mortis_engine.schemas import Response, Usage

from .git_helpers import commit, git


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
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
    assert result["usage"]["total_tokens"] == 33
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
    assert cli.main(argv) == 2
    result = json.loads(capsys.readouterr().out)
    assert len(result["coverage"]["waived"]) == 2 and result["usage"]["total_tokens"] == 0


def test_ambiguous_profile_requires_selection(repo, profile, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    commit(repo, {"app.py": "value = 3\n"})
    config.save("other", profile, None)
    command = ["review", "--base", "main"]
    assert cli.main(command) == 1
    assert "--connection" in capsys.readouterr().err
    assert cli.main([*command, "--connection", "opencode-go"]) == 0
