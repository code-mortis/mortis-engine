"""Deterministic PR assignments from pinned Git objects; repository code is never executed."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from mortis_engine.review import file_policy, source
from mortis_engine.review.errors import ReviewStopped

VERSION = "mortis-pr-plan/v1"
RULES_VERSION = "mortis-rules/v1"
# Language admission is independent of the optional structural graph's parsers.
CODE_SUFFIXES = set((
    "java kt kts scala groovy py pyi js jsx ts tsx mjs cjs c h cpp cc cxx hpp hxx cs vb fs fsi fsx "
    "go rs rb rake gemspec php phtml swift m mm sh bash zsh fish ps1 sql css scss sass less html htm "
    "ftl ftlh ftlx hbs mustache jinja2 j2 pug astro vue ipynb svelte xml yaml yml json toml ini env "
    "gradle cmake r lua pl pm ex exs erl hrl ets json5 dart tf graphql gql prisma jl hcl tfvars bicep "
    "proto nix hs lhs nim nims nimble elm properties po pot jsonnet libsonnet zig thrift capnp ml mli "
    "re rei v sv vh vhd vhdl sol vy rego"
).split())
DEFAULT_DIRS = set((
    "__tests__ __snapshots__ testdata fixtures .ipynb_checkpoints oh_modules bower_components "
    ".pnpm-store .svelte-kit .astro .angular .parcel-cache .docusaurus .bundle .gradle "
    "site-packages .tox Pods Carthage .build obj .dart_tool .terraform .stack-work kitex_gen"
).lower().split())
DEFAULT_TEST_PATTERNS = (
    "**/*_test.{go,py,rs,zig}", "**/test_*.py", "**/*.test.{js,jsx,ts,tsx,ets}",
    "**/*.spec.{js,jsx,ts,tsx}", "**/*_spec.rb", "**/*Test.java", "**/*Tests.java", "**/*Test.fs",
    "**/*Test.swift", "**/*Tests.swift", "**/*Spec.hs", "**/*Spec.lhs",
    "**/src/test/java/**", "**/src/test/**/*.{kt,kts}", "**/Tests/**/*.swift", "**/test/**/*.{jl,hs,lhs}",
    "**/tests/**/*.nim", "**/tests/**/*.R", "**/tests/**/*.elm", "**/test/**/*.zig", "**/test/**/*.ml",
    "**/tb_*.{v,sv,vhd,vhdl}", "**/*_tb.{v,sv,vhd,vhdl}", "**/*.t.sol",
    "**/test/**/*.sol", "**/tests/**/*.sol", "**/test/**/*.vy", "**/tests/**/*.vy",
)
DEFAULT_PATTERNS = (
    *DEFAULT_TEST_PATTERNS, "**/*.snap", "**/*.generated.*", "**/*.gen.go", "**/*.pb.{go,cc,h}",
    "**/*.capnp.{h,go,ts}", "**/*_capnp.{rs,py}", "lib/**/*.sol", "**/.pnp.cjs",
    "**/.pnp.loader.mjs", "**/.terraform.lock.hcl", "**/.yarn/{cache,unplugged,releases,sdks,patches}/**",
)
BUILTIN_RULE = (
    "Check changed behavior against its callers, validation and failure paths. Report only concrete "
    "introduced defects with a reachable trigger and observable consequence. Verify authorization, "
    "data integrity, resource bounds and recovery where relevant; support conclusions with source."
)

BUILTIN_CHECKS = (
    ("py pyi", "Trace exceptions, mutable defaults, async boundaries and cleanup through actual Python callers."),
    ("js jsx ts tsx mjs cjs ets", "Check runtime values, promise handling, event lifetimes and state updates; types alone do not validate input."),
    ("go", "Check returned errors, cancellation, deferred cleanup, shared state and channel lifetimes."),
    ("c h cpp cc cxx hpp hxx rs zig", "Check bounds, ownership, object lifetimes, integer conversions and concurrent access."),
    ("java kt kts scala groovy cs vb fs fsi fsx swift m mm dart", "Check nullability, resource ownership, asynchronous work and transaction boundaries."),
    ("sh bash zsh fish ps1", "Check argument quoting, command status, pipelines and destructive filesystem operations."),
    ("sql", "Check query cardinality, NULL behavior, constraints, transaction isolation and parameter handling."),
    ("tf tfvars hcl bicep nix rego", "Check effective permissions, exposed resources, deployment dependencies and upgrade safety."),
    ("json json5 yaml yml toml ini properties xml gradle cmake", "Check configuration consumers, value types, referenced paths, compatibility and effective deployment behavior."),
    ("html htm css scss sass less ftl ftlh ftlx hbs mustache jinja2 j2 pug astro vue svelte", "Check rendered values, escaping, conditional behavior, user interaction and accessible controls."),
    ("sol vy", "Check authority, value transfers, arithmetic, reentrancy and state transitions against reachable transactions."),
    ("v sv vh vhd vhdl", "Check widths, signedness, clock domains, reset behavior and timing-sensitive state transitions."),
    ("md markdown mdx rst txt adoc", "Check factual instructions and examples against the changed implementation and supported configuration."),
)


def system_rule(path, *, full_tree=False):
    suffix = PurePosixPath(path).suffix.lstrip(".").lower()
    specific = next((text for extensions, text in BUILTIN_CHECKS if suffix in extensions.split()), "")
    general = BUILTIN_RULE
    if full_tree:
        general = general.replace("changed behavior", "behavior").replace("introduced defects", "defects")
        specific = specific.replace("changed implementation", "implementation")
    return "\n\n".join(filter(None, (general, specific)))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _brace_patterns(pattern):
    """Expand bounded alternatives without evaluating shell syntax."""
    pending, result = [pattern], []
    while pending:
        value = pending.pop(0)
        match = re.search(r"\{([^{}]*)\}", value)
        if not match:
            result.append(value)
        else:
            pending.extend(value[:match.start()] + choice + value[match.end():]
                           for choice in match[1].split(","))
        if len(pending) + len(result) > 256:
            raise ValueError("Review rule glob has too many alternatives.")
    return result


def rule_matches(path, pattern):
    """Rule globs are case insensitive; * stays within one path component."""
    parts = path.lower().split("/")
    for expanded in _brace_patterns(pattern.lower()):
        components = expanded.split("/")

        pending, visited = [(0, 0)], set()
        while pending:
            index, offset = pending.pop()
            if (index, offset) in visited:
                continue
            visited.add((index, offset))
            if offset == len(components):
                if index == len(parts):
                    return True
            elif components[offset] == "**":
                pending.append((index, offset + 1))
                if index < len(parts):
                    pending.append((index + 1, offset))
            elif index < len(parts) and fnmatchcase(parts[index], components[offset]):
                pending.append((index + 1, offset + 1))
    return False


def _entry(repo, env, sha, path, stopped):
    source.repository_path(path)
    output = source.git(repo, env, "--literal-pathspecs", "ls-tree", "-z", "-l", sha, "--", path, stopped=stopped)
    if not output:
        return None
    match = re.fullmatch(r"(\d+) blob ([0-9a-f]{40})\s+(\d+)\t([^\0]+)\0\n?", output)
    if not match or match[4] != path or match[1] not in {"100644", "100755"}:
        raise ValueError("Source is not a regular text file at the pinned revision.")
    return {"blob_sha": match[2], "size": int(match[3])}


def _rule_layer(value):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("Review rule configuration must be an object.")
    result = {"rules": value.get("rules") or [], "include": value.get("include") or [],
              "exclude": value.get("exclude") or []}
    if (not isinstance(result["rules"], list)
            or any(not isinstance(result[key], list) or any(not isinstance(p, str) for p in result[key])
                   for key in ("include", "exclude"))):
        raise ValueError("Review rules require lists of rules and glob patterns.")
    entries = []
    for entry in result["rules"]:
        if (not isinstance(entry, dict) or not isinstance(entry.get("path", ""), str)
                or not isinstance(entry.get("rule", ""), str)
                or type(entry.get("merge_system_rule", False)) is not bool):
            raise ValueError("Invalid project review rule entry.")
        entries.append({"path": entry.get("path", ""), "rule": entry.get("rule", ""),
                        "merge_system_rule": entry.get("merge_system_rule", False)})
    result["rules"] = entries
    for pattern in [*result["include"], *result["exclude"], *(e["path"] for e in entries)]:
        rule_matches("validation", pattern)
    return result


def load_rules(repo, env, base_sha, options, stopped):
    """Read inert rules only from the trusted base; never consult a worker's home."""
    warnings, layers, inputs = [], [], []
    custom = _rule_layer(options.get("rules"))
    project = {}
    resolved_bytes = len(json.dumps([custom, project], ensure_ascii=False).encode())
    for layer in (custom, _rule_layer(project)):
        for item in layer["rules"]:
            text = item["rule"]
            if "\n" not in text and " " not in text and PurePosixPath(text).suffix.lower() in {".md", ".txt", ".markdown"}:
                try:
                    source.repository_path(text)
                    # Dedicated trusted rules are allowed, but cannot read arbitrary secret/instruction paths.
                    policy_path = text
                    policy = file_policy.policy(options)
                    if (file_policy.excluded(policy_path, policy)
                            or any(file_policy.matches(text, pattern) for pattern in policy["exclusions"])
                            or any(rule_matches(text, pattern) for pattern in custom["exclude"])):
                        raise ValueError("Rule reference is excluded by repository policy.")
                    reference = _entry(repo, env, base_sha, text, stopped)
                    if not reference or reference["size"] > 512 * 1024:
                        raise ValueError("Rule reference is missing or exceeds 512 KiB.")
                    resolved_bytes += reference["size"]
                    if resolved_bytes > 512 * 1024:
                        raise ValueError("Resolved review rules exceed 512 KiB.")
                    item["rule"] = source.read_source(repo, env, base_sha, text, stopped).rstrip("\n")
                    inputs.append({"path": text, **reference})
                except ValueError:
                    item["rule"] = ""
                    raise ValueError(f"Trusted-base review rule reference {text!r} is unavailable or prohibited.") from None
        layers.append(layer)
        if len(json.dumps(layers, ensure_ascii=False).encode()) > 512 * 1024:
            raise ValueError("Resolved trusted-base review rules exceed 512 KiB.")
    result = {"version": RULES_VERSION, "base_sha": base_sha, "layers": layers, "inputs": inputs,
              "warnings": warnings}
    return {**result, "digest": fingerprint(result)}


