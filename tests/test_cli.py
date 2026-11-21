import getpass
import json
import os
import stat
import sys

import pytest

from mortis_engine import cli, config, gateway
from mortis_engine.llm_http import InferenceError


class Vault:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value

    def delete_password(self, service, account):
        del self.values[service, account]


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("TEST_MORTIS_KEY", "synthetic-key-not-a-credential")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    vault = Vault()
    monkeypatch.setattr(config, "vault", lambda: vault)
    calls = []

    def test(connection, credentials):
        calls.append((connection, credentials))
        return "Connected. Model completed a tool-call round trip."

    monkeypatch.setattr(gateway, "test_connection", test)
    return calls, vault


def arguments(name="work", *extra):
    return ["connect", name, "--provider", "openai", "--model", "synthetic-model",
            "--api-key-env", "TEST_MORTIS_KEY", "--non-interactive", *extra]


@pytest.mark.parametrize("provider,protocol", [
    ("openai", "openai-responses"), ("custom", "openai"),
    ("anthropic", "anthropic"), ("bedrock", "anthropic-bedrock"),
])
def test_all_protocols_and_options(setup, provider, protocol):
    calls, vault = setup
    options = {"timeout_sec": 80, "prompt_token_ceiling": 50000, "retry_codes": [429],
               "extra_body": {"temperature": 0.3}, "auth_header": "X-Token"}
    if protocol == "anthropic-bedrock":
        options.update(aws_region="eu-west-1", aws_profile="work")
    args = arguments()
    args[args.index("openai")] = provider
    assert cli.main([*args, "--protocol", protocol, "--base-url", "https://example.com/v1",
                     "--options", json.dumps(options)]) == 0
    saved = config.profile("work")
    assert saved["options"] == options
    assert saved["protocol"] == protocol
    assert saved["api_key_env"] == "TEST_MORTIS_KEY"
    assert "synthetic-key" not in config.config_path().read_text()
    assert not vault.values
    assert calls[0][1]["api_key"] == "synthetic-key-not-a-credential"
    if os.name != "nt":
        assert stat.S_IMODE(config.config_path().stat().st_mode) == 0o600


