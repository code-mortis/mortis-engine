import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from mortis_engine.review import assessment, source, verification, workflows
from mortis_engine.review.settings import RepoSettings

from .git_helpers import commit, git
from .test_workflows import Session, candidate, decision, selected


def run_for(base, head, **options):
    return SimpleNamespace(base_sha=base, head_sha=head,
                           config={"settings": {**RepoSettings().model_dump(), **options}})


def assess(repo, run, session, **kwargs):
    with source.local_repository(repo) as (isolated, env):
        return asyncio.run(workflows._assess(isolated, env, run, candidate(run.head_sha),
            {"findings": [], "prior_candidates": []}, session, **kwargs))


def test_logging_edit_requests_base_and_rejects_preexisting_failure(repo):
    base = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n# logging change\n"})

    def respond(stage, context):
        assert context["original_sha"] == base
        if not context["sources"].get("original"):
            return decision(verdict="uncertain", required_base_paths=["app.py"])
        assert context["sources"]["original"]["app.py"] == "def inverse(value):\n    return 1 / value\n"
        return decision(verdict="rejected", explanation="The same reachable division failure predates the logging edit.",
                        evidence=[selected(context, "original"), selected(context)])

    session = Session(respond)
    result = assess(repo, run_for(base, head), session)
    assert result["verdict"] == "rejected"
    assert [item["revision"] for item in result["evidence"]] == ["original", "current"]
    assert all(item["start_line"] == 2 and item["quote"] == "    return 1 / value\n" for item in result["evidence"])
    assert len(session.calls) == 2
    assert "logging-only" in session.calls[0][1]["instructions"]
    assert "required_base_paths" not in result


def test_changed_caller_activates_unchanged_line_and_uses_actual_merge_base(repo):
    ancestor = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n", "caller.py": "inverse(1)\n"})
    git(repo, "checkout", "-qb", "feature")
    head = commit(repo, {"caller.py": "inverse(0)\n"})
    git(repo, "checkout", "main")
    base = commit(repo, {"caller.py": "inverse(2)\n"})

    def respond(stage, context):
        assert context["original_sha"] == context["attribution"]["merge_base_sha"] == ancestor
        if "caller.py" not in context["sources"].get("original", {}):
            return decision(verdict="uncertain", required_paths=["caller.py"], required_base_paths=["caller.py"])
        assert context["sources"]["original"]["caller.py"] == "inverse(1)\n"
        assert context["sources"]["current"]["caller.py"] == "inverse(0)\n"
        return decision(explanation="The changed caller now sends zero to the unchanged division.",
            evidence=[selected(context), selected(context, path="caller.py", line=1),
                      selected(context, "original", path="caller.py", line=1)])

    result = assess(repo, run_for(base, head), Session(respond))
    assert result["verdict"] == "confirmed"
    assert result["evidence"][0]["path"] == "app.py"
    assert result["evidence"][2]["quote"] == "inverse(1)\n"


def test_same_head_confirmation_is_reassessed_when_merge_base_changes(repo):
    base = commit(repo, {"app.py": "def inverse(value):\n    return value\n"})
    introduced = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n# logging change\n"})
    first = assess(repo, run_for(base, head), Session(lambda stage, context: decision(evidence=[selected(context)])))
    assert first["verdict"] == "confirmed"
    previous = {"findings": [{"id": "established", "sha": head, "snapshot": candidate(head),
        "fingerprint": workflows.planning.fingerprint(candidate(head)), "assessment": first,
        "review_sensitivity": RepoSettings().review_sensitivity}], "prior_candidates": []}

    def respond(stage, context):
        assert context["original_sha"] == introduced
        if not context["sources"].get("original"):
            return decision(verdict="uncertain", required_base_paths=["app.py"])
        return decision(verdict="rejected", relationship="duplicate", finding_id=context["history"][0]["id"],
            explanation="The new merge base already contains the failure; only logging changed.",
            history_explanation="Same defect as the earlier confirmed finding, now pre-existing in this comparison.",
            evidence=[selected(context, "original"), selected(context)])

    session = Session(respond)
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows._assess(isolated, env, run_for(introduced, head), candidate(head), previous, session))
    assert len(session.calls) == 2
    assert result["verdict"] == "rejected"
    assert result["relationship"] == "duplicate"
    assert result["finding_id"] == "established"


