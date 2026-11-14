"""Strict portable security analysis contracts."""

from typing import Literal

from pydantic import Field, field_validator

from mortis_engine.review.contracts import ReviewComment
from mortis_engine.schemas import StrictModel


class SecurityCitation(StrictModel):
    revision: Literal["original", "current"] = "current"
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=20000)

    _path = field_validator("path")(ReviewComment.relative_path)


class SecurityProposal(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=50)
    severity: Literal["critical", "high", "medium", "low"]
    path: str
    explanation: str = Field(min_length=1, max_length=12000)
    trigger: str = Field(min_length=1, max_length=4000)
    impact: str = Field(min_length=1, max_length=4000)
    remediation: str = Field(min_length=1, max_length=8000)
    verification: str = Field(min_length=1, max_length=4000)
    evidence: list[SecurityCitation] = Field(min_length=1, max_length=20)

    _path = field_validator("path")(ReviewComment.relative_path)


class SecurityAnalysis(StrictModel):
    findings: list[SecurityProposal] = Field(max_length=100)
    required_paths: list[str] = Field(max_length=10)


class SecurityAssessment(StrictModel):
    verdict: Literal["confirmed", "rejected", "uncertain"]
    finding_id: str | None
    explanation: str = Field(min_length=1, max_length=12000)
    evidence: list[SecurityCitation] = Field(max_length=20)
    required_paths: list[str] = Field(max_length=10)


class SecurityFix(StrictModel):
    verdict: Literal["fixed", "still_present", "uncertain"]
    explanation: str = Field(min_length=1, max_length=12000)
    change_explanation: str = Field(max_length=12000)
    original_issue_present: bool
    evidence: list[SecurityCitation] = Field(max_length=20)
    required_paths: list[str] = Field(max_length=10)


class SecurityEvidenceSelection(StrictModel):
    source_id: str = Field(min_length=1, max_length=40)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class SecurityProposalSelection(SecurityProposal):
    evidence: list[SecurityEvidenceSelection] = Field(min_length=1, max_length=20)


class SecurityAnalysisSelection(SecurityAnalysis):
    findings: list[SecurityProposalSelection] = Field(max_length=100)


class SecurityAssessmentSelection(SecurityAssessment):
    evidence: list[SecurityEvidenceSelection] = Field(max_length=20)


class SecurityFixSelection(SecurityFix):
    evidence: list[SecurityEvidenceSelection] = Field(max_length=20)
