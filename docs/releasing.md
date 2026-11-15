# Releasing Mortis Engine

Release preparation is local until a maintainer deliberately pushes a version tag.
Adding these files does not publish a package or change repository visibility.
The first PyPI release is pending.

## Prepare and validate

Use Python 3.15 (selected by `.python-version`), a current uv (CI uses 0.13.8)
and the locked development environment:

```sh
uv sync --locked --group dev
uv run --locked pytest -q
uv run --locked ruff check .
uv build
uv run --locked python scripts/check_dist.py
```

Start with an empty `dist/` directory; the archive check allows uv's generated
`.gitignore` but rejects stale or extra artifacts. It verifies archive paths, private/generated-file exclusions, required
runtime/parser assets, license, version metadata, and source/test/evaluation files.
The source archive deliberately retains the frozen evaluation baseline and fixtures
used by tests. Neither archive includes local agent settings or GitHub configuration.

CI also installs each archive in its own fresh environment with the locked runtime
dependencies. To reproduce those checks on macOS or Linux:

```sh
workspace=$PWD
version=$(uv run --locked python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
uv export --locked --no-dev --no-emit-project --no-hashes > /tmp/mortis-runtime-requirements.txt
for archive in "$workspace"/dist/*; do
  environment=$(mktemp -d)
  uv venv --python 3.15 "$environment"
  uv pip install --python "$environment/bin/python" -r /tmp/mortis-runtime-requirements.txt "$archive"
  (cd /tmp && "$environment/bin/python" -I "$workspace/tests/smoke_test.py" "$version" "$workspace")
done
```

The smoke check confirms the installed package is outside the checkout, validates
the CLI/library/metadata versions, and checks parser build assets without running
Docker or calling a provider. Source-checkout tests alone do not establish that
published packages install correctly.

Update `pyproject.toml`, `src/mortis_engine/__init__.py` and versioned README
installation examples together. Refresh `uv.lock` with `uv lock` when package
metadata or dependencies change. Replace the Unreleased changelog entry with the
actual version/date only when preparing that release; never claim a release that
has not been published.

## Public repository preparation

Before changing visibility:

- Review all reachable Git history and branch/tag contents for credentials and
  private source, including files deleted from the current tree. Rotate any exposed
  credentials. Coordinate any required history rewrite separately.
- Review existing Actions logs, artifacts, issues and pull requests for private
  data. Actions history and logs become visible when the repository becomes public.
- Keep real profiles, history databases, source-bearing reports and personal agent
  configuration outside Git. `reports/`, `.local/`, `.codex/` and planning notes
  named `PLAN.md` or `REVIEW_IMPROVEMENTS.md` are ignored. Ignore rules do not
  untrack files already committed.
- Check `git diff` and `git status` before committing. The local cleanup archive
  under `.local/publication-archive/` is private workspace material.
- Confirm README links, issue forms, CODEOWNERS and package metadata point to this
  repository and its current maintainers.

See [GitHub's visibility documentation](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)
for the effects of making a repository public.

The following are account/repository settings, not configuration supplied by these files:

- Protect `main` against deletion and force pushes. Require the Python 3.13,
  3.14 and 3.15 CI checks using their names from the first hosted run, and review workflow
  changes through PRs.
- Restrict creating/updating/deleting `v*` tags to release maintainers.
- Enable private vulnerability reporting before advertising the reporting form,
  plus dependency alerts, secret scanning and push protection where available.
- Keep fork PR workflows read-only and require approval for untrusted contributions.
- Configure the `pypi` environment and publisher described below before a release.

These settings and hosted workflow execution must be verified in GitHub; a local
Actionlint pass does not establish that protections are enabled or a hosted run passed.

## One-time PyPI setup, at publication time

1. Check that `mortis-engine` is available to your PyPI account, or that you have
   publishing access to the existing project. Enable two-factor authentication.
2. Create the GitHub environment `pypi`. Restrict deployments to `v*` tags and
   require a maintainer reviewer where supported by the account plan.
3. Add a PyPI pending Trusted Publisher (or a publisher on your existing project):

   | Field | Value |
   | --- | --- |
   | PyPI project | `mortis-engine` |
   | GitHub owner | `code-mortis` |
   | Repository | `mortis-engine` |
   | Workflow filename | `release.yml` |
   | Environment | `pypi` |

No stored PyPI API token is needed. A pending publisher does not reserve the project
name; its first successful publication creates the project.
See [PyPI's first-publication guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
and [publishing guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/).
An optional TestPyPI rehearsal needs a separate publisher/environment and upload
destination; the production workflow in this repository targets PyPI only.

## Publish only when ready

The release workflow accepts stable `vMAJOR.MINOR.PATCH` tags. It validates the tag,
package and CLI versions, requires the triggering SHA to match the checkout/tag
commit, and requires that commit to belong to `origin/main`.

After merging the prepared release and verifying CI, a maintainer can publish a
single explicit tag. These commands start publication:

```sh
git switch main
git pull --ff-only
git tag -a v0.1.0 -m "Mortis Engine 0.1.0"
git push origin v0.1.0
```

The workflow runs CI again for that exact commit, builds and smoke-tests the wheel
and source archive, and uploads the validated files as the `distributions` artifact.
A separate job downloads those files and publishes them to PyPI using OIDC and
attestations. It does not check out or execute project code. Only that job receives
`id-token: write`. A final job receives `contents: write` to create the GitHub Release
and attach the same distributions, with generated release notes.

Publication does not cancel an earlier run for the same tag or replace an existing
GitHub Release. Do not move a published tag or try to overwrite a PyPI version.
After publication, verify the PyPI files/provenance, GitHub assets and installation:

```sh
uv tool install mortis-engine==0.1.0
mortis --version
mortis --help
```

If PyPI publication succeeded but the GitHub Release job failed, rerun only the
failed job while its validated artifacts are retained (14 days). Do not rebuild
or repeat the PyPI publication to repair a GitHub-only failure. If a GitHub Release
already exists, inspect it before making any manual correction; the workflow
intentionally refuses replacement.

PyPI publication, GitHub Release creation and repository visibility are separate
operations. Confirm each result independently.
