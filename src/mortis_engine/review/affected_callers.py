"""Bounded discovery of surviving references to removed or renamed Git paths."""

import ast
import posixpath
import re
import warnings
from collections import defaultdict
from pathlib import PurePosixPath

from . import source, source_lines
from .errors import ReviewStopped

POLICY = "affected-callers/v5"
ABSOLUTE_IMPORT_POLICY = "affected-callers/v4"
LEGACY_TEXT_POLICIES = (None, "affected-callers/v1", "affected-callers/v2")
LEGACY_POLICIES = (*LEGACY_TEXT_POLICIES, "affected-callers/v3", ABSOLUTE_IMPORT_POLICY)
MAX_TRIGGERS = 64
MAX_FILES = 512
MAX_INVENTORY_ENTRIES = 4096
MAX_OBJECT_READS = 512
MAX_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024
MAX_MATCHES = 256
MAX_SYMBOLS = 64
CONTEXT_LINES = 3

_TOKEN = re.compile(r"[\w$@./:\-]+")
_IDENTIFIER = re.compile(r"[\w$]+")
_QUOTED = re.compile(r"""(["'])([^\r\n]{1,2000}?)\1""")
_MODULE_CONTEXT = re.compile(r"\b(?:from|import|include|load|mod|require(?:_relative)?|source|use|using)\b")
_MODULE_BINDING = re.compile(r"^[ \t]*(?:(?:pub|export)[ \t]+)?(?:from|import|use|using)\b")
_FROM_IMPORT = re.compile(r"\bfrom[ \t]+([.\w/]+)[ \t]+import[ \t]+([\w$]+)")
_IMPORT_ALIAS = re.compile(r"\bas[ \t]+([\w$]+)")
_QUALIFIER = re.compile(r"([\w$]+)(?:\.|::)$")
_PYTHON_SUFFIXES = {".py", ".pyi"}
_PYTHON_IMPORT = re.compile(r"^[ \t]*(?:from[ \t]+([\w.]+)[ \t]+)?import[ \t]+([^#;]+)")
# shortcut: common declaration forms cover literal discovery. Add resolvers when measured misses justify them.
_DECLARATION = re.compile(
    r"(?m)^[ \t]*(?:(?:async|export|default|pub|public|private|protected|internal|static|final|abstract|"
    r"virtual|override|inline|extern)[ \t]+)*(?:class|const|def|enum|fn|func|function|interface|let|module|"
    r"namespace|object|record|struct|sub|trait|type|val|var)[ \t]+([^\W\d][\w$]*)"
)


def _module_paths(path):
    file_path = PurePosixPath(path)
    paths = {path, str(file_path.with_suffix(""))}
    if file_path.stem in {"__init__", "index", "mod"} and file_path.parent.parts:
        paths.add(str(file_path.parent))
    return paths


def _spellings(path):
    return {spelling for name in _module_paths(path)
            for spelling in (name, name.replace("/", "."), name.replace("/", "::"), PurePosixPath(name).name)}


def _python_modules(path, policy):
    file = PurePosixPath(path)
    if policy not in (POLICY, ABSOLUTE_IMPORT_POLICY) or file.suffix not in _PYTHON_SUFFIXES:
        return set()
    parts = file.with_suffix("").parts
    if file.stem == "__init__":
        parts = parts[:-1]
    # Tuple keys retain the repository path for relative imports, including the src/ prefix.
    modules = {parts} if policy == POLICY else set()
    if len(file.parts) >= 3 and parts[0] == "src" and all(part.isidentifier() for part in parts[1:]):
        modules.add(".".join(parts[1:]))
    return modules


