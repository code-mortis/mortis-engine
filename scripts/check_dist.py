"""Check built archive contents before testing or publishing them."""

import argparse
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

PARSER_ASSETS = {"code_graph/Dockerfile", "code_graph/requirements.txt"}
FORBIDDEN = {".git", ".github", ".codex", ".local", ".venv", ".aws", ".pytest_cache", ".ruff_cache",
             ".coverage", "__pycache__", "dist", "reports", "PLAN.md", "REVIEW_IMPROVEMENTS.md"}
PRIVATE_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key", ".p12", ".pfx"}
SDIST_ROOTS = {
    "src", "tests", "evals", "docs", "scripts", "README.md", "LICENSE", "AGENTS.md",
    "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "uv.lock", "mortis.toml", "pyproject.toml",
    "PKG-INFO", ".gitignore",
}


def check_names(names, *, wheel):
    for name in names:
        path = PurePosixPath(name)
        assert path.parts and not path.is_absolute() and ".." not in path.parts, f"Unsafe archive path: {name}"
        assert not (set(path.parts) & FORBIDDEN), f"Private/generated archive content: {name}"
        assert not any(part == ".env" or part.startswith(".env.") for part in path.parts), name
        assert path.suffix not in PRIVATE_SUFFIXES, f"Private/generated archive content: {name}"
        if wheel:
            assert path.parts[0] == "mortis_engine" or path.parts[0].endswith(".dist-info"), name
        else:
            assert path.parts[0] in SDIST_ROOTS, f"Unexpected source archive content: {name}"
    prefix = "mortis_engine/" if wheel else "src/mortis_engine/"
    assert {prefix + asset for asset in PARSER_ASSETS} <= names, "Missing parser build assets"
    assert prefix + "__init__.py" in names, "Missing runtime package"


def check_dist(directory, root):
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    wheel, sdist = directory / f"mortis_engine-{version}-py3-none-any.whl", directory / f"mortis_engine-{version}.tar.gz"
    # uv build creates .gitignore alongside the distributions.
    artifacts = {path for path in directory.iterdir() if not (path.name == ".gitignore" and path.is_file())}
    assert artifacts == {wheel, sdist}, (
        f"Expected exactly one wheel and one source distribution; found: {sorted(path.name for path in artifacts)}"
    )
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        check_names(names, wheel=True)
        metadata = BytesParser().parsebytes(archive.read(f"mortis_engine-{version}.dist-info/METADATA"))
        assert metadata["Name"] == "mortis-engine" and metadata["Version"] == version
        assert archive.read(f"mortis_engine-{version}.dist-info/licenses/LICENSE") == (root / "LICENSE").read_bytes()
    with tarfile.open(sdist) as archive:
        members = archive.getmembers()
        prefix = f"mortis_engine-{version}/"
        assert all(member.name.startswith(prefix) and member.isfile() for member in members), "Unexpected tar member"
        names = {member.name.removeprefix(prefix) for member in members}
        check_names(names, wheel=False)
        for name in ("README.md", "LICENSE", "AGENTS.md", "CONTRIBUTING.md", "SECURITY.md",
                     "CHANGELOG.md", "uv.lock", "mortis.toml", "pyproject.toml", "tests/smoke_test.py",
                     "evals/review_improvements/run.py", "evals/review_improvements/baseline-result.json"):
            assert name in names, f"Missing source archive file: {name}"
        for tree in ("src/mortis_engine", "tests", "evals/review_improvements", "docs", "scripts"):
            for source in (root / tree).rglob("*"):
                relative = source.relative_to(root).as_posix()
                if (source.is_file() and not (set(source.parts) & FORBIDDEN)
                        and source.suffix not in PRIVATE_SUFFIXES
                        and not any(part == ".env" or part.startswith(".env.") for part in source.parts)):
                    assert relative in names, f"Missing source archive file: {relative}"
                    assert archive.extractfile(prefix + relative).read() == source.read_bytes(), relative
    print(f"Validated {wheel.name} and {sdist.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=Path("dist"))
    args = parser.parse_args()
    check_dist(args.directory.resolve(), Path(__file__).resolve().parents[1])
