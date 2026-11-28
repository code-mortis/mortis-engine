"""Current local finding decisions, separate from immutable analysis reports."""

import copy
import json
import time

from .history import History


def snapshot(history, kind, scope):
    return {row["finding"]: row["version"] for row in history.db.execute(
        "SELECT finding, MAX(version) AS version FROM triage_events WHERE kind=? AND scope=? GROUP BY finding",
        (kind, scope))}


def _events(history, kind, scope, versions=None):
    rows = history.db.execute(
        "SELECT * FROM triage_events WHERE kind=? AND scope=? ORDER BY finding, version", (kind, scope))
    return [dict(row) for row in rows if versions is None or row["version"] <= versions.get(row["finding"], 0)]


def apply_decisions(history, report, kind, scope, versions=None):
    """Overlay only explicit decisions; leave observed evidence and coverage intact."""
    events = _events(history, kind, scope, versions)
    latest = {event["finding"]: event for event in events}
    field = "status" if kind == "review" else "state"
    for finding in report.get("findings", []):
        event = latest.get(finding.get("id"))
        if event:
            finding.setdefault("evidence_status", finding.get(field, "open"))
            finding[field] = event["state"]
            finding["triage"] = {key: event[key] for key in ("state", "reason", "version", "created", "run")}
            if kind == "scan" and event["state"] == "open":
                finding.pop("rotation", None)
    for item in report.get("history", []):
        event = latest.get(item.get("id"))
        if event:
            observed_status = item.get("status", "open")
            item["status"] = event["state"]
            if isinstance(item.get("snapshot"), dict):
                item["snapshot"].setdefault("evidence_status", observed_status)
                item["snapshot"]["status"] = event["state"]
    return report


def effective_report(history, run_id, *, triage_snapshot=None, assessment_runs=None):
    """Return a copy with successful follow-ups and chosen decision versions."""
    row = history.row(run_id)
    if row["kind"] not in {"review", "scan"}:
        raise ValueError("Choose a review or security scan run.")
    report = history.report(run_id)
    by_id = {item["id"]: item for item in report.get("findings", []) if item.get("id")}
    for retry_id in history.previous_ids("assessment") if assessment_runs is None else assessment_runs:
        retry = history.report(retry_id)
        if retry.get("parent_run_id") == run_id and retry.get("finding"):
            finding = copy.deepcopy(retry["finding"])
            by_id.setdefault(finding["id"], finding)
    report["findings"] = [item for item in report.get("findings", []) if not item.get("id")] + list(by_id.values())
    return apply_decisions(history, report, row["kind"], row["scope"], triage_snapshot)


def _current(history, run_id):
    origin = history.row(run_id)
    if origin["kind"] not in {"review", "scan"}:
        raise ValueError("Choose a review or security scan run.")
    finished = history.previous_ids(origin["kind"], origin["scope"])
    if origin["kind"] == "scan" and finished:
        report = scan_history(history, finished[-1], previous_runs=finished)
        current = {"findings": [finding for finding in report["findings"] if finding.get("id")]}
    else:
        observed = {}
        for identity in finished:
            report = effective_report(history, identity)
            for retained in report.get("history", []):
                if retained.get("id") and isinstance(retained.get("snapshot"), dict):
                    observed[retained["id"]] = {**retained["snapshot"], "status": retained.get("status", "open")}
            for finding in report.get("findings", []):
                if finding.get("id"):
                    observed[finding["id"]] = finding
        current = apply_decisions(history, {"findings": list(observed.values())}, origin["kind"], origin["scope"])
    versions = snapshot(history, origin["kind"], origin["scope"])
    field = "status" if origin["kind"] == "review" else "state"
    for finding in current["findings"]:
        finding["version"] = versions.get(finding["id"], 0)
        finding["decision"] = finding.get(field, "open")
        finding.setdefault("evidence_status", finding.get(field, "open"))
    return origin, current["findings"]


def _scan_observation(finding):
    evidence = finding.get("evidence")
    if not isinstance(evidence, dict):
        evidence = finding
    return (finding.get("version"), evidence.get("source_sha"), finding.get("fingerprint"),
            finding.get("evidence_status", finding.get("state", "open")))


