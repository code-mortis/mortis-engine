"""Bounded import checks for incremental Python review coverage."""

import ast
import warnings
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import affected_callers, planning, source
from .errors import ReviewStopped


def is_python(path):
    return PurePosixPath(path).suffix in {".py", ".pyi"}


@dataclass
class _Signature:
    required_shape: tuple
    varargs: bool
    varkw: bool
    optional_keywords: set[str]
    optional_positional: tuple[str | None, ...]

    def requires_recheck(self, new):
        # Preserve the slots an existing caller could fill positionally.
        return (self.required_shape != new.required_shape or self.varargs != new.varargs or self.varkw != new.varkw
                or not self.optional_keywords <= new.optional_keywords
                or len(self.optional_positional) > len(new.optional_positional)
                or (self.varargs and len(self.optional_positional) != len(new.optional_positional))
                # A keyword accepted separately before can now collide with the same positional argument.
                or any(old != current and (old is not None or self.varkw or current in self.optional_keywords)
                       for old, current in zip(self.optional_positional, new.optional_positional, strict=False)))


def _signatures(tree):
    signatures = defaultdict(list)
    pending = [tree]
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            required = len(args.posonlyargs) + len(args.args) - len(args.defaults)
            positional = min(len(args.posonlyargs), required)
            keywords = tuple(sorted(arg.arg for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
                                    if default is None))
            names = tuple(arg.arg for arg in args.args[:required - positional])
            required_shape = (positional, names, keywords)
            optional_pos_or_kw = tuple(arg.arg for arg in args.args[required - positional:])
            optional_slots = (None,) * (len(args.posonlyargs) - positional) + optional_pos_or_kw
            optional_names = set(optional_pos_or_kw)
            optional_names.update(arg.arg for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
                                  if default is not None)
            signatures[node.name].append(_Signature(required_shape, args.vararg is not None, args.kwarg is not None,
                                                    optional_names, optional_slots))
        elif not isinstance(node, (ast.ClassDef, ast.expr)):
            pending.extend(reversed(list(ast.iter_child_nodes(node))))
    return signatures


def _needs_recheck(before, after):
    # A function may have several declarations, for example in conditional branches.
    if len(before) != len(after):
        return True
    return any(old.requires_recheck(new) for old, new in zip(before, after, strict=True))


def _imports_changed_function(tree, path, changed, package_prefixes, stopped):
    package = PurePosixPath(path).parent.parts
    for node in ast.walk(tree):
        if stopped():
            raise ReviewStopped("Python caller inspection was cancelled.")
        if isinstance(node, ast.Import):
            if any(tuple(name.name.split(".")) in changed or name.name.startswith(package_prefixes)
                   for name in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.level <= len(package) + 1:
            # One leading dot stays in the current directory, which can be the repository root.
            parent = package[:len(package) + 1 - node.level] if node.level else ()
            module = (*parent, *(node.module.split(".") if node.module else ()))
            functions = changed.get(module, ())
            for name in node.names:
                if (*module, name.name) in changed or name.name in functions or (name.name == "*" and functions):
                    return True
    return False


def affected(repo, env, previous_head, head, modified, candidates, stopped):
    """Identify importers affected by a removed function or changed calling convention.

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
    packages = set()
    for path in modified:
        file = PurePosixPath(path)
        is_package = file.stem == "__init__"
        module = file.parent.parts if is_package else file.with_suffix("").parts
        before = _signatures(parse(previous_head, path))
        after = _signatures(parse(head, path))
        functions = {name for name, prior in before.items() if _needs_recheck(prior, after.get(name, ()))}
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
            if is_package:
                packages.add(".".join(module[root:]) + ".")
    if not changed:
        return set()
    package_prefixes = tuple(packages)
    return {path for path in candidates
            if _imports_changed_function(parse(head, path), path, changed, package_prefixes, stopped)}
