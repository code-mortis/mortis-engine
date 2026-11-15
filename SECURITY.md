# Security

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/code-mortis/mortis-engine/security/advisories/new)
when the repository's private reporting form is enabled. If it is unavailable,
open an issue asking maintainers to enable private reporting, without disclosing
the vulnerability. Do not put exploit details, credentials or private source in
public issues.

Include the affected version, operating system, a minimal sanitized reproduction,
expected/observed behavior and impact. Maintainers will assess the report before
coordinating a fix and disclosure. No response-time guarantee is offered.

Until the first release is published, report against the current main revision.
After publication, security fixes target the latest published version; older
versions may require an upgrade.

## Trust boundaries

Mortis runs locally and sends eligible source, diffs and supporting evidence to
the AI connection you select. Configure a provider and destination you trust.
Provider retention and access policies apply to data sent there.

Mortis treats analyzed source and repository instructions as data. It validates
findings against pinned revisions and does not execute analyzed tests or proposed
patches. Trusted committed `mortis.toml` settings control analysis policy.
Optional graph parsing uses a separate Docker container without network, host
mounts or inherited credentials. Trivy security scans refresh advisory data and
may perform documented advisory lookups.

Connection credentials use the OS credential vault or referenced environment
variables. Local history, snapshots and exported reports can contain source and
finding details. Protect them as you would the analyzed repository. Redaction is
not a guarantee that every private value will be removed.

Never commit provider credentials, personal profiles, history databases or real
source-bearing reports. Use synthetic fixtures for tests. Rotate a real credential
if it was exposed; deleting the current file does not remove it from Git history.
