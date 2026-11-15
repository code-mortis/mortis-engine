"""Run with an isolated installed interpreter, from outside the source checkout."""

import importlib.metadata
import subprocess
import sys
from pathlib import Path


def main():
    import mortis_engine
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
    print(f"Installed Mortis {version}: CLI and parser assets OK ({package})")


if __name__ == "__main__":
    main()
