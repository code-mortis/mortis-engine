"""Bounded inert identifier search in policy-approved pinned Git blobs."""

import json
import re

from mortis_engine.schemas import Tool

from . import context, planning, source
from .errors import ReviewStopped

VERSION = "pinned-symbol-search/v1"
MAX_FILES = 128
MAX_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024
MAX_RESULTS = 32
MAX_RESULT_BYTES = 8192
MAX_SEARCHES = 2
NOTICE = ("Literal identifier occurrences only, not resolved symbols, callers or reachability. "
          "Homonyms, comments and strings can match; aliases and dynamic access can be missed. "
          "An empty result proves no absence. Request relevant paths with read_source and verify "
          "the supplied source before citing it; search matches are navigation hints, never evidence.")


def search_tool():
    return Tool(name="search_source", description=(
        "Find a literal symbol name in permitted files, including unchanged files and supporting tests, "
        "at the pinned head. No execution or Docker. At most two searches per source round. " + NOTICE),
        parameters={"type": "object", "additionalProperties": False, "required": ["symbol"],
                    "properties": {"symbol": {"type": "string", "minLength": 1, "maxLength": 128,
                                               "pattern": "^[A-Za-z_$][A-Za-z0-9_$]*$"}}})


def search(repo, env, plan, options, symbol, credentials, authorize, stopped):
    """Return locations only; the ordinary source tool separately authorizes citation evidence."""
    def check():
        authorize()
        if stopped():
            raise ReviewStopped("Review source search cancelled.")

    check()
    if (not isinstance(symbol, str) or len(symbol.encode()) > 128
            or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", symbol)):
        raise ValueError("Search requires one literal identifier of at most 128 bytes.")
    if context.sanitize(symbol, credentials, stopped) != symbol:
        raise ValueError("Search identifier contains credential material; request withheld.")
    inventory = source.git(repo, env, "ls-tree", "-r", "-z", "-l", plan.head_sha, stopped=stopped)
    entries = {}
    for entry in inventory.split("\0"):
        check()
        match = re.fullmatch(r"100(?:644|755) blob ([a-f0-9]{40})\s+(\d+)\t(.+)", entry)
        if match:
            try:
                path = source.repository_path(match[3])
            except ValueError:
                continue
            entries[path] = (match[1], int(match[2]))
    allowed = planning.allowed_context(repo, env, plan.base_sha, plan.head_sha, entries,
                                       options, stopped, rules=plan.rules)
    pattern = re.compile(r"(?<![\w$])" + re.escape(symbol) + r"(?![\w$])")
    result = {"tool": "search_source", "version": VERSION, "revision": plan.head_sha,
              "symbol": symbol, "matches": [], "scanned_files": 0, "scanned_bytes": 0,
              "omitted_files": 0, "truncated": False, "notice": NOTICE}
    paths = sorted(allowed)
    for offset, path in enumerate(paths):
        check()
        oid, length = entries[path]
        if (result["scanned_files"] >= MAX_FILES or result["scanned_bytes"] + length > MAX_BYTES):
            result["omitted_files"] += len(paths) - offset
            break
        if length > MAX_FILE_BYTES:
            result["omitted_files"] += 1
            continue
        result["scanned_files"] += 1
        result["scanned_bytes"] += length
        text = source.git(repo, env, "cat-file", "blob", oid, stopped=stopped)
        if "\0" in text or "\ufffd" in text:
            result["omitted_files"] += 1
            continue
        text = context.sanitize(text, credentials, stopped)
        try:
            context.sanitize({"path": path}, credentials, stopped)
        except ValueError:
            result["omitted_files"] += 1
            continue
        for line, value in enumerate(text.splitlines(), 1):
            check()
            if not pattern.search(value):
                continue
            hint = {"path": path, "line": line, "relation": "identifier_occurrence"}
            if (len(result["matches"]) >= MAX_RESULTS
                    or len(json.dumps({**result, "matches": [*result["matches"], hint]}, ensure_ascii=False).encode())
                    > MAX_RESULT_BYTES - 128):
                result["truncated"] = True
                break
            result["matches"].append(hint)
        if result["truncated"]:
            result["omitted_files"] += len(paths) - offset - 1
            break
    result["truncated"] |= bool(result["omitted_files"])
    check()
    return context.sanitize(result, credentials, stopped)
