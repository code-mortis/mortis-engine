"""Bounded import checks for incremental Python review coverage."""

import ast
import warnings
from collections import defaultdict
from pathlib import PurePosixPath

from . import affected_callers, planning, source
from .errors import ReviewStopped


def is_python(path):
    return PurePosixPath(path).suffix in {".py", ".pyi"}


def _required_signatures(tree):
    signatures = defaultdict(list)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            required = len(args.posonlyargs) + len(args.args) - len(args.defaults)
            positional = min(len(args.posonlyargs), required)
            keywords = tuple(sorted(arg.arg for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
                                    if default is None))
            names = tuple(arg.arg for arg in args.args[:required - positional])
            signatures[node.name].append((positional, names, keywords))
    return signatures


def _imports_changed_function(tree, path, changed, stopped):
    package = PurePosixPath(path).parent.parts
    for node in ast.walk(tree):
        if stopped():
            raise ReviewStopped("Python caller inspection was cancelled.")
        if isinstance(node, ast.Import):
            if any(tuple(name.name.split(".")) in changed for name in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.level <= len(package):
            parent = package[:len(package) + 1 - node.level] if node.level else ()
            module = (*parent, *(node.module.split(".") if node.module else ()))
            functions = changed.get(module, ())
            for name in node.names:
                if (*module, name.name) in changed or name.name in functions or (name.name == "*" and functions):
                    return True
    return False


def affected(repo, env, previous_head, head, modified, candidates, stopped):
    """Identify known importers of edited module-level required-argument signatures.

    If bounded inspection fails, the caller falls back to a full review.
    """
    reads, size = 0, 0

    def parse(revision, path):
        nonlocal reads, size
        if reads == affected_callers.MAX_OBJECT_READS:
            raise ValueError("Python caller inspection exceeds the object read limit.")
        entry = planning._entry(repo, env, revision, path, stopped)
        if entry is None:
            raise ValueError("Python caller inspection requires pinned source.")
        if entry["size"] > affected_callers.MAX_FILE_BYTES or size + entry["size"] > affected_callers.MAX_BYTES:
            raise ValueError("Python caller inspection exceeds the source size limit.")
        reads += 1
        size += entry["size"]
        text = source.git(repo, env, "cat-file", "blob", entry["blob_sha"], stopped=stopped, strict_utf8=True)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                return ast.parse(text)
        except (SyntaxError, ValueError, RecursionError) as exc:
            raise ValueError("Python caller inspection requires parseable source.") from exc

    changed = defaultdict(set)
    for path in modified:
        file = PurePosixPath(path)
        module = file.parent.parts if file.stem == "__init__" else file.with_suffix("").parts
        before = _required_signatures(parse(previous_head, path))
        after = _required_signatures(parse(head, path))
        functions = {name for name in before.keys() & after.keys() if before[name] != after[name]}
        if not functions:
            continue
        if not module or not module[-1].isidentifier():
            raise ValueError("Python caller inspection cannot resolve a changed module.")
        changed[module].update(functions)
        # The repository may place any ancestor directory on the import path.
        for root in range(len(module) - 1, -1, -1):
            if not module[root].isidentifier():
                break
            changed[module[root:]].update(functions)
    if not changed:
        return set()
    return {path for path in candidates if _imports_changed_function(parse(head, path), path, changed, stopped)}
