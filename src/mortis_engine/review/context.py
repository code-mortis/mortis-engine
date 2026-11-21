"""Redacted model context and stable, exact-revision citation identities."""

import json
from collections.abc import Mapping, Sequence, Set

from mortis_engine.security import clean_text, digest

from .errors import ReviewStopped


def sanitize(value, credentials, stopped=lambda: False):
    rules = credentials.get("redaction_rules", [])
    redaction_key = None
    if rules:
        from mortis_engine.security_scans.engine import redact_secrets
        from mortis_engine.security_scans.scanner import identity_key

        redact_secrets("", [], rules, stopped, key=b"")
        redaction_key = identity_key(create=False)
    values = sorted({value for value in (credentials.get("api_key", ""),
                    *credentials.get("extra_headers", {}).values(), *credentials.get("redaction_values", []))
                    if value}, key=len, reverse=True)

    def redact(item, structural=False):
        if stopped():
            raise ReviewStopped("Review stopped before sanitization")
        if isinstance(item, str):
            text = item
            if rules:
                from mortis_engine.security_scans.engine import redact_secrets

                text = redact_secrets(text, values, rules, stopped, key=redaction_key)
            for secret in values:
                replacement = "\n".join("[redacted]" for _ in secret.split("\n"))
                text = text.replace(secret, replacement)
                text = text.replace(json.dumps(secret)[1:-1], json.dumps(replacement)[1:-1])
            text = clean_text(text)
            if structural and text != item:
                raise ValueError("Credential material in a structural identifier; request withheld")
            return text
        if isinstance(item, Mapping):
            identifiers = {"path", "assigned_path", "head_sha", "original_sha", "id", "source_id", "revision",
                           "finding_id", "available_paths", "required_paths", "paths", "changed_paths", "current_path",
                           "base_sha", "source_sha"}
            return {redact(key, True): redact(child, structural or key in identifiers) for key, child in item.items()}
        if isinstance(item, (Sequence, Set)) and not isinstance(item, (bytes, bytearray, memoryview)):
            return [redact(child, structural) for child in item]
        if item is None or type(item) in (bool, int, float):
            return item
        raise ValueError("Unsupported model context type; request withheld")

    return redact(value)


def source_id(revision, path):
    return "source_" + digest(revision + "\0" + path)[:32]


def quote_matches(source, start_line, end_line, quote):
    if (not isinstance(source, str) or not isinstance(quote, str) or not quote.strip()
            or type(start_line) is not int or type(end_line) is not int):
        return False
    lines = source.splitlines(keepends=True)
    if not 1 <= start_line <= end_line <= len(lines):
        return False
    original = "".join(lines[start_line - 1:end_line])
    rendered = "\n".join(source.splitlines()[start_line - 1:end_line])
    return quote in original or quote in rendered


def evidence_feedback(paths, field):
    return {"kind": "evidence", "field": field, "required_paths": sorted(paths),
            "reason": "These complete files are already supplied, including empty files. "
                      "Use their source blocks and request only absent paths."}


def unavailable_evidence_feedback(paths, field):
    return {"field": field, "required_paths": sorted(paths),
            "reason": "These paths are excluded or unsupported by the configured review scope. "
                      "Use permitted sources or remain uncertain and leave requested sources empty."}
