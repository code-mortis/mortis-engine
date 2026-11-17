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
    assert not (tmp_path / "config/mortis").exists()
    assert not (tmp_path / "state").exists()


def test_installs_complete_portable_skill_without_creating_agent_directories(isolated_installation, capsys):
    assert cli.main(["agent", "install"]) == 0
    destination = isolated_installation / ".agents/skills/mortis-engine-use"
    contents = {path.relative_to(destination).as_posix(): path.read_bytes()
                for path in destination.rglob("*") if path.is_file() and path.name != ".mortis-managed"}
    assert set(contents) == {"SKILL.md", "references/configuration.md", "references/python.md"}
    assert contents == {name: (BUNDLE / name).read_bytes() for name in contents}
    assert str(destination) in capsys.readouterr().out
    assert {path.name for path in isolated_installation.iterdir()} == {".agents"}


def test_one_command_installs_and_updates_every_detected_agent(isolated_installation, tmp_path):
    roots = [isolated_installation / folder for folder in (".claude", ".cursor", ".codex")]
    roots.append(tmp_path / "config/opencode")
    for root in roots:
        root.mkdir(parents=True)
    assert cli.main(["agent", "install"]) == 0
    canonical = isolated_installation / ".agents/skills/mortis-engine-use"
    (canonical / "SKILL.md").write_text("Older managed version\n")
    assert cli.main(["agent", "install"]) == 0
    for root in roots:
        destination = root / "skills/mortis-engine-use"
        assert (destination / "SKILL.md").read_bytes() == (BUNDLE / "SKILL.md").read_bytes()
        if skills.os.name != "nt":
            assert destination.is_symlink() and destination.resolve() == canonical.resolve()


def test_legacy_opencode_and_shared_skill_parent(isolated_installation, tmp_path):
    shared = isolated_installation / ".agents/skills"
    shared.mkdir(parents=True)
    (isolated_installation / ".opencode").mkdir()
    codex = isolated_installation / ".codex"
    codex.mkdir()
    (codex / "skills").symlink_to(shared, target_is_directory=True)
    assert cli.main(["agent", "install"]) == 0
    canonical = shared / skills.SKILL_NAME
    assert canonical.is_dir() and not canonical.is_symlink()
    assert (tmp_path / "config/opencode/skills" / skills.SKILL_NAME / "SKILL.md").is_file()


def test_existing_installation_requires_force_and_is_replaced(isolated_installation, capsys):
    destination = isolated_installation / ".agents/skills/mortis-engine-use"
    destination.mkdir(parents=True)
    (destination / "SKILL.md").write_text("Local edits\n")
    (destination / "custom.md").write_text("Additional instructions\n")
    assert cli.main(["agent", "install"]) == 1
    assert "--force" in capsys.readouterr().err
    assert (destination / "SKILL.md").read_text() == "Local edits\n"
    assert (destination / "custom.md").is_file()
    assert cli.main(["agent", "install", "--force"]) == 0
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
    assert cli.main(["agent", "install"]) == 1
    assert destination.is_symlink()
    assert not (isolated_installation / ".agents").exists()
    assert cli.main(["agent", "install", "--force"]) == 0
    assert (destination / "SKILL.md").read_bytes() == (BUNDLE / "SKILL.md").read_bytes()
    if dangling:
        assert not original.exists()
    else:
        assert (original / "SKILL.md").read_text() == "Original source\n"


def test_force_does_not_replace_a_regular_file(isolated_installation):
    destination = isolated_installation / ".agents/skills/mortis-engine-use"
    destination.parent.mkdir(parents=True)
    destination.write_text("Unrelated file\n")
    assert cli.main(["agent", "install", "--force"]) == 1
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
    assert cli.main(["agent", "install", "--force"]) == 1
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
    assert cli.main(["agent", "install", "--force"]) == 1
    if restore_fails:
        backup, = destination.parent.glob(".mortis-skill-*/previous")
        assert (backup / "SKILL.md").read_text() == "Previous skill\n"
        assert str(backup) in capsys.readouterr().err
    else:
        assert (destination / "SKILL.md").read_text() == "Previous skill\n"
        assert not list(destination.parent.glob(".mortis-skill-*"))


def test_agent_install_rejects_positional_arguments_before_writing(isolated_installation):
    with pytest.raises(SystemExit) as error:
        cli.main(["agent", "install", "unsupported"])
    assert error.value.code == 2
    assert not isolated_installation.exists()