@pytest.mark.parametrize("options", [{"review_mode": "all"}, {"_assessment_attribution": None}])
def test_same_head_shortcut_is_preserved_without_attribution(repo, options):
    sha = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    run = run_for(sha, sha, **options)
    first = assess(repo, run, Session(lambda stage, context: decision(evidence=[selected(context)])))
    previous = {"findings": [{"id": "established", "sha": sha, "snapshot": candidate(sha),
        "fingerprint": workflows.planning.fingerprint(candidate(sha)), "assessment": first,
        "review_sensitivity": RepoSettings().review_sensitivity}], "prior_candidates": []}
    session = Session(lambda *args: pytest.fail("Legacy and full-tree exact repeats should reuse their assessment"))
    with source.local_repository(repo) as (isolated, env):
        result = asyncio.run(workflows._assess(isolated, env, run, candidate(sha), previous, session))
    assert result["verdict"] == "confirmed"
    assert result["relationship"] == "duplicate"
    assert result["finding_id"] == "established"
    assert not session.calls


def test_base_rename_missing_path_feedback_and_corrected_citation(repo):
    base = commit(repo, {"math.py": "def inverse(value):\n    return 1 / value\n"})
    git(repo, "mv", "math.py", "app.py")
    head = commit(repo, {"caller.py": "inverse(0)\n"})

    def respond(stage, context):
        assert context["attribution"]["renames"] == {"math.py": "app.py"}
        if not context.get("validation_feedback"):
            return decision(verdict="uncertain", required_base_paths=["app.py"])
        assert context["validation_feedback"]["revision"] == "original"
        assert context["validation_feedback"]["available_paths"] == ["math.py"]
        if "math.py" not in context["sources"].get("original", {}):
            return decision(verdict="uncertain", required_base_paths=["math.py"])
        return decision(verdict="rejected", explanation="The rename preserves the same source.",
            evidence=[selected(context, "original", "math.py"), selected(context)])

    session = Session(respond)
    result = assess(repo, run_for(base, head), session)
    assert len(session.calls) == 3
    assert result["evidence"][0]["path"] == "math.py"


def test_missing_base_evidence_stays_uncertain_and_requests_are_bounded(repo):
    base = commit(repo, {"app.py": "def inverse(value):\n    return value\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    session = Session(lambda stage, context: decision(verdict="uncertain", required_base_paths=["missing.py"]))
    result = assess(repo, run_for(base, head), session)
    assert len(session.calls) == 3
    assert result["verdict"] == result["relationship"] == "uncertain"
    assert "three passes" in result["explanation"]
    assert session.calls[1][1]["context"]["validation_feedback"]["reason"] == "source path does not exist in this revision"
    assert all(not call[1]["context"]["sources"].get("original") for call in session.calls)


@pytest.mark.parametrize("path", ["private/notes.py", "visible.py", "binary.py", "link.py"])
def test_base_evidence_preserves_exclusion_rename_binary_and_symlink_boundaries(repo, path):
    base = commit(repo, {"app.py": "def inverse(value):\n    return value\n",
                         "private/notes.py": "restricted\n", "secret.py": "protected\n"})
    (repo / "binary.py").write_bytes(b"\0binary")
    (repo / "link.py").symlink_to("app.py")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "inert boundaries")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "mv", "secret.py", "visible.py")
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    run = run_for(base, head, exclusions="private/**")
    with source.local_repository(repo) as (isolated, env):
        context = assessment.build_context(isolated, env, env, run, candidate(head),
                                           {"findings": [], "prior_candidates": []}, lambda: False)
        with pytest.raises(ValueError):
            assessment.expand_base_context(isolated, env, run, context, [path], lambda: False)
    assert path not in context["sources"].get("original", {})


def test_full_tree_and_legacy_context_keep_original_schema_and_policy(repo):
    sha = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    for options in ({"review_mode": "all"}, {"_assessment_attribution": None}):
        session = Session(lambda stage, context: decision(evidence=[selected(context)]))
        result = assess(repo, run_for(sha, sha, **options), session)
        assert result["verdict"] == "confirmed"
        call = session.calls[0][1]
        assert "attribution" not in call["context"]
        assert "required_base_paths" not in call["schema"]["properties"]
        assert assessment.ATTRIBUTION_INSTRUCTIONS not in call["instructions"]
        if options.get("review_mode") == "all":
            assert "present at this revision" in call["instructions"]


