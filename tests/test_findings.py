import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from mortis_engine import findings, operations
from mortis_engine.cli import main
from mortis_engine.history import History, encode
from mortis_engine.security_scans.service import observe


@pytest.fixture
def repo(tmp_path, monkeypatch):
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return path


def save(repo, kind="review", scope="main", finding="issue", **extra):
    with History(repo) as history:
        run = history.begin(kind, scope, {})
        report = {"schema_version": "mortis.security/v1" if kind == "scan" else "mortis.review/v1",
                  "findings": [{"id": finding, "path": "app.py", "content": "An issue", "severity": "high",
                                 **extra}], "coverage": {"complete": False}}
        history.finish(run, report)
        return run, history.row(run)["report"]


def test_triage_is_audited_reopenable_and_preserves_reports(repo, monkeypatch):
    run, original = save(repo)
    decision = findings.triage(repo, run_id=run, finding_id="issue", state="dismissed", version=0, reason="Caller checks it")
    assert decision["version"] == 1
    current = findings.list_findings(repo, run_id=run, include_history=True)
    assert current["findings"][0]["decision"] == "dismissed"
    assert current["history"][0]["previous"] == "open"
    monkeypatch.setattr(findings.time, "time", lambda: 1)
    findings.triage(repo, run_id=run, finding_id="issue", state="open", version=1, reason="Contract changed")
    reopened = findings.list_findings(repo, run_id=run)["findings"][0]
    assert reopened["version"] == 2 and reopened["decision"] == "open"
    with History(repo) as history:
        assert history.row(run)["report"] == original
        assert history.report(run)["coverage"] == {"complete": False}


def test_decisions_are_pinned_for_run_inputs_and_scoped(repo):
    run, _ = save(repo)
    with History(repo) as history:
        pinned = findings.snapshot(history, "review", "main")
    findings.triage(repo, run_id=run, finding_id="issue", state="dismissed", version=0, reason="Intentional")
    repeat, _ = save(repo)
    other, _ = save(repo, scope="other")
    with History(repo) as history:
        assert findings.effective_report(history, run, triage_snapshot=pinned)["findings"][0].get("status", "open") == "open"
        assert findings.effective_report(history, repeat)["findings"][0]["status"] == "dismissed"
        assert "status" not in findings.effective_report(history, other)["findings"][0]


def test_triage_validation_and_stale_version(repo):
    run, _ = save(repo)
    kwargs = dict(run_id=run, finding_id="issue", state="dismissed", version=0, reason="Reason")
    for overrides in ({"reason": "  "}, {"finding_id": "missing"}, {"state": "accepted_risk"}, {"version": True}):
        with pytest.raises(ValueError):
            findings.triage(repo, **{**kwargs, **overrides})
    findings.triage(repo, **kwargs)
    with pytest.raises(ValueError, match="changed"):
        findings.triage(repo, **kwargs)


def test_retained_review_evidence_status_survives_decisions(repo):
    run, _ = save(repo)
    with History(repo) as history:
        retained = history.begin("review", "main", {})
        history.finish(retained, {"findings": [], "history": [
            {"id": "issue", "status": "resolved", "snapshot": {"id": "issue", "path": "app.py"}}]})
    findings.triage(repo, run_id=retained, finding_id="issue", state="dismissed", version=0, reason="Fixture")
    item = findings.list_findings(repo, run_id=run)["findings"][0]
    assert item["decision"] == "dismissed" and item["evidence_status"] == "resolved"


def test_concurrent_decisions_do_not_lose_events(repo):
    run, _ = save(repo)
    barrier = Barrier(2)

    def decide(state):
        barrier.wait()
        try:
            return findings.triage(repo, run_id=run, finding_id="issue", state=state, version=0, reason="Race")
        except ValueError:
            return None

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(decide, ["open", "dismissed"]))
    assert sum(value is not None for value in outcomes) == 1
    assert len(findings.list_findings(repo, run_id=run, include_history=True)["history"]) == 1


def test_scan_decision_keeps_evidence_outcome_and_original_grade(repo):
    run, original = save(repo, kind="scan", outcome="present", state="open")
    findings.triage(repo, run_id=run, finding_id="issue", state="accepted_risk", version=0, reason="Temporary exception")
    current = findings.list_findings(repo, run_id=run)["findings"][0]
    assert current["decision"] == "accepted_risk" and current["outcome"] == "present"
    findings.triage(repo, run_id=run, finding_id="issue", state="false_positive", version=1, reason="Unused fixture")
    with History(repo) as history:
        assert history.row(run)["report"] == original
        assert findings.effective_report(history, run)["findings"][0]["state"] == "false_positive"


