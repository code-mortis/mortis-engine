"""Build the standalone parser image from bundled source, outside any target repository."""

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from mortis_engine.review import file_policy

from . import transport


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=transport.DEFAULT_IMAGE)
    args = parser.parse_args(argv)
    image = transport.image_name(args.tag)
    package = Path(__file__).parent
    with tempfile.TemporaryDirectory(prefix="mortis-parser-build-") as directory:
        root = Path(directory)
        for name in ("Dockerfile", "requirements.txt"):
            shutil.copyfile(package / name, root / name)
        target = root / "mortis_engine/code_graph"
        target.mkdir(parents=True)
        (target.parent / "__init__.py").write_text("")
        (target / "__init__.py").write_text("")
        for name in ("contracts.py", "extract.py", "syntax.py"):
            shutil.copyfile(package / name, target / name)
        review = target.parent / "review"
        review.mkdir()
        (review / "__init__.py").write_text("")
        shutil.copyfile(Path(file_policy.__file__), review / "file_policy.py")
        executable = shutil.which("docker", path=transport.docker_environment(root).get("PATH"))
        if executable is None:
            raise ValueError("Docker is required to build the isolated parser image.")
        configuration = root / "docker-config"
        configuration.mkdir(mode=0o700)
        subprocess.run([executable, "build", "--tag", image, str(root)],
                       env=transport.docker_environment(configuration), check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
