"""Trusted-base local review configuration, using Code Mortis option names."""

import tomllib
from typing import Any, Literal

from pydantic import Field, field_validator

from mortis_engine.code_graph.contracts import CodeGraphLimits
from mortis_engine.review import file_policy, planning, source
from mortis_engine.schemas import StrictModel

COMPARISON_FLAGS = ("incremental", "skip_unchanged")


class RunLimits(StrictModel):
    timeout_seconds: int | None = Field(default=None, ge=60, le=3600, strict=True)
    token_budget: int | None = Field(default=None, ge=1000, le=5000000, strict=True)
    max_output_tokens: int | None = Field(default=None, ge=0, le=1000000, strict=True)
    assessment_token_budget: int | None = Field(default=None, ge=1000, le=5000000, strict=True)
    verification_token_budget: int | None = Field(default=None, ge=1000, le=5000000, strict=True)

    @field_validator("timeout_seconds", "token_budget", "assessment_token_budget", "verification_token_budget",
                     "summary_token_budget", "summary_timeout_seconds", "max_files", mode="before", check_fields=False)
    @classmethod
    def unlimited(cls, value):
        return None if type(value) is int and value == 0 else value


class RepoSettings(RunLimits):
    analysis_engine: Literal["mortis"] = "mortis"
    summary_token_budget: int | None = Field(default=None, ge=1000, le=5000000, strict=True)
    summary_timeout_seconds: int | None = Field(default=None, ge=60, le=3600, strict=True)
    summary_diagram_enabled: bool = False
    ai_fix_prompts_enabled: bool = False
    automatic: bool = True
    include_bot_prs: bool = False
    auto_resolve: bool = False
    second_pass: bool = Field(default=False, strict=True)
    incremental: bool = Field(default=False, strict=True)
    skip_unchanged: bool = Field(default=False, strict=True)
    model: str = Field(default="", max_length=150)
    effort: Literal["low", "medium", "high"] = "medium"
    review_sensitivity: Literal["low_noise", "balanced", "broad"] = "balanced"
    language: str = Field(default="English", min_length=1, max_length=100)
    exclusions: str = Field(default="", max_length=4000)
    background: str = Field(default="", max_length=20000)
    max_files: int | None = Field(default=None, ge=1, strict=True)
    # Retain historical settings for readers; Mortis uses token_budget.
    delegated_context_bytes: int | None = Field(default=None, ge=4096, le=20000000)
    concurrency: int = Field(default=2, ge=1, le=8)
    code_graph_limits: CodeGraphLimits = Field(default_factory=CodeGraphLimits)
    rules: dict[str, Any] | None = None

    @field_validator("rules", mode="before")
    @classmethod
    def valid_rules(cls, value):
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) - {"rules", "include", "exclude"}:
            raise ValueError("rules requires a table containing rules, include and exclude lists.")
        for key in ("rules", "include", "exclude"):
            entries = value.get(key, [])
            if not isinstance(entries, list) or len(entries) > 1000:
                raise ValueError(f"rules.{key} must be a list with at most 1000 entries.")
        for entry in value.get("rules", []):
            if (not isinstance(entry, dict)
                    or set(entry) - {"path", "rule", "merge_system_rule"}):
                raise ValueError("rules.rules entries support only path, rule and merge_system_rule fields.")
        return planning._rule_layer(value)

    @field_validator("exclusions")
    @classmethod
    def deny_only_exclusions(cls, value):
        file_policy.patterns(value)
        return value

    @field_validator("model")
    @classmethod
    def trim_model(cls, value):
        return value.strip()

    @field_validator("language", mode="before")
    @classmethod
    def trim_language(cls, value):
        return value.strip() if isinstance(value, str) else value


def effort_override(value):
    if value is None:
        return None
    return RepoSettings.model_validate({"effort": value}, strict=True).effort


def _tables(repo, env, base_sha):
    entry = source.git(repo, env, "ls-tree", "-z", "-l", base_sha, "--", "mortis.toml")
    value = {}
    if entry:
        if entry.split(" ", 1)[0] not in {"100644", "100755"}:
            raise ValueError("Trusted-base mortis.toml must be a regular file.")
        if int(entry.split("\t", 1)[0].rsplit(None, 1)[1]) > 512 * 1024:
            raise ValueError("Trusted-base mortis.toml exceeds 512 KiB.")
        try:
            value = tomllib.loads(source.read_source(repo, env, base_sha, "mortis.toml"))
        except tomllib.TOMLDecodeError:
            raise ValueError("Trusted-base mortis.toml contains invalid TOML.") from None
        if not isinstance(value, dict):
            raise ValueError("Review configuration must be a TOML table.")
    if "review" in value or "scan" in value:
        if set(value) - {"review", "scan"} or any(not isinstance(section, dict) for section in value.values()):
            raise ValueError("Use [review] and [scan] tables for repository settings.")
        return value.get("review", {}), value.get("scan", {})
    return value, {}


def load(repo, env, base_sha):
    review, _ = _tables(repo, env, base_sha)
    return RepoSettings.model_validate(review).model_dump()


def apply_overrides(options, overrides, *, scan=False):
    from mortis_engine.security_scans.service import ScanOptions

    model = ScanOptions if scan else RepoSettings
    values = dict(overrides or {})
    if not scan and isinstance(values.get("code_graph_limits"), dict):
        values["code_graph_limits"] = {**options.get("code_graph_limits", {}), **values["code_graph_limits"]}
    options.update(model.model_validate(values, strict=True).model_dump(include=set(values)))
    return options


def output_token_limit(options):
    value = options.get("max_output_tokens")
    return 16384 if value is None else value


def load_scan(repo, env, sha):
    from mortis_engine.security_scans.service import ScanOptions

    review, scan = _tables(repo, env, sha)
    shared = RepoSettings.model_validate(review)
    scan = dict(scan)
    model = scan.pop("model", "")
    if not isinstance(model, str) or len(model) > 150:
        raise ValueError("Scan model must be a model ID of at most 150 characters.")
    return {**ScanOptions.model_validate({"language": shared.language, "exclusions": shared.exclusions,
                                         "max_output_tokens": shared.max_output_tokens,
                                         **scan}).model_dump(), "model": (model or shared.model).strip()}