@pytest.mark.parametrize("state", ["open", "accepted_risk"])
def test_scan_unknown_severity_reopened_by_decision_explains_partial_grade(repo, monkeypatch, state):
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Test", "-c",
                    "user.email=test@localhost", "-c", "commit.gpgsign=false", "commit", "--allow-empty",
                    "-qm", "fixture"], cwd=repo, check=True)
    scope = subprocess.run(["git", "symbolic-ref", "HEAD"], cwd=repo, check=True,
                           capture_output=True, text=True).stdout.strip()
    with History(repo) as history, history.db:
        history.db.execute("INSERT INTO triage_events VALUES ('scan',?,'risk',1,'origin','open',?,'reviewed',1)",
                           (scope, state))

    async def scan(*args, **kwargs):
        return {"schema_version": "mortis.security/v1", "status": "completed", "grade": "A", "problems": [],
                "findings": [{"id": "risk", "severity": "unknown", "state": "resolved", "outcome": "fixed"}],
                "coverage": {"selected_files": 1, "completed_files": 1}, "stages": {"report": {"status": "completed"}}}

    monkeypatch.setattr(operations.security, "scan", scan)
    connection = {"kind": "api", "provider": "test", "protocol": "openai", "model": "fixture",
                  "base_url": "https://example.com/v1", "options": {}}
    result = operations.run(repo, kind="scan", connection=connection, get_credentials=lambda: {})
    assert result["status"] == "partial" and result["grade"] is None
    assert result["problems"] == [
        "Unresolved findings have unknown severity; a security grade cannot be established."]
    assert result["findings"][0]["state"] == state and result["findings"][0]["severity"] == "unknown"
    assert result["coverage"] == {"selected_files": 1, "completed_files": 1}
    assert result["stages"] == {"report": {"status": "completed"}}
    with History(repo) as history:
        assert history.report(result["run_id"]) == result


def test_current_view_includes_retry_confirmations_without_rewriting_origin(repo):
    run, original = save(repo)
    with History(repo) as history:
        followup = history.begin("assessment", "main", {"parent_run_id": run})
        history.finish(followup, {"parent_run_id": run, "finding": {"id": "retry-issue", "path": "other.py"}})
    assert {f["id"] for f in findings.list_findings(repo, run_id=run)["findings"]} == {"issue", "retry-issue"}
    findings.triage(repo, run_id=run, finding_id="retry-issue", state="dismissed", version=0, reason="Covered elsewhere")
    with History(repo) as history:
        assert history.row(run)["report"] == original


def test_scan_history_includes_late_retry_of_older_scan(repo):
    older, _ = save(repo, kind="scan")
    newer, original = save(repo, kind="scan")
    with History(repo) as history:
        retry = history.begin("assessment", "main", {})
        history.finish(retry, {"parent_run_id": older, "finding": {"id": "late", "state": "open"}})
        assert {f["id"] for f in findings.scan_history(history, newer)["findings"]} == {"issue", "late"}
        assert {f["id"] for f in findings.scan_history(history, newer, assessment_runs=[])["findings"]} == {"issue"}
        assert {f["id"] for f in findings.effective_report(history, older, assessment_runs=[])["findings"]} == {"issue"}
        assert history.row(newer)["report"] == original


def test_scan_history_preserves_missing_observations_through_anchor(repo):
    older, older_report = save(repo, kind="scan", finding="carried", state="open")
    save(repo, kind="scan", finding="shared", state="open")
    anchor, anchor_report = save(repo, kind="scan", finding="shared", state="resolved")
    newer, _ = save(repo, kind="scan", finding="newer", state="open")
    save(repo, kind="scan", scope="other", finding="other-scope", state="open")

    with History(repo) as history:
        for parent, identity in ((older, "late-confirmation"), (newer, "newer-confirmation")):
            retry = history.begin("assessment", "main", {})
            history.finish(retry, {"parent_run_id": parent, "finding": {"id": identity, "state": "open"}})

        merged = findings.scan_history(history, anchor)
        assert {row["id"]: row["state"] for row in merged["findings"]} == {
            "carried": "open", "shared": "resolved", "late-confirmation": "open"}
        legacy = findings.scan_history(history, anchor, legacy=True)
        assert {row["id"]: row["state"] for row in legacy["findings"]} == {
            "shared": "resolved", "late-confirmation": "open", "newer-confirmation": "open"}
        assert history.row(older)["report"] == older_report
        assert history.row(anchor)["report"] == anchor_report


