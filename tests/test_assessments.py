import asyncio
import hashlib
import subprocess

import pytest

from mortis_engine import assessments, gateway, operations
from mortis_engine.history import History, encode
from mortis_engine.llm_http import InferenceError
from mortis_engine.review import source, workflows
from mortis_engine.review.context import source_id
from mortis_engine.review.service import effective_connection
from mortis_engine.review.settings import RepoSettings
from mortis_engine.schemas import Response, Usage
from mortis_engine.security_scans import service as security
from mortis_engine.workflow import Session

CONNECTION = {"kind": "api", "provider": "test", "protocol": "openai", "model": "fixture",
              "base_url": "https://example.com/v1", "options": {}}


def git(repo, *args):
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Test", "-c",
                           "user.email=test@localhost", "-c", "commit.gpgsign=false", *args],
                          cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "app.py").write_text("def inverse(value):\n    return value\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("def inverse(value):\n    return 1 / value\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "head")
    return repo, base, git(repo, "rev-parse", "HEAD")


def decision(verdict="confirmed"):
    return {"verdict": verdict, "relationship": "new", "finding_id": None,
            "explanation": "Zero is allowed and raises division by zero", "cause": "Division lacks guard",
            "trigger": "Call with zero", "consequence": "Caller crashes", "history_explanation": "",
            "contradicts_prior": False, "changed_conditions": "", "suggestion_supported": False,
            "evidence": [{"source_id": source_id("current", "app.py"), "start_line": 2, "end_line": 2}],
            "required_paths": []}


def failed_review(local, monkeypatch, *, unknown=False):
    repo, base, head = local
    snapshot = {"path": "app.py", "start_line": 2, "end_line": 2, "content": "Zero crashes division",
                "existing_code": "    return 1 / value", "suggestion_code": "unsafe suggestion", "source_sha": head,
                "severity": "high", "category": "correctness"}
    options = {**RepoSettings().model_dump(), "_settings_sha": base}

    async def fail(*args, **kwargs):
        raise InferenceError("fixture", "Original failure", consumption_unknown=unknown, usage=Usage(total_tokens=None if unknown else 7))

    monkeypatch.setattr(gateway, "send", fail)
    with History(repo) as history, source.local_repository(repo, object_cache=history.objects) as (isolated, env):
        run_id = history.begin("review", "main", {"triage_versions": {}, "profile": "fixture"})
        session = Session(effective_connection(CONNECTION, options), {}, history=history, run_id=run_id)
        report = {"schema_version": "mortis.review/v1", "run_id": run_id, "head_sha": head, "base_sha": base,
                  "settings_sha": base, "settings": options, "status": "partial", "findings": [],
                  "coverage": {"selected": [{"path": "app.py"}], "completed": [{"path": "app.py"}]}}
        with pytest.raises(InferenceError):
            asyncio.run(workflows._assess(isolated, env, workflows._run(report, options, session), snapshot,
                                         {"findings": [], "prior_candidates": []}, session, candidate_id="candidate-fixture"))
        report["candidates"] = [{"id": "candidate-fixture", "snapshot": snapshot, "status": "failed",
                                 "error": "Original failure", "result": None}]
        report["usage"] = session.usage()
        history.finish(run_id, report)
    return run_id


