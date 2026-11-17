"""Internal structural extraction contracts; these do not authorize source access."""

import base64
import binascii
import hashlib
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from mortis_engine.code_graph.syntax import LANGUAGES
from mortis_engine.review import file_policy

MAX_FILES = 1000
MAX_FILE_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_INDEXED_FILES = 5000
MAX_INDEXED_FILE_BYTES = 4 * 1024 * 1024
MAX_INDEXED_SOURCE_BYTES = 32 * 1024 * 1024
MAX_TREE_ENTRIES = 20_000
MAX_RESULT_BYTES = 32 * 1024 * 1024
MAX_FACTS = 100_000
MAX_REQUEST_BYTES = 64 * 1024 * 1024
EXTRACTION_SECONDS = 120
EXTRACTOR_VERSION = "tree-sitter/0.26.1-mortis/7"
APP_EXTRACTOR_VERSION = "tree-sitter/0.25.2-mortis/6"


class StrictModel(BaseModel):
    # This contract also runs in the parser image, without application dependencies.
    model_config = ConfigDict(extra="forbid")


class CodeGraphLimits(StrictModel):
    max_files: int = Field(default=MAX_FILES, ge=1, le=MAX_INDEXED_FILES, strict=True)
    max_file_bytes: int = Field(default=MAX_FILE_BYTES, ge=1, le=MAX_INDEXED_FILE_BYTES, strict=True)
    max_source_bytes: int = Field(default=MAX_SOURCE_BYTES, ge=1, le=MAX_INDEXED_SOURCE_BYTES, strict=True)

    @model_validator(mode="after")
    def file_fits_total(self) -> Self:
        if self.max_file_bytes > self.max_source_bytes:
            raise ValueError("Maximum individual file size cannot exceed Maximum total source size.")
        return self


class GraphFailure(StrictModel):
    code: str
    stage: str
    message: str
    recovery: str
    measurements: dict[str, int] = Field(default_factory=dict)
    attempt_reference: str | None = None


def stored_text(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Graph metadata must be valid UTF-8 text") from None
    if "\0" in value:
        raise ValueError("Graph metadata cannot contain NUL")
    return value


def repository_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or path.as_posix() != value
        or any(part == ".." or part.lower() == ".git" for part in path.parts)
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or len(value.encode("utf-8")) > 2000
    ):
        raise ValueError("Graph paths must be canonical repository-relative paths of at most 2000 bytes")
    return value


RepositoryPath = Annotated[str, Field(min_length=1, max_length=2000), AfterValidator(repository_path)]
GraphText = Annotated[str, AfterValidator(stored_text)]
GitSha = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
FileStatus = Literal["parsed", "partial", "unsupported", "excluded", "failed"]


class ParseDiagnostic(StrictModel):
    start_byte: int = Field(ge=0, le=MAX_INDEXED_FILE_BYTES, strict=True)
    line: int = Field(ge=1, le=MAX_INDEXED_FILE_BYTES + 1, strict=True)
    column: int = Field(ge=1, le=MAX_INDEXED_FILE_BYTES + 1, strict=True)
    context: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z_0-9]*$")
    token: GraphText = Field(min_length=1, max_length=100)
    missing_token: GraphText | None = Field(default=None, min_length=1, max_length=100)


