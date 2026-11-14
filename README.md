# Mortis Engine

Review local Git changes, verify findings and scan repository security with your saved AI connection. Mortis sends eligible source and diffs to that connection and checks reported evidence against the pinned Git revisions.

## Install from this repository

```sh
uv tool install --editable .
mortis --help
```

Requires Python 3.13–3.14. `uv` can provide the compatible Python interpreter. To install a built release wheel, use `uv tool install /path/to/mortis_engine-0.1.0-py3-none-any.whl`. PyPI publication is a later release step.

## Choose what to review

| Command | Source |
| --- | --- |
| `mortis review` | Current working tree against HEAD, including staged, unstaged and eligible untracked files |
| `mortis review --staged` | Git index against HEAD |
| `mortis review --base main --head feat/update-opentui` | Changes from the common ancestor through the named branch tip |
| `mortis review --all` | Every eligible tracked file in the current commit |
| `mortis review --all --head feat/update-opentui` | Every eligible tracked file in that branch's latest local commit |

Use `--effort low`, `--effort medium` or `--effort high` to override the repository setting for one run. Local working-tree and staged reviews use an immutable snapshot and leave your Git index, objects and refs untouched. Working-tree reviews require an initial commit. Ignored untracked files, credentials and excluded files are outside review scope.

Use `--concurrency 1` through `--concurrency 8` to override the repository setting for a review or security scan. Reviews default to two concurrent AI batches; a batch may contain multiple files. Scans default to four concurrent file analyses. The override applies only to that run and leaves `mortis.toml` unchanged.

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

`max_output_tokens` controls each AI response, including reasoning, across analysis and follow-up stages. An explicit value is preserved during recovery. With `0`, Mortis omits the output cap for OpenAI Chat Completions and Responses protocols and lets the provider choose its default; this does not guarantee the model's maximum. If a total token budget is finite, Mortis instead bounds output by the remaining budget and serializes these requests so concurrent work cannot reserve the same tokens. A provider may reject a remaining budget above its model's output maximum; set a positive `max_output_tokens` in that case. [Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create) and [Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-anthropic-claude-messages-request-response.html) require an explicit maximum, so those connections need a positive value. Omitting the setting preserves the existing 16,384-token requests and recovery up to 32,768 tokens.

Review flags also include `--assessment-token-budget`, `--verification-token-budget`, `--summary-token-budget` and `--summary-timeout-seconds`. Scans support the assessment and verification budget flags. Standalone verification uses `--verification-token-budget`; background generation uses `--token-budget`. These commands support `--max-output-tokens` and their corresponding overall timeout flag. `--resume` restores the saved overrides and rejects new limit flags.

Mortis assesses candidates before reporting confirmed findings and generates a summary of the reviewed changes. The JSON report retains rejected and uncertain candidates. With `auto_resolve = true`, later reviews of the same scope check earlier open findings against both revisions. A verified fix can update the current local status. Earlier reports remain available by ID.

The report includes commit IDs, findings, evidence, file and unit coverage, errors and token usage. A file counts as fully reviewed only when all assigned units pass validation. Missing token usage is reported as unknown. JSON on stdout uses `mortis.review/v1`. Progress goes to stderr. Exit status 0 indicates completed analysis or no eligible changes, 2 indicates partial coverage or a file-limit skip, and 1 indicates failure. Findings are available even in a partial report.

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

Resumed reviews and scans keep their saved concurrency override. Omit `--concurrency` when using `--resume`; `--progress` can be enabled when resuming.

## Scan security

```sh
mortis scan --ref HEAD
mortis scan --ref main --format json
mortis scan --concurrency 8 --effort high --progress
```

`--progress` shows the same compact live view as reviews, including snapshot preparation, Trivy checks, active AI file analyses, assessment and verification. File counts include only validated analysis results. Every scan prints elapsed time to stderr, with or without progress, including failed or interrupted attempts. JSON reports remain on stdout.

The scan requires Trivy 0.76.0 on PATH. Trivy refreshes its advisory database, then checks dependencies, configuration and secrets against an isolated local snapshot. Semantic analysis, candidate assessment and verification use the selected AI profile. Repository source is excluded from the Trivy update request. For a supported npm CVE without an established fixed version, verification can look up the CVE ID in GitHub's public advisory database. A partial scan keeps its coverage and findings and reports an unavailable grade.

An optional `[scan]` table in `mortis.toml` accepts `model`, `effort`, `concurrency`, `language`, `exclusions`, `timeout_seconds`, `token_budget`, `max_output_tokens`, `assessment_token_budget` and `verification_token_budget`. The scan output limit inherits `[review].max_output_tokens` unless overridden in `[scan]`. Scan defaults use high effort and four concurrent file requests. `mortis config show` displays effective review and scan settings. After rotating or revoking a reported secret, pass `--acknowledge-rotation FINDING_ID` on the next scan of that scope so Mortis can verify removal. Use the ID printed with the original finding.

## Optional code graph

```sh
mortis graph build
mortis review --base main --graph
```

Docker builds a separate parser image with the pinned grammars. During review the parser receives bounded file contents at one revision. It runs without network or host mounts. A review records unavailable graph context and can continue using its normal source evidence.

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

For a trusted HTTP or private-network endpoint, set `CM_ALLOW_PRIVATE_ENDPOINTS=true`, or explicitly allow its hostname with `CM_TRUSTED_ENDPOINT_HOSTS=localhost`. Explicit hostname trust allows that destination through the endpoint checks. Use a placeholder key for a local provider that does not require authentication. Endpoint policy comes from these environment settings and is not loaded from repository files.

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
| `extra_body` | Additional provider request fields, with input and authentication protections |
| `auth_header` | Custom authentication header name |
| `aws_region` | Bedrock region |
| `aws_profile` | AWS credential profile for Bedrock |

Bedrock can use ambient AWS credentials or an API key supplied through the same environment or vault methods.

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

GitHub Actions runs the test suite on Python 3.13 and 3.14 for pushes to `main`
(including merges), pull requests targeting `main` and manual dispatches.
Python 3.13 also checks Ruff, builds the wheel/source distribution
and tests native graph extraction with the parser's pinned dependencies. Provider, Trivy
and Docker boundaries use mocks in this suite; CI does not make live analysis requests.

```sh
uv sync --group dev
uv run pytest -q
uv run ruff check .
uv build
```

The `mortis_engine.cli` package contains terminal commands. Python callers can import `review_local` or `review_commits` from `mortis_engine.review` and use `mortis_engine.operations.run` for verification, summaries, background and scans. These entry points return the same versioned dictionaries as the CLI JSON output.