def _reconcile_scan_observations(history, previous_runs, run_id, report, scope):
    by_id, observed_by_run, retry_reports = {}, {}, {}
    for parent in previous_runs:
        row = history.row(parent, "scan")
        if row["scope"] != scope:
            raise ValueError("The saved security history belongs to a different scan scope.")
        observations = report if parent == run_id else history.report(parent)
        definitive = {check["finding_id"] for check in observations.get("verifications", [])
                      if check["verdict"] == "still_present"
                      or check["verdict"] == "fixed" and observations.get("status") == "completed"}
        signatures, predecessors = {}, None
        for finding in observations.get("findings", []):
            if not (identity := finding.get("id")):
                continue
            signature = signatures[identity] = _scan_observation(finding)
            current = by_id.get(identity)
            if current and signature != _scan_observation(current) and identity not in definitive:
                if predecessors is None:
                    plan = json.loads(row["plan"])
                    parents = set(plan.get("previous", [])) & observed_by_run.keys()
                    predecessors = [observed_by_run[prior] for prior in parents]
                    retry_observations = {}
                    for retry_id in plan.get("assessment_runs", []):
                        if retry_id not in retry_reports:
                            retry_reports[retry_id] = history.report(retry_id)
                        retry = retry_reports[retry_id]
                        if retry.get("parent_run_id") in parents and (confirmed := retry.get("finding")):
                            retry_observations.setdefault(confirmed["id"], _scan_observation(confirmed))
                    predecessors.append(retry_observations)
                # A carry-forward from a shared predecessor cannot undo a sibling's observation.
                if any(prior.get(identity) == signature for prior in predecessors):
                    continue
            by_id[identity] = finding
        observed_by_run[parent] = signatures
    return by_id


def scan_history(history, run_id, *, triage_snapshot=None, assessment_runs=None, previous_runs=None, legacy=False):
    """Reconcile finished same-scope scans through the saved history anchor."""
    origin = history.row(run_id, "scan")
    scope = origin["scope"]
    if legacy:
        # Unfinished old scans must reproduce their original assessment and verification inputs.
        report = effective_report(history, run_id, triage_snapshot=triage_snapshot, assessment_runs=assessment_runs)
        parents = set(history.previous_ids("scan", scope))
        by_id = {finding["id"]: finding for finding in report.get("findings", []) if finding.get("id")}
    else:
        report = history.report(run_id)
        if previous_runs is None:
            finished = history.previous_ids("scan", scope)
            previous_runs = finished[:finished.index(run_id) + 1]
        elif not previous_runs or previous_runs[-1] != run_id:
            raise ValueError("The saved security history has a different latest run.")
        parents = set(previous_runs)
        by_id = _reconcile_scan_observations(history, previous_runs, run_id, report, scope)
    for retry_id in history.previous_ids("assessment") if assessment_runs is None else assessment_runs:
        retry = history.report(retry_id)
        if retry.get("parent_run_id") in parents and retry.get("finding"):
            finding = copy.deepcopy(retry["finding"])
            by_id.setdefault(finding["id"], finding)
    report["findings"] = [finding for finding in report.get("findings", []) if not finding.get("id")] + list(by_id.values())
    return apply_decisions(history, report, "scan", scope, triage_snapshot)


def list_findings(repository, *, run_id, include_history=False):
    with History(repository) as history:
        origin, findings = _current(history, run_id)
        result = {"schema_version": "mortis.findings/v1", "run_id": run_id, "kind": origin["kind"],
                  "scope": origin["scope"], "findings": findings,
                  "counts": {state: sum(f["decision"] == state for f in findings)
                             for state in sorted({f["decision"] for f in findings})}}
        if include_history:
            result["history"] = _events(history, origin["kind"], origin["scope"])
        return result


def triage(repository, *, run_id, finding_id, state, version, reason):
    if type(version) is not int or version < 0:
        raise ValueError("Finding version must be a nonnegative integer.")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
        raise ValueError("Enter a nonblank decision reason of at most 4000 characters.")
    with History(repository) as history:
        origin = history.row(run_id)
        choices = {"review": {"open", "dismissed"}, "scan": {"open", "false_positive", "accepted_risk"}}
        if origin["kind"] not in choices or state not in choices[origin["kind"]]:
            raise ValueError("Unsupported finding decision for this workflow.")
        report = effective_report(history, run_id)
        finding = next((f for f in report["findings"] if f.get("id") == finding_id), None)
        if finding is None:
            finding = next((f["snapshot"] for f in report.get("history", [])
                            if f.get("id") == finding_id and isinstance(f.get("snapshot"), dict)), None)
        if finding is None:
            raise ValueError("The finding does not belong to this saved run.")
        from .review.context import sanitize
        from .security_scans.service import secret_rules

        rules = [rule for identity in history.previous_ids("scan") for rule in secret_rules(history.report(identity))]
        reason = sanitize(reason.strip(), {"redaction_rules": rules}).strip()
        if not reason:
            raise ValueError("Enter a nonblank decision reason.")
        try:
            history.db.execute("BEGIN IMMEDIATE")
            current = snapshot(history, origin["kind"], origin["scope"]).get(finding_id, 0)
            if version != current:
                raise ValueError("The finding changed. Refresh mortis findings before updating its state.")
            _, current_findings = _current(history, run_id)
            current_finding = next(f for f in current_findings if f["id"] == finding_id)
            event = {"kind": origin["kind"], "scope": origin["scope"], "finding": finding_id,
                     "version": current + 1, "run": run_id, "previous": current_finding["decision"],
                     "state": state, "reason": reason, "created": time.time()}
            history.db.execute("INSERT INTO triage_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(event.values()))
            history.db.commit()
        except BaseException:
            history.db.rollback()
            raise
        return {"schema_version": "mortis.triage/v1", **event}
