"""Analysis of local Git revisions and working trees."""

# Public library functions are loaded on demand so gateway-only callers need no review runtime.
def __getattr__(name):
    if name in {"review_commits", "review_local"}:
        from . import service
        return getattr(service, name)
    raise AttributeError(name)


__all__ = ["review_commits", "review_local"]
