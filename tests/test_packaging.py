"""Archive boundaries do not include development state or lose parser assets."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("check_dist", Path(__file__).resolve().parents[1] / "scripts/check_dist.py")
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


def test_distribution_paths_retain_parser_build_assets():
    for wheel, prefix in ((True, "mortis_engine/"), (False, "src/mortis_engine/")):
        names = {prefix + name for name in (*packaging.PARSER_ASSETS, "__init__.py")}
        packaging.check_names(names, wheel=wheel)
        names.remove(prefix + "code_graph/Dockerfile")
        with pytest.raises(AssertionError, match="Missing parser build assets"):
            packaging.check_names(names, wheel=wheel)


@pytest.mark.parametrize("name", [".codex/config.toml", ".github/workflows/ci.yml", ".local/report.json",
                                  "tests/__pycache__/test_cli.pyc", "../credentials", "/tmp/secret", "reports/run.json",
                                  "tests/.env", "docs/.env.production", "src/mortis_engine/local.sqlite3",
                                  "tests/.pytest_cache/state", "src/.aws/config", "docs/private.pem", "tests/run.log",
                                  "PLAN.md", "evals/review_improvements/PLAN.md", "REVIEW_IMPROVEMENTS.md"])
def test_source_distribution_rejects_private_generated_and_unsafe_paths(name):
    with pytest.raises(AssertionError):
        packaging.check_names({name}, wheel=False)


def test_wheel_rejects_nonruntime_files():
    with pytest.raises(AssertionError):
        packaging.check_names({"tests/test_cli.py"}, wheel=True)
