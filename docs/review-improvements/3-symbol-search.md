# Pinned source navigation

New review analysis requests expose `search_source` alongside `read_source`, without
requiring the optional Docker graph parser. The model supplies one literal ASCII
identifier (`symbol`, at most 128 bytes). Mortis searches regular UTF-8 Git blobs at
the pinned head, including permitted unchanged files and supporting tests.

Search uses the same evidence admission policy as source reads: mandatory, explicit,
generated and unsupported-file exclusions still apply, including both endpoints of
renames. Only default test omissions are relaxed for supporting evidence. Repository
source, tests, instructions and hooks are never executed. Full source is redacted
before matching; credentials cannot be searched or returned through structural paths.

Results contain paths and line numbers, never source snippets or citation source IDs.
These are identifier occurrences, including homonyms, comments and strings, rather
than resolved references or proof of reachability. The model must request the relevant
source with `read_source` and verify it before citing it. Aliases and dynamic access
can hide references, and empty or truncated results cannot establish absence.

Each search inspects at most 128 eligible files, 2 MiB of source, and 64 KiB per file.
It returns at most 32 matches and 8 KiB, with explicit truncation and omitted-file
counts. There are at most two searches per source round, within the existing ten-call
and effort-dependent round budgets shared with reads and validation repairs. Git tree
inventory retains the existing 20 MB subprocess-output ceiling. Cancellation and
evidence authorization are checked during enumeration, source reads and result assembly.

The new source-tool policy is `bounded-search/v3`. Saved reviews without that marker
retain `bounded-reads/v2` request definitions and hashes; historical results stay intact.
No public report schema, provider configuration or graph dependency changes.

The dedicated offline regressions are `tests/review/test_source_navigation.py`.
The repository's exclusive verification runner owns their execution. Mock responses
exercise the real search/read analysis loop; these checks do not measure model quality.
