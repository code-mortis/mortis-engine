# Supporting test evidence

Review target selection and supporting evidence have separate purposes. Tests omitted
by the default review selection policy remain outside target coverage, but can be
requested as supporting text during analysis, assessment and fix verification. A changed
permitted test can appear in the assessment's changed-path inventory; its full source
is supplied only when requested. Fix verification may include its changed hunks and
accept requests for its original or current source.

Evidence comes from regular UTF-8 Git blobs at the pinned revisions. Mortis never imports
or executes those tests. Test inputs, setup and assertions can clarify a contract; their
presence does not establish that tests passed. An assertion for a successful retry does
not refute a claim about failed credential preflight. The no-child assertion added in
`d9fbb2dd9c7fe9ea284a0d877e03e1d79cefa34e` covers that failing condition and can be
considered alongside the implementation's preflight/child-creation order.

Explicit exclusions, secret and instruction-file exclusions, generated/dependency
exclusions, regular-file validation, redaction, source limits and both endpoints of
renames remain enforced. Test fixtures and snapshots that were excluded by default
remain excluded. No new configuration, output schema or database migration is needed.
Saved reports and pinned evidence are preserved; older retries cannot recover source
objects they never retained.

The offline regressions use a deterministic reviewer to verify evidence delivery and
citation validation. They do not establish real-provider verdict quality.
