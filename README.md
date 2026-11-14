# Mortis Engine

Review changes between two local Git commits with your own AI connection. Mortis sends the selected source and diffs to the configured model and reports findings with verified file and line references.

## Install from this repository

```sh
uv tool install --editable .
mortis --help
```

Requires Python 3.12–3.14. `uv` can provide the compatible Python interpreter. To install a built release wheel, use `uv tool install /path/to/mortis_engine-0.1.0-py3-none-any.whl`. PyPI publication is a later release step.

## Review two commits

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

[[review.rules.rules]]
path = "**/*.py"
rule = "Check that exceptions preserve committed data."
merge_system_rule = true
```

Use `mortis config show --base main` to inspect the effective settings. The base commit controls the rules, file exclusions, background context and optional model override. A profile supplies the endpoint and credentials. The initial review uses the analysis token budget. Separate budgets for later assessment, verification and summaries are accepted as configuration.

The report includes commit IDs, findings, evidence, file and unit coverage, errors and token usage. A file counts as fully reviewed only when all assigned units pass validation. Missing token usage is reported as unknown. JSON on stdout uses `mortis.review/v1`. Exit status 0 indicates completed analysis or no eligible changes, 2 indicates partial coverage or a file-limit skip, and 1 indicates failure. Findings are available even in a partial report.

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

```sh
uv sync --group dev
uv run pytest -q
uv run ruff check .
uv build
```

The `mortis_engine.cli` package contains terminal commands. The reusable `mortis_engine.review.review_commits` function accepts a repository path, base, head, connection and a credential loader, and returns the same versioned dictionary as the JSON report. Security scans are the next milestone in `PLAN.md`.
