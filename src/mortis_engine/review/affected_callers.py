"""Bounded discovery of surviving references to removed or renamed Git paths."""

import ast
import builtins
import posixpath
import re
import shlex
import warnings
from bisect import bisect_right
from collections import defaultdict
from pathlib import PurePosixPath

from . import source, source_lines
from .errors import ReviewStopped

POLICY = "affected-callers/v7"
SCOPED_IMPORT_POLICY = "affected-callers/v6"
UNSCOPED_IMPORT_POLICY = "affected-callers/v5"
ABSOLUTE_IMPORT_POLICY = "affected-callers/v4"
LEGACY_TEXT_POLICIES = (None, "affected-callers/v1", "affected-callers/v2")
LEGACY_POLICIES = (*LEGACY_TEXT_POLICIES, "affected-callers/v3", ABSOLUTE_IMPORT_POLICY,
                   UNSCOPED_IMPORT_POLICY, SCOPED_IMPORT_POLICY)
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
_SHELL_SUFFIXES = {".sh", ".bash", ".zsh"}
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
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
    if (policy not in (POLICY, SCOPED_IMPORT_POLICY, UNSCOPED_IMPORT_POLICY, ABSOLUTE_IMPORT_POLICY)
            or file.suffix not in _PYTHON_SUFFIXES):
        return set()
    parts = file.with_suffix("").parts
    if file.stem == "__init__":
        parts = parts[:-1]
    # Tuple keys retain the repository path for relative imports, including the src/ prefix.
    modules = {parts} if policy != ABSOLUTE_IMPORT_POLICY else set()
    if len(file.parts) >= 3 and parts[0] == "src" and all(part.isidentifier() for part in parts[1:]):
        modules.add(".".join(parts[1:]))
    return modules


def _scope_lookup(events):
    # A function closes over enclosing functions and the module, skipping class namespaces.
    module = (None,)
    frames = [(module, module)]
    positions, scopes = [(-1, 0)], [module]
    for point, entering, is_class in sorted(events):
        if entering:
            enclosing = frames[-1][1]
            local = (point, *enclosing)
            frames.append((local, enclosing if is_class else local))
        else:
            frames.pop()
        positions.append(point)
        scopes.append(frames[-1][0])
    return lambda line, column: scopes[bisect_right(positions, (line, column)) - 1]


def _scope_at(lookup, point):
    return lookup(*point) if lookup else (None,)


def _bare_module_paths(parent, module):
    roots = {module, f"src/{module}", posixpath.join(parent, module)}
    return {root + ending for root in roots for ending in (".py", ".pyi", "/__init__.py", "/__init__.pyi")}


