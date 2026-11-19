"""Strict portable security analysis contracts."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from mortis_engine.review.contracts import ReviewComment
from mortis_engine.schemas import StrictModel


def relative_path(value):
    if (not isinstance(value, str) or not value or value in {".", ".."} or len(value) > 2000
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("Security evidence contains an invalid repository path.")
    return ReviewComment.relative_path(value)


SecurityPath = Annotated[str, Field(min_length=1, max_length=2000), AfterValidator(relative_path)]


class SecurityCitation(StrictModel):
    revision: Literal["original", "current"] = "current"
    path: SecurityPath
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=20000)

    _ordered_lines = model_validator(mode="after")(ReviewComment.ordered_lines)


class SecurityProposal(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=50)
    severity: Literal["critical", "high", "medium", "low"]
    path: SecurityPath
    explanation: str = Field(min_length=1, max_length=12000)
    trigger: str = Field(min_length=1, max_length=4000)
    impact: str = Field(min_length=1, max_length=4000)
    remediation: str = Field(min_length=1, max_length=8000)
    verification: str = Field(min_length=1, max_length=4000)
    evidence: list[SecurityCitation] = Field(min_length=1, max_length=20)


class SecurityAnalysis(StrictModel):
    findings: list[SecurityProposal] = Field(max_length=100)
    required_paths: list[SecurityPath] = Field(max_length=10)


class SecurityAssessment(StrictModel):
    verdict: Literal["confirmed", "rejected", "uncertain"]
    finding_id: str | None
    explanation: str = Field(min_length=1, max_length=12000)
    evidence: list[SecurityCitation] = Field(max_length=20)
    required_paths: list[SecurityPath] = Field(max_length=10)


class SecurityFix(StrictModel):
    verdict: Literal["fixed", "still_present", "uncertain"]
    explanation: str = Field(min_length=1, max_length=12000)
    change_explanation: str = Field(max_length=12000)
    original_issue_present: bool
    evidence: list[SecurityCitation] = Field(max_length=20)
    required_paths: list[SecurityPath] = Field(max_length=10)


class SecurityEvidenceSelection(StrictModel):
    source_id: str = Field(min_length=1, max_length=40)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    _ordered_lines = model_validator(mode="after")(ReviewComment.ordered_lines)


class SecurityProposalSelection(SecurityProposal):
    evidence: list[SecurityEvidenceSelection] = Field(min_length=1, max_length=20)


class SecurityAnalysisSelection(SecurityAnalysis):
    findings: list[SecurityProposalSelection] = Field(max_length=100)


class SecurityAssessmentSelection(SecurityAssessment):
    evidence: list[SecurityEvidenceSelection] = Field(max_length=20)


class SecurityFixSelection(SecurityFix):
    evidence: list[SecurityEvidenceSelection] = Field(max_length=20)
