"""Validate parser facts against exact source bytes and snapshot identity."""

from bisect import bisect_right
from pathlib import PurePosixPath

from .contracts import EXTRACTOR_VERSION, CodeGraphRequest, CodeGraphResult, source_lines
from .syntax import LANGUAGES, import_target


def validate_result(request: CodeGraphRequest, result: CodeGraphResult) -> CodeGraphResult:
    for key in ("schema_version", "repository_id", "commit_sha", "extractor_version", "policy_digest", "limits"):
        if getattr(result, key) != getattr(request, key):
            raise ValueError("Parser returned another snapshot identity")
    manifest = {item.path: item for item in request.files}
    if {item.path for item in result.files} != set(manifest):
        raise ValueError("Parser returned another file manifest")
    sources, lines, languages = {}, {}, {}
    for item in result.files:
        expected = manifest[item.path]
        if (item.blob_sha, item.size_bytes) != (expected.blob_sha, expected.size_bytes):
            raise ValueError("Parser returned another blob")
        if expected.exclusion:
            if item.status != "excluded" or item.reason != expected.exclusion or item.language is not None or item.line_count is not None:
                raise ValueError("Parser changed file exclusion")
            continue
        config = LANGUAGES.get(PurePosixPath(item.path).suffix)
        language = config[0] if config else None
        if item.language != language:
            raise ValueError("Parser returned another language")
        if config is None:
            if item.status != "unsupported" or item.line_count is not None:
                raise ValueError("Parser claimed unsupported syntax was parsed")
            continue
        source = expected.source()
        try:
            source.decode("utf-8")
        except UnicodeDecodeError:
            if item.status != "failed" or item.reason != "invalid_encoding" or item.line_count is not None:
                raise ValueError("Parser claimed invalid source was parsed") from None
            continue
        if request.extractor_version == EXTRACTOR_VERSION and item.status == "failed" and item.reason == "parse_error":
            if item.line_count is not None:
                raise ValueError("Parser returned invalid file coverage")
            continue
        if item.status not in {"parsed", "partial"} or item.line_count != source_lines(source) or item.unresolved_calls is None:
            raise ValueError("Parser returned invalid file coverage")
        if item.diagnostic is not None:
            diagnostic = item.diagnostic
            before = source[:diagnostic.start_byte]
            if (diagnostic.start_byte > len(source)
                or diagnostic.line != before.count(b"\n") + 1
                or diagnostic.column != len(before.rsplit(b"\n", 1)[-1]) + 1):
                raise ValueError("Parser diagnostic does not match source location")
            before.decode("utf-8")
        sources[item.path], languages[item.path] = source, language
        lines[item.path] = [index for index, value in enumerate(source) if value == 10]
    available = {item.path for item in result.files if item.status not in {"excluded", "failed"}}
    for item in [*result.symbols, *result.imports]:
        if item.file_path not in sources:
            raise ValueError("Parser facts require a validated source file")
        source = sources[item.file_path]
        newlines = lines[item.file_path]
        if (item.start_line, item.end_line) != (bisect_right(newlines, item.start_byte - 1) + 1,
                                                bisect_right(newlines, item.end_byte - 2) + 1):
            raise ValueError("Parser ranges do not match source lines")
        if hasattr(item, "kind"):
            definitions = LANGUAGES[PurePosixPath(item.file_path).suffix][1].split()
            if languages[item.file_path] in {"javascript", "typescript", "tsx"}:
                definitions.append("variable_declarator")
            if item.kind not in definitions:
                raise ValueError("Parser returned an unsupported declaration kind")
        fragment = source[item.start_byte:item.end_byte].decode("utf-8")
        # Range validation includes UTF-8 boundaries and literal evidence from the admitted blob.
        evidence = item.name if hasattr(item, "name") else item.specifier
        if evidence not in fragment:
            raise ValueError("Parser fact is absent from its source range")
    for item in result.imports:
        target = import_target(item.file_path, item.specifier, languages[item.file_path], available)
        if item.target_path != target:
            raise ValueError("Parser returned an unvalidated import target")
    return result
