# Changelog

## Unreleased

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