def test_interactive_key_and_headers_are_saved_in_vault(setup, monkeypatch, capsys):
    calls, vault = setup
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(["local", "openai", "", "", "model", "y",
                    '{"timeout_sec":60,"prompt_token_ceiling":58888}', "y", "keyring"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    secrets = iter(["synthetic-vault-key", '{"X-Secret":"synthetic-header"}'])
    monkeypatch.setattr(getpass, "getpass", lambda prompt: next(secrets))
    assert cli.main(["connect", "--advanced"]) == 0
    saved = config.profile("local")
    assert config.credentials(saved) == {"api_key": "synthetic-vault-key", "extra_headers": {"X-Secret": "synthetic-header"}}
    assert len(vault.values) == 1
    assert cli.main(["config", "show"]) == 0
    output = capsys.readouterr()
    for secret in ("synthetic-vault-key", "synthetic-header"):
        assert secret not in output.out + output.err + config.config_path().read_text()
    assert calls[0][0]["options"]["prompt_token_ceiling"] == 58888


@pytest.mark.parametrize("options", [
    [],
    ["--provider", "opencode", "--protocol", "openai", "--base-url", "https://opencode.ai/zen/go/v1",
     "--options", '{"timeout_sec":900,"prompt_token_ceiling":500000}'],
])
def test_opencode_connect_only_prompts_for_hidden_key(setup, monkeypatch, capsys, options):
    calls, _ = setup
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    prompts = []

    def hidden(prompt):
        prompts.append(prompt)
        return "synthetic-probe-key"

    monkeypatch.setattr(getpass, "getpass", hidden)
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail(f"Unexpected prompt: {prompt}"))
    assert cli.main(["connect", "opencode-go", "--model", "muse-spark-1.3-contributor", *options]) == 0
    assert calls[0][0]["base_url"] == "https://opencode.ai/zen/go/v1"
    assert calls[0][0]["protocol"] == "openai"
    assert prompts == ["API key (input hidden): "]
    assert calls[0][1]["api_key"] == "synthetic-probe-key"
    out = capsys.readouterr()
    assert "synthetic-probe-key" not in out.out + out.err + config.config_path().read_text()


@pytest.mark.parametrize("method", ["keyring", "env"])
@pytest.mark.parametrize("entry", ["", "{}"])
def test_advanced_header_entry_keeps_or_clears_headers(setup, monkeypatch, method, entry):
    calls, _ = setup
    monkeypatch.setenv("TEST_HEADER", "synthetic-header")
    assert cli.main(arguments("work", "--headers-env", '{"X-Project":"TEST_HEADER"}')) == 0
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(["", "", "", "n", "y", method, entry])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(getpass, "getpass", lambda prompt: entry)
    assert cli.main(["connect", "work", "--advanced", "--api-key-env", "TEST_MORTIS_KEY"]) == 0
    assert calls[-1][1]["extra_headers"] == ({} if entry else {"X-Project": "synthetic-header"})


def test_blank_hidden_input_preserves_existing_env_auth(setup, monkeypatch):
    calls, vault = setup
    assert cli.main(arguments()) == 0
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(getpass, "getpass", lambda prompt: "")
    assert cli.main(["connect", "work", "--provider", "openai", "--protocol", config.profile("work")["protocol"],
                     "--base-url", "https://api.openai.com/v1", "--model", "other", "--options", "{}",
                     "--headers-env", "{}"]) == 0
    assert calls[-1][1]["api_key"] == "synthetic-key-not-a-credential"
    assert config.profile("work")["api_key_env"] == "TEST_MORTIS_KEY"
    assert not vault.values


@pytest.mark.parametrize("provider,protocol,exit_code", [
    ("bedrock", "anthropic-bedrock", 0), ("openai", "openai-responses", 1),
])
def test_empty_hidden_input_on_new_profile(setup, monkeypatch, provider, protocol, exit_code):
    calls, _ = setup
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(getpass, "getpass", lambda prompt: "")
    assert cli.main(["connect", "new", "--provider", provider, "--protocol", protocol,
                     "--base-url", "", "--model", "model", "--options", "{}", "--headers-env", "{}"]) == exit_code
    assert len(calls) == (1 if exit_code == 0 else 0)
    assert config.config_path().exists() == (exit_code == 0)


def test_failed_test_preserves_existing_profile_and_credentials(setup, monkeypatch, capsys):
    _, vault = setup
    assert cli.main(arguments()) == 0
    previous = config.config_path().read_bytes()

    def fail(connection, credentials):
        raise InferenceError("authentication", credentials["api_key"])

    monkeypatch.setattr(gateway, "test_connection", fail)
    assert cli.main(arguments("work", "--model", "new-model")) == 1
    assert config.config_path().read_bytes() == previous
    assert not vault.values
    output = capsys.readouterr()
    assert "authentication" in output.err and "work" in output.err
    assert "synthetic-key" not in output.err


def test_changed_destination_requires_new_credentials(setup):
    calls, _ = setup
    assert cli.main(arguments()) == 0
    assert cli.main(["connect", "work", "--base-url", "https://other.example/v1", "--non-interactive"]) == 1
    assert len(calls) == 1
    assert config.profile("work")["base_url"] != "https://other.example/v1"


def test_environment_headers_and_retest(setup, monkeypatch):
    calls, _ = setup
    monkeypatch.setenv("MORTIS_HEADER", "synthetic-header")
    assert cli.main(arguments("work", "--headers-env", '{"X-Token":"MORTIS_HEADER"}')) == 0
    assert calls[-1][1]["extra_headers"] == {"X-Token": "synthetic-header"}
    assert "synthetic-header" not in config.config_path().read_text()
    assert cli.main(["connect", "work", "--test"]) == 0
    assert cli.main(["connect", "work", "--headers-env", "{}", "--non-interactive"]) == 0
    assert calls[-1][1]["extra_headers"] == {}


def test_ambient_bedrock(setup):
    calls, _ = setup
    assert cli.main(["connect", "aws", "--provider", "bedrock", "--model", "synthetic-model",
                     "--ambient-auth", "--non-interactive"]) == 0
    assert calls[-1][1] == {"api_key": "", "extra_headers": {}}
    assert config.profile("aws")["protocol"] == "anthropic-bedrock"


@pytest.mark.parametrize("extra", [
    ["--options", '{"unsupported":17}'],
    ["--options", '{"timeout_sec":3}'],
    ["--headers-env", '{"X-Header":123}'],
    ["--api-key-env", "MISSING_MORTIS_KEY"],
    ["--options", '{"extra_body":{"api_key":"synthetic-forbidden-secret"}}'],
    ["--options", '{"extra_body":{"messages.0.content":"synthetic-forbidden-secret"}}'],
    ["--options", '{"extra_body":{"text":{"format":{"type":"text"}}}}'],
    ["--options", '{"extra_body":{"authentication.token":"synthetic-forbidden-secret"}}'],
    ["--options", '{"extra_body":{"prompt_cache_key":"synthetic-forbidden-secret"}}'],
])
def test_invalid_inputs_do_not_call_provider_or_save(setup, extra, capsys):
    calls, _ = setup
    assert cli.main(arguments("work", *extra)) == 1
    assert not calls
    assert not config.config_path().exists()
    assert "synthetic-forbidden-secret" not in capsys.readouterr().err


def test_updates_merge_other_profiles_and_reject_same_profile_race(setup):
    first = {"provider": "openai", "protocol": "openai-responses", "model": "a"}
    second = {**first, "model": "b"}
    config.save("one", first, None)
    config.save("two", second, None)
    with pytest.raises(ValueError, match="changed during testing"):
        config.save("one", {"model": "c"}, None)
    assert config.load()["connections"] == {"one": {**first, "options": {}}, "two": {**second, "options": {}}}


def test_failed_file_replace_preserves_config_and_cleans_vault(setup, monkeypatch):
    _, vault = setup
    assert cli.main(arguments()) == 0
    before = config.config_path().read_bytes()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter([""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(getpass, "getpass", lambda prompt: "synthetic-new-vault-key")

    def fail_replace(*args):
        raise OSError("disk full")

    monkeypatch.setattr(config.os, "replace", fail_replace)
    assert cli.main(["connect", "work"]) == 1
    assert config.config_path().read_bytes() == before
    assert not vault.values
    assert not list(config.config_path().parent.glob("config-*.toml"))


def test_unavailable_plaintext_backend_rejected(monkeypatch):
    class Unsafe:
        pass

    monkeypatch.setattr(config.keyring, "get_keyring", lambda: Unsafe())
    with pytest.raises(ValueError, match="OS credential vault"):
        config.vault()


def test_corrupt_configuration_fails_without_replacement(setup):
    path = config.config_path()
    path.parent.mkdir(parents=True)
    path.write_text("broken = [")
    assert cli.main(arguments()) == 1
    assert path.read_text() == "broken = ["


def test_rotating_missing_environment_key_does_not_require_old_value(setup, monkeypatch):
    calls, _ = setup
    assert cli.main(arguments()) == 0
    monkeypatch.delenv("TEST_MORTIS_KEY")
    monkeypatch.setenv("NEW_MORTIS_KEY", "synthetic-new-key")
    assert cli.main(["connect", "work", "--api-key-env", "NEW_MORTIS_KEY", "--non-interactive"]) == 0
    assert calls[-1][1]["api_key"] == "synthetic-new-key"
    assert config.profile("work")["api_key_env"] == "NEW_MORTIS_KEY"


def test_provider_json_null_round_trips(setup):
    options = {"extra_body": {"some_provider_setting": None, "nested": [{"value": None}]}}
    assert cli.main(arguments("work", "--options", json.dumps(options))) == 0
    assert config.profile("work")["options"] == options
    assert cli.main(["connect", "work", "--test"]) == 0


def test_saved_vault_credentials_cannot_follow_manual_destination_edit(setup, monkeypatch):
    calls, vault = setup
    stored = {"provider": "openai", "protocol": "openai-responses", "base_url": "https://api.openai.com/v1",
              "model": "model", "options": {}, "credential_id": "bound-credentials"}
    vault.set_password(config.SERVICE, "bound-credentials", json.dumps({
        "destination": list(config.credential_destination(stored)),
        "credentials": {"api_key": "synthetic-bound-key", "extra_headers": {}},
    }))
    config.save("work", stored, None)
    changed = {**stored, "base_url": "https://different.example/v1"}
    config.save("work", changed, stored)
    assert cli.main(["connect", "work", "--test"]) == 1
    assert not calls


def test_interactive_model_discovery_after_draft_credentials(setup, monkeypatch, capsys):
    from mortis_engine import discovery

    calls, _ = setup
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(["work", "openai", "?", "2"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    discoveries = []

    def list_models(connection, credentials):
        discoveries.append((connection, credentials))
        return [("first-model", "First"), ("second-model", "Second")]

    monkeypatch.setattr(discovery, "list_models", list_models)
    assert cli.main(["connect", "--api-key-env", "TEST_MORTIS_KEY"]) == 0
    assert config.profile("work")["model"] == "second-model"
    assert discoveries[0][1]["api_key"] == "synthetic-key-not-a-credential"
    assert calls[0][0]["model"] == "second-model"
    assert "2. second-model" in capsys.readouterr().out


def test_listing_saved_models_does_not_probe_or_save(setup, monkeypatch, capsys):
    from mortis_engine import discovery

    calls, _ = setup
    assert cli.main(arguments()) == 0
    before = config.config_path().read_bytes()
    monkeypatch.setattr(discovery, "list_models", lambda connection, credentials: [("first-model", "First")])
    assert cli.main(["connect", "work", "--list-models"]) == 0
    assert len(calls) == 1
    assert config.config_path().read_bytes() == before
    assert "first-model" in capsys.readouterr().out


@pytest.mark.parametrize("change", [
    {"headers_env": []}, {"headers_env": {"X-Secret": 17}}, {"api_key_env": ["SECRET"]},
    {"credential_id": ["credential"]}, {"model": None}, {"options": "invalid"},
])
@pytest.mark.parametrize("command", [["connect", "work", "--test"], ["config", "show"]])
def test_malformed_saved_profiles_fail_cleanly_before_dispatch(setup, monkeypatch, change, command, capsys):
    import tomli_w

    calls, _ = setup
    saved = {"provider": "openai", "protocol": "openai-responses", "base_url": "https://example.com",
             "model": "model", "options": {}, **change}
    if change.get("model", "present") is None:
        del saved["model"]
    path = config.config_path()
    path.parent.mkdir(parents=True)
    path.write_text(tomli_w.dumps({"version": 1, "connections": {"work": saved}}))
    assert cli.main(command) == 1
    assert not calls
    assert "Traceback" not in capsys.readouterr().err