@pytest.mark.parametrize("later_verdict", [None, "still_present", "scanner_failed"])
def test_scan_history_keeps_fresh_evidence_across_forked_predecessors(repo, later_verdict):
    scope = "main"
    fixed = {"id": "fixed", "state": "open", "outcome": "present", "version": 2, "source_sha": "1" * 40}
    recurring = {"id": "recurring", "state": "resolved", "outcome": "fixed", "version": 3, "source_sha": "1" * 40}
    retry_finding = {**fixed, "id": "from-retry"}
    with History(repo) as history:
        prior = history.begin("scan", scope, {"previous": []})
        history.finish(prior, {"schema_version": "mortis.security/v1", "status": "completed",
                               "findings": [fixed, recurring]})
        retry = history.begin("assessment", scope, {"parent_run_id": prior})
        history.finish(retry, {"parent_run_id": prior, "finding": retry_finding})

        plan = {"scan_policy": "security-scan/v2", "previous": [prior], "assessment_runs": [retry]}
        first = history.begin("scan", scope, plan)
        second = history.begin("scan", scope, plan)
        direct = {"schema_version": "mortis.security/v1", "status": "completed", "findings": [
            {**fixed, "state": "resolved", "outcome": "fixed", "fix_verification": {"verdict": "fixed"}},
            {**recurring, "state": "open", "outcome": "present", "version": 4, "source_sha": "2" * 40},
            {**retry_finding, "state": "resolved", "outcome": "fixed", "fix_verification": {"verdict": "fixed"}},
        ], "verifications": [{"finding_id": identity, "verdict": "fixed"} for identity in ("fixed", "from-retry")]}
        history.finish(first, direct)

        failed = later_verdict == "scanner_failed"
        verdict = "still_present" if later_verdict == "still_present" else "uncertain"
        second_report = {"schema_version": "mortis.security/v1", "status": "failed" if failed else "partial",
                         "findings": [
                             {**fixed, "outcome": "present" if verdict == "still_present" else "not_observed"},
                             {**recurring, "outcome": "not_observed" if failed else "fixed"},
                             {**retry_finding, "outcome": "not_observed"},
                         ], "verifications": [] if failed else [{"finding_id": "fixed", "verdict": verdict},
                                                                {"finding_id": "from-retry", "verdict": "uncertain"}]}
        history.finish(second, second_report)

        merged = {row["id"]: row for row in findings.scan_history(history, second)["findings"]}
        assert merged["recurring"] == direct["findings"][1]
        assert merged["from-retry"] == direct["findings"][2]
        expected = second_report["findings"][0] if verdict == "still_present" else direct["findings"][0]
        assert merged["fixed"] == expected
        assert history.report(first) == direct and history.report(second) == second_report

    current = {row["id"]: row for row in findings.list_findings(repo, run_id=second)["findings"]}
    assert current["recurring"]["state"] == "open" and current["fixed"]["state"] == expected["state"]


@pytest.mark.parametrize("reobserved", [False, True], ids=["verified", "reobserved"])
def test_failed_sibling_preserves_same_revision_observation(repo, reobserved):
    evidence = {"kind": "code", "source_sha": "a" * 40, "fingerprint": "f" * 64}
    original = observe(evidence)
    check = {"verdict": "still_present", "explanation": "Confirmed in the current source."}
    fresh = observe(evidence, original) if reobserved else {**original, "fix_verification": check}
    verified = [] if reobserved else [{"finding_id": original["id"], **check}]

    with History(repo) as history:
        parent = history.begin("scan", "main", {})
        history.finish(parent, {"status": "completed", "findings": [original]})
        first = history.begin("scan", "main", {"previous": [parent]})
        sibling = history.begin("scan", "main", {"previous": [parent]})
        history.finish(first, {"status": "completed", "findings": [fresh], "verifications": verified})
        history.finish(sibling, {"status": "failed", "findings": [{**original, "outcome": "not_observed"}]})
        assert findings.scan_history(history, sibling)["findings"] == [fresh]

        next_run = history.begin("scan", "main", {"previous": [parent, first, sibling]})
        carried = {**fresh, "outcome": "not_observed"}
        history.finish(next_run, {"status": "failed", "findings": [carried]})
        assert findings.scan_history(history, next_run)["findings"] == [carried]


