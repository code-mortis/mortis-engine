"""Immutable local review inputs, built without checkout, filters or index writes."""

import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import planning, settings, source

MAX_SOURCE_BYTES = 20_000_000
MAX_SNAPSHOT_BYTES = 100_000_000


@dataclass
class Snapshot:
    repo: Path
    env: dict
    base_sha: str
    head_sha: str
    merge_base: str
    settings_sha: str
    mode: str
    excluded: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _worktree_root(path):
    path = Path(path).expanduser().resolve(strict=True)
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    raise ValueError("Working-tree and staged reviews require a Git working tree.")


def _index(repo, env, git_dir):
    original = git_dir / "index"
    if not original.exists():
        raise ValueError("The repository has no index. Commit the initial revision before reviewing local changes.")
    (repo / "index").write_bytes(source._read_local(original, 64_000_000))
    # Split indexes reference a shared file beside the copied index.
    for shared in git_dir.glob("sharedindex.*"):
        if not re.fullmatch(r"sharedindex\.[0-9a-f]{40}", shared.name):
            raise ValueError("Invalid shared Git index name.")
        (repo / shared.name).write_bytes(source._read_local(shared, 64_000_000))
    common = git_dir
    if (git_dir / "commondir").exists():
        common = (git_dir / source._read_local(git_dir / "commondir", 4096).decode("utf-8").strip()).resolve(strict=True)
    exclude = common / "info/exclude"
    if exclude.exists():
        (repo / "info").mkdir(exist_ok=True)
        (repo / "info/exclude").write_bytes(source._read_local(exclude, 512_000))
    env["GIT_INDEX_FILE"] = str(repo / "index")


def _entries(repo, env):
    values = source.git(repo, env, "-c", "core.fsmonitor=false", "ls-files", "--stage", "-z")
    result = {}
    for value in values.split("\0"):
        if not value:
            continue
        match = re.fullmatch(r"(100644|100755|120000|160000) ([0-9a-f]{40}) ([0-3])\t(.+)", value)
        if not match or match[3] != "0":
            raise ValueError("Resolve unmerged or unsupported index entries before reviewing local changes.")
        path = source.repository_path(match[4])
        result[path] = (match[1], match[2])
    return result


def _cached_times(repo, env):
    output = source.git(repo, env, "ls-files", "--debug", "-z")
    pattern = (r"([^\0]+)\0  ctime: (\d+):(\d+)\n  mtime: (\d+):(\d+)\n"
               r"  dev: [^\n]+\n  uid: [^\n]+\n  size: (\d+)\tflags: [^\n]+\n")
    return {match[1]: (int(match[2]) * 1_000_000_000 + int(match[3]),
                      int(match[4]) * 1_000_000_000 + int(match[5]), int(match[6]))
            for match in re.finditer(pattern, output)}


def _worktree_stat(root, path):
    parts = source.repository_path(path).split("/")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory = os.open(root, flags)
    try:
        for part in parts[:-1]:
            next_directory = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        return os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError("Working-tree paths cannot traverse symbolic-link directories.") from None
    finally:
        os.close(directory)