class _PythonNames:
    def __init__(self):
        self.bare_imports = defaultdict(set)
        self.imported_aliases = []
        self.loads = []
        self.bindings = []
        self.first_iterables = {}

    def collect(self, node, position):
        if isinstance(node, ast.Name):
            entries = self.loads if isinstance(node.ctx, ast.Load) else self.bindings
            entries.append((node.id, position(node.lineno, node.col_offset)))
        elif isinstance(node, ast.alias):
            self.bindings.append((node.asname or node.name.split(".")[0], position(node.lineno, node.col_offset)))
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            self.bindings.extend((name, position(node.lineno, node.col_offset)) for name in node.names)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
            if name := node.rest if isinstance(node, ast.MatchMapping) else node.name:
                self.bindings.append((name, position(node.lineno, node.col_offset)))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            if not isinstance(node, ast.Lambda):
                self.bindings.append((node.name, position(node.lineno, node.col_offset)))
            if isinstance(node, ast.ClassDef):
                return
            body = node.body if isinstance(node, ast.Lambda) else _first_statement(node)
            point = position(body.lineno, body.col_offset)
            arguments = node.args
            self.bindings.extend((arg.arg, point) for arg in
                                 (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
                                  arguments.vararg, arguments.kwarg) if arg is not None)

    def bound_aliases(self, scopes_at, modules, symbols, parent, inventory):
        bindings = defaultdict(set)
        for local, point in self.bindings:
            bindings[_scope_at(scopes_at, point)[0], local].add(point)
        aliases = {}
        for module, imported, local, point in self.imported_aliases:
            if not (declarations := symbols.get(imported)):
                continue
            dependencies = modules.get(module, set()) & declarations
            if isinstance(module, str) and module.isidentifier():
                present = {path for path in _bare_module_paths(parent, module) if path in inventory}
                if len(present) == 1:
                    dependencies.update(present & declarations)
            if dependencies:
                aliases[_scope_at(scopes_at, point)[0], local, point] = dependencies
        return {name: sorted(points) for name, points in bindings.items()}, aliases

    def load_scopes(self, scopes_at, point):
        scopes = _scope_at(scopes_at, point)
        if not self.first_iterables:
            return scopes

        def in_first_iterable(scope):
            span = self.first_iterables.get(scope)
            return span is not None and span[0] <= point < span[1]

        # The first iterable runs at the opening delimiter, even in a class body.
        while in_first_iterable(scopes[0]):
            line, column = scopes[0]
            scopes = _scope_at(scopes_at, (line, column - 1))
        return tuple(scope for scope in scopes if not in_first_iterable(scope))

    def alias_uses(self, scopes_at, bindings, aliases, *, track_unbound):
        uses = defaultdict(set)
        unbound = defaultdict(set)
        alias_names = {name for _, name, _ in aliases}
        for local, point in self.loads:
            worth_checking_prior = track_unbound and not hasattr(builtins, local)
            if local not in alias_names and not worth_checking_prior:
                continue
            scopes = self.load_scopes(scopes_at, point)
            binding = next(((scope, local) for scope in scopes if (scope, local) in bindings), None)
            if binding is None:
                if worth_checking_prior:
                    unbound[local].add(point[0])
                continue
            scope, _ = binding
            definitions = bindings[binding]
            if scope == scopes[0]:
                preceding = bisect_right(definitions, point)
                if not preceding:
                    if worth_checking_prior and scope is None:
                        unbound[local].add(point[0])
                    continue
                definition = definitions[preceding - 1]
            elif len(definitions) == 1:
                definition = definitions[0]
            else:
                # A closure can run before or after an outer binding is reassigned.
                continue
            dependencies = aliases.get((scope, local, definition))
            if dependencies:
                uses[point[0]].update(dependencies)
        return uses, unbound


def _first_statement(node):
    first = node.body[0]
    decorators = getattr(first, "decorator_list", ())
    return decorators[0] if decorators else first


