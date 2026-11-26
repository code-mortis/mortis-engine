"""Git blob coordinates for new reviews; retain historical coordinates on replay."""

from contextlib import contextmanager
from contextvars import ContextVar

POLICY = "git-lf/v1"
_policy = ContextVar("review_source_line_policy", default=POLICY)


@contextmanager
def use(policy):
    if policy not in (None, POLICY):
        raise ValueError("The saved source line policy is unsupported.")
    token = _policy.set(policy)
    try:
        yield
    finally:
        _policy.reset(token)


def split(text: str, *, keepends: bool = False) -> list[str]:
    if _policy.get() is None:
        return text.splitlines(keepends=keepends)
    # Git counts LF delimiters. CRLF is one ending; bare CR and U+2028 are data.
    lines = text.split("\n")
    tail = lines.pop()
    if keepends:
        result = [line + "\n" for line in lines]
    else:
        result = [line.removesuffix("\r") for line in lines]
    if tail:
        result.append(tail)
    return result
