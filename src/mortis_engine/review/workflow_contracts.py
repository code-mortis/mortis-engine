"""Strict portable contracts for review workflow stages."""

from typing import Literal

from pydantic import Field, field_validator

from mortis_engine.schemas import StrictModel


class BackgroundResult(StrictModel):
    background: str = Field(min_length=1, max_length=20000)

    @field_validator("background")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("The model returned an empty project background.")
        return value.strip()

class SummaryGroup(StrictModel):
    title: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=1200)
    paths: list[str] = Field(min_length=1, max_length=1000)

class DiagramNode(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
    label: str = Field(min_length=1, max_length=120)
    paths: list[str] = Field(min_length=1, max_length=8)

class DiagramEdge(StrictModel):
    source: str
    target: str

class SummaryDiagram(StrictModel):
    nodes: list[DiagramNode] = Field(min_length=1, max_length=12)
    edges: list[DiagramEdge] = Field(max_length=20)

class SummaryResult(StrictModel):
    narrative: str = Field(min_length=1, max_length=3000)
    groups: list[SummaryGroup] = Field(max_length=12)
    diagram: SummaryDiagram | None = None

class VerificationEvidence(StrictModel):
    revision: Literal["original", "current"]
    path: str = Field(min_length=1, max_length=2000)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=60000)

class VerificationResult(StrictModel):
    verdict: Literal["fixed", "still_present", "uncertain"]
    original_issue_present: bool
    explanation: str = Field(min_length=1, max_length=10000)
    change_explanation: str = Field(max_length=10000)
    dismissal_recommended: bool
    evidence: list[VerificationEvidence] = Field(max_length=30)

class AssessmentResult(StrictModel):
    verdict: Literal["confirmed", "rejected", "uncertain"]
    relationship: Literal["new", "duplicate", "uncertain"]
    finding_id: str | None
    explanation: str = Field(min_length=1, max_length=10000)
    cause: str = Field(max_length=10000)
    trigger: str = Field(max_length=10000)
    consequence: str = Field(max_length=10000)
    history_explanation: str = Field(max_length=10000)
    contradicts_prior: bool
    changed_conditions: str = Field(max_length=10000)
    suggestion_supported: bool
    evidence: list[VerificationEvidence] = Field(max_length=30)
