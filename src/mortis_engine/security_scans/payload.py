"""Lossless references to saved quotes already present in complete source blocks."""

import json

from mortis_engine.review import file_policy


def excluded_source_paths(value):
    paths = set()

    def collect(value):
        if isinstance(value, dict):
            for field, child in value.items():
                if field == "sources" and isinstance(child, dict):
                    paths.update(path for sources in child.values() if isinstance(sources, dict) for path in sources)
                elif field == "context_paths" and isinstance(child, list):
                    paths.update(path for path in child if isinstance(path, str))
                elif field in {"path", "from_path", "assigned_path", "current_path"} and isinstance(child, str):
                    paths.add(child)
                else:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(value)
    return sorted(path for path in paths if file_policy.excluded(path))


def reusable_source_context(checkpoint):
    paths = checkpoint.get("context_paths")
    return (not excluded_source_paths(checkpoint)
            and (checkpoint.get("source_packing") is not True or isinstance(paths, list) and bool(paths)))


def check_saved_sources(context):
    if excluded_source_paths({"sources": context.get("sources", {})}):
        raise ValueError("Saved assessment source is excluded by the current mandatory file policy.")


def compact_evidence(context):
    sources = context.get("sources", {})
    references = {}
    for reference, source in context.get("citation_sources", {}).items():
        revision, path = source.get("revision"), source.get("path")
        if (isinstance(reference, str) and isinstance(revision, str) and isinstance(path, str)
                and isinstance(sources.get(revision, {}).get(path), str)):
            references.setdefault((revision, path), reference)
    lines = {}
    revision_shas = {"current": context.get("head_sha"), "original": context.get("original_sha")}

    def compact(value, *, evidence=False, source_sha=None, historical=False):
        if isinstance(value, list):
            return [compact(child, evidence=evidence, source_sha=source_sha, historical=historical) for child in value]
        if not isinstance(value, dict):
            return value
        source_sha = value.get("source_sha", source_sha)
        result = {key: compact(child, evidence=key == "evidence", source_sha=source_sha,
                               historical=historical or key == "history")
                  for key, child in value.items()}
        if not evidence or historical and source_sha is None:
            return result
        revision, path, quote = value.get("revision"), value.get("path"), value.get("quote")
        start, end = value.get("start_line"), value.get("end_line")
        if not (isinstance(revision, str) and isinstance(path, str) and isinstance(quote, str)
                and quote.strip() and type(start) is int and type(end) is int):
            return result
        if source_sha is not None and source_sha != revision_shas.get(revision):
            return result
        reference = references.get((revision, path))
        if reference is None or value.get("quote_source_id", reference) != reference:
            return result
        if "quote_source_id" not in value and len(json.dumps({"quote_source_id": reference})) >= len(json.dumps({"quote": quote})):
            return result
        if (revision, path) not in lines:
            lines[revision, path] = sources[revision][path].splitlines(keepends=True)
        source = lines[revision, path]
        if 1 <= start <= end <= len(source) and quote == "".join(source[start - 1:end]):
            del result["quote"]
            result["quote_source_id"] = reference
        return result

    return compact(context)
