"""Bundled skills install locally without changing profiles or following old links."""

from pathlib import Path

import pytest

from mortis_engine import cli, config, gateway
from mortis_engine.cli import skills

BUNDLE = Path(__file__).resolve().parents[1] / ".agents/skills/mortis-engine-use"


@pytest.fixture(autouse=True)
def isolated_installation(tmp_path, monkeypatch):
    personal = tmp_path / "personal directory"
    monkeypatch.setattr(Path, "home", lambda: personal)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    def forbidden(*args, **kwargs):
        pytest.fail("Skill installation accessed profiles, credentials or a provider")

    monkeypatch.setattr(config, "load", forbidden)
    monkeypatch.setattr(config, "credentials", forbidden)
    monkeypatch.setattr(gateway, "send", forbidden)
    monkeypatch.setattr(gateway, "test_connection", forbidden)
    yield personal
    assert not (tmp_path / "config").exists()
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("agent,folder", [("codex", ".agents"), ("claude-code", ".claude")])
def test_installs_complete_portable_skill(isolated_installation, agent, folder, capsys):
    assert cli.main(["skill", "install", agent]) == 0
    destination = isolated_installation / folder / "skills/mortis-engine-use"
    contents = {path.relative_to(destination).as_posix(): path.read_bytes()
                for path in destination.rglob("*") if path.is_file()}
    assert set(contents) == {"SKILL.md", "references/configuration.md", "references/python.md"}
    assert contents == {name: (BUNDLE / name).read_bytes() for name in contents}
    assert str(destination) in capsys.readouterr().out


def test_existing_installation_requires_force_and_is_replaced(isolated_installation, capsys):
    assert cli.main(["skill", "install", "codex"]) == 0
    destination = isolated_installation / ".agents/skills/mortis-engine-use"
    (destination / "SKILL.md").write_text("Local edits\n")
    (destination / "custom.md").write_text("Additional instructions\n")
    assert cli.main(["skill", "install", "codex"]) == 1
    assert "--force" in capsys.readouterr().err
    assert (destination / "SKILL.md").read_text() == "Local edits\n"
    assert (destination / "custom.md").is_file()
    assert cli.main(["skill", "install", "codex", "--force"]) == 0
    assert (destination / "SKILL.md").read_bytes() == (BUNDLE / "SKILL.md").read_bytes()
    assert not (destination / "custom.md").exists()
    assert not list(destination.parent.glob(".mortis-skill-*"))


@pytest.mark.parametrize("dangling", [False, True])
def test_force_replaces_symlink_without_touching_target(isolated_installation, tmp_path, dangling):
    original = tmp_path / "original-skill"
    if not dangling:
        original.mkdir()
        (original / "SKILL.md").write_text("Original source\n")
    destination = isolated_installation / ".claude/skills/mortis-engine-use"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(original, target_is_directory=True)
    assert cli.main(["skill", "install", "claude-code"]) == 1
    assert destination.is_symlink()
    assert cli.main(["skill", "install", "claude-code", "--force"]) == 0
    assert destination.is_dir() and not destination.is_symlink()
    if dangling:
        assert not original.exists()
    else:
        assert (original / "SKILL.md").read_text() == "Original source\n"


def test_force_does_not_replace_a_regular_file(isolated_installation):
    destination = isolated_installation / ".agents/skills/mortis-engine-use"
    destination.parent.mkdir(parents=True)
    destination.write_text("Unrelated file\n")
    assert cli.main(["skill", "install", "codex", "--force"]) == 1
    assert destination.read_text() == "Unrelated file\n"


@pytest.mark.parametrize("existing", [False, True])
def test_copy_failure_preserves_previous_installation(isolated_installation, monkeypatch, existing):
    destination = isolated_installation / ".agents/skills/mortis-engine-use"
    if existing:
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text("Previous skill\n")

    def fail(*args, **kwargs):
        raise OSError("Copy failed")

    monkeypatch.setattr(skills.shutil, "copytree", fail)
    assert cli.main(["skill", "install", "codex", "--force"]) == 1
    if existing:
        assert (destination / "SKILL.md").read_text() == "Previous skill\n"
    else:
        assert not destination.exists()
    assert not list(destination.parent.glob(".mortis-skill-*"))


@pytest.mark.parametrize("restore_fails", [False, True])
def test_publication_failure_retains_previous_skill(isolated_installation, monkeypatch, capsys, restore_fails):
    destination = isolated_installation / ".agents/skills/mortis-engine-use"
    destination.mkdir(parents=True)
    (destination / "SKILL.md").write_text("Previous skill\n")
    rename = Path.rename

    def fail(path, target):
        if path.name == "new" or (restore_fails and path.name == "previous"):
            raise OSError("Rename failed")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail)
    assert cli.main(["skill", "install", "codex", "--force"]) == 1
    if restore_fails:
        backup, = destination.parent.glob(".mortis-skill-*/previous")
        assert (backup / "SKILL.md").read_text() == "Previous skill\n"
        assert str(backup) in capsys.readouterr().err
    else:
        assert (destination / "SKILL.md").read_text() == "Previous skill\n"
        assert not list(destination.parent.glob(".mortis-skill-*"))


def test_unknown_agent_is_rejected_before_writing(isolated_installation):
    with pytest.raises(SystemExit) as error:
        cli.main(["skill", "install", "unsupported"])
    assert error.value.code == 2
    assert not isolated_installation.exists()
