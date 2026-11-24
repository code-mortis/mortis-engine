# Mortis Engine

Review local Git changes, verify findings and scan repository security with your saved AI connection. Mortis sends eligible source and diffs to that connection and checks reported evidence against the pinned Git revisions.

## Install

```sh
uv tool install mortis-engine
mortis --version
mortis --help
```

Requires Python 3.13–3.15 and Git. Development defaults to Python 3.15 through
`.python-version`; `uv` can provide the compatible Python interpreter.
Mortis Engine is available on [PyPI](https://pypi.org/project/mortis-engine/).
To upgrade, run `uv tool upgrade mortis-engine`. To install from a local checkout,
use `uv tool install --editable .`.
Trivy is required for security scans and Docker is optional
for code graph context; neither executable is bundled in the Python package.

## Use with coding agents

The portable [Mortis Engine skill](.agents/skills/mortis-engine-use/SKILL.md) teaches coding
agents to preview review scope, run reviews and security scans, inspect saved
results, and handle verification, retries and finding decisions.
Install the CLI above; the skill supplies instructions, not the engine executable.
Reviews and scans also need a [saved AI connection](#connect-with-environment-variables).
Scope previews need no connection or provider calls.

Mortis Engine bundles the skill and its references. Install them for your user
account with the CLI:

```sh
mortis agent install
```

The command installs the skill under `~/.agents/skills/` and registers it in the
detected Claude Code, Cursor, Codex and OpenCode skill directories. These
registrations link to the canonical copy on macOS and Linux and use copies on
Windows. Installation runs locally without a provider connection.
Rerun the command after upgrading to refresh managed installations. Use
`mortis agent install --force` to replace a manually installed copy or link.

### Use the skill

Open a new agent session in the repository you want to analyze and enter one of
these prompts in the agent's chat:

| Agent | Example prompt |
| --- | --- |
| Codex | `$mortis-engine-use Preview my current working-tree review scope without calling a provider.` |
| Claude Code | `/mortis-engine-use Preview my current working-tree review scope without calling a provider.` |

For analysis, ask the agent to review your local changes or scan a committed
revision with Mortis. Name the saved connection if you have several, and request
the findings, coverage and saved run ID. Reviews and scans send eligible source
to that connection and may consume provider tokens.

## Choose what to review

| Command | Source |
| --- | --- |
| `mortis review` | Current working tree against HEAD, including staged, unstaged and eligible untracked files |
| `mortis review --staged` | Git index against HEAD |
| `mortis review --base main --head feat/update-opentui` | Changes from the common ancestor through the named branch tip |
| `mortis review --all` | Every eligible tracked file in the current commit |
| `mortis review --all --head feat/update-opentui` | Every eligible tracked file in that branch's latest local commit |

Use `--effort low`, `--effort medium` or `--effort high` to override the repository setting for one run. Local working-tree and staged reviews use an immutable snapshot and leave your Git index, objects and refs untouched. Working-tree reviews require an initial commit. Ignored untracked files, credentials and excluded files are outside review scope.

Working-tree reviews read files without following symlinks or running Git filters. On a platform without safe no-follow file access, stage the changes you intend to review and use `--staged`. Files over 20 MB or otherwise unreadable are reported as coverage gaps when the snapshot can still be built.

Preview the same local planner without an AI connection or saved analysis run:

```sh
mortis review --base main --preview
mortis review --all --preview --format json
```

The preview lists selected and excluded files, exclusion reasons, source/diff bytes,
units and planned batches. `--prompt-token-ceiling` reproduces a connection's input
bound without loading its credentials. Batch counts exclude provider envelopes,
evidence followups and later workflow stages. See [preview details](docs/review-improvements/2-scope-preview.md).
New full-tree reviews send source once; historical resumed plans retain their original
representation and task identities. The [fixed-input measurement](docs/review-improvements/1-full-source.md)
records the byte and batch reduction.

Use `--concurrency 1` through `--concurrency 8` to override the repository setting for a review or security scan. Reviews default to two concurrent AI batches; a batch may contain multiple files. Scans default to four concurrent file analyses. The override applies only to that run and leaves `mortis.toml` unchanged.

Analysis, assessment and fix verification start the next eligible task as soon as a
slot opens. Related candidates wait for the earlier result to enter the assessment
history. Evidence followups for one finding and deterministic scanner checks each run
sequentially. Verification retains report order.

Requests waiting for a temporary token reservation or a retry release their execution
slot, so other eligible work can proceed. When a review batch exceeds the output
limit, its smaller recovery batches share the analysis concurrency limit and budget.

Separate Mortis processes sharing the same local state directory also share a ceiling
of eight active model requests per connection. This applies across repositories and
workflow stages. Profiles using the same destination and credential reference share
capacity even with different names or models. Progress shows waiting requests; waiting
counts toward the configured deadline. This coordinates local clients, not provider
account rate limits or other machines. Different credential references form separate
groups, even when they happen to contain the same API key.

Add `--progress` for a compact live view showing reviewed and failed file counts, active AI requests and elapsed time. Files in active requests appear on separate rows, with up to three names and a count of any others. Mortis sends files in batches; file counts update when a request finishes and its results pass validation. Redirected progress uses plain lines. Every review prints its elapsed time to stderr, including failed or interrupted attempts; resuming measures the current invocation. Progress and timing keep JSON on stdout valid.

The view also shows possible findings as validated analysis results arrive. During assessment it shows assessed/total candidates, unique confirmed findings, rejected and uncertain candidates, failed assessments and duplicate confirmations. Failed assessments count as processed candidates and remain separate from rejected findings. These counts stay visible through summary generation. Scan progress shows Trivy findings separately from AI proposals and confirmations; unobserved findings retained from earlier scans are not counted as current confirmations. Finding text remains in the final report.

## Review a branch

From your working branch, run:

```sh
mortis review --base main --head HEAD
```

Mortis uses your only saved profile automatically. With several profiles, select one by name:

```sh
mortis review --base main --head HEAD --connection opencode-go --format json
```

Mortis compares the common ancestor of the two commits with the head commit. Local edits and untracked files are outside this comparison. Branches, tags and commit IDs must be available in the local clone. Use `--repo` to select another repository. GitHub access and a running Code Mortis instance are unnecessary.

### Ask about or describe the changes

```sh
mortis ask --base main --head HEAD --question-file question.txt --format json
mortis ask --base main --head HEAD --question-file question.txt --path app.py --line 4:6 --side right --format json
mortis describe --base main --head HEAD --format json
```

Both commands compare the common ancestor with the head revision, use the base commit's review settings, and send eligible diff context to the selected connection. Use `--connection NAME` when you have several saved connections.

`ask` takes a UTF-8 question file with 1–8,000 characters and returns citations pinned to exact commits. To focus on a location, pass `--path`, `--line START[:END]` and `--side` together. A range can overlap multiple hunks on the `left` (merge base) or `right` (head) side. `describe` drafts a local title, narrative and groups of changes.

JSON uses `mortis.ask/v1` or `mortis.describe/v1`. Both `completed` and `insufficient_context` return exit code 0. Check `status` before reading `result`, and check `limited_context` when present for hunks omitted by the context budget. Use `mortis runs RUN_ID` when the result includes a `run_id`. These commands start a new comparison each time.

### Review subsequent commits

Branch comparisons can reuse coverage from the last compatible review of the same
base and head refs. Use a branch name or its checked-out `HEAD` to keep a stable
history scope as new commits arrive. Both options default to `false`.

```sh
mortis review --base main --incremental --skip-unchanged
```

Set either option as a default in the base branch's `mortis.toml`:

```toml
[review]
incremental = true
skip_unchanged = true
```

`incremental` analyzes files that changed since the last fully covered head. Those
files use their cumulative diff from the current comparison's common ancestor. The
summary still covers the eligible changes in the branch, and previous findings stay
in history. Reused coverage describes prior analysis of unchanged files. A first run,
an incomplete review, a changed base or review configuration, or unavailable
ancestry triggers a full comparison. `--graph` and `--second-pass` also use the full
comparison. `max_files` applies to the cumulative scope.

`skip_unchanged` skips model calls when the compatible prior review covered the
same head tree, even with a new commit SHA. The skipped report exits successfully
and links to the prior run. Review flags `--incremental` / `--no-incremental` and
`--skip-unchanged` / `--no-skip-unchanged` override the committed settings. Use both
negative flags to force a full review. The Python API accepts the same booleans in
`overrides`. `--resume` keeps the saved choice. `--preview` shows the full scope
without reading history and rejects the comparison-specific flags.

### Configure a branch review

Create an optional `mortis.toml` on the base branch to configure the review:

```toml
[review]
language = "Spanish"
effort = "medium"
review_sensitivity = "balanced"
max_files = 40
concurrency = 2
timeout_seconds = 300
token_budget = 100000
max_output_tokens = 32768

[[review.rules.rules]]
path = "**/*.py"
rule = "Check that exceptions preserve committed data."
merge_system_rule = true
```

Use `mortis config show --base main` to inspect the effective settings. The base commit controls the rules, file exclusions, background context and optional model override. A profile supplies the endpoint and credentials. Analysis, candidate assessment, fix verification and summaries each use their configured token budget. Use `assessment_token_budget`, `verification_token_budget`, `summary_token_budget` and `summary_timeout_seconds` to configure follow-up stages. `summary_diagram_enabled` enables a structured diagram and `ai_fix_prompts_enabled` includes repair prompts for open findings. For working-tree, staged and full-tree reviews, Mortis reads settings from the selected committed revision.

CLI flags override the committed TOML settings for one run without changing the file:

```sh
mortis review --all --max-output-tokens 0 --token-budget 0 --timeout-seconds 0 --max-files 0
mortis scan --max-output-tokens 32768 --assessment-token-budget 200000
mortis summary --run RUN_ID --max-output-tokens 32768 --summary-token-budget 0
```

Use `0` for unlimited total token budgets, overall timeouts and maximum files. It is normalized to no limit internally. Concurrency still requires 1–8. Provider request timeouts, transport size limits and provider account/model limits still apply.

`max_output_tokens` controls each AI response, including reasoning, across analysis and follow-up stages. An explicit positive value is preserved during recovery. Omitting the setting keeps the 16,384-token default and recovery up to 32,768 tokens.

Set `max_output_tokens = 0` to let the provider determine the output allowance when the total token budget is unlimited. OpenAI Chat Completions, OpenAI Responses and Bedrock Converse use the provider's default. Anthropic Messages fetches the selected model's output capacity from the Models API and uses it for the request. If the endpoint cannot supply that capacity, use an explicit positive limit.

With `max_output_tokens = 0` and a finite token budget, Mortis derives an output cap
from the remaining budget and reserves that budget until the request settles.
Use a positive output limit within the model's capacity to allow multiple requests
to fit in the budget at once.

Review flags also include `--assessment-token-budget`, `--verification-token-budget`, `--summary-token-budget` and `--summary-timeout-seconds`. Scans support the assessment and verification budget flags. Standalone verification uses `--verification-token-budget`; background generation uses `--token-budget`. These commands support `--max-output-tokens` and their corresponding overall timeout flag. `--resume` restores the saved overrides and rejects new limit flags.

Mortis assesses candidates before reporting confirmed findings and generates a summary of the reviewed changes. The JSON report retains rejected and uncertain candidates. With `auto_resolve = true`, later reviews of the same scope check earlier open findings against both revisions. A verified fix can update the current local status. Earlier reports remain available by ID.

Default-omitted tests may be read as pinned supporting evidence without becoming review
targets. Explicit exclusions and protected paths still apply. Tests are never executed:
only their supplied inputs and assertions can support or refute a claim. Assessment
can request bounded merge-base evidence to distinguish an existing defect from one
introduced or activated by the change. Full-tree reviews continue to evaluate defects
present at the selected revision.

For an exact Python line replacement, Mortis checks the proposed source in memory.
If a valid original would become syntactically invalid, the confirmed finding remains
and its suggestion is withheld. Ambiguous replacements and originally invalid source
retain the assessor's decision; this check does not establish runtime correctness.
See [Python suggestion checks](docs/python-suggestions.md) for the exact limits.

Discovery can search a literal symbol in bounded, permitted source at the pinned
revision, including unchanged files. Matches are navigation hints, not proof of a
caller relationship: Mortis requires a separate source read before accepting a
citation. This works without Docker; aliases and dynamic access may be missed.
See [symbol search limits](docs/review-improvements/3-symbol-search.md).

The optional `--second-pass` experiment searches for additional candidates through the
same analysis budget and normal assessment. It is disabled by default and can also be
configured with `[review].second_pass = true`; resume retains the saved choice. A failed
second pass preserves validated first-pass results and reports incomplete analysis.
See [the experiment](docs/second-pass.md) for limits. The offline replay suite and
recorded results are in the companion `mortis-benchmarks` project.

The report includes commit IDs, findings, evidence, file and unit coverage, errors and token usage. A file counts as fully reviewed in the current run only when all assigned units pass validation. Incremental and unchanged reviews identify reused coverage in `coverage.reused` and link to the prior run in `comparison`. Missing token usage is reported as unknown. JSON on stdout uses `mortis.review/v1`. Progress goes to stderr. Exit status 0 indicates completed analysis, no eligible changes or an unchanged comparison. Exit status 2 indicates partial coverage or a file-limit skip, and 1 indicates failure. Findings are available even in a partial report.

## Save and export reports

Use `--output` once or several times to save a review or security scan. The filename
extension selects Markdown (`.md` or `.markdown`) or structured JSON (`.json`):

```sh
mortis review --all --progress --output review.md --output review.json
mortis scan --ref HEAD --progress --output scan.md --output scan.json
```

The terminal still shows the normal report; `--format json` continues to control stdout.
Progress and elapsed time stay on stderr. Both files describe the same analysis and
do not require extra AI calls. Partial reports are saved too, with their incomplete
coverage and errors preserved; review and scan exit codes retain their usual meaning.

Markdown includes the summary and any generated groups/diagram, findings and evidence,
supported suggestions or remediation, coverage, errors, model, token usage and duration.
JSON contains the complete versioned result. New review/scan results save
`elapsed_seconds`, measuring the analysis invocation, including preparation and follow-up
stages but excluding export. For resumed runs it measures that invocation. Older saved
reports remain unchanged and show unavailable timing rather than an estimated duration.

Export an existing saved result without a connection or another analysis:

```sh
mortis report --run RUN_ID --output reports/review.md --output reports/review.json
```

Use `--repo` when selecting a different local repository. The export uses the original
saved review or scan report; later retries and triage remain available through
`mortis findings`. Successful export returns exit code 0 even if the saved analysis was
partial; its original status remains in the report.

Output files are private to the current user. Missing parent directories are created
when saving. Existing files, symlinks, duplicate destinations and unsupported extensions
are rejected, so reports cannot overwrite source or prior output. Choose new filenames
when saving another report. Export errors include the saved run ID when available.
If a saved report has an invalid structure, Markdown export fails and the saved
result stays intact.

When saving inside the repository, add the output directory to `.gitignore` if you
want future working-tree reviews to exclude those generated reports.

## Saved results and follow-up commands

```sh
mortis runs
mortis runs RUN_ID
mortis verify --run RUN_ID --ref HEAD
mortis summary --run RUN_ID
mortis background --ref HEAD
```

`mortis runs` lists IDs for the current repository. Replace `RUN_ID` with an ID from that list. Verification checks the earlier finding against current source. Background prints a proposed project description. To use it in reviews, save it under `[review].background` in the trusted configuration.

Run history, task reservations and retained snapshot objects live under `$XDG_STATE_HOME/mortis` (default `~/.local/state/mortis`). These private files can contain reviewed source. Use `mortis review --resume RUN_ID` to continue an interrupted review with its pinned revisions and saved connection. Each workflow accepts `--resume`. Validated task results can be reused. A request interrupted after dispatch retains its reservation with unknown usage.

Resumed reviews and scans keep their saved concurrency override and assessment dispatch
policy. Runs created before continuous assessment resume in batches. Omit
`--concurrency` when using `--resume`. You can enable `--progress` when resuming.

### Retry a failed assessment

```sh
mortis assessments --run RUN_ID
mortis assess --run RUN_ID --candidate CANDIDATE_ID --progress
mortis assess --resume RETRY_RUN_ID --progress
```

Reviews and security scans save candidate assessment inputs before dispatch. The listing
shows which failed candidates can be retried and explains blocked retries. A targeted
retry assesses only that candidate using its original source, settings and connection.
It saves a separate `mortis.assessment/v1` result and prints its run ID, verdict and any
confirmed finding. Analysis, Trivy and summary generation are not repeated. Repeating a
successful retry returns the saved result.

Retries have their own invocation deadline and assessment budget, with usage recorded
separately and cumulatively across the candidate's attempts. Unknown token consumption
blocks a retry and retains its reservation. Changed connections or missing original
evidence also block dispatch. Older runs without saved assessment inputs remain readable
but cannot be retried through this command. Original reports, coverage and grades stay
available through `mortis runs RUN_ID`.

A change to finding decisions in the scope blocks a retry whose saved history is stale.
If decisions change while a retry runs, its result and usage are saved for inspection,
and the result does not update current findings.

### Record finding decisions

```sh
mortis findings --run RUN_ID
mortis triage --run RUN_ID --finding FINDING_ID --state dismissed --version 0 --reason "Caller already checks this condition"
mortis findings --run RUN_ID --history --format json
```

`mortis findings` shows the current findings for that run's scope, including confirmed
assessment retries and local decisions. Review decisions support `dismissed` and `open`.
Security decisions support `false_positive`, `accepted_risk` and `open`. Reopen a finding
by selecting `open` and supplying the current version printed by `mortis findings`.
Every decision requires a reason. A stale version is rejected so simultaneous commands
cannot overwrite each other's decisions.

Decisions apply to subsequent runs in the same repository and scope. They are saved in
an audit trail alongside the original reports, and require no AI connection. Evidence
outcomes remain visible separately from user decisions. Accepted risks still count toward
security ratings; false positives do not count in future ratings. Triage does not claim
that a fix was verified or a reported secret was rotated. All four follow-up commands
support `--repo` and `--format json`.

## Scan security

```sh
mortis scan --ref HEAD
mortis scan --ref main --format json
mortis scan --concurrency 8 --effort high --progress
```

`--progress` shows the same compact live view as reviews, including snapshot preparation, Trivy checks, active AI file analyses, assessment and verification. File counts include only validated analysis results. Every scan prints elapsed time to stderr, with or without progress, including failed or interrupted attempts. JSON reports remain on stdout.

The scan requires Trivy 0.76.2 on PATH. Trivy refreshes its advisory database, then checks dependencies, configuration and secrets against an isolated local snapshot. Semantic analysis, candidate assessment and verification use the selected AI profile. Repository source is excluded from the Trivy update request. For a supported npm CVE without an established fixed version, verification can look up the CVE ID in GitHub's public advisory database. A partial scan keeps its coverage and findings and reports an unavailable grade.

An optional `[scan]` table in `mortis.toml` accepts `model`, `effort`, `concurrency`, `language`, `exclusions`, `timeout_seconds`, `token_budget`, `max_output_tokens`, `assessment_token_budget` and `verification_token_budget`. The scan output limit inherits `[review].max_output_tokens` unless overridden in `[scan]`. Scan defaults use high effort and four concurrent file requests. `mortis config show` displays effective review and scan settings. After rotating or revoking a reported secret, pass `--acknowledge-rotation FINDING_ID` on the next scan of that scope so Mortis can verify removal. Use the ID printed with the original finding.

## Optional code graph

```sh
mortis graph build
mortis review --base main --graph
```

Docker builds a separate parser image with the pinned grammars. After upgrading
from 0.1.2 or earlier, rerun `mortis graph build` before using `--graph`. During
review the parser receives bounded file contents at one revision. It runs without
network or host mounts. A review can continue using available graph facts and its
normal source evidence when graph coverage is incomplete. Resumed reviews reuse
their saved graph context when available.

Set extraction limits in the trusted committed configuration:

```toml
[review.code_graph_limits]
max_files = 1000
max_file_bytes = 1048576
max_source_bytes = 8388608
```

For one run, use `mortis review --graph --graph-max-files 2000` or the corresponding
`--graph-max-file-bytes` and `--graph-max-source-bytes` flags. Individual flags preserve
the other committed limits. `mortis config show` displays effective values. Defaults are
1,000 files, 1 MiB per file and 8 MiB total; maximum values are 5,000 files, 4 MiB per file
and 32 MiB total. All must be positive integers, and the individual file limit must fit
within the total limit. These bounds cannot be made unlimited. Resume restores saved
limits and rejects new overrides. Persistent graph storage and querying belong to the app.

## Connect interactively

```sh
mortis connect
```

In a terminal, `mortis connect` asks for the connection details you leave out. Flags supply those values directly. For scripts, use `--non-interactive` with an environment variable for the key.

For OpenCode Go, run:

```sh
mortis connect opencode-go --model muse-spark-1.3-contributor
```

A new profile named after a provider uses that preset’s protocol and URL. With the model supplied, paste your key at the hidden prompt and press Enter. Mortis tests the connection and saves it to your OS credential vault. An existing profile keeps its own provider settings.

Use `--advanced` to edit protocol, endpoint, options and extra headers interactively. A blank header entry keeps the current headers; `{}` clears them. The same settings can be supplied directly through flags. Use `--api-key-env VARIABLE` to read a key from the environment. Enter `?` at the model prompt to select from the provider catalog. Bedrock also supports AWS credentials.

Model listing is optional and depends on provider support. For providers without discovery, including Bedrock, enter the model ID directly. The connection test makes two small model requests to verify a tool-call round trip and may consume provider tokens. It saves the profile after the test succeeds. Failures identify the selected profile and never switch providers. A failed edit preserves the previous saved connection.

```sh
mortis connect work --test
mortis connect work --list-models
mortis connect --list-providers
mortis config show
mortis config show work
```

Provider options are stored as JSON within the TOML file to preserve provider-specific values such as `null`. Profiles are stored in `~/.config/mortis/config.toml`, or `$XDG_CONFIG_HOME/mortis/config.toml` when set. `mortis config show` displays connection settings and credential references, with vault contents omitted. Running `mortis connect work` edits that profile. Changing provider, endpoint, protocol or authentication destination requires selecting credentials again.

## Connect with environment variables

Set `MORTIS_API_KEY` securely in your shell or secret manager, then run the following. The saved profile refers to that environment variable, so it must also be set for subsequent tests.

```sh
mortis connect work \
  --provider openai \
  --model YOUR_MODEL \
  --api-key-env MORTIS_API_KEY \
  --non-interactive
```

Custom endpoints support an explicit protocol and API URL.

```sh
mortis connect local \
  --provider custom \
  --protocol openai \
  --base-url http://localhost:4000/v1 \
  --model YOUR_MODEL \
  --api-key-env MORTIS_LOCAL_KEY \
  --options '{"timeout_sec":120,"prompt_token_ceiling":58888}' \
  --non-interactive
```

For a private-network endpoint, set `CM_ALLOW_PRIVATE_ENDPOINTS=true`; HTTP is allowed
only when every resolved address is private or loopback. Public endpoints still require
HTTPS. Alternatively, explicitly allow a hostname with `CM_TRUSTED_ENDPOINT_HOSTS=localhost`.
Explicit hostname trust allows that destination through the endpoint checks. Connections
use validated IP addresses while retaining the hostname for TLS verification and HTTP
routing. Use a placeholder key for a local provider that does not require authentication.
Endpoint policy comes from these environment settings and is not loaded from repository files.

Extra header values can also come from environment variables.

```sh
mortis connect work \
  --headers-env '{"X-Custom-Header":"MORTIS_CUSTOM_HEADER"}' \
  --non-interactive
```

Use `--headers-env '{}'` to clear extra headers. A supplied `--options` object replaces the saved provider options. Otherwise edits retain existing options for the same provider. All four API protocols are supported: `openai`, `openai-responses`, `anthropic`, and `anthropic-bedrock`. Provider presets are packaged with the CLI.

| Option | Meaning |
| --- | --- |
| `timeout_sec` | Provider request timeout, 5–900 seconds |
| `prompt_token_ceiling` | Conservative input ceiling per request, 4,096–2,000,000 tokens |
| `retry_codes` | Provider HTTP 4xx codes classified as retryable by the transport |
| `extra_body` | Additional provider request fields, subject to validation of engine-managed inputs, output and authentication |
| `auth_header` | Custom authentication header name |
| `aws_region` | Bedrock region |
| `aws_profile` | AWS credential profile for Bedrock |

Use nested properties for supported `extra_body` settings, such as `{"reasoning":{"effort":"medium"}}` with matching review effort. Whole-value overrides of `reasoning`, `text` or `output_config` and replacement of structured output formats are rejected. A protocol's supported output budget alias must match the effective per-request limit when Mortis supplies one.

Bedrock can use ambient AWS credentials or an API key supplied through the same environment or vault methods. With an API key, a custom `auth_header` cannot be `X-Amz-Date` or `X-Amz-Security-Token`. Choose a model or inference profile that supports the [Converse API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html) and the tool and structured-output features in use. IAM credentials need `bedrock:InvokeModel` permission.

```sh
mortis connect aws \
  --provider bedrock \
  --model YOUR_BEDROCK_MODEL_ID \
  --ambient-auth \
  --options '{"aws_region":"us-east-1","aws_profile":"work"}' \
  --non-interactive
```

## Development

Coding-agent guidance and the project Codex roles are documented in [AGENTS.md](AGENTS.md).

The project-local [release preparation skill](.agents/skills/mortis-release/SKILL.md)
synchronizes versions, changelog, repository and site documentation, and maintained
skills before a release. Invoke `$mortis-release` in Codex. In other agents, ask
them to follow `.agents/skills/mortis-release/SKILL.md` from this checkout.
Both skill sources live in `.agents/skills/`; only `mortis-engine-use` is bundled
with the Python package. Release preparation validates changes; publication remains
a separate maintainer action.

GitHub Actions runs the test suite on Python 3.13, 3.14 and 3.15 for pushes to `main`
(including merges), pull requests targeting `main` and manual dispatches.
Python 3.15 checks Ruff, builds and checks the wheel/source distribution and
smoke-tests isolated installations. Python 3.13 tests native graph extraction with
the parser's pinned dependencies. Provider, Trivy
and Docker boundaries use mocks in this suite; CI does not make live analysis requests.

```sh
uv sync --locked --group dev
uv run --locked pytest -q
uv run --locked ruff check .
uv build
uv run --locked python scripts/check_dist.py
```

The `mortis_engine.cli` package contains terminal commands. Python callers can import `review_local` or `review_commits` from `mortis_engine.review` and use `mortis_engine.operations.run` for verification, summaries, background and scans. These entry points return the same versioned dictionaries as the CLI JSON output.

`mortis_engine.operations` also exports `list_assessments`, `retry_assessment`,
`list_findings` and `triage` for the corresponding local follow-up commands.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before contributing and
[SECURITY.md](SECURITY.md) to report a vulnerability privately. Release preparation,
GitHub settings and PyPI setup are documented in [docs/releasing.md](docs/releasing.md).