@pytest.mark.parametrize("verdict", ["confirmed", "rejected", "uncertain"])
def test_retry_review_exact_input_idempotency_and_immutable_origin(local, monkeypatch, verdict):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    with History(repo) as history:
        original = history.row(parent)["report"]
        plan = history.row(parent)["plan"]
        value = history.assessment_inputs(parent)["candidate-fixture"]
    assert value["schema_version"] == "mortis.assessment-input/v1"
    assert assessments.list_assessments(repo, run_id=parent)["candidates"][0]["retry_eligible"]
    sent = []

    async def send(connection, credentials, request, **kwargs):
        sent.append(request)
        assert "adjudicate" in request.instructions.lower()
        return Response(output=decision(verdict), usage=Usage(total_tokens=11))

    monkeypatch.setattr(gateway, "send", send)
    (repo / "app.py").write_text("the current worktree must never be evidence")
    result = operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture",
                                          connection=CONNECTION, get_credentials=lambda: {})
    assert result["schema_version"] == "mortis.assessment/v1"
    assert result["status"] == "completed" and result["result"]["verdict"] == verdict
    assert result["usage"]["total_tokens"] == 11 and result["lineage_usage"]["total_tokens"] == 18
    assert bool(result["finding"]) == (verdict == "confirmed")
    again = operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture",
                                       connection=CONNECTION, get_credentials=lambda: pytest.fail("credentials read"))
    assert again == result and len(sent) == 1
    with History(repo) as history:
        assert history.row(parent)["report"] == original and history.row(parent)["plan"] == plan
        assert history.report(result["run_id"]) == result
    current = operations.list_findings(repo, run_id=parent)["findings"]
    assert len(current) == (1 if verdict == "confirmed" else 0)
    if current:
        assert current[0]["id"] == result["finding"]["id"] and current[0]["decision"] == "open"


def test_unknown_original_usage_and_changed_connection_block_before_credentials(local, monkeypatch):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch, unknown=True)
    row = operations.list_assessments(repo, run_id=parent)["candidates"][0]
    assert not row["retry_eligible"] and "Unknown" in row["blocked_reason"]
    with pytest.raises(ValueError, match="Unknown"):
        operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                    get_credentials=lambda: pytest.fail("credentials read"))
    parent = failed_review(local, monkeypatch)
    with pytest.raises(ValueError, match="connection changed"):
        operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture",
                                    connection={**CONNECTION, "model": "changed"},
                                    get_credentials=lambda: pytest.fail("credentials read"))


@pytest.mark.parametrize("failure", ["redaction", "identifier", "credentials"])
def test_retry_preflight_failure_does_not_create_child_run(local, monkeypatch, failure):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    with History(repo) as history:
        original = dict(history.row(parent))
    monkeypatch.setattr(gateway, "send", lambda *args, **kwargs: pytest.fail("Dispatched withheld retry"))

    def get_credentials():
        if failure == "credentials":
            raise ValueError("Fixture credentials unavailable")
        return {"api_key": "app.py" if failure == "identifier" else "return 1 / value"}

    error = {"redaction": "safe redaction", "identifier": "structural identifier", "credentials": "credentials unavailable"}
    for _ in range(2):
        with pytest.raises(ValueError, match=error[failure]):
            operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture",
                                        connection=CONNECTION, get_credentials=get_credentials)
        with History(repo) as history:
            assert assessments._children(history, parent, "candidate-fixture") == []
            assert dict(history.row(parent)) == original
        assert operations.list_assessments(repo, run_id=parent)["candidates"][0]["retry_eligible"]


def test_unknown_retry_lineage_cannot_be_bypassed_by_a_new_child(local, monkeypatch):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    with History(repo) as history:
        child = history.begin("assessment", "main", {"parent_run_id": parent, "candidate_id": "candidate-fixture"})
        history.start_attempt(child, {"stage": "assessment", "task_key": "lost", "reserved_tokens": 200,
                              "status": "running", "usage": {}, "consumption_unknown": True}, "input")
        history.finish(child, {"schema_version": "mortis.assessment/v1", "status": "failed"})
    with pytest.raises(ValueError, match="Unknown"):
        operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                    get_credentials=lambda: pytest.fail("credentials read"))


def test_retry_resumes_cached_assessment_without_dispatching_twice(local, monkeypatch):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    sent = []

    async def send(*args, **kwargs):
        sent.append(1)
        return Response(output=decision(), usage=Usage(total_tokens=11))

    monkeypatch.setattr(gateway, "send", send)
    real_finding = assessments._finding
    monkeypatch.setattr(assessments, "_finding", lambda *args: (_ for _ in ()).throw(RuntimeError("interrupted after validation")))
    with pytest.raises(RuntimeError, match="interrupted"):
        operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                    get_credentials=lambda: {})
    with History(repo) as history:
        child = assessments._children(history, parent, "candidate-fixture")[0]["id"]
    monkeypatch.setattr(assessments, "_finding", real_finding)
    result = operations.retry_assessment(repo, resume=child, connection=CONNECTION, get_credentials=lambda: {})
    assert result["status"] == "completed" and result["run_id"] == child and sent == [1]