def _worktree_bytes(root, path):
    """Open every path component without following repository symbolic links."""
    parts = source.repository_path(path).split("/")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory = os.open(root, directory_flags)
    try:
        for part in parts[:-1]:
            next_directory = os.open(part, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            # Store the link text as a link blob; source readers reject its mode.
            return "120000", os.readlink(parts[-1], dir_fd=directory).encode("utf-8")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Working-tree source must be a regular file or symbolic link.")
        if info.st_size > MAX_SOURCE_BYTES:
            raise ValueError("Working-tree source exceeds the 20 MB read limit.")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(parts[-1], flags, dir_fd=directory), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("Working-tree source changed while the snapshot was being created.")
            value = stream.read(MAX_SOURCE_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(value) > MAX_SOURCE_BYTES:
            raise ValueError("Working-tree source exceeds the 20 MB read limit.")
        if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Working-tree source changed while the snapshot was being created.")
        return "100755" if opened.st_mode & 0o111 else "100644", value
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError("Working-tree source cannot be read safely without following symbolic links.") from None
    finally:
        os.close(directory)


def _commit(repo, env, tree, parent=None):
    identity = {**env, "GIT_AUTHOR_NAME": "Mortis", "GIT_AUTHOR_EMAIL": "mortis@localhost",
                "GIT_COMMITTER_NAME": "Mortis", "GIT_COMMITTER_EMAIL": "mortis@localhost",
                "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"}
    return source.git(repo, identity, "commit-tree", tree, *(["-p", parent] if parent else []),
                      input_bytes=b"Mortis immutable local snapshot\n").strip()


def _tree(repo, env, entries):
    index = repo / "snapshot-index"
    temporary_env = {**env, "GIT_INDEX_FILE": str(index)}
    source.git(repo, temporary_env, "read-tree", "--empty")
    records = b"".join(f"{mode} {sha}\t{path}\0".encode() for path, (mode, sha) in sorted(entries.items()))
    if records:
        source.git(repo, temporary_env, "update-index", "-z", "--index-info", input_bytes=records)
    return source.git(repo, temporary_env, "write-tree").strip()


def _local_tree(repo, env, root, git_dir, mode, stopped, options, rules):
    _index(repo, env, git_dir)
    staged = _entries(repo, env)
    excluded = []
    if mode == "staged":
        return _tree(repo, env, staged), excluded
    paths = source.git(repo, env, "-c", "core.fsmonitor=false", "--work-tree=" + str(root),
                       "ls-files", "--cached", "--others", "--exclude-standard", "--full-name", "-z",
                       stopped=stopped).split("\0")
    flags = source.git(repo, env, "-c", "core.fsmonitor=false", "ls-files", "-v", "-z").split("\0")
    sparse_paths = {value[2:] for value in flags if value[:2].lower() == "s "}
    # Validate path traversal before Git inspects worktree stat information.
    stats = {path: _worktree_stat(root, path) for path in staged}
    cached_times = _cached_times(repo, env)
    source.git(repo, env, "--work-tree=" + str(root), "update-index", "--no-assume-unchanged", "-z", "--stdin",
               input_bytes=b"".join(path.encode() + b"\0" for path in staged), stopped=stopped)
    changed = set(filter(None, source.git(
        repo, env, "-c", "core.fsmonitor=false", "--work-tree=" + str(root),
        "diff-files", "--name-only", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "-z",
        stopped=stopped).split("\0")))
    # Some Git builds ignore subsecond stat differences when reporting diff-files.
    changed.update(path for path, info in stats.items() if info is not None
                   and cached_times.get(path) != (info.st_ctime_ns, info.st_mtime_ns, info.st_size))
    entries, total = {}, 0
    for path in sorted(set(filter(None, paths))):
        source.repository_path(path)
        if not planning.allowed_path(path, options, rules):
            excluded.append({"path": path, "reason": "excluded"})
            if not planning.allowed_path(path, options, rules, supporting_evidence=True):
                # Reuse an existing excluded blob without reading or ingesting its contents.
                if path in staged and (_worktree_stat(root, path) is not None or path in sparse_paths):
                    entries[path] = staged[path]
                continue
        if stopped():
            raise source.ReviewStopped("The review was cancelled.")
        if path in staged and (path not in changed or staged[path][0] == "160000"):
            entries[path] = staged[path]
            continue
        value = _worktree_bytes(root, path)
        if value is None:
            if path in sparse_paths:
                entries[path] = staged[path]
            continue
        file_mode, content = value
        total += len(content)
        if total > MAX_SNAPSHOT_BYTES:
            raise ValueError("Working-tree source exceeds the 100 MB snapshot admission limit.")
        sha = source.git(repo, env, "hash-object", "-w", "--stdin", input_bytes=content, stopped=stopped).strip()
        entries[path] = (file_mode, sha)
    return _tree(repo, env, entries), excluded


@contextmanager
def snapshot(repository, *, mode="commits", base=None, head="HEAD", object_cache=None, stopped=lambda: False,
             overrides=None):
    """Pin a review input. Persist generated objects only in the supplied private cache."""
    if mode not in {"commits", "worktree", "staged", "all"}:
        raise ValueError("Choose commits, worktree, staged or all as the review mode.")
    if mode in {"worktree", "staged"} and (base is not None or head != "HEAD"):
        raise ValueError("Working-tree and staged reviews use the current HEAD as their base.")
    with source.local_repository(repository, object_cache=object_cache) as (repo, env):
        target = source.resolve_commit(repo, env, head)
        if mode == "commits":
            if base is None:
                raise ValueError("Choose a base reference for a commit comparison.")
            base_sha = source.resolve_commit(repo, env, base)
            yield Snapshot(repo, env, base_sha, target, source.merge_base(repo, env, base_sha, target), base_sha, mode)
        elif mode == "all":
            settings_sha = source.resolve_commit(repo, env, base) if base is not None else target
            empty = _commit(repo, env, _tree(repo, env, {}))
            tree = source.git(repo, env, "rev-parse", target + "^{tree}").strip()
            pinned = _commit(repo, env, tree, empty)
            if object_cache is not None:
                source._retain_objects(repo / "objects", source._object_cache(object_cache))
            yield Snapshot(repo, env, empty, pinned, empty, settings_sha, mode)
        else:
            root = _worktree_root(repository)
            options = settings.load(repo, env, target)
            if overrides:
                settings.apply_overrides(options, overrides)
            rules = planning.load_rules(repo, env, target, options, stopped)
            tree, excluded = _local_tree(repo, env, root, source.find_repository(root), mode, stopped, options, rules)
            pinned = _commit(repo, env, tree, target)
            if object_cache is not None:
                source._retain_objects(repo / "objects", source._object_cache(object_cache))
            yield Snapshot(repo, env, target, pinned, target, target, mode, excluded)
