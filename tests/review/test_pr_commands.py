import json

import pytest

from mortis_engine import cli, config, gateway
from mortis_engine.review import pr_commands
from mortis_engine.schemas import Response, Usage

from .git_helpers import commit, git

CONNECTION = {"kind": "api", "provider": "custom", "protocol": "openai", "model": "fixture", "options": {}}


@pytest.fixture(autouse=True)
def state(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("pr-commands")
    for key, directory in (("XDG_STATE_HOME", "state"), ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
        monkeypatch.setenv(key, str(root / directory))


def test_ask_uses_pinned_base_policy_and_verified_diff_citations(repo, monkeypatch):
    base = commit(repo, {"app.py": "value = 1\n", "private.py": "old = 0\n",
                         "mortis.toml": '[review]\nmodel = "trusted-model"\nexclusions = "private.py"\n'})
    head = commit(repo, {"app.py": "value = 2\n", "private.py": "new = 1\n",
                         "mortis.toml": '[review]\nmodel = "head-model"\n'})
    calls = []

    async def send(connection, credentials, request, **kwargs):
        supplied = json.loads(request.messages[0].content)
        assert connection["model"] == "trusted-model"
        assert supplied["question"] == "Why did the value change?"
        assert [row["path"] for row in supplied["files"]] == ["app.py", "mortis.toml"]
        assert all(row["path"] != "private.py" for row in supplied["hunks"])
        calls.append(request)
        citation = 99 if len(calls) == 1 else 1
        return Response(output={"answer": "The default is now two.", "citations": [{"hunk": citation, "side": "right"}]},
                        usage=Usage(total_tokens=25))

    monkeypatch.setattr(gateway, "send", send)
    result = pr_commands.run(repo, command="ask", base=base, head=head, question="Why did the value change?",
                             connection=CONNECTION, get_credentials=lambda: {"api_key": "test-key"})
    assert len(calls) == 2
    assert result["status"] == "completed"
    assert result["result"]["citations"] == [{"path": "app.py", "sha": head, "start_line": 1, "end_line": 1}]


def test_ask_about_deleted_lines_uses_the_left_revision(repo, monkeypatch):
    base = commit(repo, {"app.py": "removed = 1\n"})
    git(repo, "rm", "app.py")
    git(repo, "commit", "-qm", "remove")
    head = git(repo, "rev-parse", "HEAD")

    async def send(connection, credentials, request, **kwargs):
        supplied = json.loads(request.messages[0].content)
        assert supplied["selection"] == {"path": "app.py", "first": 1, "last": 1, "side": "left"}
        assert supplied["hunks"][0]["right"] is None
        return Response(output={"answer": "This declaration was removed.", "citations": [{"hunk": 1, "side": "left"}]},
                        usage=Usage(total_tokens=10))

    monkeypatch.setattr(gateway, "send", send)
    result = pr_commands.run(repo, command="ask", base=base, head=head, question="What was removed?",
                             location=("app.py", 1, 1, "left"), connection=CONNECTION, get_credentials=lambda: {})
    assert result["result"]["citations"] == [{"path": "app.py", "sha": base, "start_line": 1, "end_line": 1}]
    unavailable = pr_commands.run(repo, command="ask", base=base, head=head, question="What was removed?",
                                  location=("app.py", 1, 1, "right"), connection=CONNECTION,
                                  get_credentials=lambda: pytest.fail("An unavailable line must not call the provider"))
    assert unavailable["status"] == "insufficient_context"


def test_describe_rejects_a_diff_location(repo):
    base = commit(repo, {"app.py": "value = 1\n"})
    head = commit(repo, {"app.py": "value = 2\n"})

    with pytest.raises(ValueError, match="Supply a location only with ask"):
        pr_commands.run(repo, command="describe", base=base, head=head,
                        location=("app.py", 1, 1, "right"), connection=CONNECTION,
                        get_credentials=lambda: pytest.fail("Describe must reject a location before loading credentials"))


def test_cli_describe_returns_a_grounded_title_and_structured_summary(repo, capsys, monkeypatch):
    base = commit(repo, {"app.py": "value = 1\n"})
    head = commit(repo, {"app.py": "value = 2\n"})
    monkeypatch.setattr(config, "load", lambda: {"connections": {"fixture": {}}})
    monkeypatch.setattr(config, "connection", lambda *args: CONNECTION)
    monkeypatch.setattr(config, "credentials", lambda profile: {})

    async def send(connection, credentials, request, **kwargs):
        assert "+value = 2" in request.messages[0].content
        return Response(output={"title": "Increase the default value", "narrative": "The default is now two.",
                               "groups": [{"title": "Default", "description": "Updates the default.", "paths": ["app.py"]}]},
                        usage=Usage(total_tokens=14))

    monkeypatch.setattr(gateway, "send", send)
    assert cli.main(["describe", "--repo", str(repo), "--base", base, "--head", head, "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == "mortis.describe/v1"
    assert report["status"] == "completed"
    assert report["result"]["title"] == "Increase the default value"
    assert report["result"]["groups"][0]["paths"] == ["app.py"]