def test_candidate_claim_and_stale_triage_block_replay(local, monkeypatch):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    claim = hashlib.sha256(encode([parent, "candidate-fixture"]).encode()).hexdigest()[:32]
    with History(repo) as history, history.claim(claim):
        with pytest.raises(ValueError, match="owns"):
            operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                        get_credentials=lambda: pytest.fail("credentials read"))
    with History(repo) as history, history.db:
        history.db.execute("INSERT INTO triage_events VALUES ('review','main','other',1,?,'open','dismissed','reason',1)", (parent,))
    with pytest.raises(ValueError, match="decisions changed"):
        operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                    get_credentials=lambda: pytest.fail("credentials read"))


def test_failed_retry_preserves_cost_and_allows_new_known_attempt(local, monkeypatch):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    result = operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                        get_credentials=lambda: {})
    assert result["status"] == "failed" and result["usage"]["total_tokens"] == 7
    assert result["lineage_usage"]["total_tokens"] == 14
    assert operations.list_assessments(repo, run_id=parent)["candidates"][0]["retry_eligible"]


def test_decision_conflict_retains_validated_retry_result_without_publishing(local, monkeypatch):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    with History(repo) as history:
        other = history.begin("review", "main", {})
        history.finish(other, {"findings": [{"id": "other", "status": "open"}]})

    async def send(*args, **kwargs):
        operations.triage(repo, run_id=other, finding_id="other", state="dismissed", version=0, reason="Changed")
        return Response(output=decision(), usage=Usage(total_tokens=11))

    monkeypatch.setattr(gateway, "send", send)
    result = operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture",
                                         connection=CONNECTION, get_credentials=lambda: {})
    assert result["status"] == "failed" and result["finding"] is None
    assert result["result"]["verdict"] == "confirmed" and "decisions changed" in result["error"]
    with History(repo) as history:
        assert history.report(result["run_id"]) == result
    assert all(f["id"] == "other" for f in operations.list_findings(repo, run_id=parent)["findings"])


def test_legacy_failed_candidate_has_explicit_blocker(local):
    repo, _, head = local
    with History(repo) as history:
        parent = history.begin("review", "main", {})
        history.finish(parent, {"head_sha": head, "findings": [], "candidates": [{"id": "legacy", "snapshot": {"path": "app.py"},
                                                                               "status": "failed", "error": "failed"}]})
    assert "predates" in operations.list_assessments(repo, run_id=parent)["candidates"][0]["blocked_reason"]


