"""Constrained public advisory proof for dependency findings without a reported fix."""

import re
from datetime import UTC, datetime

import httpx

from mortis_engine.http_transport import PinnedHTTPTransport, resolve_sync
from mortis_engine.security import safe_endpoint

URL = "https://api.github.com/advisories"


def fetch(cve):
    if not isinstance(cve, str) or not re.fullmatch(r"CVE-\d{4}-\d{4,}", cve):
        raise ValueError("Public advisory lookup requires a validated CVE identifier.")
    resolve_sync(lambda: safe_endpoint(URL), 10)
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=10, transport=PinnedHTTPTransport()) as client:
        with client.stream("GET", URL, params={"cve_id": cve, "type": "reviewed", "per_page": 100},
                           headers={"Accept": "application/vnd.github+json", "User-Agent": "mortis-engine"}) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 2_000_000:
                    raise ValueError("Public advisory response exceeds the 2 MB limit.")
            import json

            return json.loads(body)


def dependency_advisory(evidence, packages, stopped, cache):
    from .service import stable_version

    rule = evidence.get("rule_id", "")
    if evidence.get("package_type") not in {"bun", "npm", "yarn", "pnpm"} or not re.fullmatch(r"CVE-\d{4}-\d{4,}", rule):
        return None
    stopped()
    if rule not in cache:
        try:
            cache[rule] = (fetch(rule), datetime.now(UTC).isoformat())
        except (httpx.HTTPError, OSError, ValueError) as exc:
            cache[rule] = exc
    stopped()
    if isinstance(cache[rule], Exception):
        raise ValueError("GitHub public advisory metadata could not be verified; no dependency fix was established.") from cache[rule]
    rows, checked_at = cache[rule]
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    advisory = rows[0]
    ghsa = advisory.get("ghsa_id", "")
    if (not isinstance(ghsa, str) or not re.fullmatch(r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}", ghsa)
        or advisory.get("cve_id") != rule or advisory.get("type") != "reviewed"
        or "withdrawn_at" not in advisory or advisory["withdrawn_at"] is not None
        or advisory.get("html_url") != f"https://github.com/advisories/{ghsa}"):
        return None
    try:
        updated = datetime.fromisoformat(advisory["updated_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if updated.tzinfo is None or updated > datetime.fromisoformat(checked_at):
        return None
    entries = advisory.get("vulnerabilities")
    if not isinstance(entries, list) or not all(isinstance(row, dict) and isinstance(row.get("package"), dict) for row in entries):
        return None
    entries = [row for row in entries if row["package"] == {"ecosystem": "npm", "name": evidence["package"]}]
    if len(entries) != 1:
        return None
    affected = entries[0].get("vulnerable_version_range")
    # shortcut: only a single inclusive stable npm upper bound proves removal; other ranges stay uncertain.
    stable = r"(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){2}"
    boundary = re.fullmatch(rf"<=\s*({stable})", affected) if isinstance(affected, str) else None
    limit = stable_version(boundary[1]) if boundary else None
    original = stable_version(evidence["installed_version"])
    versions = sorted({p["version"] for p in packages})
    if (not limit or not original or not re.fullmatch(stable, evidence["installed_version"]) or not versions
        or any(p["name"] != evidence["package"] for p in packages) or original > limit
        or any(not re.fullmatch(stable, version) or stable_version(version) <= limit for version in versions)):
        return None
    return {"schema_version": "mortis.dependency-advisory/v1", "ghsa_id": ghsa, "cve_id": rule,
        "url": advisory["html_url"], "updated_at": advisory["updated_at"], "checked_at": checked_at,
        "package": evidence["package"], "ecosystem": "npm", "affected_range": affected,
        "original_version": evidence["installed_version"], "current_versions": versions}
