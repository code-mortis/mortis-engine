import json
from collections import UserList
from types import MappingProxyType

import pytest

from mortis_engine.review.context import quote_matches, sanitize


@pytest.mark.parametrize("container", [list, tuple, set, frozenset, UserList])
def test_sanitize_nested_containers_before_json_serialization(container):
    secret = "fixture-context-credential"
    value = MappingProxyType({"source": [container([secret]), {"nested": (secret,)}]})
    cleaned = sanitize(value, {"api_key": secret})
    assert cleaned == {"source": [["[redacted]"], {"nested": ["[redacted]"]}]}
    assert secret not in json.dumps(cleaned)
    with pytest.raises(ValueError, match="structural"):
        sanitize({"required_paths": container([secret])}, {"api_key": secret})


@pytest.mark.parametrize("value", [b"fixture-secret", bytearray(b"fixture-secret"),
                                   memoryview(b"fixture-secret"), object()])
def test_sanitize_rejects_unsupported_context_without_echoing_it(value):
    with pytest.raises(ValueError, match="Unsupported model context type") as error:
        sanitize({"nested": (value,)}, {"api_key": "fixture-secret"})
    assert "fixture-secret" not in str(error.value)


def test_sanitize_preserves_json_scalar_values():
    value = {"values": [None, True, False, 42, 0.5]}
    assert sanitize(value, {}) == value


@pytest.mark.parametrize("token", [
    "ghs_" + "a" * 36,
    "ghs_12345_" + "a" * 35 + "." + "b-_" * 100 + "." + "c_" * 86 + "-",
], ids=["classic", "stateless"])
@pytest.mark.parametrize("credentials", [{}, {"extra_headers": {"X-Version": "1"}}])
def test_sanitize_redacts_unknown_installation_tokens_in_diffs(token, credentials):
    context = {"diff": f'-key="{token}"\n+message=Expired token {token}.\n'}
    assert sanitize(context, credentials) == {"diff": '-key="[redacted]"\n+message=Expired token [redacted].\n'}


@pytest.mark.parametrize(("start", "end", "quote"), [
    (1, 1, ""), (1, 1, " \n"), (1, 1, None), (1, 1, 1),
    (0, 1, "first"), (-1, 2, "second"), (2, 1, "first"),
    (1, 3, "first"), (3, 3, "second"), (True, 1, "first"),
    (1, True, "first"), (1.0, 1, "first"), (1, "1", "first"),
    (None, 1, "first"), (1, 1, "second"),
])
def test_quote_matches_rejects_invalid_citations(start, end, quote):
    assert not quote_matches("first\nsecond\n", start, end, quote)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_quote_matches_preserves_original_and_rendered_line_endings(newline):
    source = newline.join(["first", "second", "third", ""])
    assert quote_matches(source, 2, 3, f"second{newline}third{newline}")
    assert quote_matches(source, 2, 3, "second\nthird")
    assert not quote_matches(source, 2, 3, "first")
    assert not quote_matches("", 1, 1, "anything")