def test_saved_legacy_context_reused_without_rebuilding_or_changing_schema(repo, monkeypatch):
    sha = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    run = run_for(sha, sha, _assessment_attribution=None)
    with source.local_repository(repo) as (isolated, env):
        context = assessment.build_context(isolated, env, env, run, candidate(sha),
                                           {"findings": [], "prior_candidates": []}, lambda: False)
    saved = copy.deepcopy(context)
    session = Session(lambda stage, supplied: decision(evidence=[selected(supplied)]))
    session.run_id = "origin"
    session.history = SimpleNamespace(assessment_inputs=lambda identity: {"candidate": {"context": saved}})
    monkeypatch.setattr(assessment, "build_context", lambda *args, **kwargs: pytest.fail("Saved context was rebuilt"))
    result = assess(repo, run_for(sha, sha), session, candidate_id="candidate")
    assert result["verdict"] == "confirmed"
    assert saved == context
    assert "required_base_paths" not in session.calls[0][1]["schema"]["properties"]


def test_base_source_limits_count_revision_pairs_and_context_bytes(repo, monkeypatch):
    base = commit(repo, {"app.py": "def inverse(value):\n    return value\n"})
    head = commit(repo, {"app.py": "def inverse(value):\n    return 1 / value\n"})
    run = run_for(base, head)
    with source.local_repository(repo) as (isolated, env):
        context = assessment.build_context(isolated, env, env, run, candidate(head),
                                           {"findings": [], "prior_candidates": []}, lambda: False)
        context["sources"]["current"].update({f"file{i}.py": "pass\n" for i in range(99)})
        with pytest.raises(ValueError, match="100-source-file"):
            assessment.expand_base_context(isolated, env, run, context, ["app.py"], lambda: False)
        context["sources"]["current"] = {"app.py": "pass\n"}
        monkeypatch.setattr(verification, "read_requested_source", lambda *args, **kwargs: "x" * 1_500_000)
        with pytest.raises(ValueError, match="context limit"):
            assessment.expand_base_context(isolated, env, run, context, ["app.py"], lambda: False)


def test_base_requests_require_uncertain_and_validate_paths():
    context = {"attribution": {"version": 1}, "history": [], "sources": {}, "finding": {}, "head_sha": "a" * 40}
    with pytest.raises(ValueError, match="remain uncertain"):
        assessment.validate_response(decision(required_base_paths=["app.py"]), context)
    with pytest.raises(ValueError):
        assessment.validate_response(decision(verdict="uncertain", required_base_paths=["../app.py"]), context)
    with pytest.raises(ValueError, match="at most 10"):
        assessment.validate_response(decision(verdict="uncertain", required_paths=[f"{i}.py" for i in range(10)],
                                               required_base_paths=["app.py"]), context)


@pytest.mark.parametrize("legacy", [False, True])
def test_service_pins_attribution_before_analysis_and_restores_legacy(repo, tmp_path_factory, monkeypatch, legacy):
    from mortis_engine.history import History, encode
    from mortis_engine.review import engine, service

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("attribution-state")))
    commit(repo, {"app.py": "value = 1\n"})
    connection = {"kind": "api", "provider": "test", "protocol": "openai", "model": "fixture",
                  "base_url": "https://example.com/v1", "options": {}}
    observed = []

    def interrupted(repo, env, plan, connection, options, credentials, **kwargs):
        observed.append(options["_assessment_attribution"])
        raise KeyboardInterrupt

    monkeypatch.setattr(engine, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.review_local(repo, mode="all", connection=connection, get_credentials=lambda: {})
    with History(repo) as history:
        identity = history.list()[0]["id"]
        saved = json.loads(history.row(identity)["plan"])
        assert saved["assessment_attribution"] == assessment.ATTRIBUTION_VERSION
        if legacy:
            saved.pop("assessment_attribution")
            history.db.execute("UPDATE runs SET plan=? WHERE id=?", (encode(saved), identity))
            history.db.commit()
        original_plan = history.row(identity)["plan"]
    with pytest.raises(KeyboardInterrupt):
        service.review_local(repo, resume=identity, connection=connection, get_credentials=lambda: {})
    assert observed == [assessment.ATTRIBUTION_VERSION, None if legacy else assessment.ATTRIBUTION_VERSION]
    with History(repo) as history:
        assert history.row(identity)["plan"] == original_plan