class CodeGraphFile(StrictModel):
    path: RepositoryPath
    blob_sha: GitSha
    size_bytes: int = Field(ge=0, le=2**63 - 1, strict=True)
    language: GraphText | None = Field(default=None, min_length=1, max_length=32)
    status: FileStatus
    line_count: int | None = Field(default=None, ge=0, le=2**31 - 1, strict=True)
    unresolved_calls: int | None = Field(default=None, ge=0, le=2**31 - 1, strict=True)
    reason: Literal["syntax_error", "unsupported_language", "excluded", "file_too_large", "invalid_encoding", "parse_error"] | None = None
    diagnostic: ParseDiagnostic | None = None

    @model_validator(mode="after")
    def extraction_status(self) -> Self:
        if self.diagnostic is not None and self.status != "partial":
            raise ValueError("Only partly parsed files can have syntax diagnostics")
        if self.status in {"parsed", "partial"}:
            if self.language is None or self.line_count is None or self.size_bytes > MAX_INDEXED_FILE_BYTES:
                raise ValueError("Parsed files require language, line count and the per-file source limit")
            expected = None if self.status == "parsed" else "syntax_error"
            if self.reason != expected:
                raise ValueError("File reason does not match its parse status")
        else:
            reasons = {"unsupported": {"unsupported_language"}, "excluded": {"excluded", "file_too_large"},
                       "failed": {"invalid_encoding", "parse_error"}}
            if self.reason not in reasons[self.status] or self.unresolved_calls is not None:
                raise ValueError("Unparsed files require a reason and cannot report call counts")
        return self


class CodeGraphLocation(StrictModel):
    file_path: RepositoryPath
    start_byte: int = Field(ge=0, le=MAX_INDEXED_FILE_BYTES, strict=True)
    end_byte: int = Field(gt=0, le=MAX_INDEXED_FILE_BYTES, strict=True)
    start_line: int = Field(ge=1, le=2**31 - 1, strict=True)
    end_line: int = Field(ge=1, le=2**31 - 1, strict=True)

    @model_validator(mode="after")
    def ordered_range(self) -> Self:
        if self.end_byte <= self.start_byte or self.end_line < self.start_line:
            raise ValueError("Graph source ranges must be ordered and nonempty")
        return self


class CodeGraphSymbol(CodeGraphLocation):
    name: GraphText = Field(min_length=1, max_length=512)
    kind: str = Field(min_length=1, max_length=64, pattern=r"^[a-z_]+$")

    @property
    def key(self) -> str:
        # Names alone merge unrelated declarations; identity is local to a snapshot.
        value = f"{self.file_path}\0{self.start_byte}\0{self.end_byte}\0{self.kind}"
        return hashlib.sha256(value.encode()).hexdigest()


class CodeGraphImport(CodeGraphLocation):
    specifier: GraphText = Field(min_length=1, max_length=2000)
    target_path: RepositoryPath | None = None
    provenance: Literal["relative_path", "unresolved"]

    @model_validator(mode="after")
    def resolution(self) -> Self:
        if (self.target_path is None) != (self.provenance == "unresolved"):
            raise ValueError("Unresolved imports must have no target")
        if self.target_path is not None and not self.specifier.startswith("."):
            raise ValueError("Only relative-file import resolution is supported")
        return self


class CodeGraphResult(StrictModel):
    schema_version: Literal["code-graph/v2"] = "code-graph/v2"
    repository_id: int = Field(gt=0, le=2**63 - 1, strict=True)
    commit_sha: GitSha
    extractor_version: GraphText = Field(min_length=1, max_length=128)
    policy_digest: Digest
    limits: CodeGraphLimits = Field(default_factory=CodeGraphLimits)
    files: list[CodeGraphFile] = Field(max_length=MAX_TREE_ENTRIES)
    symbols: list[CodeGraphSymbol] = Field(default_factory=list, max_length=MAX_FACTS)
    imports: list[CodeGraphImport] = Field(default_factory=list, max_length=MAX_FACTS)

    @model_validator(mode="after")
    def consistent_snapshot(self) -> Self:
        files = {item.path: item for item in self.files}
        if len(files) != len(self.files):
            raise ValueError("Graph file paths must be unique within a snapshot")
        admitted = [item for item in self.files if item.status in {"parsed", "partial", "failed"}]
        if len(admitted) > self.limits.max_files or any(item.size_bytes > self.limits.max_file_bytes for item in admitted):
            raise ValueError("Graph source exceeds the configured file limits")
        if sum(item.size_bytes for item in admitted) > self.limits.max_source_bytes:
            raise ValueError("Graph source exceeds the snapshot byte limit")
        if len({item.key for item in self.symbols}) != len(self.symbols):
            raise ValueError("Graph declaration identities must be unique within a snapshot")
        if len({(item.file_path, item.start_byte) for item in self.imports}) != len(self.imports):
            raise ValueError("Graph import sites must be unique within a snapshot")
        for items in (self.symbols, self.imports):
            for item in items:
                source = files.get(item.file_path)
                if source is None or source.status not in {"parsed", "partial"}:
                    raise ValueError("Graph facts require a parsed source file in the same snapshot")
                if item.end_byte > source.size_bytes or item.end_line > source.line_count:
                    raise ValueError("Graph fact ranges must fit their source file")
        for item in self.imports:
            if item.target_path is not None:
                target = files.get(item.target_path)
                if target is None or target.status in {"excluded", "failed"}:
                    raise ValueError("Resolved imports require an available target in the same snapshot")
        if len(self.model_dump_json().encode()) > MAX_RESULT_BYTES:
            raise ValueError("Graph result exceeds the serialized byte limit")
        return self