def _python_imports(path, text, modules, stopped, *, relative, scoped=False, names=None):
    if not modules or (names is None and "import" not in text):
        return {}, None
    caller = PurePosixPath(path)
    if caller.suffix not in _PYTHON_SUFFIXES:
        return {}, None
    # Callers pass only source blobs already admitted by MAX_FILE_BYTES and MAX_BYTES.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None, None
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

    def record(spelling, node, imported=(), qualifier=None, aliases=()):
        if spelling in modules:
            index, column = position(node.lineno, node.col_offset)
            alias_positions = tuple(position(name.lineno, name.col_offset) for name in aliases)
            imports[index].append((spelling, imported, qualifier, alias_positions, column))

    parent = caller.parent.parts
    events = []
    for node in ast.walk(tree):
        if stopped():
            raise ReviewStopped("Caller discovery was cancelled.")
        if names is not None:
            names.collect(node, position)
        if scoped and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = _first_statement(node)
            events.append((position(first.lineno, first.col_offset), True, isinstance(node, ast.ClassDef)))
            events.append((position(node.end_lineno, node.end_col_offset), False, False))
        elif scoped and names is not None and isinstance(node, (ast.Lambda, *_COMPREHENSIONS)):
            # A comprehension's own bindings begin after its opening delimiter.
            first = node.body if isinstance(node, ast.Lambda) else node
            column = first.col_offset + int(isinstance(node, _COMPREHENSIONS))
            entry = position(first.lineno, column)
            if isinstance(node, _COMPREHENSIONS):
                iterable = node.generators[0].iter
                names.first_iterables[entry] = (position(iterable.lineno, iterable.col_offset),
                                                position(iterable.end_lineno, iterable.end_col_offset))
            events.append((entry, True, False))
            events.append((position(node.end_lineno, node.end_col_offset), False, False))
        elif isinstance(node, ast.Import):
            for name in node.names:
                record(name.name, name, qualifier=name.asname or name.name.rsplit(".", 1)[-1])
                if names is not None and name.name.isidentifier():
                    names.bare_imports[position(name.lineno)[0]].add(name.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and relative and node.level <= len(parent) + 1:
                package = parent[:len(parent) + 1 - node.level]
                module = package + tuple(node.module.split(".") if node.module else ())
            elif not node.level and node.module:
                module = node.module
                if names is not None and module.isidentifier():
                    names.bare_imports[position(node.lineno)[0]].add(module)
            else:
                continue
            record(module, node,
                   tuple(name.asname or name.name for name in node.names if node.level or not name.asname),
                   aliases=(name for name in node.names if node.level and name.asname))
            for name in node.names:
                imported = (*module, name.name) if node.level else f"{module}.{name.name}"
                record(imported, name, qualifier=name.asname or name.name)
                if (names is not None and name.asname
                        and (module in modules or (isinstance(module, str) and module.isidentifier()))):
                    names.imported_aliases.append((module, name.name, name.asname, position(name.lineno, name.col_offset)))
    return imports, _scope_lookup(events) if events else None


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


def _escaped_shell_references(line, parent, relative_paths):
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return
    for token in tokens:
        if len(token) > 2000 or (" " not in token and "\t" not in token):
            continue
        for path in _relative_spellings(token, parent) or (token,):
            yield from relative_paths.get(path, ())


def _references(line, path, labels, relative_paths, file_names, *, binding_policy=None, src_modules=None,
                bare_imports=(), inventory=(), escaped_shell=False):
    parent = str(PurePosixPath(path).parent)
    sibling_bindings = binding_policy not in (None, "affected-callers/v1")
    in_module_context = bool(_MODULE_CONTEXT.search(line))
    if escaped_shell and ("\\ " in line or "\\\t" in line):
        yield from _escaped_shell_references(line, parent, relative_paths)
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
            if candidates and not quoted and spelling in bare_imports:
                locations = _bare_module_paths(parent, spelling)
                # A pinned, exact module path wins over a basename from an unrelated package.
                if any(location in inventory for location in locations):
                    candidates = locations.intersection(candidates)
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
    scoped_imports = policy in (POLICY, SCOPED_IMPORT_POLICY)
    relative_imports = scoped_imports or policy == UNSCOPED_IMPORT_POLICY

    def previous_entry(path):
        old_path, status = changes.get(path, (None, ""))
        original = old_path if status.startswith("R") else path
        return original, base.get(original) if allowed(original) else None

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
        suffix = PurePosixPath(path).suffix
        track_previous_aliases = path in changes and bool(old_modules and symbols)
        python_names = (_PythonNames() if policy == POLICY and python_modules and suffix in _PYTHON_SUFFIXES
                        and ("import" in text or track_previous_aliases) else None)
        escaped_shell = policy == POLICY and suffix in _SHELL_SUFFIXES
        imports, scopes_at = _python_imports(path, text, python_modules, stopped, relative=relative_imports,
                                             scoped=bool(python_names or (scoped_imports and replacement_modules)),
                                             names=python_names)
        old_fallback = old_modules if imports is None else None
        replacement_fallback = replacement_modules if imports is None else None
        imports = imports or {}
        direct, rebound_symbols, rebound_modules = [], defaultdict(set), defaultdict(set)
        migrated_aliases = defaultdict(dict)
        for index, line in enumerate(lines):
            if stopped():
                raise ReviewStopped("Caller discovery was cancelled.")
            bare_imports = python_names.bare_imports.get(index, ()) if python_names else ()
            linked = set(_references(line, path, labels, old_paths, file_names, src_modules=old_fallback,
                                     bare_imports=bare_imports, inventory=head, escaped_shell=escaped_shell))
            explicit, migrated, relative_bindings = set(), set(), set()
            for spelling, names, qualifier, aliases, column in imports.get(index, ()):
                explicit.update(old_modules.get(spelling, ()))
                resolved = replacement_modules.get(spelling, ())
                if resolved:
                    migrated.update(resolved)
                    if isinstance(spelling, tuple):
                        relative_bindings.update(resolved)
                    binding_scope = scopes_at(index, column)[0] if scopes_at else None
                    for name in names:
                        rebound_symbols[binding_scope, name].update(resolved)
                    if qualifier:
                        rebound_modules[binding_scope, qualifier].update(resolved)
                    for alias_line, column in aliases:
                        migrated_aliases[alias_line].setdefault(column, set()).update(resolved)
            replacement = set()
            if replacements and (binding := _MODULE_BINDING.match(line)):
                replacement = set(_references(line, path, replacements, replacement_paths, replacement_names,
                                              binding_policy=policy, src_modules=replacement_fallback))
                # Prefer exact AST bindings to words in a recognized Python import.
                text_bindings = replacement - relative_bindings if scoped_imports else replacement
                if text_bindings:
                    binding_scope = scopes_at(index, binding.end())[0] if scopes_at else None
                    for name in _IDENTIFIER.findall(line):
                        rebound_symbols[binding_scope, name].update(text_bindings.intersection(symbols.get(name, ())))
                    aliases = _IMPORT_ALIAS.findall(line)
                    for dependency in text_bindings:
                        module = PurePosixPath(source_by_trigger[dependency]["new_path"]).stem
                        for name in aliases or [module]:
                            rebound_modules[binding_scope, name].add(dependency)
            if replacement or migrated:
                linked -= (replacement | migrated) - set(_references(line, path, labels, old_paths, file_names,
                                                                      binding_policy=policy, src_modules=old_fallback,
                                                                      bare_imports=bare_imports, inventory=head,
                                                                      escaped_shell=escaped_shell))
            direct.append(linked | explicit)
        existing = set().union(*direct)
        original_context = None
        alias_uses = {}
        if python_names and python_names.loads and (python_names.imported_aliases or track_previous_aliases):
            bindings, aliases = python_names.bound_aliases(scopes_at, old_modules, symbols,
                                                           str(PurePosixPath(path).parent), base)
            alias_uses, unbound = python_names.alias_uses(scopes_at, bindings, aliases, track_unbound=track_previous_aliases)
            if unbound:
                original_path, original = previous_entry(path)
                if original and (original != head[path] or original_path != path):
                    old_text = read(original) or ""
                    original_names = _PythonNames() if "import" in old_text else None
                    old_imports, original_scopes = _python_imports(original_path, old_text, old_modules, stopped,
                                                                  relative=True, scoped=True, names=original_names)
                    original_context = old_text, old_imports, original_names
                    if original_names and original_names.imported_aliases:
                        definitions, aliases = original_names.bound_aliases(original_scopes, old_modules, symbols,
                                                                            str(PurePosixPath(original_path).parent), base)
                        # Only module bindings can be carried across revisions without matching lexical scopes.
                        for (binding_scope, name, point), dependencies in aliases.items():
                            if binding_scope is None and definitions[binding_scope, name] == [point]:
                                for index in unbound.get(name, ()):
                                    alias_uses[index].update(dependencies)
        original_refs = None
        sites = []
        for index, line in enumerate(lines):
            if stopped():
                raise ReviewStopped("Caller discovery was cancelled.")
            linked = direct[index].copy()
            linked.update(alias_uses.get(index, ()))
            used = set()
            alias_columns = migrated_aliases.get(index)
            offset = previous = 0
            for word in _IDENTIFIER.finditer(line):
                name = word.group()
                if name not in symbols:
                    continue
                qualifier = _QUALIFIER.search(line[max(0, word.start() - 128):word.start()])
                if scopes_at or alias_columns:
                    # AST columns count UTF-8 bytes.
                    offset += len(line[previous:word.start()].encode())
                    previous = word.start()
                bindings, bound_name = (rebound_modules, qualifier[1]) if qualifier else (rebound_symbols, name)
                visible = scopes_at(index, offset) if scopes_at else (None,)
                rebound = set().union(*(bindings.get((scope, bound_name), ()) for scope in visible))
                references = symbols[name] - (rebound - existing)
                if alias_columns:
                    references.difference_update(alias_columns.get(offset, ()))
                used.update(references)
            if used - linked:
                near = set().union(*direct[max(0, index - CONTEXT_LINES):index + CONTEXT_LINES + 1])
                if used - linked - near and original_refs is None:
                    original_path, original = previous_entry(path)
                    if original and original == head[path] and original_path == path:
                        original_refs = existing
                    else:
                        if original_context is None:
                            old_text = (read(original) if original else None) or ""
                            original_names = _PythonNames() if python_names and "import" in old_text else None
                            old_imports, _ = _python_imports(original_path, old_text, old_modules, stopped,
                                                             relative=relative_imports, names=original_names)
                        else:
                            old_text, old_imports, original_names = original_context
                        original_refs = {reference for old_index, old_line in enumerate(source_lines.split(old_text))
                                         for reference in _references(old_line, original_path, labels,
                                                                      old_paths, file_names,
                                                                      src_modules=old_modules if old_imports is None else None,
                                                                      bare_imports=original_names.bare_imports.get(old_index, ())
                                                                      if original_names else (), inventory=base,
                                                                      escaped_shell=escaped_shell)}
                        original_refs.update(reference for row in (old_imports or {}).values()
                                             for spelling, *_ in row for reference in old_modules.get(spelling, ()))
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