def exclusion_reason(path, options, rules, *, supporting_evidence=False):
    """Explain target admission; evidence relaxes only default test omissions."""
    if file_policy.excluded(path):
        return "mandatory_exclusion"
    if file_policy.excluded(path, file_policy.policy(options)):
        return "configured_exclusion"
    # The runtime custom layer always contains mandatory excludes and text includes,
    # preserving its priority over project-local include/exclude configuration.
    custom = rules["layers"][0]
    if any(rule_matches(path, pattern) for pattern in custom["exclude"]):
        return "rule_exclusion"
    if any(rule_matches(path, pattern) for pattern in [*custom["include"], *file_policy.TEXT_CONTEXT]):
        return None
    source_path = file_policy.template_source_path(path)
    suffix = PurePosixPath(source_path).suffix.lstrip(".").lower()
    if (suffix or source_path != path) and suffix not in CODE_SUFFIXES:
        return "unsupported_source"
    directories = DEFAULT_DIRS - {"__tests__"} if supporting_evidence else DEFAULT_DIRS
    if any(part.lower() in directories for part in PurePosixPath(path).parts):
        return "default_directory"
    for pattern in DEFAULT_PATTERNS:
        if supporting_evidence and pattern in DEFAULT_TEST_PATTERNS:
            continue
        if any(rule_matches(value, pattern) for value in (path, source_path)):
            return "default_test" if pattern in DEFAULT_TEST_PATTERNS else "default_pattern"
    return None


