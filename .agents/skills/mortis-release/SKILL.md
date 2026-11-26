---
name: mortis-release
description: Prepare or audit a Mortis Engine release. Reconcile package versions, changelog, repository and site documentation, and maintained skills with the release diff, then validate the wheel and source distribution. Use for Engine release preparation and readiness checks. Publication requires separate authorization.
---

# Mortis Engine release preparation

Prepare a reviewable release across this engine checkout and the documentation
site. The maintained skills live directly in `.agents/skills/`. The engine
package bundles the end-user `mortis-engine-use` skill.

## Establish the release scope

1. Read the current `AGENTS.md`, `README.md`, `CONTRIBUTING.md`,
   `docs/releasing.md`, `.github/workflows/ci.yml` and
   `.github/workflows/release.yml`. Inspect both repositories' status before
   editing and preserve unrelated work.
2. Use the requested stable version `X.Y.Z` and proposed tag `vX.Y.Z` when
   supplied. If the request only says "next release", recommend a version from
   the verified changes and state the assumption. Ask only if breaking changes
   or multiple release lines make that choice materially ambiguous. The current
   release guard accepts stable versions of the form `vMAJOR.MINOR.PATCH`.
3. Inspect local tags, history, the diff from the preceding release to HEAD, and
   staged, unstaged and untracked changes. Do not equate the highest tag with the
   last published release without checking its provenance. Use available hosted
   release/CI information when needed; label anything unverified. Do not switch
   branches, reset work or create tags just to obtain a comparison.
4. Locate `../mortis-site`, or use the maintainer's supplied site checkout.
   Read its guidance, `README.md`, `package.json` and current docs before editing.
   If unavailable or not writable, locate an accessible checkout or request the
   needed filesystem access while continuing engine preparation. Report site
   synchronization as pending until completed; do not claim readiness with that gap.
5. Read [the public-surface map](references/public-surface-map.md). Build the
   release notes from changed implementation, callers, tests and actual CLI help,
   including changes already committed since the preceding release. A commit
   subject or an old report alone is not proof of behavior.

## Synchronize the release

- Update `[project].version` in `pyproject.toml` and `__version__` in
  `src/mortis_engine/__init__.py` together. Refresh `uv.lock` with `uv lock` when
  package metadata changes; review the diff and avoid unrelated dependency
  upgrades. Check the lockfile's `mortis-engine` entry, CLI version and built
  package metadata against the target.
- Search tracked source, tests, scripts, workflows, examples, docs and skills for
  the previous product version. Update references that intentionally track the
  new release. Preserve historical changelog entries, frozen evaluation baselines,
  exact-revision evidence links, dependency versions and independent versioned
  contracts such as `mortis.review/v1`. Tests with synthetic version fixtures do
  not automatically need a new number. The site's private package version is not
  the engine version.
- Keep `CHANGELOG.md` in its existing style. Retain an `Unreleased` section for
  future work, then add the target version and preparation date with a concise
  summary and evidence-backed changes since the preceding release. Describe
  compatibility, security or installation changes when relevant. Do not describe
  the prepared version as already published or use a future tag as evidence.
- Update affected README commands, configuration and Python examples, repository
  guides and release instructions. Follow the surface map rather than rewriting
  every document. Keep source exclusions, private state, pinned revisions,
  partial coverage and unknown usage accurately described.
- Synchronize the affected guides under `src/content/docs/docs/engine/` in
  `mortis-site`. Review the checked-release references in the docs index and
  the Engine setup example on the homepage. Preserve existing guide URLs and
  heading bookmarks. Update the navigation and bookmark mapping when a route or
  heading moves. The surface map identifies the owning files.
- Treat the Engine, Action and App versions independently. If an Engine change
  affects the Action's pinned Engine, scan prerequisites or report consumption,
  verify compatibility against the Action revision that its guide recommends.
  Keep that guide tied to the version it documents. Record any required Action
  follow-up and preserve the App's own version and evidence links. Keep links
  and availability claims for a prepared release out of the deployed site until
  the referenced release and assets exist.
- Audit affected comparison claims against their cited versions and evidence.
  Evaluation suites live in `../mortis-benchmarks`. Use the recorded evidence for
  the matching revision and scope. Refer separately requested benchmark work
  to `mortis-benchmark` when available.

## Audit every maintained skill

Read every maintained skill under `.agents/skills/`, including the usage skill
and this release skill.

