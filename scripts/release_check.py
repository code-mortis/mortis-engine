"""Fail closed unless a stable version tag names the checked-out mainline commit."""

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path


def check_release(tag, expected_sha, root):
    if not re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag):
        raise SystemExit("Use a stable vMAJOR.MINOR.PATCH tag")
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if tag != f"v{version}":
        raise SystemExit("Tag and project version differ")
    cli = subprocess.check_output([sys.executable, "-m", "mortis_engine", "--version"], cwd=root, text=True).strip()
    if cli != f"mortis {version}":
        raise SystemExit("CLI and project version differ")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    tagged = subprocess.check_output(["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"], cwd=root, text=True).strip()
    if not head == tagged == expected_sha:
        raise SystemExit("Tag, checkout and triggering commit differ")
    subprocess.run(["git", "merge-base", "--is-ancestor", head, "refs/remotes/origin/main"], cwd=root, check=True)
    print(f"Validated {tag} at mainline commit {head}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("sha")
    args = parser.parse_args()
    check_release(args.tag, args.sha, Path(__file__).resolve().parents[1])
