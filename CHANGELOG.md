# Changelog

## Unreleased

## 0.1.8 - 2026-11-27

- Working-tree reviews handle unstaged file-to-directory and directory-to-file replacements and honor Git's `text=auto` line-ending rules. Staged reviews omit files marked only with `git add -N` and include genuinely staged empty files.
- Reviews find more callers of deleted or renamed files, including shell paths with escaped spaces and Python relative imports, multiline imports and calls through aliases. Assessment receives the deletion or rename verified against the merge base and head when checking an unchanged caller.
- Incremental reviews recheck previously reviewed Python importers when a module-level function is removed, switches between synchronous and asynchronous, or changes how existing arguments bind to positional, keyword or variadic parameters. Compatible optional additions still permit reuse. The first comparison after upgrading establishes a fresh baseline. Interrupted reviews retain their saved plan.
- Review planning reads selected source directly from its validated Git blob, and graph-enabled reviews collect neighboring imports in one pass across the import edges.

## 0.1.7 - 2026-11-26

- Incremental branch reviews recheck previously reviewed Python importers when a module-level function's required parameters change or it gains or loses `*args` or `**kwargs`. Fully reverting a reviewed file change triggers a fresh review of the remaining branch diff when coverage would otherwise be reused. The first comparison after upgrading establishes a new baseline.
- Caller discovery recognizes absolute Python imports of deleted or renamed modules under `src/`, including imports in unchanged files.
- New reviews count source lines the same way Git does, including in files with bare carriage returns or Unicode line separators. Resumed reviews keep their saved line numbering. Citation repair distinguishes an unknown source from a line range outside the supplied excerpt.
- Graph-enabled reviews report where optional graph context was dropped to fit a discovery request. JSON records the path, reason and affected review units in `code_graph.discovery_omissions`.
- Identical review candidates have separate assessment attempts. Resuming an interrupted assessment retry reuses its saved response when available.
- Inference distinguishes DNS lookup failures from endpoint policy rejections and can retry temporary DNS failures. Failures known to occur before dispatch release the token reservation. An uncertain dispatch still blocks replay.

## 0.1.6 - 2026-11-25

- Branch, staged and working-tree reviews look for surviving references to deleted or renamed files, including in eligible unchanged files. Previews and review reports list the affected caller ranges separately. When caller coverage is partial, the CLI returns exit code 2 and reports any validated findings.
- A changed branch tree with an eligible deletion or rename gets a full comparison so callers are checked again. Unchanged-tree skips can reuse complete caller coverage from a compatible run. The first comparison after upgrading needs a new baseline, and interrupted reviews keep their saved scope.
- Rebuild the optional code graph parser with `mortis graph build` before using `--graph` after upgrading. The new image uses Python 3.15, Tree-sitter 0.27 and updated C# and PHP grammars.
- Review workflow evaluation harnesses and historical results moved to the companion `mortis-benchmarks` repository. The source distribution retains the Engine regression tests and their fixtures.

## 0.1.5 - 2026-11-24

- Use `mortis ask --base main --question-file question.txt` to ask about a local Git comparison. Answers cite eligible diff hunks by path, commit and line range. Use `--path`, `--line` and `--side` together to select lines across one or more hunks.
- Use `mortis describe --base main` to draft a title, narrative and grouped changes from the same comparison. The draft stays local.
- Both commands use the selected connection and the base commit's review settings. JSON uses `mortis.ask/v1` or `mortis.describe/v1`. Inspect `status` for `insufficient_context` and `limited_context` when present for hunks omitted by the context budget.

## 0.1.4 - 2026-11-24

- Use `mortis review --base main --incremental` to analyze files changed since a compatible, fully covered branch review. The summary still covers the branch comparison, and reports distinguish new analysis from reused coverage.
- Use `--skip-unchanged` to skip model calls when a compatible review already covered the same Git tree. The saved report links to the previous run. Both options are off by default and can be set independently in `[review]` or passed as Python `overrides`.
- Keep the chosen scope when resuming a review. Incomplete or incompatible history triggers a full comparison, and previews continue to show the full scope without reading history.

## 0.1.3 - 2026-11-21

- Harden local snapshots and private history. Working-tree reviews read files without following symlinks or running Git filters. Files that cannot be read remain visible as coverage gaps, with Git change and rename metadata when available. Private key files remain excluded even with backup suffixes.
- Preserve readable code graph facts when another file fails extraction. Interrupted reviews reuse their saved graph context when available. Run `mortis graph build` again before using `--graph` after upgrading.
- Check the original and current paths when verifying findings. Withhold saved assessment evidence that current exclusions disallow. Security scans keep validated results and withhold the grade when required supporting evidence is unavailable.
- Validate custom `extra_body` options before inference. Reject overrides of engine-managed fields, including whole `reasoning`, `text` and `output_config` values. Bedrock bearer authentication cannot use AWS signing header names.
- Check the release tag against the triggering commit and `origin/main` before running tagged code. Reject private files with mixed-case extensions or backup suffixes in distribution archives.

## 0.1.2 - 2026-11-19

- Run eligible review and scan assessments and AI fix checks as slots open. Related candidates remain ordered, verification preserves report order, and older runs resume with their saved assessment policy.
- Release execution slots while requests wait for retries or temporary token reservations. Smaller recovery batches share the original analysis concurrency limit and budget.
- Connect to validated endpoint addresses while preserving TLS hostname checks. The private-endpoint option permits HTTP only for private or loopback addresses. Protect Bedrock signing headers and the session-scoped prompt cache key for OpenAI Responses.
- Validate security evidence paths and line ranges and reject graph facts without validated source. Malformed saved reports fail Markdown export with a clear error while preserving the original report.
- Require Trivy 0.76.2 for scans and update the optional code graph parser. Rerun `mortis graph build` before using `--graph` with this version.

## 0.1.1 - 2026-11-17

- Bundle the portable Mortis Engine skill in the wheel and source distribution. Use `mortis agent install` to install or update it for detected coding agents.
- Use Bedrock Converse for inference, including tool calls, structured output and reasoning replay. Bedrock connections need a model or inference profile that supports the Converse features in use.
- Honor explicit Anthropic output limits. With no Mortis output cap and an unlimited token budget, Anthropic uses the selected model's reported output capacity and Bedrock lets the service choose its default.
- Pass operation deadlines through to HTTP and Bedrock inference, removing the adapters' additional timeout cap.

## 0.1.0 - 2026-11-15

Initial Mortis Engine release, available on [PyPI](https://pypi.org/project/mortis-engine/0.1.0/).

- Review working-tree, staged, branch and full-tree Git snapshots with pinned evidence.
- Assess and verify findings; preserve reports, local history and incomplete coverage.
- Export JSON/Markdown reports, retry failed assessments and record local finding decisions.
- Scan repository security with Trivy and the selected AI connection.
- Extract optional isolated code graph context and support an opt-in second discovery pass.
- Package the Python library and `mortis` CLI for Python 3.13–3.15, with Python 3.15 as the development default.
