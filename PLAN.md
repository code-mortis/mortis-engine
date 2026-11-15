# Plan del CLI de Mortis Engine

El CLI instalable ejecuta el análisis sobre Git local usando la conexión guardada. El README documenta los comandos y las opciones de configuración.

## Repositorios

1. **mortis-engine** contiene el CLI, los transportes de IA, la revisión, la evaluación de candidatos, la verificación, los resúmenes, el historial y los scans con Trivy. La librería y el CLI devuelven informes JSON versionados.
2. **Code Mortis app** podrá consumir esos contratos y encargarse de la autorización de GitHub, la programación de trabajos y la publicación. La adopción requiere comparar resultados sobre entradas idénticas.
3. **La GitHub Action** podrá instalar una versión fijada del CLI, revisar el checkout y convertir los hallazgos confirmados en comentarios del PR. El publicador deberá respetar los SHA y la cobertura del informe.

Las pruebas del CLI comprueban contratos, cobertura, límites y manejo de fallos. La evaluación comparativa de calidad con proveedores reales requiere una ejecución independiente.

## Implementation plan: four remaining engine capabilities

Status: implemented locally. Offline verification: 547 tests passed, one optional
tree-sitter test skipped; Ruff and wheel/source builds passed. Source baseline:
`d759f31` (2026-11-15). Live provider quality and app integration remain separate.

Implement these in the standalone library and expose them through the CLI:

1. Configurable graph extraction limits.
2. Retry one failed candidate assessment.
3. Local finding triage with decision history.
4. Coordinate inference concurrency across local processes.

Persistent graph indexing, PostgreSQL storage and graph queries stay in the Code
Mortis app. The CLI continues to extract optional graph context for one pinned
revision. App integration, GitHub publication and distributed workers are outside
this plan. The three separate CLI improvements (exclusion previews, summary
groups/diagrams in the main report, and per-run exclusions) remain separate work.

### Shared rules

- Preserve original run plans, reports, evidence, coverage and inference accounting.
  Follow-up decisions and retry results are separate records with links to their origin.
- Reuse SQLite history, retained Git objects, validators and transport cleanup. No new
  database service, daemon or dependency is required.
- Keep CLI JSON and public Python results versioned. Existing `mortis runs RUN_ID`
  continues to return the saved report; a current findings view is explicitly separate.
- Add tables transactionally and idempotently. Test upgrades from existing histories;
  never backfill missing evidence by guessing or rewrite old reports to fit a new schema.
- Maintain source exclusions, redaction, private state permissions and terminal escaping.
  Local state operations must not require a working AI connection or read its secrets.
- README examples describe the implemented commands. Requirements below document the
  implementation's boundaries and acceptance checks.

### 1. Configurable graph extraction limits

**Current owners:** `review/settings.py`, `code_graph/contracts.py`,
`code_graph/__init__.py`, `review/service.py`, `cli/review.py`, `cli/config.py`.
`CodeGraphLimits` already validates these values, and extraction already reads
`options.code_graph_limits`; normal repository settings do not expose that field.

Expose the existing contract as a nested setting:

```toml
[review.code_graph_limits]
max_files = 1000
max_file_bytes = 1048576
max_source_bytes = 8388608
```

- Keep defaults of 1,000 files, 1 MiB per file and 8 MiB total. Keep existing caps of
  5,000 files, 4 MiB per file and 32 MiB total, and require file bytes <= total bytes.
  Values are positive integers; these isolation limits do not support `0 = unlimited`.
- Add `--graph-max-files`, `--graph-max-file-bytes` and `--graph-max-source-bytes` to
  `mortis review`. Merge individual overrides with committed values before validation.
  Require `--graph` for these overrides; configuring limits alone does not enable parsing.
- Show effective values in `mortis config show`. Support the same nested values through
  the existing public Python `overrides` argument, without adding a second config path.
- Pin effective limits in new run plans. Resume restores them and rejects new graph
  overrides. Legacy plans without this field use the fixed v1 defaults above, even if
  defaults change in a later release.
- Preserve current behavior when extraction exceeds its limits: record graph context
  as unavailable and continue ordinary review. Do not silently truncate or broaden access.
  This does not add graph processing to security scans.

**Acceptance:** TOML and Python/CLI overrides reach the mocked parser; partial nested
overrides preserve other settings; invalid types/ranges fail before dispatch; resumed
and legacy plans retain their limits; exclusion, rename and parser isolation tests pass.
Extend `tests/code_graph/test_graph.py`, review command/planning tests and config tests.

### 2. Retry one failed candidate assessment

**Current owners:** `review/workflows.py`, `review/assessment.py`,
`security_scans/service.py`, `security_scans/inference.py`, `workflow.py`, `history.py`,
`operations.py`, `cli/analysis.py`. Completed runs currently return their saved report
on resume. Review candidates have IDs; failed security assessments need durable
candidate records rather than only a failed unit and error message.

