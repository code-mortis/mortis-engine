"""Validated finding contract shared with the application review engine."""

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ReviewComment(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    content: str = Field(min_length=1, max_length=60000)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    category: str = Field(default="other", max_length=50)
    severity: Literal["critical", "high", "medium", "low"] = "medium"
    existing_code: str = Field(default="", max_length=60000)
    suggestion_code: str = Field(default="", max_length=60000)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value):
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or ".git" in path.parts
            or "\\" in value
            or "\x00" in value
        ):
            raise ValueError("Finding path must stay inside the repository")
        return value

    @model_validator(mode="after")
    def ordered_lines(self):
        if self.end_line < self.start_line:
            raise ValueError("Finding end_line must follow start_line")
        return self
