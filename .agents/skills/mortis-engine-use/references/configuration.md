# Configuration and optional context

Read this for connection setup, committed settings, limit overrides or graph use.
Use `mortis SUBCOMMAND --help` and the engine checkout's README for the installed
version's full options.

## Connections

`mortis connect --list-providers` lists packaged presets without a provider call.
Creating/editing a connection tests it with two small model requests before
saving. `mortis connect NAME --test` and `--list-models` also contact the selected
provider. Run them only within the user's authorized setup or diagnostic task,
not as routine readiness checks. Analysis itself does not need a separate probe.

For non-interactive setup, have the user supply the credential through their
environment/secret manager. Pass the variable's **name**, never its value:

```sh
mortis connect work --provider openai --model YOUR_MODEL --api-key-env MORTIS_API_KEY --non-interactive
```

Supported protocols are `openai`, `openai-responses`, `anthropic` and
`anthropic-bedrock`. A custom destination uses `--provider custom`, an explicit
`--protocol` and `--base-url`. Bedrock uses Converse. Select a model or inference
profile with the tool and structured-output support Mortis needs. Bedrock can use `--ambient-auth` with
`--options '{"aws_region":"us-east-1","aws_profile":"work"}'` instead of a key.
`--headers-env` maps header names to environment-variable names.

Profiles live under `$XDG_CONFIG_HOME/mortis/config.toml` (default
`~/.config/mortis/config.toml`); credentials remain environment or OS-vault
references. Use `mortis config show [NAME]` to inspect safe metadata, not direct
credential reads. `--options` replaces the saved options object; changing
provider or destination requires selecting credentials again. Failed edits
preserve the previous profile.

Private/HTTP destinations require explicit host trust through
`CM_TRUSTED_ENDPOINT_HOSTS` or `CM_ALLOW_PRIVATE_ENDPOINTS`. Never enable either
just to suppress an endpoint error; use only the endpoint and trust scope the
user selected. Repository configuration does not control endpoint trust.

## Trusted revision settings

The analyzed repository may contain `mortis.toml`. Editing its working copy does
not change configuration read from an already committed revision:

| Workflow | Configuration source |
| --- | --- |
| `review --base BASE --head HEAD` | Selected base commit |
| Working-tree or staged review | HEAD |
| `review --all --head REF` | Selected committed REF |
| `scan --ref REF` | Selected committed REF |
| Resume | Saved settings and overrides |

Inspect with `mortis config show --repo /path/to/repo --base REF`. The base
controls rules, exclusions, background and an optional model override. The
profile supplies the provider endpoint and credentials. Use one-run flags for
requested overrides; do not commit configuration just to run an analysis.

## Bounds and costs

Reviews default to two concurrent AI batches; scans default to four concurrent
file requests. Both accept `--concurrency 1` through `8` and `--effort low|medium|high`.
Processes sharing local state also share a ceiling of eight active model
requests per connection identity. Waiting consumes the workflow deadline.

Reviews accept `--max-files`, `--token-budget`, `--timeout-seconds`,
`--max-output-tokens`, `--assessment-token-budget`, `--verification-token-budget`,
`--summary-token-budget` and `--summary-timeout-seconds`. Scans accept the same
flags except file and summary limits. Other workflows expose only relevant
stage limits; consult their help. Budgets apply to their individual stages, not
a single aggregate run budget.

`0` means no Mortis limit for total token budgets, overall timeouts and review
file limits. Preserve finite limits unless the user requests a change.
Provider request timeouts, transport bounds and model/account limits still apply.

`--max-output-tokens` caps each response including reasoning. With `0`, OpenAI
protocols omit the output cap when the total budget is unlimited; with a finite
budget, output is bounded by remaining tokens and requests are serialized.
With an unlimited token budget, Bedrock Converse omits `maxTokens` and uses
the service default. Anthropic Messages reads the selected model's output
capacity from the Models API. Set a positive limit if the endpoint lacks that
metadata or the provider rejects the derived cap. Resume restores saved limits
and rejects new ones.

`--second-pass` is an optional review experiment, disabled by default. Do not
enable it or increase limits as an implicit recovery step.

## Graph and secret rotation

Docker is optional for review graph context; Trivy is required for scans.
`mortis graph build` builds the pinned parser image and can download build
dependencies. `mortis review --graph ...` uses the isolated parser; unavailable
graph context is recorded and the normal source review can continue. Do not
enable networking, host mounts, inherited credentials or Docker-socket access
on the parser container.

Graph flags require `--graph`: `--graph-max-files`, `--graph-max-file-bytes`,
`--graph-max-source-bytes`. Their positive bounds cannot be unlimited; inspect
help/README before overriding. Persistent graph queries belong to the app.

Only after the user confirms rotation/revocation of a reported secret, add
`--acknowledge-rotation FINDING_ID` to a new scan of the same scope. Removing a
secret from source alone is not rotation.

History and retained snapshots live under `$XDG_STATE_HOME/mortis` (default
`~/.local/state/mortis`). Preserve previous reports and reservations. For offline
tests or examples, set both XDG configuration and state paths to temporary,
absolute directories; never aim tests at personal profiles, history or vaults.