- For each skill, report either **changed** with the behavior and supporting
  source/tests, or **unchanged** with a concise reason. Distinguish updates already
  in the release range from edits made during preparation. Fix actual gaps;
  do not edit a skill just to mark it changed.
- Always check `.agents/skills/mortis-engine-use/SKILL.md` and its
  `references/configuration.md` and `references/python.md` against current CLI
  parsing, public Python signatures, configuration, recovery and output contracts.
  Cover new flags, requirements, exit codes and saved-run limitations when those
  changed. Audit this release skill against the current workflows and site
  structure. If `mortis-benchmark` is present, verify its checkout and suite
  references when evaluation layout changes.
- Keep `.agents/skills/` as the single source. Package only `mortis-engine-use`
  through the build configuration. Inspect `src/mortis_engine/cli/skills.py`,
  `scripts/check_dist.py`, `tests/test_skill_cli.py` and `tests/smoke_test.py` when
  installation or skill resources change. Update required asset checks if the
  skill gains or loses references, and ensure the entire skill ships in both
  archives and installs from each outside the checkout.
- Keep development skills project-local and outside runtime distributions.
  Do not refresh personal skill installations or add agent-specific metadata as
  a side effect of release preparation.

## Validate the prepared result

One agent owns local state-backed test execution at a time. Use fresh absolute
XDG configuration and state paths; never use personal profiles, saved runs or
the OS vault. For example, from the engine root:

```sh
release_check_dir=$(mktemp -d)
export XDG_CONFIG_HOME="$release_check_dir/config"
export XDG_STATE_HOME="$release_check_dir/state"
export AWS_EC2_METADATA_DISABLED=true
uv sync --locked --group dev
uv run --locked pytest -q
uv run --locked ruff check .
uv build --out-dir "$release_check_dir/dist"
uv run --locked python scripts/check_dist.py "$release_check_dir/dist"
```

Use Python 3.15 for development and preserve Python 3.13–3.15 compatibility.
Use fresh build output instead of deleting prior artifacts. Run focused
regressions first when behavior changes, then the complete release checks.
CI tests native graph extraction on Python 3.13, 3.14 and 3.15 with the parser's
pinned dependencies. Reproduce the Python 3.13 check from
`.github/workflows/ci.yml` for extraction changes and record the status of each
hosted Python job when available.

Install the wheel and source archive into separate temporary environments with
the locked runtime dependencies, then run `tests/smoke_test.py` with each installed
interpreter from outside the source checkout, as described in `docs/releasing.md`.
Verify CLI/library/metadata versions, parser assets and `mortis agent install`
for the detected agents.
Do not use personal home directories to smoke-test installation. If dependencies
were reused or any installation check was skipped, state that boundary explicitly.
Check neither archive contains development skills, personal configuration,
credentials or source-bearing reports.

In the site checkout, use its actual scripts. Currently:

```sh
bun install --frozen-lockfile
bun run check
bun run build
bun test
```

Build before the site tests, which inspect generated `dist/` pages. Check links,
rendered examples and preserved bookmarks in affected pages. Visually inspect
material layout or navigation changes using the available browser workflow.

The real `scripts/release_check.py TAG SHA` guard requires an existing tag naming
the exact checkout commit and ancestry in `origin/main`. It is a publication
guard, not a substitute for preparation checks. Run its regression tests as part
of pytest; do not manufacture refs or bypass the guard to make a local preparation
pass. Report the real tag/commit guard as pending until its prerequisites exist.

Review failures and fix causes within release scope. Do not disable a failing
check or silently substitute a partial run. Report local checks, hosted CI,
installed-package smoke tests and any live integration evidence separately.
Mocked checks do not establish live provider, Trivy or Docker behavior. Connection
probes, model discovery and paid evaluations are not routine release validation.

## Finish with a handoff

Provide the proposed package version/tag and preceding release, a concise summary
of changed engine and site files, the status of each maintained skill, the checks
and results, and any pending compatibility or publication work. Link the prepared
changelog section. State whether the release is prepared locally or published.
Pushing a `v*` tag triggers the workflow that validates, publishes to PyPI and
creates a GitHub Release.

Stop at the prepared diff unless the maintainer has separately authorized further
actions. This skill alone does not authorize commits, PR submission, tags, pushes,
PyPI publication, GitHub Release changes, deployments or resolving issue/review
threads. Preserve existing task authorization; do not ask for it again if it was
already explicitly granted.
