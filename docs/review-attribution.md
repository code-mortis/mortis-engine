# Change attribution during candidate assessment

Candidate assessment can request up to ten current or merge-base files per pass when
the initial source and diff do not establish whether a defect was introduced by a
change. A logging-only edit should not report an unchanged failure that already
existed. A changed caller can introduce a reachable failure on an unchanged line.

New change-review contexts pin the actual common ancestor as `original_sha` and load
requested base files under `sources.original`. Final evidence keeps the existing
`original`/`current` revision names, repository paths and exact source line ranges.
The review report's `merge_base` and `head_sha` identify those revisions. Renames are
mapped from base to head; missing paths produce correction feedback, never source.

Assessment retains its three-pass bound, 100 source revision/path pairs and 1.5 MB
context limit. Repository exclusions, protected rename endpoints, regular text-file
checks and secret redaction also apply to base evidence. Source is never executed.
Unavailable or insufficient evidence leaves attribution uncertain.

`--all` continues to assess defects present at the selected revision without requiring
merge-base evidence. New runs save an attribution version before analysis. Older
plans and pinned assessment inputs retain their original request schema and prompts;
normal resume and targeted retry reuse saved candidate contexts. Prior reports and
unknown-consumption reservations remain unchanged.

Offline regressions exercise evidence delivery and validation with scripted model
responses. They do not measure live model attribution accuracy.
