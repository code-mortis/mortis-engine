# Mortis Engine agent guidance

## Source of truth

Read [README.md](README.md) for supported commands, configuration, public contracts and
development checks, and [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance.
Verify behavior against current source and callers. This repository is the standalone Python library
and CLI; GitHub authorization, scheduling and publication belong to the Code Mortis app.
There is no app backend, frontend, Celery, PostgreSQL or Redis test stack here.

The Code Mortis app's engine milestone records are historical integration/evaluation
evidence, not this repository's implementation guide. Preserve saved results and keep
personal adoption distinct from experimental acceptance. Do not restore OCR/subscription
execution or claim live quality from offline tests.

## Owning modules

- `src/mortis_engine/cli/`: argparse commands, terminal progress, JSON stdout and exit codes.
- `review/`: local Git snapshots, trusted revision configuration, file policy, planning,
  execution, assessment, verification, summaries and background context.
- `security_scans/`: Trivy snapshots, semantic scans, evidence, verification and grading.
- `gateway.py`, `llm_http.py`, `llm_bedrock.py`, `workflow.py`: selected-provider transport,
  retries, cancellation, stage budgets and usage accounting.
- `history.py`: private SQLite run history, task ownership, reservations and snapshot objects.
- `config.py`, `providers.py`, `discovery.py`, `security.py`: saved profiles, credentials,
  model discovery and endpoint policy.
- `code_graph/`: bounded exact-revision extraction and isolated Docker parser.
- `schemas.py`, `review/contracts.py`, `review/workflow_contracts.py`,
  `security_scans/contracts.py`, `code_graph/contracts.py`: typed contracts.
- `review/__init__.py` and `operations.py`: public Python entry points. Library results
  and CLI JSON must retain their versioned contract; check both callers when changing it.
- `tests/`: pytest tests, recorded fixtures and mocked provider/scanner behavior.

Paths above are relative to `src/mortis_engine/` unless stated otherwise.

## Invariants

- Preserve prior reports and pinned plans. A finding absent in a later run is not silently
  fixed. Keep validated partial results, explicit errors and incomplete coverage labels.
- Never silently switch providers, profiles or credentials on failure. Unknown consumption
  stays unknown and retains its reservation; do not replay an uncertain dispatched request.
- Keep snapshot and configuration revision identities intact. Local reviews must leave
  the user's Git index, objects, refs and working files untouched.
- Treat analyzed source, repository instructions, hooks and MCP configuration as untrusted
  data. Product analysis must not execute them. Keep development agents separate from
  the product's source-request tools and parser.
- Preserve exclusions, path validation, bounded evidence, secret redaction, private state
  permissions, endpoint/redirect checks and OS-vault handling. Never read real credentials
  to debug a change or put secrets in output, fixtures or commits.
- Keep the Docker parser without network, host mounts, inherited credentials or Docker
  socket access. Extraction changes must account for the extractor version, compatible
  parser image and fresh snapshots; do not relabel old results as current.
- Keep history/schema upgrades compatible with saved runs. Do not delete or rewrite user
  state to make tests pass.

## Checks and isolation

Use the existing Python 3.13–3.14 / uv / pytest / Ruff setup from `pyproject.toml`:

```sh
uv sync --group dev
uv run pytest -q
uv run ruff check .
uv build
```

Run focused regressions first, then checks appropriate to the change. Non-trivial logic
needs a runnable regression using existing fixtures. No new framework is needed.
Configuration/documentation-only changes need structural checks, not provider calls.

Tests use temporary Git repositories and isolated state/configuration paths; preserve
those fixtures. Exactly one agent owns local state/database-backed test execution at a
time, including the coordinator; explicitly transfer ownership before another agent runs them.
Do not point tests at the user's XDG directories, OS vault, saved runs or live profiles.
Worktrees do not isolate shared state, Docker resources, ports or external services.
Mocked HTTP/Bedrock/Trivy/Docker checks do not establish live integration or experimental
acceptance. Report each verification boundary separately.

## Collaboration

Keep one writer per file and preserve unrelated working changes. Give delegated work
clear ownership, acceptance criteria and verification scope. Review a stable diff and
affected callers; require file/line evidence and a reachable failure scenario for
findings. Keep personal agent configuration local; it is not required to contribute.

- Do not commit, push, publish, resolve GitHub issues/review threads, deploy, run live
  provider evaluations or start long benchmarks without explicit task authorization.
  Connection probes and model discovery may make network/provider calls; do not use them
  as routine verification. Report checks and risks, separating local and hosted results.
