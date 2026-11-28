import pytest

from mortis_engine.security import clean_text


@pytest.mark.parametrize("token", ["abc+def/ghi=", "+abc", "/abc==", "~abc._-123", "abc.def_ghi-123",
                                   "ghs_sample+more/parts="])
def test_clean_text_redacts_complete_bearer_token(token):
    assert clean_text(f'Authorization: bEaReR {token}, next="value"') == (
        'Authorization: bEaReR [redacted], next="value"'
    )


def test_clean_text_redacts_multiple_bearer_tokens_and_known_secrets():
    assert clean_text("Bearer abc+/=~\nBearer /def==\nkey=fixture-secret", ("fixture-secret",)) == (
        "Bearer [redacted]\nBearer [redacted]\nkey=[redacted]"
    )
