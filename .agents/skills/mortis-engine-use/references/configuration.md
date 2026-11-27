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

Mortis validates `extra_body` to protect engine-managed inputs, output and
authentication. Use supported properties, such as `{"reasoning":{"effort":"medium"}}`
with matching run effort. Replacing the whole `reasoning`, `text` or
`output_config` value or the engine's structured output format is rejected.
Bedrock bearer `auth_header` cannot be `X-Amz-Date` or `X-Amz-Security-Token`.

Public endpoints require HTTPS unless the hostname is explicitly trusted through
`CM_TRUSTED_ENDPOINT_HOSTS`. With `CM_ALLOW_PRIVATE_ENDPOINTS=true`, HTTP is
allowed only when every resolved address is private or loopback. Mortis connects
to a validated address and retains the hostname for TLS and HTTP routing. Use
only the endpoint and trust scope the user selected. Repository configuration
does not control endpoint trust. Bedrock manages its own signing headers.
Inference reports `endpoint_dns` for lookup failures and `endpoint_policy` for
address validation failures. Temporary DNS failures are eligible for retry.
A request stopped during DNS resolution releases its token reservation.

## Trusted revision settings

The analyzed repository may contain `mortis.toml`. Editing its working copy does
not change configuration read from an already committed revision:

| Workflow | Configuration source |
| --- | --- |
| `review`, `ask` or `describe` with `--base BASE --head HEAD` | Selected base commit |
| Working-tree or staged review | HEAD |
| `review --all --head REF` | Selected committed REF |
| `scan --ref REF` | Selected committed REF |
| Resume | Saved settings and overrides |

Inspect with `mortis config show --repo /path/to/repo --base REF`. The base
controls rules, exclusions, background and an optional model override. The
profile supplies the provider endpoint and credentials. Use one-run flags for
requested overrides; do not commit configuration just to run an analysis.

## Subsequent branch reviews

`[review].incremental` and `[review].skip_unchanged` are independent booleans
that default to `false`. For a comparison with `--base`, use a branch name or
its checked-out `HEAD` to keep a consistent history scope. `--incremental` and
`--skip-unchanged` enable the corresponding setting for one run.
`--no-incremental` and `--no-skip-unchanged` disable them. Use both negative
flags to force a full comparison.

Incremental analysis uses the cumulative branch diff for files changed since
the last compatible, fully covered head. The summary and `max_files` still
cover the eligible branch comparison. Missing or incomplete history, a changed
Engine version, base or configuration, and unavailable ancestry can trigger a
full comparison. A changed head tree with an eligible deletion or rename gets a
full comparison to check callers again. Fully reverting a reviewed file change
also triggers a full comparison if coverage would otherwise be reused.
Removing a module-level Python function or changing its required or variadic
parameters can bring previously reviewed importers back into scope. Removing or
renaming an optional keyword or displacing a parameter from its positional slot
can also trigger a recheck. Compatible optional additions and body-only changes
allow coverage reuse. If bounded inspection cannot finish, Mortis runs the full
comparison.
`--graph` and `--second-pass` always
use the full comparison. With unchanged skipping enabled, a compatible review
of the same head tree can reuse complete caller coverage even when the commit
SHA changed.

## Bounds and costs

Reviews default to two concurrent AI batches; scans default to four concurrent
file requests. Both accept `--concurrency 1` through `8` and `--effort low|medium|high`.
Processes sharing local state also share a ceiling of eight active model
requests per connection identity. Eligible analysis, assessment and AI fix checks
start as slots open. Requests release their execution slot during retry backoff or
while waiting for tokens to become available. Waiting still consumes the workflow
deadline.

Reviews accept `--max-files`, `--token-budget`, `--timeout-seconds`,
`--max-output-tokens`, `--assessment-token-budget`, `--verification-token-budget`,
`--summary-token-budget` and `--summary-timeout-seconds`. Scans accept the same
flags except file and summary limits. Other workflows expose only relevant
stage limits; consult their help. Budgets apply to their individual stages, not
a single aggregate run budget.

`ask` uses the base commit's review analysis budget and timeout
(`token_budget` and `timeout_seconds`). `describe` uses the review summary
budget and timeout (`summary_token_budget` and `summary_timeout_seconds`).
Both use the committed review file and output limits.

`0` means no Mortis limit for total token budgets, overall timeouts and review
file limits. Preserve finite limits unless the user requests a change.
Provider request timeouts, transport bounds and model/account limits still apply.

`--max-output-tokens` caps each response including reasoning. With `0` and an
unlimited token budget, OpenAI and Bedrock Converse use their service defaults.
Anthropic Messages reads the selected model's output capacity from the Models API.
With `0` and a finite budget, Mortis derives the cap from the remaining budget
and reserves that budget until the request settles. Use a positive limit within
the model's capacity to fit multiple requests or when the provider rejects the
derived cap or cannot report its capacity. Resume restores saved limits
and rejects new ones.

`--second-pass` is an optional review experiment, disabled by default. Do not
enable it or increase limits as an implicit recovery step.

## Graph and secret rotation

Docker is optional for review graph context; Trivy is required for scans.
`mortis graph build` builds the pinned parser image and can download build
dependencies. After upgrading to 0.1.6, rerun the build before using graph
context. `mortis review --graph ...` uses the isolated parser. A review can
use available graph facts when coverage is incomplete and resume with saved
graph context when present. Do not enable networking, host mounts, inherited
credentials or Docker-socket access on the parser container.

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