def _python_imports(path, text, modules, stopped, *, relative):
    if not modules or "import" not in text:
        return {}
    caller = PurePosixPath(path)
    if caller.suffix not in _PYTHON_SUFFIXES:
        return {}
    # Callers pass only source blobs already admitted by MAX_FILE_BYTES and MAX_BYTES.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    git_lines = [(0, 0)]
    has_cr = "\r" in text
    if has_cr:
        start = 0
        for ending in re.finditer(r"\r\n|[\r\n]", text):
            index, offset = git_lines[-1]
            if ending[0].endswith("\n"):
                git_lines.append((index + 1, 0))
            else:
                git_lines.append((index, offset + len(text[start:ending.end()].encode())))
            start = ending.end()
    imports = defaultdict(list)

    def position(line, column=0):
        index, offset = git_lines[line - 1] if has_cr else (line - 1, 0)
        return index, offset + column

    def record(spelling, line, imported=(), qualifier=None, aliases=()):
        if spelling in modules:
            index, _ = position(line)
            alias_positions = tuple(position(name.lineno, name.col_offset) for name in aliases)
            imports[index].append((spelling, imported, qualifier, alias_positions))

    parent = caller.parent.parts
    for node in ast.walk(tree):
        if stopped():
            raise ReviewStopped("Caller discovery was cancelled.")
        if isinstance(node, ast.Import):
            for name in node.names:
                record(name.name, name.lineno, qualifier=name.asname or name.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.level and relative and node.level <= len(parent) + 1:
                package = parent[:len(parent) + 1 - node.level]
                module = package + tuple(node.module.split(".") if node.module else ())
            elif not node.level and node.module:
                module = node.module
            else:
                continue
            record(module, node.lineno,
                   tuple(name.asname or name.name for name in node.names if node.level or not name.asname),
                   aliases=(name for name in node.names if node.level and name.asname))
            for name in node.names:
                imported = (*module, name.name) if node.level else f"{module}.{name.name}"
                record(imported, name.lineno, qualifier=name.asname or name.name)
    return imports


def _relative_spellings(token, parent):
    if token.startswith(("./", "../")):
        # Imports generally start at the caller, while invocations may start at the repository root.
        candidates = [posixpath.join(parent, token)]
        if token.startswith("./"):
            candidates.append(token)
    elif token.startswith(".") and "/" not in token:
        name = token.lstrip(".")
        parents = [".."] * (len(token) - len(name) - 1)
        candidates = [posixpath.join(parent, *parents, name.replace(".", "/"))]
    else:
        return None
    normalized = dict.fromkeys(posixpath.normpath(candidate) for candidate in candidates)
    return [path for path in normalized if path not in {".", ".."} and not path.startswith(("../", "/"))]


def _references(line, path, labels, relative_paths, file_names, *, binding_policy=None, src_modules=None):
    parent = str(PurePosixPath(path).parent)
    sibling_bindings = binding_policy not in (None, "affected-callers/v1")
    in_module_context = bool(_MODULE_CONTEXT.search(line))
    if src_modules and PurePosixPath(path).suffix in _PYTHON_SUFFIXES and (match := _PYTHON_IMPORT.match(line)):
        module, names = match.groups()
        if module:
            yield from src_modules.get(module, ())
        for value in names.split(","):
            words = value.strip(" \t()\r\n").split()
            if words:
                name = f"{module}.{words[0]}" if module else words[0]
                yield from src_modules.get(name, ())
    tokens = [(match.group().rstrip(".:"), False) for match in _TOKEN.finditer(line)]
    tokens.extend((match[2], True) for match in _QUOTED.finditer(line))
    tokens.extend((f"{match[1]}{'' if match[1].endswith('.') else '.'}{match[2]}", False)
                  for match in _FROM_IMPORT.finditer(line))
    for token, quoted in tokens:
        if not token or len(token) > 2000:
            continue
        relative = _relative_spellings(token, parent)
        if relative is not None:
            for spelling in relative:
                yield from relative_paths.get(spelling, ())
            continue
        # Qualified paths can be followed by a symbol, as in `crate::old::function`.
        spellings = {token}
        if "::" in token and in_module_context:
            qualified = token.removeprefix("::")
            scope, _, tail = qualified.partition("::")
            if scope in {"crate", "self", "super"}:
                qualified = tail
            spellings.update((qualified, qualified.rpartition("::")[0]))
        for spelling in spellings:
            candidates = relative_paths.get(spelling) or labels.get(spelling, ())
            if not candidates and not sibling_bindings:
                continue
            local = labels.get(posixpath.join(parent, spelling), ()) if parent != "." else ()
            resolved_module = spelling in relative_paths or (sibling_bindings and bool(local))
            qualified = (any(separator in spelling for separator in ("/", ".", "::"))
                         or (in_module_context and resolved_module))
            if qualified or (quoted and spelling in file_names) or (in_module_context and binding_policy is None):
                yield from local or candidates


def _inventory(repo, env, sha, stopped, limits):
    output = source.git(repo, env, "ls-tree", "-r", "-z", "-l", sha, stopped=stopped, strict_utf8=True)
    entries = {}
    for index, row in enumerate(output.split("\0", MAX_INVENTORY_ENTRIES + 1)):
        if stopped():
            raise ReviewStopped("Caller discovery was cancelled.")
        if index == MAX_INVENTORY_ENTRIES and row:
            limits.add("inventory_entries")
            break
        match = re.fullmatch(r"100(?:644|755) blob ([0-9a-f]{40})\s+(\d+)\t([^\0]+)", row)
        if match:
            try:
                path = source.repository_path(match[3])
            except ValueError:
                limits.add("invalid_path")
                continue
            entries[path] = (match[1], int(match[2]))
    return entries


def _excerpt(path, sha, text):
    lines = source_lines.split(text)
    start = next((index for index, line in enumerate(lines) if 0 < len(line.encode()) <= 400 and line.strip()), None)
    if start is None:
        return None
    end = start + 1
    while end < min(len(lines), start + 3) and len("\n".join(lines[start:end + 1]).encode()) <= 400:
        end += 1
    return {"revision": sha, "path": path, "start_line": start + 1, "end_line": end,
            "text": "\n".join(lines[start:end])}


def discover(repo, env, plan, changes, triggers, allowed, stopped, *, policy=POLICY):
    """Return public scope and pinned source for the planner's focused units."""
    limits = set()
    scope = {"policy": policy, "triggers": [], "sites": [], "scanned_files": 0, "scanned_bytes": 0,
             "limits_reached": []}
    source_by_trigger, labels, replacements, symbols, matches = {}, defaultdict(set), defaultdict(set), defaultdict(set), {}
    old_paths, replacement_paths = defaultdict(set), defaultdict(set)
    old_modules, replacement_modules = defaultdict(set), defaultdict(set)
    file_names, replacement_names = set(), set()
    if len(triggers) > MAX_TRIGGERS:
        limits.add("trigger_count")
    triggers = triggers[:MAX_TRIGGERS]
    base = {}
    try:
        base = _inventory(repo, env, plan.merge_base, stopped, limits)
        head = _inventory(repo, env, plan.head_sha, stopped, limits)
    except ReviewStopped:
        raise
    except ValueError:
        scope["limits_reached"] = sorted(limits | {"source_inventory"})
        scope["triggers"] = [_trigger(path, new_path, base.get(path, (None,))[0], plan.merge_base)
                             for path, new_path in triggers]
        return scope, source_by_trigger, matches

    cached, object_bytes = {}, 0

    def read(entry):
        nonlocal object_bytes
        blob, size = entry
        if blob in cached:
            return cached[blob]
        if size > MAX_FILE_BYTES:
            limits.add("file_bytes")
            return None
        if object_bytes + size > MAX_BYTES:
            limits.add("object_bytes")
            return None
        if len(cached) == MAX_OBJECT_READS:
            limits.add("object_reads")
            return None
        object_bytes += size
        try:
            text = source.git(repo, env, "cat-file", "blob", blob, stopped=stopped, strict_utf8=True)
        except ReviewStopped:
            raise
        except ValueError:
            limits.add("source_unavailable")
            cached[blob] = None
            return None
        if len(text.encode()) != size or (policy in LEGACY_TEXT_POLICIES and "\ufffd" in text):
            limits.add("source_unavailable")
            text = None
        cached[blob] = None if text is None or "\0" in text else text
        return cached[blob]

    for path, new_path in triggers:
        if path not in base or (new_path and new_path not in head):
            if "inventory_entries" in limits:
                scope["triggers"].append(_trigger(path, new_path, base.get(path, (None,))[0], plan.merge_base))
            continue
        text = read(base[path])
        item = _trigger(path, new_path, base[path][0], plan.merge_base)
        scope["triggers"].append(item)
        evidence = _excerpt(path, plan.merge_base, text) if text else None
        source_by_trigger[path] = {**item, **({"evidence": evidence} if evidence else {})}
        old_spellings = _spellings(path)
        file_names.add(PurePosixPath(path).name)
        for label in old_spellings:
            labels[label].add(path)
        for spelling in _module_paths(path):
            old_paths[spelling].add(path)
        modules = _python_modules(path, policy)
        for module in modules:
            old_modules[module].add(path)
        if new_path:
            replacement_names.add(PurePosixPath(new_path).name)
            for label in _spellings(new_path) - old_spellings:
                replacements[label].add(path)
            for spelling in _module_paths(new_path) - _module_paths(path):
                replacement_paths[spelling].add(path)
            for module in _python_modules(new_path, policy) - modules:
                replacement_modules[module].add(path)
        if text:
            declarations = sorted(set(_DECLARATION.findall(text)))
            if len(declarations) > MAX_SYMBOLS:
                limits.add("symbol_count")
            for symbol in declarations[:MAX_SYMBOLS]:
                symbols[symbol].add(path)

    count = 0
    python_modules = old_modules.keys() | replacement_modules.keys()
    relative_imports = policy == POLICY
    for path in sorted(head):
        if not labels:
            break
        if not allowed(path):
            continue
        if scope["scanned_files"] == MAX_FILES:
            limits.add("scanned_files")
            break
        scope["scanned_files"] += 1
        if scope["scanned_bytes"] + head[path][1] > MAX_BYTES:
            limits.add("scanned_bytes")
            continue
        text = read(head[path])
        if not text:
            continue
        scope["scanned_bytes"] += head[path][1]
        lines = source_lines.split(text, keepends=True)
        imports = _python_imports(path, text, python_modules, stopped, relative=relative_imports)
        old_fallback = old_modules if imports is None else None
        replacement_fallback = replacement_modules if imports is None else None
        imports = imports or {}
        direct, rebound_symbols, rebound_modules = [], defaultdict(set), defaultdict(set)
        migrated_aliases = defaultdict(dict)
        for index, line in enumerate(lines):
            if stopped():
                raise ReviewStopped("Caller discovery was cancelled.")
            linked = set(_references(line, path, labels, old_paths, file_names, src_modules=old_fallback))
            explicit, migrated = set(), set()
            for spelling, names, qualifier, aliases in imports.get(index, ()):
                explicit.update(old_modules.get(spelling, ()))
                resolved = replacement_modules.get(spelling, ())
                if resolved:
                    migrated.update(resolved)
                    for name in names:
                        rebound_symbols[name].update(resolved)
                    if qualifier:
                        rebound_modules[qualifier].update(resolved)
                    for alias_line, column in aliases:
                        migrated_aliases[alias_line].setdefault(column, set()).update(resolved)
            replacement = set()
            if replacements and _MODULE_BINDING.match(line):
                replacement = set(_references(line, path, replacements, replacement_paths, replacement_names,
                                              binding_policy=policy, src_modules=replacement_fallback))
                if replacement:
                    for name in _IDENTIFIER.findall(line):
                        rebound_symbols[name].update(replacement.intersection(symbols.get(name, ())))
                    aliases = _IMPORT_ALIAS.findall(line)
                    for dependency in replacement:
                        module = PurePosixPath(source_by_trigger[dependency]["new_path"]).stem
                        for name in aliases or [module]:
                            rebound_modules[name].add(dependency)
            if replacement or migrated:
                linked -= (replacement | migrated) - set(_references(line, path, labels, old_paths, file_names,
                                                                      binding_policy=policy, src_modules=old_fallback))
            direct.append(linked | explicit)
        existing = set().union(*direct)
        original_refs = None
        sites = []
        for index, line in enumerate(lines):
            if stopped():
                raise ReviewStopped("Caller discovery was cancelled.")
            linked = direct[index].copy()
            used = set()
            alias_columns = migrated_aliases.get(index)
            offset = previous = 0
            for word in _IDENTIFIER.finditer(line):
                name = word.group()
                if name not in symbols:
                    continue
                qualifier = _QUALIFIER.search(line[max(0, word.start() - 128):word.start()])
                rebound = rebound_modules.get(qualifier[1], set()) if qualifier else rebound_symbols.get(name, set())
                references = symbols[name] - (rebound - existing)
                if alias_columns:
                    # AST columns count UTF-8 bytes. Skip the declaration, not later uses of its old name.
                    offset += len(line[previous:word.start()].encode())
                    previous = word.start()
                    references.difference_update(alias_columns.get(offset, ()))
                used.update(references)
            if used - linked:
                near = set().union(*direct[max(0, index - CONTEXT_LINES):index + CONTEXT_LINES + 1])
                if used - linked - near and original_refs is None:
                    old_path, status = changes.get(path, (None, ""))
                    original_path = old_path if status.startswith("R") else path
                    original = base.get(original_path) if allowed(original_path) else None
                    if original and original == head[path] and original_path == path:
                        original_refs = existing
                    else:
                        old_text = (read(original) if original else None) or ""
                        old_imports = _python_imports(original_path, old_text, old_modules, stopped,
                                                      relative=relative_imports)
                        original_refs = {reference for old_line in source_lines.split(old_text)
                                         for reference in _references(old_line, original_path, labels,
                                                                      old_paths, file_names,
                                                                      src_modules=old_modules if old_imports is None else None)}
                        original_refs.update(reference for row in (old_imports or {}).values()
                                             for spelling, _, _, _ in row for reference in old_modules.get(spelling, ()))
                linked.update(used & (near | (original_refs or set())))
            if not linked:
                continue
            if count == MAX_MATCHES:
                limits.add("match_count")
                break
            sites.append((index + 1, sorted(linked)))
            count += 1
        if sites:
            matches[path] = (lines, sites)
        if "match_count" in limits:
            break
    scope["limits_reached"] = sorted(limits)
    return scope, source_by_trigger, matches


def _trigger(path, new_path, blob_sha, merge_base):
    return {"path": path, "new_path": new_path, "blob_sha": blob_sha,
            "status": "renamed" if new_path else "deleted", "source_sha": merge_base}