def test_scan_failed_candidate_retries_without_scanner_or_analysis(local, monkeypatch):
    repo, _, head = local
    calls = []

    async def initial(connection, credentials, request, **kwargs):
        if "Audit the assigned file" in request.instructions:
            proposal = {"title": "Authorization bypass", "category": "authorization", "severity": "high", "path": "app.py",
                        "explanation": "Missing guard", "trigger": "Zero", "impact": "Crash", "remediation": "Guard input",
                        "verification": "Inspect callers", "evidence": [{"source_id": "source_1", "start_line": 1, "end_line": 2}]}
            return Response(output={"findings": [proposal], "required_paths": []}, usage=Usage(total_tokens=5))
        raise InferenceError("fixture", "Known failed assessment", consumption_unknown=False, usage=Usage(total_tokens=7))

    monkeypatch.setattr(gateway, "send", initial)
    options = security.ScanOptions().model_dump()
    with History(repo) as history:
        parent = history.begin("scan", "main", {"triage_versions": {}, "profile": "fixture"})
        session = Session(effective_connection(CONNECTION, options), {}, history=history, run_id=parent)
        result = asyncio.run(security.scan(repo, ref=head, session=session, scanner=lambda *args: {
            "findings": [], "targets": {}, "problems": [], "metadata": {"database_fresh": True}}))
        result["run_id"] = parent
        history.finish(parent, result)
        original = history.row(parent)["report"]
    candidate = operations.list_assessments(repo, run_id=parent)["candidates"][0]
    assert candidate["status"] == "failed" and candidate["retry_eligible"]

    async def retry(connection, credentials, request, **kwargs):
        calls.append(request.instructions)
        assert "Audit the assigned file" not in request.instructions
        return Response(output={"verdict": "confirmed", "finding_id": None, "explanation": "The supplied code confirms the issue",
            "evidence": [{"source_id": "source_1", "start_line": 1, "end_line": 2}], "required_paths": []}, usage=Usage(total_tokens=13))

    monkeypatch.setattr(gateway, "send", retry)
    monkeypatch.setattr(security, "scan", lambda *args, **kwargs: pytest.fail("Repeated scan"))
    retried = operations.retry_assessment(repo, run_id=parent, candidate_id=candidate["candidate_id"],
                                         connection=CONNECTION, get_credentials=lambda: {})
    assert retried["status"] == "completed" and retried["finding"]["kind"] == "code" and len(calls) == 1
    with History(repo) as history:
        assert history.row(parent)["report"] == original


def test_retention_excludes_secret_binary_and_user_excluded_blobs(local, monkeypatch):
    repo, base, _ = local
    (repo / ".env").write_text("TOKEN=fixture-secret-never-retain\n")
    (repo / "private.key").write_text("fixture-key-never-retain\n")
    (repo / "data.bin").write_bytes(b"binary\x00fixture")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "excluded fixtures")
    head = git(repo, "rev-parse", "HEAD")
    parent = failed_review((repo, base, head), monkeypatch)
    with History(repo) as history:
        value = history.assessment_inputs(parent)["candidate-fixture"]
        assert "retry_blocked_reason" not in value
        check_env = {"PATH": "/usr/bin:/bin", "GIT_OBJECT_DIRECTORY": str(history.objects)}
        for path in (".env", "private.key", "data.bin"):
            blob = git(repo, "rev-parse", head + ":" + path)
            assert blob not in value["retained_objects"]
            with pytest.raises(ValueError):
                source.git(repo, check_env, "cat-file", "-e", blob)


def test_scan_grade_counts_accepted_risk_after_decision_overlay(local, monkeypatch):
    repo, _, head = local
    scope = git(repo, "symbolic-ref", "HEAD")
    with History(repo) as history, history.db:
        history.db.execute("INSERT INTO triage_events VALUES ('scan',?,'risk',1,'origin','open','accepted_risk','temporary',1)", (scope,))

    async def scan(*args, **kwargs):
        return {"schema_version": "mortis.security/v1", "status": "completed", "grade": "A", "commit": head,
                "findings": [{"id": "risk", "kind": "code", "severity": "high", "state": "resolved"}]}

    monkeypatch.setattr(security, "scan", scan)
    result = operations.run(repo, kind="scan", connection=CONNECTION, get_credentials=lambda: {})
    assert result["findings"][0]["state"] == "accepted_risk" and result["grade"] == "D"


def test_missing_retained_object_blocks_listing_and_execution(local, monkeypatch):
    repo, _, _ = local
    parent = failed_review(local, monkeypatch)
    with History(repo) as history, history.db:
        value = history.assessment_inputs(parent)["candidate-fixture"]
        value["retained_objects"].append("0" * 40)
        history.db.execute("UPDATE assessment_inputs SET input=? WHERE run=?", (encode(value), parent))
    row = operations.list_assessments(repo, run_id=parent)["candidates"][0]
    assert not row["retry_eligible"] and "evidence is unavailable" in row["blocked_reason"]
    with pytest.raises(ValueError, match="evidence is unavailable"):
        operations.retry_assessment(repo, run_id=parent, candidate_id="candidate-fixture", connection=CONNECTION,
                                    get_credentials=lambda: pytest.fail("credentials read"))