def allowed_path(path, options, rules, *, supporting_evidence=False):
    return exclusion_reason(path, options, rules, supporting_evidence=supporting_evidence) is None


def allowed_context(repo, env, base_sha, head_sha, paths, options, stopped, *, rules=None):
    rules = rules or load_rules(repo, env, options.get("_settings_sha", base_sha), options, stopped)
    allowed = {path for path in paths if allowed_path(path, options, rules, supporting_evidence=True)}
    if not allowed:
        return allowed
    # Trusted rules come from the current base; PR rename scope comes from its merge base.
    merge_base = source.git(repo, env, "merge-base", base_sha, head_sha, stopped=stopped).strip()
    if not re.fullmatch(r"[a-f0-9]{40}", merge_base):
        raise ValueError("Supporting context requires the exact PR merge base.")
    for path, old_path, _status in _changes(repo, env, merge_base, head_sha, stopped):
        if old_path and not all(allowed_path(value, options, rules, supporting_evidence=True)
                                for value in (path, old_path)):
            allowed.difference_update((path, old_path))
    return allowed


def rule_for(path, rules, *, full_tree=False):
    for layer in rules["layers"]:
        for entry in layer["rules"]:
            if ((entry["rule"] or entry["merge_system_rule"]) and rule_matches(path, entry["path"])):
                return "\n\n".join(filter(None, (system_rule(path, full_tree=full_tree) if entry["merge_system_rule"] else "", entry["rule"])))
    return system_rule(path, full_tree=full_tree)