```sh
mortis assessments --run RUN_ID
mortis assess --run RUN_ID --candidate CANDIDATE_ID --progress
mortis assess --resume RETRY_RUN_ID --progress
```

- Support failed AI assessments from both reviews and security scans. The listing shows
  candidate ID, path, status, error and retry eligibility, including why a retry is blocked.
  Successful or merely uncertain assessments are not retry targets in this phase.
- Persist a versioned, sanitized assessment input before its first dispatch: candidate,
  source/configuration revisions, effective settings, relevant history, graph hints if
  used, and validator/input version. Retain required source through existing snapshot
  storage. Expose stable security candidate IDs without changing existing report fields.
- Reuse the existing assessment and evidence-validation code for just that candidate,
  including bounded requests for more evidence. Do not repeat file analysis, Trivy,
  verification or summary generation.
- Create a linked follow-up run with its own immutable result and attempt records.
  Preserve provider/model, effort, exclusions and evidence from the original input;
  refuse changed connections or unavailable/incompatible evidence before any AI call.
- Give an explicit retry a fresh invocation deadline and assessment budget inherited
  from the pinned settings. Report its cost separately and expose cumulative lineage
  usage. Do not erase or reset the original attempt's charges or reservations.
- Unknown consumption in the relevant assessment lineage blocks retry. For security,
  also respect unknown consumption under the original shared scan budget. There is no
  force-replay option. A new child run must not bypass these checks.
- Claim the candidate for the entire operation to prevent concurrent duplicate retries.
  Resume an interrupted retry through its own ID. Once a retry succeeds, another command
  for that candidate returns the saved success without spending more tokens.
- Introduce the read-only `mortis findings --run RUN_ID` view in this phase; triage will
  extend it with decisions and their history. Store confirmed findings in linked results
  and include them in this view and subsequent same-scope history without inventing new
  coverage or changing the parent's
  status, summary or grade. Preserve existing finding identity and duplicate rules;
  reject a conflicting history update rather than overwrite a newer decision.
- Older runs remain readable. Allow targeted retry only when exact inputs can be proven
  from saved records; otherwise explain that the run predates retry support. Never use
  the current worktree as a substitute for missing original evidence.
- Expose listing and execution through public Python operations. Use a new
  `mortis.assessment/v1` result for follow-up runs. Keep JSON stdout clean, send progress
  and elapsed time to stderr, and print the assessment verdict/finding in text mode.

**Acceptance:** a failed review candidate and a failed security candidate can each be
retried with no analysis/scanner/summary calls; confirmed, rejected and failed outcomes
persist correctly; duplicate commands and resume do not dispatch twice; unknown usage,
stale connections, missing evidence and conflicting history fail safely; original report
bytes and coverage stay unchanged. Extend workflow, security, runtime and CLI tests.

### 3. Local finding triage with decision history

**Current owners:** `history.py`, `review/workflows.py`, `security_scans/service.py`,
`operations.py`, `cli/analysis.py` and the review/security finding contracts.

```sh
mortis findings --run RUN_ID
mortis triage --run REVIEW_RUN_ID --finding FINDING_ID --state dismissed --version 0 --reason "Explained by the caller contract"
mortis triage --run SCAN_RUN_ID --finding FINDING_ID --state false_positive --version 0 --reason "Evidence refers to an unused fixture"
mortis triage --run SCAN_RUN_ID --finding FINDING_ID --state accepted_risk --version 1 --reason "Temporary exception until dependency upgrade"
mortis triage --run SCAN_RUN_ID --finding FINDING_ID --state open --version 2 --reason "Exception expired"
mortis findings --run RUN_ID --history
```

- Match existing domain semantics: reviews support `dismissed` and `open`; security
  findings support `false_positive`, `accepted_risk` and `open`. Reopening is an explicit
  decision. Manual triage does not claim that source was fixed or a secret was rotated.
- Store append-only events keyed by repository, workflow kind, scope and stable finding
  identity. Record origin run, previous/new state, nonblank bounded reason, timestamp
  and increasing version. Initial version is 0; stale `--version` fails atomically.
- Validate that the finding belongs to the selected run and scope. Preserve existing
  redaction and safe output rules for reasons. Triage performs no model/provider call.
- Add `mortis findings` as the current view of saved observations, successful retries
  and triage events. Show evidence outcome separately from the user's decision, including
  the version needed for the next mutation. `--history` exposes the decision trail.
- Apply decisions to subsequent same-scope review/scan history, duplicate assessment,
  fix prompts and current counts. Decisions follow existing finding identity; a similar
  title or path is insufficient to transfer a dismissal to a different defect.
