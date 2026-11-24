# Mortis release surface map

Paths in the engine table are relative to the engine repository root. Read the
current owning modules and affected callers before changing their documentation.

| Changed surface | Release review targets |
| --- | --- |
| Product version and requirements | `pyproject.toml`, `src/mortis_engine/__init__.py`, `uv.lock`, `.python-version`, README installation requirements, applicable version fixtures and workflows |
| CLI commands, flags, output or exits | `src/mortis_engine/cli/`, `tests/test_cli.py`, affected CLI tests, README, site CLI guide and usage skill |
| Public Python API and result schemas | `review/__init__.py`, `operations.py`, owning contracts/callers/tests, README Python entry points, site Python guide, usage skill Python reference |
| Review scope, configuration and planning | `review/` settings/snapshots/planning, `mortis.toml`, README, relevant `docs/` guides, site reviews/configuration/Python examples, usage skill configuration reference |
| Verification, retries, triage and saved history | `history.py`, `assessments.py`, `findings.py`, review workflows, their regressions, CLI/Python docs and usage skill recovery guidance; preserve old results and unknown-usage reservations |
| Provider transport, credentials and endpoint policy | `config.py`, `providers.py`, `security.py`, gateway/HTTP/Bedrock/workflow modules, connection docs and usage skill; never inspect real credentials or add routine probes |
| Security scanning and ratings | `security_scans/`, scanner requirements, scan contracts/tests, README and site scan examples. Check compatibility with the installer at the Action revision recommended by the site when the Trivy requirement changes. |
| Code graph extraction | `code_graph/` extractor version, parser image/requirements and contracts/tests, graph limits and docs; keep parser isolation and old snapshot identities intact |
| Packaging and skill installation | `src/mortis_engine/cli/skills.py`, `.agents/skills/mortis-engine-use/` and its build inclusion, `scripts/check_dist.py`, `tests/test_packaging.py`, `tests/test_skill_cli.py` and `tests/smoke_test.py` |
| Release process and CI | `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `.github/dependabot.yml`, `scripts/release_check.py`, `tests/test_release.py`, `docs/releasing.md`, README development checks and this development skill |
| Any user-facing change | `CHANGELOG.md`, affected README/site sections, relevant repository docs and every affected maintained skill |

Module paths without `src/mortis_engine/` in the table refer to that package.
Build inclusion is limited to `.agents/skills/mortis-engine-use/`. Use
`pyproject.toml` and `scripts/check_dist.py` for the current archive contents.
Evaluation suites moved to `../mortis-benchmarks`. They have their own pinned
evidence and stay outside the Engine distributions.

Preserve the maintainer's uv requirement and CI/release pins. If Dependabot
ships an older uv, investigate the updater's supported configuration and report
an upstream limitation when applicable. Do not lower project requirements or
downgrade tools to make an updater pass.

## Documentation site

The default checkout is `../mortis-site`. Paths in this table are relative to
that checkout. Use the current per-product guides as the canonical pages.

| Engine change | Site files to inspect |
| --- | --- |
| Engine index and checked release references | `src/content/docs/docs/engine/index.mdx`, `src/content/docs/docs/index.mdx` |
| CLI installation, connection and skill installation | `src/content/docs/docs/engine/cli.md` |
| Review scopes, incremental reviews and diff questions/descriptions | `src/content/docs/docs/engine/reviews.md` |
| Trusted revision configuration and examples | `src/content/docs/docs/engine/configuration.md` |
| Scan setup, Trivy and coverage | `src/content/docs/docs/engine/scans.md` |
| Reports, saved runs and follow-up actions | `src/content/docs/docs/engine/saved-runs.md` |
| Python installation, public entry points and report handling | `src/content/docs/docs/engine/python.md` |
| Product navigation, moved routes and headings | `src/data/doc-sections.ts`, `astro.config.mjs`, `src/data/doc-bookmarks.ts`, `src/pages/docs/[oldGuide].astro`, affected guide frontmatter and `tests/site.test.ts` |
| Homepage commands and setup links | `src/components/ReviewDesk.astro`, `src/components/review-desk/content.ts`, `src/components/review-desk/Setup.astro` |
| Comparisons or evidence links | `src/content/comparison.md`, `src/content/comparison-tools.ts`, `src/pages/comparison.astro`; review any version assertions in `tests/site.test.ts` |
| App or GitHub Action compatibility | `src/content/docs/docs/app/` and `src/content/docs/docs/action/`; follow each product's actual Engine dependency, published release and documented limitations |
| Site validation | `package.json`, `bun.lock`, `tests/site.test.ts`; build before testing generated routes and headings |

Preserve the existing Astro/Starlight structure, typography and content voice.
Verify code examples against engine source and deterministic local checks. Check
dated comparison evidence before changing its date or pinned revision. Add
release-specific pages only when the site already has a matching content surface
or the maintainer explicitly requested one.

## Publication boundary

The current engine workflow accepts stable `vMAJOR.MINOR.PATCH` tags, validates the
exact triggering/mainline commit, runs CI for that commit, publishes the validated
distributions to PyPI through the `pypi` environment, then creates the GitHub Release
with generated notes and those same assets. The documentation site is a separate
checkout and deployment; changing engine files does not update or publish it.

For an already published release with a failed GitHub Release job, follow
`docs/releasing.md`: reuse the retained validated artifacts and repair only the
failed stage when authorized. Never move an existing published tag, overwrite a
PyPI version or repeat a successful publication to repair a later failure.