@dataclass
class ReviewUnit:
    key: str
    path: str
    old_path: str | None
    status: str
    source_sha: str
    start_line: int
    end_line: int
    source: str
    patch: str
    rule: str
    input_hash: str


@dataclass
class SelectedFile:
    path: str
    old_path: str | None
    status: str
    source_sha: str
    blob_sha: str | None = None
    units: list[ReviewUnit] = field(default_factory=list)
    error: str | None = None


@dataclass
class ReviewPlan:
    base_sha: str
    head_sha: str
    merge_base: str
    policy_digest: str
    rules_digest: str
    rules: dict
    selected: list[SelectedFile]
    exclusions: list[dict]
    units: list[ReviewUnit] = field(default_factory=list)
    batches: list[list[ReviewUnit]] = field(default_factory=list)
    limit_reason: str | None = None

    def snapshot(self):
        return asdict(self)


def _changes(repo, env, merge_base, head_sha, stopped):
    raw = source.git(repo, env, "diff", "--no-ext-diff", "--no-textconv", "--find-renames",
                     "--name-status", "-z", merge_base, head_sha, stopped=stopped)
    fields, changes, index = raw.rstrip("\0\n").split("\0"), [], 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        if not re.fullmatch(r"[AMDT]|[RC]\d{1,3}", status) or index + 1 >= len(fields):
            raise ValueError("Invalid pinned Git change inventory.")
        old, index = source.repository_path(fields[index + 1]), index + 2
        path = old
        if status.startswith(("R", "C")):
            if index >= len(fields):
                raise ValueError("Incomplete pinned rename inventory.")
            path, index = source.repository_path(fields[index]), index + 1
        changes.append((path, old if status.startswith(("R", "C")) else None, status))
    if len({path for path, _, _ in changes}) != len(changes):
        raise ValueError("Duplicate paths in pinned Git change inventory.")
    return sorted(changes)


def _binary_paths(repo, env, merge_base, head_sha, stopped):
    output = source.git(repo, env, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--numstat",
                        "-z", merge_base, head_sha, stopped=stopped)
    result = set()
    for row in output.rstrip("\0\n").split("\0"):
        if not row:
            continue
        values = row.split("\t", 2)
        if len(values) != 3:
            raise ValueError("Invalid pinned Git size inventory.")
        path = source.repository_path(values[2])
        if values[0] == "-" or values[1] == "-":
            result.add(path)
    return result


