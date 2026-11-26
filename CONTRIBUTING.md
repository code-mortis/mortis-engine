# Contributing to Mortis Engine

Mortis Engine is a local Python library and CLI. See [README.md](README.md) for
supported behavior and [AGENTS.md](AGENTS.md) for repository boundaries, module
ownership and invariants.

## Development

Use Python 3.15 for development and a current
[uv](https://docs.astral.sh/uv/getting-started/installation/) (CI uses 0.13.10).
The repository's `.python-version` selects Python 3.15. Then run:

```sh
git clone https://github.com/code-mortis/mortis-engine.git
cd mortis-engine
uv sync --locked --group dev
uv run --locked mortis --help
```

Git is required. The offline suite uses mocked providers, Trivy and Docker;
normal tests need no AI account or API keys. The native parser check installs
its separately pinned Python dependencies.

CI also checks Python 3.13 and 3.14 compatibility. Ruff targets the minimum
supported version so lint fixes do not introduce newer-only syntax.

## Checks

```sh
uv run --locked pytest -q
uv run --locked ruff check .
uv build
uv run --locked python scripts/check_dist.py
```

CI also installs the wheel and source archive in separate temporary environments
and runs `tests/smoke_test.py` outside the checkout. Release instructions include
the same installation checks in [docs/releasing.md](docs/releasing.md).

Tests must use temporary repositories and isolated configuration/state paths.
Never run them against personal profiles, saved runs or the OS credential vault.
Coordinate exclusive ownership of state-backed tests when multiple agents work
in this checkout. Live provider evaluations are separate and may incur charges.

## Changes and pull requests

Use issues for reproducible bugs and feature proposals. Discuss large behavior
changes before implementing them. Keep each PR focused and describe the problem,
resulting behavior and checks performed. Link the relevant issue when one exists.

Preserve versioned CLI/Python contracts, pinned revisions, saved history, explicit
provider selection, source exclusions and incomplete-coverage reporting. Include a
focused regression for non-trivial behavior changes. Use existing helpers and
dependencies. Update public documentation when supported behavior changes.

AI-assisted contributions are welcome when the contributor understands the change,
reviews it and verifies it. Personal agent settings, including the entire
`.codex/` directory, are local-only and are not required to contribute.

## Sharing evidence

Remove credentials, private URLs, personal paths and proprietary source from issues,
fixtures, reports and screenshots. Do not attach your configuration or history
database. Follow [SECURITY.md](SECURITY.md) for vulnerability reports.
