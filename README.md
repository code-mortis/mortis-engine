# Mortis Engine

Set up and test AI connections for local Mortis analysis. This first milestone supplies the installable `mortis` command and the same API connection options as Code Mortis.

## Install from this repository

```sh
uv tool install --editable .
mortis --help
```

Requires Python 3.12–3.14. `uv` can provide the compatible Python interpreter. To install a built release wheel, use `uv tool install /path/to/mortis_engine-0.1.0-py3-none-any.whl`. PyPI publication is a later release step.

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

The shared Python package exposes provider schemas and transport functions independently of the CLI. PR reviews and security scans are the next milestones in `PLAN.md`.
