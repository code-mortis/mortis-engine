import pytest
from pydantic import ValidationError

from mortis_engine.security_scans import contracts, engine


@pytest.mark.parametrize("model, fields", [
    (contracts.SecurityCitation, {"path": "src/app.py", "quote": "value"}),
    (contracts.SecurityEvidenceSelection, {"source_id": "source_1"}),
])
def test_security_citation_ranges_must_be_ordered(model, fields):
    with pytest.raises(ValidationError, match="end_line must follow start_line"):
        model.model_validate({**fields, "start_line": 2, "end_line": 1}, strict=True)
    for end in (2, 3):
        result = model.model_validate({**fields, "start_line": 2, "end_line": end}, strict=True)
        assert result.start_line == 2 and result.end_line == end


MODELS = [contracts.SecurityAnalysis, contracts.SecurityAnalysisSelection,
          contracts.SecurityAssessment, contracts.SecurityAssessmentSelection,
          contracts.SecurityFix, contracts.SecurityFixSelection]


def payload(model, paths):
    if issubclass(model, contracts.SecurityAnalysis):
        return {"findings": [], "required_paths": paths}
    value = {"verdict": "uncertain", "explanation": "Need source", "evidence": [], "required_paths": paths}
    if issubclass(model, contracts.SecurityAssessment):
        return {**value, "finding_id": None}
    return {**value, "change_explanation": "", "original_issue_present": False}


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("path", ["", ".", "..", "../app.py", "src/../../app.py", "/etc/passwd",
    ".git/config", "src/.git/config", "src\\app.py", "C:\\app.py", "src/\x00app.py", "src/\napp.py",
    "src/\tapp.py", "src/\x7fapp.py", "x" * 2001])
def test_required_paths_enforce_repository_containment(model, path):
    with pytest.raises(ValidationError):
        model.model_validate(payload(model, [path]), strict=True)
    with pytest.raises(ValueError):
        engine.relative_path(path)


@pytest.mark.parametrize("model", MODELS)
def test_required_paths_keep_valid_paths_and_list_bound(model):
    paths = ["src/app.py", "tests/test app.py", "src/ñ.py"]
    assert model.model_validate(payload(model, paths), strict=True).required_paths == paths
    assert model.model_validate(payload(model, []), strict=True).required_paths == []
    with pytest.raises(ValidationError):
        model.model_validate(payload(model, ["src/app.py"] * 11), strict=True)
    assert model.model_json_schema()["properties"]["required_paths"]["items"]["maxLength"] == 2000