- Snapshot the triage version used by a new run. A run finishing after a newer user
  decision must not undo it. Retain the new decision in the current view and keep the
  run's historical input visible. Reject stale follow-up writes with an actionable error.
- Accepted risks remain unresolved and continue to count toward the existing security
  rating policy. False positives are excluded from future effective ratings. Old scan
  grades remain unchanged, and incomplete coverage never becomes complete through triage.
- Use versioned findings/triage operation results and public Python entry points.
  No new status vocabulary is added to historical review/security report contracts.

**Acceptance:** every supported decision and reopening is recorded; blank reasons,
unknown IDs, wrong scope and stale versions are rejected; simultaneous writers do not
lose events; triage survives restart and later runs without mutating old reports;
accepted risks still affect grading; no credentials are needed. Test pre-feature database
upgrades and review/security consumers as well as CLI and Python contracts.

### 4. Shared concurrency across local processes

**Current owners:** `workflow.py`, `gateway.py`, `llm_http.py`, `llm_bedrock.py`,
`history.py`, `config.py`, review/operation entry points and CLI progress rendering.
The current per-run limit and run ownership lock do not constrain separate runs.

- Keep `--concurrency 1..8` as the per-run worker limit. Add an aggregate ceiling of
  eight active model requests per connection across Mortis processes sharing one local
  state directory. Two runs requesting eight workers must share eight request slots.
  A run requesting two still has at most two workers. No additional tuning flag is needed.
- Put admission state under the private global Mortis state root, outside the per-repo
  history directories, so reviews in different repositories coordinate correctly.
- Derive the connection group from normalized provider/protocol/destination and nonsecret
  credential references (vault ID, environment-variable name or AWS profile). Exclude
  model, repository, run ID and display name. Aliased profiles with the same references
  share a group. Never store credential values or credential hashes for admission.
- Carry that identity into library workflows too. Without a nonsecret credential
  reference, conservatively group calls by endpoint/auth destination. Different secret
  references cannot be proven to represent the same provider account without a broader
  account-management feature; do not claim account-wide rate limiting.
- Reuse platform file-lock patterns for eight numbered slots. Hold the descriptor for
  the request lifetime; a dead local process releases its OS lock. Do not use expiring
  leases or delete a lock file based on age/PID while a request may still own it.
- Acquire a slot in the shared workflow dispatch path before reserving tokens or writing
  a dispatch marker. Recheck deadlines and budgets after waiting. Reused cached results
  consume no slot. Cancellation while waiting spends no provider tokens.
- Apply admission to analysis, assessment, verification, summary, background and targeted
  retries for both review and scan workflows. Release only after HTTP/Bedrock transport
  cleanup completes, including repeated cancellation. Do not hold slots during retry
  backoff or local parsing/Trivy execution.
- Report `Waiting for connection capacity` through existing progress events and show
  active/waiting request counts. Queue time counts toward the configured deadline;
  existing JSON stdout, noninteractive output and elapsed timing remain intact.
- OS-lock recovery releases local execution capacity, not an unknown token reservation.
  A killed client cannot prove that a remote provider stopped processing its request.
  Scope is processes under one local state root, not separate machines, containers or
  network filesystems. FIFO scheduling and provider requests-per-minute limits are excluded.

**Acceptance:** subprocess tests prove two repositories cannot exceed eight concurrent
mock requests for one group; separate groups proceed independently; aliases and model
overrides share capacity; waiting cancellation/timeout makes no dispatch; killing an
owner releases its slots; delayed transport cleanup retains capacity until it finishes;
unknown-usage reservations remain intact. Exercise HTTP and Bedrock paths, and platform
locking behavior where a supported runner is available.

### Delivery and verification

Implement in four independently reviewable phases: **graph limits → shared admission
→ assessment retry → triage**. Admission then protects all new inference, and triage
builds on the current findings view needed for retry results. Each phase includes its
own configuration/API wiring, CLI help, README updates and focused regressions.

Before each phase, refresh the source baseline and confirm callers, exports, fixtures
and state migration requirements. Keep one writer per file. For substantial code work,
use the repository's implementer and relevant workflow, contract and security reviewers;
transfer exclusive state/database test ownership to `verification_runner` for final checks.

Completion requires focused regressions, the full pytest suite, Ruff, wheel/sdist builds
and installed CLI smoke checks. Use temporary repositories and isolated configuration/
state roots. Verify migration using an old database fixture and confirm the original
plans/reports are unchanged. Test terminal progress and JSON output separately.

Mock provider, scanner and parser boundaries for these changes. Live model quality,
real Trivy/Docker integration, app adoption, cross-machine coordination and publishing
are not established by these checks and are not part of implementation authorization.
