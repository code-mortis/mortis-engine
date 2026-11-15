import json

import pytest
from code_graph.test_graph import parsed
from pydantic import ValidationError
from review.git_helpers import commit, git

from mortis_engine import cli, config, gateway
from mortis_engine.code_graph import transport
from mortis_engine.history import History
from mortis_engine.review import engine, review_local, settings
from mortis_engine.schemas import Response, Usage

DEFAULTS = {"max_files": 1000, "max_file_bytes": 1048576, "max_source_bytes": 8388608}
CONFIGURED = {"max_files": 12, "max_file_bytes": 256, "max_source_bytes": 512}


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("GRAPH_FIXTURE_KEY", "fixture-token")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-qb", "main")
    commit(repo, {"app.py": "value = 1\n", "mortis.toml": "[review.code_graph_limits]\n"
                  + "\n".join(f"{key} = {value}" for key, value in CONFIGURED.items()) + "\n"})
    monkeypatch.chdir(repo)
    config.save("fixture", {"provider": "openai", "protocol": "openai", "model": "fixture-model",
                           "base_url": "https://api.openai.com/v1", "api_key_env": "GRAPH_FIXTURE_KEY"}, None)

    async def send(connection, credentials, request, **kwargs):
        if "narrative" in request.output_schema["properties"]:
            output = {"narrative": "Fixture summary", "groups": []}
        else:
            payload = json.loads(request.messages[0].content)
            output = {"task_key": payload["task_key"], "completed_units": [
                unit["key"] for unit in payload["assigned_units"]], "findings": []}
        return Response(output=output, usage=Usage(total_tokens=1))

    monkeypatch.setattr(gateway, "send", send)
    return repo


def test_nested_defaults_and_partial_python_overrides():
    assert settings.RepoSettings().code_graph_limits.model_dump() == DEFAULTS
    options = {"language": "Spanish", "code_graph_limits": CONFIGURED.copy()}
    settings.apply_overrides(options, {"code_graph_limits": {"max_source_bytes": 300}})
    assert options == {"language": "Spanish", "code_graph_limits": {**CONFIGURED, "max_source_bytes": 300}}


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "1", None, 5001])
def test_graph_limits_reject_invalid_types_and_ranges(value):
    with pytest.raises(ValidationError):
        settings.apply_overrides({}, {"code_graph_limits": {"max_files": value}})


@pytest.mark.parametrize("limits", [None, [], {"unknown": 1}, {"max_file_bytes": 4194305},
                                   {"max_source_bytes": 33554433}, {"max_source_bytes": 255}])
def test_graph_limits_validate_the_merged_contract(limits):
    options = {"code_graph_limits": CONFIGURED.copy()}
    with pytest.raises(ValidationError):
        settings.apply_overrides(options, {"code_graph_limits": limits})
    assert options == {"code_graph_limits": CONFIGURED}


@pytest.mark.parametrize("caller", ["cli", "python"])
def test_committed_limits_and_partial_overrides_reach_parser(repository, monkeypatch, capsys, caller):
    captured = []
    monkeypatch.setattr(transport, "extract", lambda request, **kwargs: captured.append(request) or parsed(request))
    if caller == "cli":
        assert cli.main(["review", "--all", "--graph", "--graph-max-source-bytes", "300", "--format", "json"]) == 0
        report = json.loads(capsys.readouterr().out)
    else:
        profile = config.profile("fixture")
        report = review_local(repository, mode="all", graph=True, connection=config.connection("fixture", profile),
                              get_credentials=lambda: config.credentials(profile),
                              overrides={"code_graph_limits": {"max_source_bytes": 300}})
    expected = {**CONFIGURED, "max_source_bytes": 300}
    assert report["code_graph"]["status"] == "ready"
    assert captured[0].limits.model_dump() == expected
    with History(repository) as history:
        assert json.loads(history.row(report["run_id"])["plan"])["code_graph_limits"] == expected
    assert cli.main(["config", "show"]) == 0
    assert json.loads(capsys.readouterr().out)["review"]["settings"]["code_graph_limits"] == CONFIGURED
    assert git(repository, "status", "--porcelain") == ""


def test_configured_graph_limits_do_not_enable_parser(repository, monkeypatch, capsys):
    monkeypatch.setattr(transport, "extract", lambda *args, **kwargs: pytest.fail("Graph enabled by limits alone"))
    assert cli.main(["review", "--all", "--format", "json"]) == 0
    assert "code_graph" not in json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("field,value", [("files", 20), ("file-bytes", 300), ("source-bytes", 600)])
