"""Trusted-base local review configuration, using Code Mortis option names."""

import tomllib
from typing import Any, Literal

from pydantic import Field, field_validator

from mortis_engine.review import file_policy, planning, source
from mortis_engine.schemas import StrictModel


class RunLimits(StrictModel):
    timeout_seconds: int | None = Field(default=None, ge=60, le=3600)
    token_budget: int | None = Field(default=None, ge=1000, le=5000000)


class RepoSettings(RunLimits):
    analysis_engine: Literal["mortis"] = "mortis"
    assessment_token_budget: int | None = Field(default=None, ge=1000, le=5000000)
    verification_token_budget: int | None = Field(default=None, ge=1000, le=5000000)
    summary_token_budget: int | None = Field(default=None, ge=1000, le=5000000)
    summary_timeout_seconds: int | None = Field(default=None, ge=60, le=3600)
    summary_diagram_enabled: bool = False
    ai_fix_prompts_enabled: bool = False
    automatic: bool = True
    include_bot_prs: bool = False
    auto_resolve: bool = False
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
                raise ValueError("rules.rules entries require path, rule and merge_system_rule fields.")
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


UNSUPPORTED = {"summary_diagram_enabled", "ai_fix_prompts_enabled", "auto_resolve"}


def load(repo, env, base_sha):
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
        if set(value) == {"review"}:
            value = value["review"]
        if not isinstance(value, dict):
            raise ValueError("Review configuration must be a TOML table.")
    options = RepoSettings.model_validate(value).model_dump()
    active = [key for key in UNSUPPORTED if options[key]]
    if active:
        raise ValueError("This CLI reviews initial changes only. Unsupported active settings: " + ", ".join(sorted(active)))
    return options
