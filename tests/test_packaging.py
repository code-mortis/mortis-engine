"""Archive boundaries do not include development state or lose parser assets."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

SPEC = importlib.util.spec_from_file_location("check_dist", Path(__file__).resolve().parents[1] / "scripts/check_dist.py")
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


def test_distribution_paths_retain_parser_build_assets():
    for wheel, prefix in ((True, "mortis_engine/"), (False, "src/mortis_engine/")):
        names = {prefix + name for name in (*packaging.PARSER_ASSETS, "__init__.py")}
        names.update(packaging.SKILL_PREFIX[wheel] + name for name in packaging.SKILL_ASSETS)
        packaging.check_names(names, wheel=wheel)
        names.remove(prefix + "code_graph/Dockerfile")
        with pytest.raises(AssertionError, match="Missing parser build assets"):
            packaging.check_names(names, wheel=wheel)


@pytest.mark.parametrize("missing", sorted(packaging.SKILL_ASSETS))
def test_distribution_paths_require_complete_skill(missing):
    for wheel, prefix in ((True, "mortis_engine/"), (False, "src/mortis_engine/")):
        names = {prefix + name for name in (*packaging.PARSER_ASSETS, "__init__.py")}
        names.update(packaging.SKILL_PREFIX[wheel] + name for name in packaging.SKILL_ASSETS)
        names.remove(packaging.SKILL_PREFIX[wheel] + missing)
        with pytest.raises(AssertionError, match="Missing coding-agent skill assets"):
            packaging.check_names(names, wheel=wheel)


@pytest.mark.parametrize("wheel", [False, True])
def test_distribution_rejects_development_release_skill(wheel):
    prefix = "mortis_engine/" if wheel else "src/mortis_engine/"
    names = {prefix + name for name in (*packaging.PARSER_ASSETS, "__init__.py")}
    names.update(packaging.SKILL_PREFIX[wheel] + name for name in packaging.SKILL_ASSETS)
    names.add(("mortis_engine/" if wheel else ".agents/skills/") + "mortis-release/SKILL.md")
    with pytest.raises(AssertionError, match="Private/generated archive content"):
        packaging.check_names(names, wheel=wheel)


def test_source_distribution_allows_only_the_usage_skill_under_agents():
    names = {"src/mortis_engine/" + name for name in (*packaging.PARSER_ASSETS, "__init__.py")}
    names.update(packaging.SKILL_PREFIX[False] + name for name in packaging.SKILL_ASSETS)
    names.add(".agents/skills/unrelated/SKILL.md")
    with pytest.raises(AssertionError, match="Unexpected agent source"):
        packaging.check_names(names, wheel=False)


@pytest.mark.parametrize("name", [".codex/config.toml", ".github/workflows/ci.yml", ".local/report.json",
                                  "tests/__pycache__/test_cli.pyc", "../credentials", "/tmp/secret", "reports/run.json",
                                  "tests/.env", "docs/.env.production", "src/mortis_engine/local.sqlite3",
                                  "tests/.pytest_cache/state", "src/.aws/config", "docs/private.pem", "tests/run.log",
                                  "docs/sample.PEM.backup", "tests/DB.SQLITE3.txt",
                                  "PLAN.md", "evals/review_improvements/run.py", "REVIEW_IMPROVEMENTS.md"])
def test_source_distribution_rejects_private_generated_and_unsafe_paths(name):
    with pytest.raises(AssertionError):
        packaging.check_names({name}, wheel=False)


def test_wheel_rejects_nonruntime_files():
    with pytest.raises(AssertionError):
        packaging.check_names({"tests/test_cli.py"}, wheel=True)


@pytest.mark.parametrize("extra", [None, ".gitignore", "old.whl", "README.txt", ".unexpected"])
def test_distribution_inventory_allows_only_archives_and_uv_gitignore(tmp_path, extra):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    directory = tmp_path / "dist"
    directory.mkdir()
    for name in ("mortis_engine-0.1.0-py3-none-any.whl", "mortis_engine-0.1.0.tar.gz"):
        (directory / name).touch()
    if extra:
        (directory / extra).write_text("*")
    with patch.object(packaging.zipfile, "ZipFile", side_effect=RuntimeError("archive validation reached")) as archive:
        if extra in {None, ".gitignore"}:
            with pytest.raises(RuntimeError, match="archive validation reached"):
                packaging.check_dist(directory, tmp_path)
            archive.assert_called_once()
        else:
            with pytest.raises(AssertionError, match="Expected exactly one wheel"):
                packaging.check_dist(directory, tmp_path)
            archive.assert_not_called()