@pytest.mark.parametrize("legacy", [False, True])
def test_scan_resume_uses_its_saved_history_and_policy(repo, monkeypatch, legacy):
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Test", "-c",
                    "user.email=test@localhost", "-c", "commit.gpgsign=false", "commit", "--allow-empty",
                    "-qm", "fixture"], cwd=repo, check=True)
    scope = subprocess.run(["git", "symbolic-ref", "HEAD"], cwd=repo, check=True,
                           capture_output=True, text=True).stdout.strip()
    older, _ = save(repo, kind="scan", scope=scope, finding="carried", state="open")
    anchor, _ = save(repo, kind="scan", scope=scope, finding="current", state="open")
    calls = []

    async def scan(*args, previous, scan_policy, **kwargs):
        calls.append((scan_policy, [row["id"] for row in previous["findings"]]))
        if len(calls) == 1:
            raise InterruptedError("Fixture interruption")
        return {"schema_version": "mortis.security/v1", "status": "completed", "findings": previous["findings"]}

    monkeypatch.setattr(operations.security, "scan", scan)
    connection = {"kind": "api", "provider": "test", "protocol": "openai", "model": "fixture",
                  "base_url": "https://example.com/v1", "options": {}}
    options = {"kind": "scan", "connection": connection, "get_credentials": lambda: {}}
    with pytest.raises(InterruptedError, match="Fixture interruption"):
        operations.run(repo, **options)
    with History(repo) as history, history.db:
        run_id = next(row["id"] for row in history.list() if not row["finished"])
        plan = json.loads(history.row(run_id)["plan"])
        assert plan["previous"] == [older, anchor]
        assert plan["scan_policy"] == "security-scan/v2"
        if legacy:
            plan.pop("scan_policy")
            plan["previous"] = [anchor]
            history.db.execute("UPDATE runs SET plan=? WHERE id=?", (encode(plan), run_id))

    save(repo, kind="scan", scope=scope, finding="finished-later", state="open")
    result = operations.run(repo, resume=run_id, **options)
    assert calls == [("security-scan/v2", ["carried", "current"]),
                     (None, ["current"]) if legacy else ("security-scan/v2", ["carried", "current"])]
    assert result["run_id"] == run_id
    assert result["grade"] == "D"


@pytest.mark.parametrize("identity", [{}, {"id": None}, {"id": ""}])
def test_scan_history_preserves_findings_without_ids(repo, identity):
    older, _ = save(repo, kind="scan")
    unkeyed = [{"path": path, "content": "An issue", **identity} for path in ("legacy.py", "other.py")]
    report = {"schema_version": "mortis.security/v1", "coverage": {"complete": False},
              "findings": [*unkeyed, {"id": "issue", "state": "open"}]}
    late = {"id": "late", "state": "open"}
    with History(repo) as history:
        newer = history.begin("scan", "main", {})
        history.finish(newer, report)
        retry = history.begin("assessment", "main", {})
        history.finish(retry, {"parent_run_id": older, "finding": late})
        assert findings.scan_history(history, newer) == {**report, "findings": [*report["findings"], late]}
        assert findings.scan_history(history, newer, assessment_runs=[]) == report
        assert history.row(newer)["report"] == encode(report)


def test_legacy_database_upgrade_and_cli_need_no_connection(repo, capsys):
    run, original = save(repo)
    with History(repo) as history:
        database = history.root / "history.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("DROP TABLE triage_events")
    assert main(["triage", "--repo", str(repo), "--run", run, "--finding", "issue", "--state", "dismissed",
                 "--version", "0", "--reason", "Intentional", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == "mortis.triage/v1"
    assert main(["findings", "--repo", str(repo), "--run", run, "--history", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["findings"][0]["decision"] == "dismissed"
    with History(repo) as history:
        assert encode(history.report(run)) == original


@pytest.mark.parametrize("original_rules", [{}, {"a" * 64: ["CFG001", "CFG002"]}])
def test_scan_rule_checkpoint_preserves_first_input_after_upgrade_and_reopen(repo, original_rules):
    run, original_report = save(repo, kind="scan")
    with History(repo) as history, history.db:
        history.db.execute("DROP TABLE scan_rules")

    with History(repo) as history:
        assert history.pin_scan_rules(run, original_rules) == original_rules
        assert history.pin_scan_rules(run, {"b" * 64: ["CFG003"]}) == original_rules
    with History(repo) as history:
        assert history.pin_scan_rules(run, {"c" * 64: ["CFG004"]}) == original_rules
        assert history.row(run)["report"] == original_report
