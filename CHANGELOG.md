# Changelog

## Unreleased

- Rebuild the optional code graph parser with `mortis graph build` to use Python 3.15, Tree-sitter 0.27 and the updated C# and PHP grammars.

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
