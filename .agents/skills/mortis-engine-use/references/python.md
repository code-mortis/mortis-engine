# Python integration

Use this when implementing a Python caller, rather than shelling out to the CLI.
Public results retain the same versioned dictionaries as CLI JSON. Avoid private
planning, transport and history APIs. Read the owning signatures in the installed
version before adding arguments.

## Preview without a connection

```python
from mortis_engine.review import preview_local

plan = preview_local("/path/to/repo", mode="commits", base="main", head="HEAD")
assert plan["schema_version"] == "mortis.review-preview/v1"
```

Modes are `worktree` (default), `staged`, `commits` and `all`. Optional preview
arguments include `effort`, `overrides` and `prompt_token_ceiling`. Preview does
not read profiles, credentials or saved history and does not invoke inference.

## Review with the selected saved profile

```python
from mortis_engine import config
from mortis_engine.review import review_local

name = "work"  # The profile selected for this task.
saved = config.profile(name)
connection = config.connection(name, saved)

result = review_local(
    "/path/to/repo",
    mode="commits",
    base="main",
    head="HEAD",
    connection=connection,
    get_credentials=lambda: config.credentials(saved),
    profile=name,
)
```

Keep credential resolution lazy through `get_credentials`; never print or
serialize its return value. Supplying `profile` records the selected profile
for later CLI recovery. `review_local` also accepts `effort`, `graph`, `resume`,
`progress`, `concurrency`, `overrides` and `on_plan`. Override keys use underscores,
for example `{"max_files": 100, "token_budget": 50000}`. Resume uses saved scope
and settings; do not combine it with new overrides or concurrency.

`review_commits(repository, *, base, head="HEAD", connection, get_credentials, ...)`
is the narrower comparison entry point. Use `review_local` when the caller needs
a saved profile name, other modes or resume.

## Other public workflows

`mortis_engine.operations.run` accepts `kind="scan"`, `"verify"`, `"summary"` or
`"background"`, plus the same selected `connection`, lazy `get_credentials` and
`profile`. `ref` defaults to `"HEAD"`. Verification and summary take
`finding_run=REVIEW_RUN_ID`; scans accept `rotations` and `concurrency`. These
are synchronous entry points; do not call them directly inside an already
running asyncio loop.

```python
from mortis_engine import operations

result = operations.run(
    "/path/to/repo",
    kind="verify",
    finding_run="REVIEW_RUN_ID",
    ref="HEAD",
    connection=connection,
    get_credentials=lambda: config.credentials(saved),
    profile=name,
)
```

Additional exports in `mortis_engine.operations`:

| Function | Required keyword arguments |
| --- | --- |
| `list_assessments(repository, ...)` | `run_id` |
| `retry_assessment(repository, ...)` | `run_id`, `candidate_id`, `connection`, `get_credentials`; or `resume` with connection/credentials |
| `list_findings(repository, ...)` | `run_id`; optional `include_history=True` |
| `triage(repository, ...)` | `run_id`, `finding_id`, `state`, `version`, `reason` |

Retries use saved evidence, preserve the original report and can be blocked by
unknown usage or stale history. Triage requires the user's decision and current
version, just as with the CLI. Inspect `status`, coverage, stage failures and
usage in returned dictionaries; receiving a result is not proof of completion.