def _patch_rows(patch, total_lines):
    metadata, rows, old, new, active = [], [], 0, 0, False
    for row in patch.splitlines(keepends=True):
        match = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", row)
        if match:
            old, new, active = int(match[1]), int(match[2]), True
        elif active and row[:1] in {"+", "-", " ", "\\"}:
            anchor = min(max(1, new), max(1, total_lines))
            if row.startswith("\\") and rows:
                anchor = rows[-1][0]
            rows.append((anchor, old, new, row))
            old += row.startswith(("-", " "))
            new += row.startswith(("+", " "))
        else:
            metadata.append(row)
    return "".join(metadata), rows


def _range_patch(metadata, rows):
    if not rows:
        return metadata
    result, group = [], []
    for item in rows:
        if group and (item[1] > group[-1][1] + 1 or item[2] > group[-1][2] + 1):
            result.append(group)
            group = []
        group.append(item)
    if group:
        result.append(group)
    output = metadata
    for group in result:
        old_count = sum(item[3].startswith(("-", " ")) for item in group)
        new_count = sum(item[3].startswith(("+", " ")) for item in group)
        output += f"@@ -{group[0][1]},{old_count} +{group[0][2]},{new_count} @@\n"
        output += "".join(item[3] for item in group)
    return output


def split_units(path, old_path, status, sha, text, patch, rule, maximum):
    lines = text.splitlines(keepends=True)
    metadata, rows = _patch_rows(patch, len(lines))
    by_line = {}
    for row in rows:
        by_line.setdefault(row[0], []).append(row)
    chunks, chunk_lines, chunk_rows, start = [], [], [], 1
    fixed_size = len((metadata + rule + path + (old_path or "")).encode()) + 512
    used = fixed_size
    for number, line in enumerate(lines or [""], 1):
        selected_rows = by_line.get(number, [])
        line_size = len(line.encode())
        size = line_size + sum(len(row[3].encode()) + 80 for row in selected_rows)
        if fixed_size + line_size > maximum:
            raise ValueError("A required source/diff line exceeds the bounded review context.")
        if fixed_size + size > maximum:
            if chunk_lines:
                chunks.append((start, number - 1, "".join(chunk_lines), _range_patch(metadata, chunk_rows)))
            chunk_lines, chunk_rows, start, used = [], [], number + 1, fixed_size
            row_chunk, row_size = [], fixed_size + line_size
            for row in selected_rows:
                required = len(row[3].encode()) + 80
                if fixed_size + line_size + required > maximum:
                    raise ValueError("A required source/diff line exceeds the bounded review context.")
                if row_chunk and row_size + required > maximum:
                    chunks.append((number, number, line, _range_patch(metadata, row_chunk)))
                    row_chunk, row_size = [], fixed_size + line_size
                row_chunk.append(row)
                row_size += required
            if row_chunk:
                chunks.append((number, number, line, _range_patch(metadata, row_chunk)))
            continue
        if chunk_lines and used + size > maximum:
            chunks.append((start, number - 1, "".join(chunk_lines), _range_patch(metadata, chunk_rows)))
            chunk_lines, chunk_rows, start, used = [], [], number, fixed_size
        chunk_lines.append(line)
        chunk_rows.extend(selected_rows)
        used += size
    if chunk_lines:
        chunks.append((start, max(1, len(lines)), "".join(chunk_lines), _range_patch(metadata, chunk_rows)))
    units = []
    for first, last, content, difference in chunks:
        value = {"path": path, "old_path": old_path, "status": status, "source_sha": sha,
                 "start_line": first, "end_line": last, "source": content, "patch": difference, "rule": rule}
        identity = fingerprint(value)
        units.append(ReviewUnit(key=f"pr-{identity}", **value, input_hash=identity))
    return units


