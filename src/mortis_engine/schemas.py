"""Shared API connection and provider-neutral inference contracts."""

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ConnectionInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["api"] = "api"
    provider: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    protocol: Literal["openai", "openai-responses", "anthropic", "anthropic-bedrock"]
    model: str = Field(min_length=1, max_length=150)
    base_url: str = Field(default="", max_length=1000)
    api_key: str | None = Field(default=None, max_length=20000, repr=False)
    extra_headers: dict[str, str] | None = Field(default=None, repr=False)
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_connection(self):
        self.model = self.model.strip()
        if not self.model:
            raise ValueError("Choose a model for this API connection")
        if self.protocol != "anthropic-bedrock" and any(k.startswith("aws_") for k in self.options):
            raise ValueError("AWS options require the Bedrock protocol")
        return self

    @field_validator("options")
    @classmethod
    def allowed_options(cls, value):
        allowed = {"timeout_sec", "extra_body", "retry_codes", "aws_region", "aws_profile", "auth_header", "prompt_token_ceiling"}
        if set(value) - allowed:
            raise ValueError("Provider options contain an unsupported setting. Allowed keys: timeout_sec, extra_body, retry_codes, aws_region, aws_profile, auth_header, prompt_token_ceiling.")
        if "prompt_token_ceiling" in value and (
            type(value["prompt_token_ceiling"]) is not int or not 4096 <= value["prompt_token_ceiling"] <= 2_000_000
        ):
            raise ValueError("prompt_token_ceiling must be a whole number from 4096 to 2000000 tokens.")
        if "timeout_sec" in value and (
            type(value["timeout_sec"]) is not int or not 5 <= value["timeout_sec"] <= 900
        ):
            raise ValueError("timeout_sec must be a whole number from 5 to 900 seconds.")
        if "extra_body" in value:
            if not isinstance(value["extra_body"], dict):
                raise ValueError("extra_body must be a JSON object with keys and values.")
            if set(value["extra_body"]) & {"messages", "input", "tools", "model", "stream", "api_key"}:
                raise ValueError("extra_body cannot replace review inputs, tools, or authentication")
        if "retry_codes" in value and (
            not isinstance(value["retry_codes"], list)
            or any(type(code) is not int or not 400 <= code < 500 for code in value["retry_codes"])
        ):
            raise ValueError("retry_codes must be a list of whole HTTP status codes from 400 to 499.")
        for key in ("aws_region", "aws_profile", "auth_header"):
            if key in value and (not isinstance(value[key], str) or len(value[key]) > 150):
                raise ValueError(f"{key} must be text no longer than 150 characters.")
        return value

    @field_validator("extra_headers")
    @classmethod
    def header_values(cls, value):
        if value and any(
            "\n" in key + val or "\r" in key + val or len(val) > 20000 for key, val in value.items()
        ):
            raise ValueError("Extra header names and values must not contain line breaks; each value must be no longer than 20,000 characters.")
        return value


class Tool(StrictModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    description: str = ""
    parameters: dict[str, Any]


class ToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any]


class Message(StrictModel):
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    provider_data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_role(self):
        if (self.role == "tool") != bool(self.tool_call_id):
            raise ValueError("Only tool messages require a tool call ID")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("Only assistant messages may request tools")
        if self.provider_data and self.role != "assistant":
            raise ValueError("Only assistant messages may retain provider reasoning state")
        return self


class Request(StrictModel):
    messages: list[Message] = Field(min_length=1)
    instructions: str = ""
    tools: list[Tool] = Field(default_factory=list)
    tool_choice: str = "auto"
    output_schema: dict[str, Any] | None = None
    max_output_tokens: int = Field(strict=True, ge=0)
    effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    session_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=200,
                            pattern=r"^[A-Za-z0-9_.:-]+$")

    @model_validator(mode="after")
    def tool_history(self):
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("Tool names must be unique")
        if self.tool_choice not in {"auto", "required", *(tool.name for tool in self.tools)}:
            raise ValueError("Tool choice must be auto, required, or a declared tool name")
        if self.tool_choice != "auto" and not self.tools:
            raise ValueError("Tool choice requires declared tools")
        pending, seen = set(), set()
        for message in self.messages:
            if message.role == "tool":
                if message.tool_call_id not in pending:
                    raise ValueError("Tool results must answer a pending tool call exactly once")
                pending.remove(message.tool_call_id)
            else:
                if pending:
                    raise ValueError("Answer all pending tool calls before continuing")
                for call in message.tool_calls:
                    if call.id in seen:
                        raise ValueError("Tool call IDs must be unique")
                    pending.add(call.id)
                    seen.add(call.id)
        if pending:
            raise ValueError("Answer all pending tool calls before sending a request")
        return self


class Usage(StrictModel):
    input_tokens: int | None = Field(default=None, strict=True, ge=0)
    output_tokens: int | None = Field(default=None, strict=True, ge=0)
    total_tokens: int | None = Field(default=None, strict=True, ge=0)
    cache_read_tokens: int | None = Field(default=None, strict=True, ge=0)
    cache_write_tokens: int | None = Field(default=None, strict=True, ge=0)
    reasoning_tokens: int | None = Field(default=None, strict=True, ge=0)


class Response(StrictModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    request_id: str | None = None
    finish_reason: Literal["complete", "tool_calls"] = "complete"
    output: Any | None = None
    provider_data: dict[str, Any] = Field(default_factory=dict)
