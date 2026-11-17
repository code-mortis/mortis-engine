"""Run with an isolated installed interpreter, from outside the source checkout."""

import importlib.metadata
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


def main():
    import mortis_engine
    from mortis_engine import cli
    from mortis_engine.code_graph import build

    version, checkout = sys.argv[1:]
    package = Path(mortis_engine.__file__).resolve()
    assert package.is_relative_to(Path(sys.prefix).resolve()), package
    assert not package.is_relative_to(Path(checkout).resolve()), package
    assert mortis_engine.__version__ == importlib.metadata.version("mortis-engine") == version
    executable = Path(sys.executable).with_name("mortis")
    assert subprocess.check_output([executable, "--version"], text=True).strip() == f"mortis {version}"
    assert "review" in subprocess.check_output([executable, "--help"], text=True)
    assert "--tag" in subprocess.check_output([sys.executable, "-m", build.__name__, "--help"], text=True)
    for name in ("Dockerfile", "requirements.txt", "contracts.py", "extract.py", "syntax.py"):
        assert (Path(build.__file__).parent / name).read_bytes(), name
    with tempfile.TemporaryDirectory(prefix="mortis-skill-smoke-") as directory:
        personal = Path(directory)
        config = personal / "config"
        roots = [personal / folder for folder in (".claude", ".codex", ".cursor")]
        roots.append(config / "opencode")
        for root in roots:
            root.mkdir(parents=True)
        with patch.object(Path, "home", return_value=personal), patch.dict(os.environ, XDG_CONFIG_HOME=str(config)):
            assert cli.main(["agent", "install"]) == 0
            for root in [personal / ".agents", *roots]:
                target = root / "skills/mortis-engine-use"
                for name in ("SKILL.md", "references/configuration.md", "references/python.md"):
                    assert (target / name).read_bytes() == (package.parent / "_skill" / name).read_bytes()
    print(f"Installed Mortis {version}: CLI, parser assets and skill installers OK ({package})")


if __name__ == "__main__":
    main()
