import pytest
from pydantic import ValidationError

from mortis_engine.review.service import effective_connection
from mortis_engine.review.settings import RepoSettings
from mortis_engine.security_scans.service import ScanOptions


@pytest.mark.parametrize("model", [RepoSettings, ScanOptions])
def test_zero_limits_normalize_without_losing_unlimited_output(model):
    fields = {key: 0 for key in model.model_fields if key.endswith("token_budget") or key in {
        "max_output_tokens", "timeout_seconds", "summary_timeout_seconds", "max_files"}}
    value = model.model_validate(fields).model_dump()
    assert value["max_output_tokens"] == 0
    assert all(value[key] is None for key in fields if key != "max_output_tokens")
    assert model().max_output_tokens is None


@pytest.mark.parametrize("model", [RepoSettings, ScanOptions])
@pytest.mark.parametrize("value", [-1, True, 1.5, "0"])
def test_invalid_output_and_budget_values_fail_at_configuration_boundary(model, value):
    for field in ("max_output_tokens", "token_budget", "timeout_seconds"):
        with pytest.raises(ValidationError):
            model.model_validate({field: value})


@pytest.mark.parametrize("protocol", ["anthropic", "anthropic-bedrock"])
def test_unlimited_is_supported_for_anthropic_transports(protocol):
    assert effective_connection({"protocol": protocol, "model": "selected-model"}, {"max_output_tokens": 0})["model"] == "selected-model"