def plan_review(repo, env, base_sha, head_sha, merge_base, options, stopped, *,
                max_unit_bytes=49152, max_batch_bytes=98304, max_source_bytes=20_000_000, trusted_rules_sha=None):
    for sha in (base_sha, head_sha, merge_base):
        if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise ValueError("PR planning requires exact Git revisions.")
    if any(type(limit) is not int or limit < 1024 for limit in (max_unit_bytes, max_batch_bytes, max_source_bytes)):
        raise ValueError("PR context bounds must be whole bytes of at least 1 KiB.")
    if max_batch_bytes < max_unit_bytes:
        raise ValueError("A batch must admit a complete bounded unit.")
    if source.git(repo, env, "merge-base", base_sha, head_sha, stopped=stopped).strip() != merge_base:
        raise ValueError("PR planning cannot substitute a different merge base.")
    rules = load_rules(repo, env, trusted_rules_sha or base_sha, options, stopped)
    plan = ReviewPlan(base_sha, head_sha, merge_base, fingerprint({"file_policy": file_policy.policy(options), "rules": rules["digest"]}),
                      rules["digest"], rules, [], [])
    binary = _binary_paths(repo, env, merge_base, head_sha, stopped)
    for path, old_path, status in _changes(repo, env, merge_base, head_sha, stopped):
        if not all(allowed_path(value, options, rules) for value in (path, old_path) if value):
            plan.exclusions.append({"path": path, "old_path": old_path, "reason": "excluded"})
        elif path in binary or old_path in binary:
            plan.exclusions.append({"path": path, "old_path": old_path, "reason": "binary"})
        elif status == "D":
            plan.exclusions.append({"path": path, "reason": "deleted", "source_sha": merge_base})
        else:
            plan.selected.append(SelectedFile(path, old_path, status, head_sha))
    maximum = options.get("max_files")
    if maximum is not None and (type(maximum) is not int or maximum < 1):
        raise ValueError("PR max_files must be positive or unlimited.")
    if maximum is not None and len(plan.selected) > maximum:
        plan.limit_reason = f"{len(plan.selected)} reviewable files exceeds the maximum of {maximum}."
        return plan
    used = 0
    for selected in plan.selected:
        if stopped():
            raise ReviewStopped("The review was cancelled or superseded.")
        try:
            entry = _entry(repo, env, head_sha, selected.path, stopped)
            if not entry:
                raise ValueError("Required source is missing at the pinned head.")
            selected.blob_sha = entry["blob_sha"]
            if entry["size"] > max_source_bytes:
                raise ValueError("Required source exceeds the PR source admission limit; splitting cannot bypass it.")
            if used + entry["size"] > max_source_bytes:
                raise ValueError("Required source exceeds the aggregate PR source admission limit.")
            text = source.read_source(repo, env, head_sha, selected.path, stopped)
            patch = source.git(repo, env, "--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv", "--find-renames",
                               "--unified=3", merge_base, head_sha, "--", *dict.fromkeys(filter(None, (selected.old_path, selected.path))), stopped=stopped)
            size = len((text + patch).encode())
            if used + size > max_source_bytes:
                raise ValueError("Required source and diff exceed the aggregate PR source admission limit.")
            units = split_units(selected.path, selected.old_path, selected.status, head_sha,
                                text, patch, rule_for(selected.path, rules, full_tree=options.get('review_mode') == 'all'), max_unit_bytes)
            size = sum(len((unit.source + unit.patch).encode()) for unit in units)
            if used + size > max_source_bytes:
                raise ValueError("Required split context exceeds the aggregate PR source admission limit.")
            used += size
            selected.units = units
            plan.units.extend(units)
        except ValueError as exc:
            selected.error = str(exc)
    batch, size = [], 0
    for unit in plan.units:
        unit_size = len((unit.source + unit.patch + unit.rule).encode()) + 512
        if batch and size + unit_size > max_batch_bytes:
            plan.batches.append(batch)
            batch, size = [], 0
        batch.append(unit)
        size += unit_size
    if batch:
        plan.batches.append(batch)
    return plan
