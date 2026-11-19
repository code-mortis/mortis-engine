"""A release needs matching stable versions and the triggering mainline commit."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SPEC = importlib.util.spec_from_file_location("release_check", Path(__file__).resolve().parents[1] / "scripts/release_check.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
SHA = "a" * 40


@pytest.mark.parametrize("tag", ["main", "v0.1", "v01.1.0", "v0.1.0rc1", "v0.1.0+local", "v0.1.0\n"])
def test_release_rejects_invalid_tags_before_any_command(tmp_path, tag):
    with patch.object(release.subprocess, "check_output") as command:
        with pytest.raises(SystemExit, match="stable"):
            release.check_release(tag, SHA, tmp_path)
        command.assert_not_called()


@pytest.mark.parametrize(("outputs", "sha", "error"), [
    (["mortis 0.1.1"], SHA, "CLI"),
    (["mortis 0.1.0", SHA, "b" * 40], SHA, "triggering"),
    (["mortis 0.1.0", SHA, SHA], "b" * 40, "triggering"),
])
def test_release_rejects_version_and_revision_mismatch(tmp_path, outputs, sha, error):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    with patch.object(release.subprocess, "check_output", side_effect=outputs):
        with pytest.raises(SystemExit, match=error):
            release.check_release("v0.1.0", sha, tmp_path)


@pytest.mark.parametrize("optimization", ["-O", "-OO", "env"])
@pytest.mark.parametrize(("tag", "outputs", "sha", "error"), [
    ("main", [], SHA, "stable"),
    ("v9.9.9", [], SHA, "Tag and project"),
    ("v0.1.0", ["mortis 0.1.1"], SHA, "CLI"),
    ("v0.1.0", ["mortis 0.1.0", SHA, "b" * 40], SHA, "triggering"),
    ("v0.1.0", ["mortis 0.1.0", SHA, SHA], "b" * 40, "triggering"),
])
def test_release_checks_survive_optimized_python(tmp_path, optimization, tag, outputs, sha, error):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    code = """
import json, runpy, sys
from pathlib import Path
from unittest.mock import patch
check = runpy.run_path(sys.argv[1])["check_release"]
with patch("subprocess.check_output", side_effect=json.loads(sys.argv[5])), patch("subprocess.run"):
    check(sys.argv[2], sys.argv[3], Path(sys.argv[4]))
"""
    result = subprocess.run([sys.executable, *([] if optimization == "env" else [optimization]),
        "-c", code, SPEC.origin, tag, sha, str(tmp_path), json.dumps(outputs)],
        env={**os.environ, "PYTHONOPTIMIZE": "1"}, capture_output=True, text=True)
    assert result.returncode != 0
    assert error in result.stderr
    assert "Validated" not in result.stdout


def test_release_requires_main_ancestry(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    with patch.object(release.subprocess, "check_output", side_effect=["mortis 0.1.0", SHA, SHA]):
        with patch.object(release.subprocess, "run") as ancestry:
            release.check_release("v0.1.0", SHA, tmp_path)
            ancestry.assert_called_once_with(["git", "merge-base", "--is-ancestor", SHA, "refs/remotes/origin/main"],
                                              cwd=tmp_path, check=True)
        with patch.object(release.subprocess, "check_output", side_effect=["mortis 0.1.0", SHA, SHA]):
            with patch.object(release.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "git")):
                with pytest.raises(subprocess.CalledProcessError):
                    release.check_release("v0.1.0", SHA, tmp_path)


@pytest.mark.parametrize("annotated", [False, True])
def test_release_resolves_real_tags_and_rejects_off_main_commits(tmp_path, monkeypatch, annotated):
    for name in os.environ:
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def git(*args):
        return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                                        "-c", "tag.gpgsign=false", "-c", "user.name=Mortis Test",
                                        "-c", "user.email=test@localhost", *args], cwd=tmp_path, text=True).strip()

    git("init", "-qb", "main", "--template=")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    (tmp_path / "mortis_engine.py").write_text('print("mortis 0.1.0")\n')
    git("add", ".")
    git("commit", "-qm", "main fixture")
    main = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", main)
    tag_args = ("-a", "-m", "fixture") if annotated else ()
    git("tag", *tag_args, "v0.1.0")
    release.check_release("v0.1.0", main, tmp_path)
    git("checkout", "-qb", "unmerged")
    git("commit", "--allow-empty", "-qm", "unmerged fixture")
    git("tag", "-d", "v0.1.0")
    git("tag", *tag_args, "v0.1.0")
    with pytest.raises(subprocess.CalledProcessError):
        release.check_release("v0.1.0", git("rev-parse", "HEAD"), tmp_path)
