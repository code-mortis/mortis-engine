"""Immutable local review inputs, built without checkout, filters or index writes."""

import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import batched
from pathlib import Path

from mortis_engine.code_graph.contracts import blob_sha

from . import planning, settings, source

MAX_SOURCE_BYTES = 20_000_000
MAX_SNAPSHOT_BYTES = 100_000_000
_GIT_PRINTABLE_AND_NEWLINES = b"\b\t\n\f\r\x1b" + bytes(range(32, 127)) + bytes(range(128, 256))


class OversizedWorktreeSource(ValueError):
    pass


class _WorktreePathReplaced(Exception):
    """A file and directory exchanged places at this path or an ancestor."""


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
    unavailable: dict[str, str] = field(default_factory=dict)
    unavailable_changes: dict[str, tuple[str | None, str]] = field(default_factory=dict)


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
    values = source.git(repo, env, "-c", "core.fsmonitor=false", "ls-files", "--stage", "-z", strict_utf8=True)
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


def _open_worktree_directory(directory, part, flags):
    try:
        return os.open(part, flags, dir_fd=directory)
    except NotADirectoryError:
        # O_NOFOLLOW can also report ENOTDIR for a symlink. Only a real file displaces the directory.
        if stat.S_ISREG(os.stat(part, dir_fd=directory, follow_symlinks=False).st_mode):
            raise _WorktreePathReplaced from None
        raise


def _worktree_stat(root, path, *, gitlink):
    parts = source.repository_path(path).split("/")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root, flags)
    try:
        for part in parts[:-1]:
            next_directory = _open_worktree_directory(directory, part, flags)
            os.close(directory)
            directory = next_directory
        info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        if not gitlink and stat.S_ISDIR(info.st_mode):
            raise _WorktreePathReplaced
        return info
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError("Working-tree paths cannot traverse symbolic-link directories.") from None
    finally:
        os.close(directory)


