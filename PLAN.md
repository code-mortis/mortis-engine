# Repository boundaries

Mortis Engine is the standalone Python library and `mortis` CLI. It reviews local
Git snapshots, assesses and verifies findings, generates reports, stores local run
history, and scans security with Trivy and the selected AI connection.

The Code Mortis app owns GitHub authorization, scheduling, hosted workers and
publishing analysis comments. A future companion Action can install a pinned CLI
release and consume its versioned reports. Those integrations are separate from
this repository's Python package release workflow.

Keep runtime state local. This project does not need PostgreSQL, Redis, Celery,
a server, or a frontend. The optional Docker parser extracts bounded context at
one pinned revision; it is not a persistent graph service.

The README documents supported behavior. Technical details live in `docs/`, and
`evals/review_improvements/` retains reproducible offline evaluation fixtures and
their frozen baseline. Offline tests establish contracts and failure handling;
they do not establish live provider quality or app integration acceptance.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and
[docs/releasing.md](docs/releasing.md) for release preparation and publication.
