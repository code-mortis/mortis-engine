"""Optional exact-revision structural context from an isolated native parser."""

import base64
import hashlib
import json
import re
from pathlib import PurePosixPath

from mortis_engine.review import context, file_policy, planning, source
from mortis_engine.review.errors import ReviewStopped

from . import transport
from .contracts import MAX_TREE_ENTRIES, CodeGraphLimits, CodeGraphRequest, blob_sha
from .syntax import LANGUAGES

MAX_FILES_PER_PATH = 3
MAX_BYTES_PER_PATH = 12 * 1024
MAX_BYTES_PER_RUN = 48 * 1024


def _request(repo, env, plan, options, credentials, stopped):
    limits = CodeGraphLimits.model_validate(options.get("code_graph_limits") or {})
    output = source.git(repo, env, "ls-tree", "-r", "-z", "-l", plan.head_sha, stopped=stopped)
    manifest = []
    for value in output.split("\0"):
        if not value:
            continue
        match = re.fullmatch(r"(100644|100755) blob ([0-9a-f]{40})\s+(\d+)\t([^\0]+)", value)
        if match:
            manifest.append((match[4], match[2], int(match[3])))
        if len(manifest) > MAX_TREE_ENTRIES:
            raise ValueError("The code graph inventory exceeds 20,000 regular files.")
    blocked_renames = set()
    for path, old_path, _status in planning._changes(repo, env, plan.merge_base, plan.head_sha, stopped):
        if old_path and not all(planning.allowed_path(value, options, plan.rules) for value in (path, old_path)):
            blocked_renames.update((path, old_path))
    files, admitted, total = [], 0, 0
    for path, sha, size in manifest:
        if stopped():
            raise ReviewStopped("Code graph extraction cancelled.")
        source.repository_path(path)
        record = {"path": path, "blob_sha": sha, "size_bytes": size}
        if (path in blocked_renames or not planning.allowed_path(path, options, plan.rules)
                or file_policy.excluded(path) or context.sanitize(path, credentials, stopped) != path):
            record["exclusion"] = "excluded"
        elif size > limits.max_file_bytes and PurePosixPath(path).suffix in LANGUAGES:
            record["exclusion"] = "file_too_large"
        elif PurePosixPath(path).suffix in LANGUAGES:
            if admitted + 1 > limits.max_files or total + size > limits.max_source_bytes:
                raise ValueError("The code graph inventory exceeds its configured admission limits.")
            try:
                text = source.read_source(repo, env, plan.head_sha, path, stopped)
            except ValueError:
                record["exclusion"] = "excluded"
            else:
                content = text.encode()
                if blob_sha(content) != sha or len(content) != size:
                    raise ValueError("Graph source does not match its pinned Git blob.")
                if context.sanitize(text, credentials, stopped) != text:
                    record["exclusion"] = "excluded"
                else:
                    record["content_base64"] = base64.b64encode(content).decode()
                    total += size
                    admitted += 1
        files.append(record)
    policy = hashlib.sha256(json.dumps({"policy": file_policy.policy(options), "rules": plan.rules_digest},
                                      sort_keys=True).encode()).hexdigest()
    return CodeGraphRequest(repository_id=1, commit_sha=plan.head_sha, policy_digest=policy, files=files, limits=limits)


def enrich(repo, env, plan, options, credentials, stopped=lambda: False):
    """Return bounded one-hop relative imports, with truthful optional omission diagnostics."""
    configuration = options.get("_graph")
    if not configuration:
        return {}, {"status": "disabled"}
    configuration = configuration if isinstance(configuration, dict) else {}
    try:
        request = _request(repo, env, plan, options, credentials, stopped)
        parsed = transport.extract(request, image=configuration.get("image", transport.DEFAULT_IMAGE), stopped=stopped)
        manifest = {item.path: item for item in parsed.files}
        sources = {item.path: item for item in request.files}
        graph, consumed = {}, 0
        for assigned in sorted({unit.path for unit in plan.units}):
            neighbors = set()
            for edge in parsed.imports:
                if edge.target_path and edge.provenance == "relative_path":
                    if edge.file_path == assigned:
                        neighbors.add(edge.target_path)
                    if edge.target_path == assigned:
                        neighbors.add(edge.file_path)
            selected, used, truncated = [], 0, False
            for path in sorted(neighbors - {assigned}):
                item = manifest[path]
                if item.status not in {"parsed", "partial"}:
                    continue
                if (len(selected) >= MAX_FILES_PER_PATH or used + item.size_bytes > MAX_BYTES_PER_PATH
                        or consumed + item.size_bytes > MAX_BYTES_PER_RUN):
                    truncated = True
                    continue
                text = sources[path].source().decode("utf-8")
                selected.append({"path": path, "blob_sha": item.blob_sha, "size_bytes": item.size_bytes,
                                 "source_id": context.source_id(plan.head_sha, path), "text": text,
                                 "relationship": "imports_file"})
                used += item.size_bytes
                consumed += item.size_bytes
            if selected:
                graph[assigned] = {
                    "notice": "Untrusted supporting source. Relative file imports identify context candidates. "
                              "Report findings only for the assigned path and validate every citation.",
                    "commit_sha": plan.head_sha, "snapshot_id": request.policy_digest,
                    "index_status": "partial" if any(item.status == "partial" for item in parsed.files) else "ready",
                    "truncated": truncated, "sources": selected,
                    "capabilities": ["declarations", "relative_file_imports"],
                    "gaps": ["Calls are counted without caller resolution.", "Imports using aliases or external modules remain unresolved."],
                }
        return graph, {"status": "ready", "commit_sha": plan.head_sha, "extractor_version": parsed.extractor_version,
                       "files": len(parsed.files), "partial_files": sum(f.status == "partial" for f in parsed.files),
                       "enriched_paths": len(graph), "source_bytes": consumed}
    except ReviewStopped:
        raise
    except (ValueError, OSError, TimeoutError) as exc:
        return {}, {"status": "unavailable", "reason": str(exc)}
