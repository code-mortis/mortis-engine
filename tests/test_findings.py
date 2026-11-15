import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from mortis_engine import findings
from mortis_engine.cli import main
from mortis_engine.history import History, encode


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