def blob_sha(source: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" + source).hexdigest()


def source_lines(source: bytes) -> int:
    return source.count(b"\n") + bool(source and not source.endswith(b"\n"))


class CodeGraphSource(StrictModel):
    path: RepositoryPath
    blob_sha: GitSha
    size_bytes: int = Field(ge=0, le=2**63 - 1, strict=True)
    content_base64: str | None = Field(default=None, max_length=4 * ((MAX_INDEXED_FILE_BYTES + 2) // 3))
    exclusion: Literal["excluded", "file_too_large"] | None = None

    def source(self) -> bytes:
        return base64.b64decode(self.content_base64, validate=True)

    @model_validator(mode="after")
    def content_matches_manifest(self) -> Self:
        if self.exclusion is not None:
            if self.content_base64 is not None:
                raise ValueError("Excluded files must not carry source")
        elif PurePosixPath(self.path).suffix not in LANGUAGES:
            if self.content_base64 is not None:
                raise ValueError("Unsupported formats carry metadata only")
        else:
            if self.content_base64 is None or self.size_bytes > MAX_INDEXED_FILE_BYTES:
                raise ValueError("Admitted files require bounded source")
            try:
                source = self.source()
            except (ValueError, binascii.Error):
                raise ValueError("Source must be valid base64") from None
            if len(source) != self.size_bytes or blob_sha(source) != self.blob_sha:
                raise ValueError("Source does not match the file/blob manifest")
        return self


class CodeGraphRequest(StrictModel):
    schema_version: Literal["code-graph/v2"] = "code-graph/v2"
    repository_id: int = Field(gt=0, le=2**63 - 1, strict=True)
    commit_sha: GitSha
    extractor_version: Literal[EXTRACTOR_VERSION, APP_EXTRACTOR_VERSION] = EXTRACTOR_VERSION
    policy_digest: Digest
    limits: CodeGraphLimits = Field(default_factory=CodeGraphLimits)
    files: list[CodeGraphSource] = Field(max_length=MAX_TREE_ENTRIES)

    @model_validator(mode="after")
    def bounded_manifest(self) -> Self:
        if len({item.path for item in self.files}) != len(self.files):
            raise ValueError("Request paths must be unique")
        for item in self.files:
            if file_policy.excluded(item.path) and item.exclusion != "excluded":
                raise ValueError("Mandatory exclusions cannot carry source")
            if item.exclusion == "file_too_large" and item.size_bytes <= self.limits.max_file_bytes:
                raise ValueError("File does not exceed the configured per-file limit")
        admitted = [item for item in self.files if item.exclusion is None and PurePosixPath(item.path).suffix in LANGUAGES]
        if len(admitted) > self.limits.max_files or any(item.size_bytes > self.limits.max_file_bytes for item in admitted):
            raise ValueError("Request exceeds the configured file limits")
        if sum(item.size_bytes for item in admitted) > self.limits.max_source_bytes:
            raise ValueError("Request exceeds the source byte limit")
        return self
