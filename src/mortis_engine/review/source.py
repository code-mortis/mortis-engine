"""Read pinned local Git objects through an isolated, credential-free Git repository."""

import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from mortis_engine.review.errors import ReviewStopped


def git(directory, env, *args, stopped=lambda: False, timeout=180):
    executable = shutil.which("git", path=os.pathsep.join(
        entry for entry in env.get("PATH", os.defpath).split(os.pathsep) if Path(entry).is_absolute()))
    if executable is None:
        raise ValueError("Git is required for local reviews. Install Git and add its directory to PATH.")
    command = [executable, "--no-pager", "--literal-pathspecs", "-c", "core.hooksPath=/dev/null",
               "-c", "protocol.allow=never", "-c", "credential.helper=", *args]
    if stopped():
        raise ReviewStopped("The review was cancelled.")
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=errors, start_new_session=True)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if stopped():
                    raise ReviewStopped("The review was cancelled.")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Local Git inspection reached its time limit.")
                if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > 20_000_000:
                    raise ValueError("Local Git output exceeds 20 MB.")
                try:
                    process.wait(timeout=0.05)
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode:
                raise ValueError("Git cannot inspect the requested local commits. Check that the refs and their history exist locally.")
            if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > 20_000_000:
                raise ValueError("Local Git output exceeds 20 MB.")
            output.seek(0)
            return output.read(20_000_000).decode("utf-8", "replace")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def repository_path(value):
    path = PurePosixPath(value)
    if (not value or not path.parts or path.as_posix() != value or path.is_absolute() or ".." in path.parts
            or ".git" in path.parts or "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("Source path must stay inside the repository.")
    return value


def read_source(repo, env, sha, path, stopped=lambda: False):
    repository_path(path)
    entry = git(repo, env, "ls-tree", "-z", "-l", sha, "--", path, stopped=stopped)
    match = re.fullmatch(r"(100644|100755) blob ([0-9a-f]{40})\s+(\d+)\t([^\0]+)\0\n?", entry)
    if not match or match[4] != path:
        raise ValueError("Source is missing or is not a regular file at the pinned revision.")
    if int(match[3]) > 20_000_000:
        raise ValueError("Source exceeds the 20 MB read limit.")
    content = git(repo, env, "cat-file", "blob", match[2], stopped=stopped)
    if "\x00" in content or "\ufffd" in content:
        raise ValueError("Required source is binary or is not UTF-8 text.")
    return content


def _read_local(path, maximum):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        if path.is_symlink():
            raise ValueError("Symbolic links in Git metadata are unsupported.")
        with os.fdopen(os.open(path, flags), "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Git metadata must contain only regular files.")
            if info.st_size > maximum:
                raise ValueError("Git metadata exceeds the local inspection size limit.")
            value = stream.read(maximum + 1)
            if len(value) > maximum:
                raise ValueError("Git metadata exceeds the local inspection size limit.")
            return value
    except OSError:
        raise ValueError("Git metadata is missing or cannot be read safely.") from None


def find_repository(path):
    """Return the Git directory, or None when the directory has no repository."""
    try:
        path = Path(path).expanduser().resolve(strict=True)
    except OSError:
        raise ValueError("The requested repository directory does not exist or is inaccessible.") from None
    if not path.is_dir():
        raise ValueError("Choose a local Git repository directory.")
    for candidate in (path, *path.parents):
        marker = candidate / ".git"
        if marker.is_symlink():
            raise ValueError("A symbolic-link Git directory is unsupported.")
        if marker.is_dir():
            return marker
        if marker.exists():
            try:
                content = _read_local(marker, 4096).decode("utf-8").strip()
                if not content.startswith("gitdir: ") or "\n" in content:
                    raise ValueError("Invalid Git worktree pointer.")
                target = (candidate / content[8:]).resolve(strict=True)
            except (OSError, UnicodeError):
                raise ValueError("Invalid or missing Git worktree directory.") from None
            if not target.is_dir():
                raise ValueError("Invalid Git worktree directory.")
            return target
    if (path / "HEAD").exists() and (path / "objects").is_dir():
        return path
    return None


def _regular_tree(path):
    if path.is_symlink():
        raise ValueError("Symbolic links in Git object or reference storage are unsupported.")
    if path.is_dir():
        for root, dirs, files in os.walk(path, followlinks=False):
            for name in (*dirs, *files):
                try:
                    info = (Path(root) / name).lstat()
                except FileNotFoundError:
                    # Git maintenance may remove a temporary entry during enumeration.
                    continue
                if stat.S_ISLNK(info.st_mode):
                    raise ValueError("Symbolic links in Git object or reference storage are unsupported.")
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise ValueError("Git object and reference storage requires regular files and directories.")
    elif path.exists() and not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Git object and reference storage requires regular files and directories.")


@contextmanager
def local_repository(path):
    """Ignore local config, hooks, replacements, alternates and worktree content."""
    git_dir = find_repository(path)
    if git_dir is None:
        raise ValueError("Run mortis review inside a local Git repository or pass --repo.")
    common = git_dir
    if (git_dir / "commondir").exists():
        try:
            pointer = _read_local(git_dir / "commondir", 4096).decode("utf-8").strip()
            common = (git_dir / pointer).resolve(strict=True)
        except (OSError, UnicodeError):
            raise ValueError("The Git worktree common directory is missing or invalid.") from None
    objects = common / "objects"
    _regular_tree(objects)
    if not objects.is_dir() or (objects / "info/alternates").exists():
        raise ValueError("Local review requires a complete object store without alternates.")
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    env["PATH"] = os.pathsep.join(entry for entry in env.get("PATH", os.defpath).split(os.pathsep)
                                  if Path(entry).is_absolute())
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
               GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", GIT_ATTR_NOSYSTEM="1",
               GIT_OPTIONAL_LOCKS="0", LC_ALL="C", GIT_CONFIG_SYSTEM=os.devnull)
    with tempfile.TemporaryDirectory(prefix="mortis-source-") as tmp:
        isolated = Path(tmp)
        git(isolated, env, "init", "--quiet", "--bare", "--template=", "--object-format=sha1")
        # Object access bypasses repository config, including promisor remotes and filters.
        env["GIT_OBJECT_DIRECTORY"] = str(objects)
        refs = common / "refs"
        _regular_tree(refs)
        total, count = 0, 0
        if refs.is_dir():
            for original in refs.rglob("*"):
                if original.is_dir():
                    continue
                value = _read_local(original, 4096)
                total += len(value)
                count += 1
                if total > 16_000_000 or count > 100_000:
                    raise ValueError("Git references exceed the local inspection size limit.")
                target = isolated / "refs" / original.relative_to(refs)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(value)
        for name in ("packed-refs", "shallow"):
            original = common / name
            if original.exists():
                value = _read_local(original, 16_000_000 - total)
                total += len(value)
                (isolated / name).write_bytes(value)
        (isolated / "HEAD").write_bytes(_read_local(git_dir / "HEAD", 4096))
        yield isolated, env


def resolve_commit(repo, env, ref):
    if not isinstance(ref, str) or not ref or ref.startswith("-") or any(ord(c) < 32 for c in ref):
        raise ValueError("Choose a valid local commit reference.")
    sha = git(repo, env, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Local reviews require SHA-1 Git commit objects.")
    return sha


def merge_base(repo, env, base_sha, head_sha):
    try:
        result = git(repo, env, "merge-base", base_sha, head_sha).strip()
    except ValueError:
        message = ("The common ancestor is unavailable in this shallow clone. Fetch the required history before reviewing."
                   if (Path(repo) / "shallow").is_file()
                   else "The commits have no common ancestor available locally. Check the selected refs and history.")
        raise ValueError(message) from None
    if not re.fullmatch(r"[0-9a-f]{40}", result):
        raise ValueError("The commits require a local common ancestor.")
    return result
