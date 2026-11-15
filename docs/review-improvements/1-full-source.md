# Full-tree source requests

New `mortis review --all` runs send each assigned source excerpt once. Previously,
the full-tree snapshot compared every file against an empty tree, so its synthetic
added-file diff repeated the same source in every request. The planner also counted
that redundant diff and per-line hunk overhead when splitting and batching units.

The new source-only policy omits those synthetic diffs. Splitting and batching use
the assigned unit's JSON bytes, including escaped quotes, backslashes and newlines.
Source content, original line bounds and citation validation remain intact. Follow-up
requests and output-limit recovery retain this representation. Changed-file reviews
continue to send the original source and real diff.

New run plans save `full_tree_source_policy: "source-only/v1"` before dispatch.
Resuming a historical plan without this field deliberately keeps the previous units,
batch identities and request representation. This allows cached validated results and
unknown-consumption reservations to retain their original meaning. Old saved plans
and reports are never rewritten; an old full-tree run still contains duplicated source.

## Reproduce request sizes locally

From this checkout, using its installed development dependencies:

```sh
uv run python scripts/measure_full_tree_source.py --repo . \
  --head 205b77eae8a20a4c4f76d62d9c9e362c90c58197
```

This uses the same pinned revision, committed settings, file inventory, default unit
and batch bounds and offline fixture connection for both representations. `before`
selects the preserved historical planning path; `after` selects source-only planning.
The script asserts identical complete source coverage and prints unit counts, planned
batches, prepared request counts, source/diff byte totals, serialized provider-body
bytes and request hashes. It uses the same compact UTF-8 JSON serialization as the
gateway's request admission check. No provider, credentials, history database or saved
run is accessed. Git snapshot objects exist only in a temporary private repository.

The historical planner and request builder at `205b77e` match the pre-change source
of this improvement. The pinned committed settings are used without CLI overrides.
`--protocol openai-responses` or `--protocol anthropic` measures another HTTP envelope;
Anthropic requires a positive committed `max_output_tokens` setting. These are byte
measurements of initial analysis requests, not token estimates, billed costs, model
quality, or measured live latency. Graph enrichment and subsequent model-requested
source reads are excluded.

## Verified measurement

For exact revision `205b77eae8a20a4c4f76d62d9c9e362c90c58197`, OpenAI Chat
Completions serialization, fixture model `measurement`, graph disabled, committed
medium effort and default output allowance:

| Metric | Before | After |
| --- | ---: | ---: |
| Eligible files | 76 | 76 |
| Source units | 92 | 76 |
| Planned batches / prepared requests | 14 / 14 | 8 / 8 |
| Assigned source bytes | 580,175 | 580,175 |
| Assigned diff bytes | 609,248 | 0 |
| Serialized initial request bytes | 1,504,223 | 772,626 |
| Largest serialized request | 117,086 | 115,225 |

This removes 731,597 serialized request bytes (48.64%) with the same complete source
coverage. Request envelopes include schemas and framing in addition to the planner's
assigned-unit batch limit, so serialized requests can exceed 98,304 bytes. Gateway
admission still checks the complete encoded provider body before dispatch.

The dedicated regression and related planning, execution, command, snapshot and graph
tests passed together: 203 passed, 1 skipped. These checks used temporary repositories,
isolated state and mocked providers. Targeted Ruff checks also passed. Historical saved
plan tests reused cached analysis with unchanged request hashes, saved plans and attempt
records for both the missing-policy layout and the new source-only policy.
