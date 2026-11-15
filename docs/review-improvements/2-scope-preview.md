# Local review scope preview

`mortis review --preview` builds the same immutable snapshot and calls the same
planner as review execution. It prints selected paths, exclusions with reasons,
admission errors, assigned source/diff bytes, source units and planned batches.
It reads committed repository settings, with the ordinary review limit overrides.
It does not load saved profiles, read credentials, open history, retain snapshot
objects, create a run or dispatch analysis. Temporary Git objects are cleaned up.

```sh
mortis review --preview
mortis review --staged --preview
mortis review --base main --head HEAD --preview --format json
mortis review --all --head HEAD --preview --max-files 0
```

The public Python entry point returns the same `mortis.review-preview/v1` result:

```python
from mortis_engine.review import preview_local

preview = preview_local(".", mode="commits", base="main", head="HEAD")
print(preview["totals"])
```

`mode` accepts `worktree` (the default), `staged`, `commits` and `all`, with the
same revision and trusted-configuration semantics as `review_local`. A preview
never pins a later execution: if files, revisions, settings or bounds change,
run a new preview. Worktree snapshots use the same committed path policy before
reading local source, including mandatory secret exclusions.

## Sizing and parity

The default unit bound is 49,152 bytes and the batch bound is 98,304 bytes.
An execution connection may specify `prompt_token_ceiling`, which reduces the
unit bound. Supply that value explicitly to reproduce its planning without
reading the connection:

```sh
mortis review --all --preview --prompt-token-ceiling 10000 --format json
```

Python accepts `prompt_token_ceiling=10000`. The valid range is 4,096 through
2,000,000 tokens, matching connection validation. The result records the explicit
assumption and effective bounds. Parity requires identical input revisions,
settings, overrides and unit bounds. The regression compares preview paths,
errors, unit keys and ranges, byte totals and batches with the real service's
execution plan for all four modes, with default and reduced unit bounds.

`source_bytes` and `diff_bytes` count UTF-8 bytes assigned to planned units,
including repeated context when splitting requires it. `assigned_unit_json_bytes`
adds the exact JSON representation of each assigned unit, including its rules
and metadata. It excludes request framing, schemas, citation metadata and provider
envelopes. These values are neither token estimates nor billed costs. They contain
counts only; the preview does not print source, diffs, rules or credentials.

`planned_batches` counts the real planner's batches. Provider-specific request
admission can split or reject them later; graph enrichment, model-requested source,
recovery, assessments, verification and summary requests are outside this count.
No provider-specific estimate is made. The CLI rejects `--connection`, `--resume`,
`--graph`, `--second-pass`, `--progress`, `--concurrency` and `--output` with preview;
redirect stdout to save its text or JSON. `--prompt-token-ceiling` requires preview.

A file-limit block retains selected paths but leaves units and batches empty;
per-file byte counts are `null` because admission did not load their source.
An oversized or otherwise unreadable selected file keeps its error and no units.
Such a preview returns exit code 2. A complete or empty preview returns 0; invalid
inputs return 1. These statuses make no assertion about successful analysis.

## Exclusions and generated reports

Reasons come from the planner's own policy: `mandatory_exclusion`,
`configured_exclusion`, `rule_exclusion`, `unsupported_source`, `default_directory`,
`default_test` or `default_pattern`. Git inventory exclusions retain `binary` and
`deleted`. Rename eligibility checks both old and new paths. Supporting-evidence
admission can differ from review-target selection; preview reports review targets.

Generated review reports can be eligible source when committed or left unignored.
At `c1b8a2f`, `review-main.json` (251,831 raw bytes) and `review-main.md` (88,978 raw
bytes) were committed alongside the implementation change. The JSON describes an
earlier full-tree run at `205b77e`, so its coverage is historical and does not show
how much context those newly added report files consume in a later review.
Inspect that later change directly:

```sh
mortis review --preview --base 'c1b8a2f^' --head c1b8a2f --format json
```

The preview exposes report-file units and bytes instead of silently changing the
file policy. To keep future generated artifacts outside worktree review, place them
under an ignored output directory such as `reports/`. To exclude already tracked
artifacts, add their paths to `[review].exclusions` on the trusted configuration
revision. An ignore rule alone does not exclude a tracked file.

## Measured historical scope

The offline preview of `c1b8a2f^..c1b8a2f` with default unit/batch bounds selects
5 files, excludes 3 files and plans 29 units in 10 batches. Generated reports
account for 26 of the 29 units:

| Selected file | Units | Source bytes | Diff bytes | Assigned unit JSON bytes |
| --- | ---: | ---: | ---: | ---: |
| `review-main.json` | 18 | 251,831 | 258,676 | 558,320 |
| `review-main.md` | 8 | 88,978 | 92,157 | 194,868 |
| Reports combined | 26 | 340,809 | 350,833 | 753,188 |
| Complete change | 29 | 408,971 | 359,855 | 836,154 |

These are measured planner assignments, using committed settings and no saved
connection. The reports occupy approximately 90.1% of assigned unit JSON bytes.
The preview makes that existing scope visible without rewriting the historical
reports, changing eligibility or making provider requests.