def test_each_cli_limit_merges_with_committed_values(repository, monkeypatch, capsys, field, value):
    captured = []
    monkeypatch.setattr(transport, "extract", lambda request, **kwargs: captured.append(request.limits.model_dump()) or parsed(request))
    assert cli.main(["review", "--all", "--graph", "--graph-max-" + field, str(value)]) == 0
    capsys.readouterr()
    assert captured == [{**CONFIGURED, "max_" + field.replace("-", "_"): value}]


def test_exceeding_graph_limits_continues_ordinary_review(repository, monkeypatch, capsys):
    commit(repository, {"dep.py": "value = 2\n"})
    monkeypatch.setattr(transport, "extract", lambda *args, **kwargs: pytest.fail("Over-limit source reached parser"))
    assert cli.main(["review", "--all", "--graph", "--graph-max-files", "1", "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "completed"
    assert report["code_graph"]["status"] == "unavailable"
    assert "admission limits" in report["code_graph"]["reason"]
    assert {item["path"] for item in report["coverage"]["completed"]} == {"app.py", "dep.py", "mortis.toml"}


@pytest.mark.parametrize("field", ["files", "file-bytes", "source-bytes"])
def test_cli_graph_override_requires_graph_before_connection_loading(monkeypatch, capsys, field):
    monkeypatch.setattr(config, "load", lambda: pytest.fail("Invalid graph options reached connection loading"))
    assert cli.main(["review", "--graph-max-" + field, "1"]) == 1
    assert "require --graph" in capsys.readouterr().err


def test_python_graph_override_requires_graph_before_history_access(monkeypatch):
    monkeypatch.setattr(History, "__init__", lambda *args, **kwargs: pytest.fail("Invalid graph options opened history"))
    with pytest.raises(ValueError, match="require --graph"):
        review_local(".", connection={}, get_credentials=lambda: {}, overrides={"code_graph_limits": {"max_files": 2}})


def test_invalid_merged_limits_fail_before_credentials_or_dispatch(repository, monkeypatch, capsys):
    monkeypatch.setattr(config, "credentials", lambda *_: pytest.fail("Invalid graph limits read credentials"))
    monkeypatch.setattr(transport, "extract", lambda *_: pytest.fail("Invalid graph limits reached parser"))
    assert cli.main(["review", "--all", "--graph", "--graph-max-source-bytes", "255"]) == 1
    assert "Maximum individual file size" in capsys.readouterr().err
    with History(repository) as history:
        assert history.list() == []


@pytest.mark.parametrize("legacy", [False, True])
def test_resume_pins_limits_and_legacy_uses_fixed_defaults(repository, monkeypatch, capsys, legacy):
    captured = []
    monkeypatch.setattr(transport, "extract", lambda request, **kwargs: captured.append(request.limits.model_dump()) or parsed(request))
    original = engine.run

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(engine, "run", interrupt)
    assert cli.main(["review", "--all", "--graph", "--graph-max-source-bytes", "300"]) == 130
    capsys.readouterr()
    with History(repository) as history:
        run_id = history.list()[0]["id"]
        plan = json.loads(history.row(run_id)["plan"])
        if legacy:
            del plan["code_graph_limits"]
            plan["overrides"] = {}
            with history.db:
                history.db.execute("UPDATE runs SET plan=? WHERE id=?", (json.dumps(plan), run_id))
        saved_plan = history.row(run_id)["plan"]
    assert cli.main(["review", "--resume", run_id, "--graph-max-files", "20"]) == 1
    assert "--resume uses" in capsys.readouterr().err
    monkeypatch.setattr(engine, "run", original)
    load = settings.load
    monkeypatch.setattr(settings, "load", lambda *args: {**load(*args), "code_graph_limits": {
        "max_files": 30, "max_file_bytes": 1024, "max_source_bytes": 4096}})
    assert cli.main(["review", "--resume", run_id, "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["run_id"] == run_id
    expected = DEFAULTS if legacy else {**CONFIGURED, "max_source_bytes": 300}
    assert captured == [{**CONFIGURED, "max_source_bytes": 300}, expected]
    with History(repository) as history:
        assert history.row(run_id)["plan"] == saved_plan
