"""Bounded, inert Python name navigation within one prepared security snapshot."""

import ast
import json
from pathlib import PurePosixPath
from threading import Lock
from types import SimpleNamespace

from mortis_engine.review.errors import ReviewStopped
from mortis_engine.review.source import repository_path

VERSION = "security-source-navigation/v1"
MAX_FILES = 128
MAX_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024
MAX_REFERENCES = 16384
MAX_SYMBOLS = 16
MAX_NAME_BYTES = 128
MAX_HINTS = 16
MAX_HINT_PATHS = 8
MAX_HINT_BYTES = 4096
_creation_lock = Lock()


def _check(stopped):
    if stopped():
        raise ReviewStopped("Security source navigation cancelled.")


def _tree(source):
    if len(source.encode()) > MAX_FILE_BYTES:
        return None
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _index(item, paths, stopped):
    references, count, size, scanned = {}, 0, 0, 0
    omissions = {"unreadable": 0, "syntax_or_file_limit": 0, "scan_limit": 0}
    for offset, path in enumerate(paths):
        _check(stopped)
        if scanned >= MAX_FILES or size >= MAX_BYTES or count >= MAX_REFERENCES:
            omissions["scan_limit"] += len(paths) - offset
            break
        scanned += 1
        try:
            # Prepared regular files belong to item.sha; stat avoids loading oversize unrelated source.
            length = (item.files / path).stat().st_size
            if length > MAX_FILE_BYTES:
                omissions["syntax_or_file_limit"] += 1
                continue
            if size + length > MAX_BYTES:
                omissions["scan_limit"] += len(paths) - offset
                break
            source = item.source(path)
        except (ValueError, OSError, UnicodeError):
            omissions["unreadable"] += 1
            continue
        length = len(source.encode())
        if size + length > MAX_BYTES:
            omissions["scan_limit"] += len(paths) - offset
            break
        size += length
        tree = _tree(source)
        if tree is None:
            omissions["syntax_or_file_limit"] += 1
            continue
        seen = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                name = node.id
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                name = node.attr
            elif isinstance(node, ast.alias):
                name = node.name.rsplit(".", 1)[-1]
            else:
                continue
            if name in seen or len(name.encode()) > MAX_NAME_BYTES:
                continue
            if count >= MAX_REFERENCES:
                omissions["scan_limit"] += 1
                break
            seen.add(name)
            references.setdefault(name, []).append((path, node.lineno))
            count += 1
    _check(stopped)
    return references, scanned, omissions


def source_navigation(item, sources, available_paths, stopped):
    """Suggest unresolved references; the existing required_paths loop supplies actual evidence.

    The caller must pass its sanitized current-source map and permitted paths. The
    per-snapshot cache stores only names and locations, never source or AST objects.
    """
    _check(stopped)
    paths = []
    unsupported = 0
    for path in sorted(set(available_paths)):
        repository_path(path)
        if not item.inventory.get(path, {}).get("code"):
            continue
        if PurePosixPath(path).suffix not in {".py", ".pyi"}:
            unsupported += 1
            continue
        paths.append(path)
    allowed = set(paths)
    result = {"version": VERSION, "revision": item.sha, "hints": [], "searched_files": 0,
              "omissions": {"unreadable": 0, "syntax_or_file_limit": 0, "scan_limit": 0,
                            "unsupported_language": unsupported}, "truncated": False,
              "notice": "Python-only name/import/attribute references and conservative relative-file imports, "
                        "not resolved callers or proof of reachability. "
                        "Names can refer to unrelated objects; aliases, dynamic access and omitted files may hide references. "
                        "An empty hint list does not establish that a function has no callers. "
                        "Request relevant paths through required_paths; only supplied source can be cited."}
    if allowed.issubset(sources):
        return result
    key = (item.sha, tuple(paths))
    with _creation_lock:
        cache = getattr(item, "_security_source_navigation", None)
        if cache is None:
            cache = SimpleNamespace(lock=Lock(), key=None, value=None)
            item._security_source_navigation = cache
    while not cache.lock.acquire(timeout=0.05):
        _check(stopped)
    try:
        _check(stopped)
        if cache.key != key:
            value = _index(item, paths, stopped)
            cache.key, cache.value = key, value
        references, scanned, omissions = cache.value
    finally:
        cache.lock.release()
    symbols, dependencies = set(), []
    for path, source in sorted(sources.items()):
        _check(stopped)
        if path not in allowed:
            continue
        tree = _tree(source)
        if tree is not None:
            symbols.update(node.name for node in ast.walk(tree)
                           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level:
                    target = import_target(path, "." * node.level + (node.module or ""), "python", allowed)
                    if target and target not in sources:
                        dependencies.append({"path": target, "from_path": path, "line": node.lineno,
                                             "relation": "imports_file"})
    selected_symbols = sorted(name for name in symbols if len(name.encode()) <= MAX_NAME_BYTES)[:MAX_SYMBOLS]
    candidates = dependencies
    for name in selected_symbols:
        for path, line in references.get(name, []):
            if path not in sources:
                candidates.append({"path": path, "symbol": name, "line": line,
                                   "relation": "unresolved_name_reference"})
    hints, selected_paths, hint_bytes = [], set(), 0
    truncated = len(symbols) > len(selected_symbols) or any(omissions.values())
    for hint in candidates:
        length = len(json.dumps(hint).encode()) + 2
        if (len(hints) >= MAX_HINTS or hint_bytes + length > MAX_HINT_BYTES
                or hint["path"] not in selected_paths and len(selected_paths) >= MAX_HINT_PATHS):
            truncated = True
            continue
        hints.append(hint)
        selected_paths.add(hint["path"])
        hint_bytes += length
    _check(stopped)
    return {**result, "hints": hints, "searched_files": scanned,
            "omissions": {**omissions, "unsupported_language": unsupported}, "truncated": truncated}


def relative_target(path, specifier, available, python=False, level=0):
    """Resolve only unambiguous, in-corpus relative file dependencies."""
    if python:
        if not level:
            return None
        base = path.parent
        for _ in range(level - 1):
            if base == PurePosixPath("."):
                return None
            base = base.parent
        base = base.joinpath(*specifier.split(".")) if specifier else base
        candidates = [str(base) + ".py", str(base / "__init__.py")]
    else:
        if not specifier.startswith("."):
            return None
        parts = list(path.parent.parts)
        for part in specifier.split("/"):
            if part == "..":
                if not parts:
                    return None
                parts.pop()
            elif part not in ("", "."):
                parts.append(part)
        base = PurePosixPath(*parts)
        candidates = [str(base)] if base.suffix else [
            str(base) + ext for ext in (".ts", ".tsx", ".js", ".jsx")
        ] + [str(base / ("index" + ext)) for ext in (".ts", ".tsx", ".js", ".jsx")]
    matches = [name for name in candidates if name in available]
    return matches[0] if len(matches) == 1 else None


def import_target(path, specifier, language, available):
    if language == "python":
        level = len(specifier) - len(specifier.lstrip("."))
        return relative_target(PurePosixPath(path), specifier[level:], available, python=True, level=level)
    if language in {"javascript", "typescript", "tsx"}:
        # Escaped literals and aliases need a language resolver, not filename guessing.
        if "\\" in specifier or not specifier.startswith(("./", "../")):
            return None
        return relative_target(PurePosixPath(path), specifier, available)
    return None
