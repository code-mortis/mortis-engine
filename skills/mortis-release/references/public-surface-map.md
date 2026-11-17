# Mortis release surface map

Paths in the engine table are relative to the engine repository root. Read the
current owning modules and affected callers before changing their documentation.

| Changed surface | Release review targets |
| --- | --- |
| Product version and requirements | `pyproject.toml`, `src/mortis_engine/__init__.py`, `uv.lock`, `.python-version`, README installation requirements, applicable version fixtures and workflows |
| CLI commands, flags, output or exits | `src/mortis_engine/cli/`, `tests/test_cli.py`, affected CLI tests, README, site CLI guide and usage skill |
| Public Python API and result schemas | `review/__init__.py`, `operations.py`, owning contracts/callers/tests, README Python entry points, site library guide, usage skill Python reference |
| Review scope, configuration and planning | `review/` settings/snapshots/planning, `mortis.toml`, README, relevant `docs/` guides, site CLI/library examples, usage skill configuration reference |
| Verification, retries, triage and saved history | `history.py`, `assessments.py`, `findings.py`, review workflows, their regressions, CLI/Python docs and usage skill recovery guidance; preserve old results and unknown-usage reservations |
| Provider transport, credentials and endpoint policy | `config.py`, `providers.py`, `security.py`, gateway/HTTP/Bedrock/workflow modules, connection docs and usage skill; never inspect real credentials or add routine probes |
| Security scanning and ratings | `security_scans/`, scanner requirements, scan contracts/tests, README and site scan examples; distinguish Trivy results, AI assessment, retained findings and incomplete grading |
| Code graph extraction | `code_graph/` extractor version, parser image/requirements and contracts/tests, graph limits and docs; keep parser isolation and old snapshot identities intact |
| Packaging and skill installation | `src/mortis_engine/cli/skills.py`, canonical usage skill and all references, `skills/mortis-engine-use/` compatibility links, `scripts/check_dist.py`, packaging/installer tests and `tests/smoke_test.py` |
| Release process and CI | `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `scripts/release_check.py`, `tests/test_release.py`, `docs/releasing.md`, README development checks and this development skill |
| Any user-facing change | `CHANGELOG.md`, affected README/site sections, relevant repository docs and every affected maintained skill |

Module paths without `src/mortis_engine/` in the table refer to that package.
Development skills under root `skills/` and their `.agents/skills/` and
`.claude/skills/` aliases stay outside the packaged skill data. Do not copy these
development instructions into the customer-facing usage skill.

## Documentation site

Default checkout: `../mortis-site`. Inspect its Git status and guidance separately.
Do not assume this checkout exists or silently omit it from readiness reporting.

| Engine change | Site files to inspect |
| --- | --- |
| CLI installation, connection, scopes, limits, output and follow-ups | `src/content/docs/docs/cli.md` |
| Python installation, public entry points and report handling | `src/content/docs/docs/library.md` |
| Documentation entry points or new guides | `src/content/docs/docs/index.mdx`, `astro.config.mjs`, `src/components/SiteNav.astro` and affected guide frontmatter |
| Landing commands or supported capabilities | `src/pages/index.astro`, `src/components/ReviewCommands.tsx`; inspect `src/pages/concepts/[direction].astro` if its maintained examples changed |
| Comparisons or evidence links | `src/content/comparison.md`, `src/content/comparison-tools.json`, `src/pages/comparison.astro` |
| App or GitHub Action integration | `src/content/docs/docs/app.md`, `src/content/docs/docs/github-action.md`; retain their actual availability labels |
| Site validation | `package.json`, `bun.lock`, `tests/site.test.ts`; current tests read the built site, so build first |

Preserve the existing Astro/Starlight structure, typography and content voice.
Verify code examples against engine source and deterministic local checks. Check
dated comparison evidence before changing its date or pinned revision. Add
release-specific pages only when the site already has a matching content surface
or the maintainer explicitly requested one.

## Publication boundary

Read the workflow rather than assuming Noodle's release machinery applies. The
current engine workflow accepts stable `vMAJOR.MINOR.PATCH` tags, validates the
exact triggering/mainline commit, runs CI for that commit, publishes the validated
distributions to PyPI through the `pypi` environment, then creates the GitHub Release
with generated notes and those same assets. The documentation site is a separate
checkout and deployment; changing engine files does not update or publish it.

For an already published release with a failed GitHub Release job, follow
`docs/releasing.md`: reuse the retained validated artifacts and repair only the
failed stage when authorized. Never move an existing published tag, overwrite a
PyPI version or repeat a successful publication to repair a later failure.