def _worktree_bytes(root, path):
    """Open every path component without following repository symbolic links."""
    parts = source.repository_path(path).split("/")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root, directory_flags)
    try:
        for part in parts[:-1]:
            next_directory = _open_worktree_directory(directory, part, directory_flags)
            os.close(directory)
            directory = next_directory
        info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            # Store the link text as a link blob; source readers reject its mode.
            return "120000", os.readlink(parts[-1], dir_fd=directory).encode("utf-8")
        if stat.S_ISDIR(info.st_mode):
            raise _WorktreePathReplaced
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Working-tree source must be a regular file or symbolic link.")
        if info.st_size > MAX_SOURCE_BYTES:
            raise OversizedWorktreeSource("Working-tree source exceeds the 20 MB read limit.")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        with os.fdopen(os.open(parts[-1], flags, dir_fd=directory), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("Working-tree source changed while the snapshot was being created.")
            value = stream.read(MAX_SOURCE_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(value) > MAX_SOURCE_BYTES:
            raise OversizedWorktreeSource("Working-tree source exceeds the 20 MB read limit.")
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


def _text_paths(repo, env, paths, stopped):
    # Ask the copied index for declarative EOL attributes without loading filter drivers.
    records = source.git(repo, env, "check-attr", "--cached", "-z", "--stdin", "text", "eol",
                         input_bytes=b"".join(path.encode() + b"\0" for path in paths), stopped=stopped,
                         strict_utf8=True).split("\0")[:-1]
    return {path: text for path, _, text, _, _, eol in batched(records, 6, strict=True)
            if text != "unset" and (text in {"set", "auto"} or eol in {"lf", "crlf"})}


def _git_auto_text(content):
    # Match Git's CRLF conversion classifier, including its treatment of bytes outside ASCII.
    carriage_returns = content.count(b"\r")
    if b"\0" in content or carriage_returns != content.count(b"\r\n"):
        return False
    nonprintable = len(content.translate(None, _GIT_PRINTABLE_AND_NEWLINES))
    printable = len(content) - carriage_returns - content.count(b"\n") - nonprintable
    return printable // 128 >= nonprintable - content.endswith(b"\x1a")


def _inspect_indexed_crlf(repo, env, sha, remaining, stopped):
    """Return whether the indexed blob is CRLF text, bytes read, and any coverage error."""
    size = int(source.git(repo, env, "cat-file", "-s", sha, stopped=stopped).strip())
    if size > MAX_SOURCE_BYTES:
        return False, 0, "Indexed source exceeds the 20 MB read limit for automatic line-ending normalization."
    if size > remaining:
        return False, 0, "Indexed source inspection exceeds the 100 MB limit for automatic line-ending normalization."
    content = source.git(repo, env, "cat-file", "blob", sha, stopped=stopped, raw_output=True)
    return b"\r\n" in content and _git_auto_text(content), size, None


def _local_tree(repo, env, root, git_dir, mode, stopped, options, rules, target):
    _index(repo, env, git_dir)
    staged = _entries(repo, env)
    excluded, unavailable = [], {}
    if mode == "staged":
        # Git omits intent-to-add entries when writing its original index flags to a tree.
        tree = source.git(repo, env, "write-tree", stopped=stopped).strip()
        return tree, excluded, unavailable, {}
    if not all(getattr(os, flag, 0) for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")) or not {
        os.open, os.stat, os.readlink
    }.issubset(os.supports_dir_fd) or os.stat not in os.supports_follow_symlinks:
        raise ValueError("Working-tree snapshots require platform support for safe no-follow file access. Use --staged.")
    paths = source.git(repo, env, "-c", "core.fsmonitor=false", "--work-tree=" + str(root),
                       "ls-files", "--cached", "--others", "--exclude-standard", "--full-name", "-z",
                       stopped=stopped, strict_utf8=True).split("\0")
    flags = source.git(repo, env, "-c", "core.fsmonitor=false", "ls-files", "-v", "-z", strict_utf8=True).split("\0")
    sparse_paths = {value[2:] for value in flags if value[:2].lower() == "s "}
    paths = sorted(set(filter(None, paths)))
    text_paths = None
    index_crlf, index_bytes = {}, 0
    entries, total = {}, 0
    for path in paths:
        source.repository_path(path)
        if not planning.allowed_path(path, options, rules):
            excluded.append({"path": path, "reason": "excluded"})
            if not planning.allowed_path(path, options, rules, supporting_evidence=True):
                # Reuse an existing excluded blob without reading or ingesting its contents.
                if path in staged:
                    try:
                        info = _worktree_stat(root, path, gitlink=staged[path][0] == "160000")
                    except _WorktreePathReplaced:
                        continue
                    if info is not None or path in sparse_paths:
                        entries[path] = staged[path]
                continue
        if stopped():
            raise source.ReviewStopped("The review was cancelled.")
        if path in staged and staged[path][0] == "160000":
            entries[path] = staged[path]
            continue
        try:
            value = _worktree_bytes(root, path)
        except _WorktreePathReplaced:
            continue
        except OversizedWorktreeSource as exc:
            # Keep unreadable files out of the pinned source tree and report target gaps to the planner.
            unavailable[path] = str(exc)
            continue
        if value is None:
            if path in sparse_paths:
                entries[path] = staged[path]
            continue
        file_mode, content = value
        # Cached stat data cannot prove the worktree file still matches the staged object.
        if path in staged and staged[path] == (file_mode, blob_sha(content)):
            entries[path] = staged[path]
            continue
        if file_mode in {"100644", "100755"} and b"\r\n" in content:
            if text_paths is None:
                text_paths = _text_paths(repo, env, paths, stopped)
            text = text_paths.get(path)
            if text is not None and (text != "auto" or _git_auto_text(content)):
                normalized = content.replace(b"\r\n", b"\n")
                if path in staged and staged[path] == (file_mode, blob_sha(normalized)):
                    entries[path] = staged[path]
                    continue
                preserve_crlf = False
                if text == "auto" and path in staged:
                    # Git auto conversion keeps CRLF when the indexed blob is already CRLF text.
                    indexed_sha = staged[path][1]
                    if indexed_sha not in index_crlf:
                        has_crlf, inspected, error = _inspect_indexed_crlf(
                            repo, env, indexed_sha, MAX_SNAPSHOT_BYTES - index_bytes, stopped)
                        index_crlf[indexed_sha] = (has_crlf, error)
                        index_bytes += inspected
                    preserve_crlf, error = index_crlf[indexed_sha]
                    if error:
                        unavailable[path] = error
                        continue
                if not preserve_crlf:
                    content = normalized
        total += len(content)
        if total > MAX_SNAPSHOT_BYTES:
            raise ValueError("Working-tree source exceeds the 100 MB snapshot admission limit.")
        sha = source.git(repo, env, "hash-object", "-w", "--stdin", input_bytes=content, stopped=stopped).strip()
        entries[path] = (file_mode, sha)
    unavailable_changes = {}
    if any(path in staged for path in unavailable):
        # Keep index metadata for omitted files without putting their staged blobs in the snapshot.
        unavailable_changes = {path: (old_path, status)
            for path, old_path, status in planning._changes(repo, env, target, None, stopped)
            if path in unavailable and status != "D"
            and (not status.startswith("R") or (old_path not in entries and old_path not in unavailable))}
    return _tree(repo, env, entries), excluded, unavailable, unavailable_changes


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
            tree, excluded, unavailable, unavailable_changes = _local_tree(
                repo, env, root, source.find_repository(root), mode, stopped, options, rules, target)
            pinned = _commit(repo, env, tree, target)
            if object_cache is not None:
                source._retain_objects(repo / "objects", source._object_cache(object_cache))
            yield Snapshot(repo, env, target, pinned, target, target, mode, excluded, unavailable=unavailable,
                           unavailable_changes=unavailable_changes)
