import json

import pytest

from mortis_engine import gateway
from mortis_engine.history import History
from mortis_engine.review import review_commits, review_local, service
from mortis_engine.schemas import Response, Usage

from .git_helpers import commit, git
from .test_workflows import decision


@pytest.fixture
def comparison_repo(repo, tmp_path_factory, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("incremental-state")))
    paths = [f"{letter}.py" for letter in "abcde"]
    commit(repo, {path: f"{path[0]} = 0\n" for path in paths})
    git(repo, "switch", "-qc", "topic")
    commit(repo, {path: f"{path[0]} = 1\n" for path in paths})
    return paths


@pytest.fixture
def provider(monkeypatch):
    requests = {"analysis": [], "summary": []}
    behavior = {"report_finding": False, "fail_summary": False}

    async def send(connection, credentials, request, **kwargs):
        properties = request.output_schema["properties"]
        content = request.messages[0].content
        payload = json.loads(content.split("\n\n", 2)[1] if content.startswith("# ") else content)
        if "narrative" in properties:
            requests["summary"].append(payload["paths"])
            if behavior["fail_summary"]:
                raise TimeoutError("Fixture summary timeout")
            output = {"narrative": "Fixture summary", "groups": []}
        elif "verdict" in properties:
            source_id = next(key for key, item in payload["citation_sources"].items()
                             if item["revision"] == "current" and item["path"] == "a.py")
            output = decision(evidence=[{"source_id": source_id, "start_line": 1, "end_line": 1}])
        else:
            units = payload["assigned_units"]
            requests["analysis"].append(units)
            findings = []
            if behavior["report_finding"]:
                for unit, source in zip(units, payload["citation_sources"], strict=True):
                    if unit["path"] == "a.py":
                        findings.append({"unit_key": unit["key"], "path": "a.py", "start_line": 1, "end_line": 1,
                            "severity": "high", "category": "correctness", "content": "Invalid fixture value",
                            "trigger": "The fixture value is read", "consequence": "The wrong value is returned",
                            "evidence": [{"source_id": source["source_id"], "start_line": 1, "end_line": 1}]})
            output = {"task_key": payload["task_key"], "completed_units": [unit["key"] for unit in units],
                      "findings": findings}
        return Response(output=output, usage=Usage(total_tokens=3))

    monkeypatch.setattr(gateway, "send", send)
    return requests, behavior


@pytest.fixture
def connection():
    return {"provider": "custom", "protocol": "openai", "model": "fixture", "options": {}}


def paths(report, coverage):
    return [item["path"] for item in report["coverage"][coverage]]


def test_incremental_review_and_unchanged_skip(repo, comparison_repo, provider, connection):
    requests, behavior = provider
    behavior["report_finding"] = True

    def review(get_credentials=dict, **overrides):
        return review_commits(repo, base="main", connection=connection, get_credentials=get_credentials,
                              overrides={"incremental": True, "skip_unchanged": True, **overrides})

    base = git(repo, "rev-parse", "main")
    first = review()
    assert first["comparison"]["reason"] == "no_baseline"
    assert paths(first, "completed") == comparison_repo
    finding, = first["findings"]
    behavior["report_finding"] = False

    current = first
    for file in ("b.py", "c.py"):
        head = commit(repo, {file: f"{file[0]} = 2\n"})
        previous = current
        refs = git(repo, "show-ref")
        objects = git(repo, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)")
        index = (repo / ".git" / "index").read_bytes()
        current = review()
        assert (current["base_sha"], current["merge_base"], current["head_sha"], current["settings_sha"]) == (
            base, base, head, base)
        assert current["comparison"]["strategy"] == "incremental"
        assert current["comparison"]["baseline_run_id"] == previous["run_id"]
        assert paths(current, "completed") == [file]
        assert paths(current, "reused") == [path for path in comparison_repo if path != file]
        unit, = requests["analysis"][-1]
        assert unit["path"] == file and f"-{file[0]} = 0" in unit["diff"]
        assert requests["summary"][-1] == comparison_repo
        retained, = current["history"]
        assert (retained["id"], retained["status"], retained["observation"]) == (finding["id"], "open", "not_observed")
        assert git(repo, "status", "--porcelain") == ""
        assert git(repo, "show-ref") == refs and (repo / ".git" / "index").read_bytes() == index
        assert git(repo, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)") == objects

    git(repo, "commit", "--allow-empty", "-qm", "empty")
    before = {stage: len(items) for stage, items in requests.items()}
    skipped = review(get_credentials=lambda: pytest.fail("Unchanged comparison loaded credentials"))
    assert skipped["status"] == "skipped" and skipped["head_sha"] != current["head_sha"]
    assert skipped["comparison"]["strategy"] == "skipped_unchanged"
    assert skipped["comparison"]["baseline_run_id"] == current["run_id"]
    assert skipped["usage"]["total_tokens"] == 0 and skipped["usage"]["requests"] == []
    assert skipped["findings"] == [] and skipped["history"][0]["status"] == "open"
    assert {stage: len(items) for stage, items in requests.items()} == before
    assert paths(skipped, "completed") == [] and paths(skipped, "reused") == comparison_repo
    git(repo, "commit", "--amend", "--allow-empty", "-qm", "rewrite empty commit")
    rewritten = review(incremental=False, get_credentials=lambda: pytest.fail("Rewritten head loaded credentials"))
    assert rewritten["comparison"]["strategy"] == "skipped_unchanged"
    assert rewritten["comparison"]["baseline_run_id"] == skipped["run_id"]
    assert {stage: len(items) for stage, items in requests.items()} == before
    assert paths(review(incremental=False, skip_unchanged=False), "completed") == comparison_repo


def test_incremental_rename_and_revert(repo, comparison_repo, provider, connection):
    kwargs = {"base": "main", "connection": connection, "get_credentials": dict, "overrides": {"incremental": True}}
    review_commits(repo, **kwargs)
    git(repo, "mv", "a.py", "renamed.py")
    commit(repo, {"b.py": "b = 0\n"})
    report = review_commits(repo, **kwargs)
    assert paths(report, "selected") == ["c.py", "d.py", "e.py", "renamed.py"]
    assert report["comparison"]["strategy"] == "full"
    assert report["comparison"]["reason"] == "affected_callers_require_full_review"
    assert paths(report, "completed") == paths(report, "selected")
    assert paths(report, "reused") == []
    assert provider[0]["summary"][-1] == ["a.py", *paths(report, "selected")]


@pytest.mark.parametrize("change", ["base", "model", "partial", "force_push", "legacy", "limit"])
def test_incompatible_or_incomplete_history_does_not_waive_full_review(
    repo, comparison_repo, provider, connection, change,
):
    _, behavior = provider
    behavior["fail_summary"] = change == "partial"
    overrides = {"incremental": True, "skip_unchanged": True}
    if change == "limit":
        overrides["max_files"] = len(comparison_repo)
    kwargs = {"base": "main", "connection": connection, "get_credentials": dict, "overrides": overrides}
    first = review_commits(repo, **kwargs)
    behavior["fail_summary"] = False
    if change == "base":
        git(repo, "switch", "-q", "main")
        commit(repo, {"mortis.toml": '[[review.rules.rules]]\npath = "**/*.py"\nrule = "Check numeric bounds."\n'})
        git(repo, "switch", "-q", "topic")
    elif change == "model":
        kwargs["connection"] = {**connection, "model": "other-model"}
    elif change == "legacy":
        with History(repo) as history:
            plan = json.loads(history.row(first["run_id"])["plan"])
            del plan["comparison"]
            with history.db:
                history.db.execute("UPDATE runs SET plan=? WHERE id=?", (json.dumps(plan), first["run_id"]))
    if change == "force_push":
        git(repo, "reset", "--hard", "main")
        commit(repo, {file: f"{file[0]} = 2\n" for file in comparison_repo})
    elif change == "limit":
        commit(repo, {"f.py": "f = 1\n"})
    else:
        commit(repo, {"b.py": "b = 2\n"})
    result = review_commits(repo, **kwargs)
    assert paths(result, "reused") == []
    if change == "limit":
        assert result["comparison"]["strategy"] == "incremental"
        assert paths(result, "waived") == [*comparison_repo, "f.py"]
    else:
        assert result["comparison"]["strategy"] == "full"
        assert paths(result, "completed") == comparison_repo


def test_resume_keeps_pinned_incremental_selection(repo, comparison_repo, provider, connection, monkeypatch):
    kwargs = {"base": "main", "connection": connection, "get_credentials": dict, "overrides": {"incremental": True}}
    first = review_commits(repo, **kwargs)
    head = commit(repo, {"b.py": "b = 2\n"})

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    with monkeypatch.context() as patch, pytest.raises(KeyboardInterrupt):
        patch.setattr(service.engine, "run", interrupt)
        review_commits(repo, **kwargs)
    with History(repo) as history:
        run = history.list()[0]
        saved = json.loads(history.row(run["id"])["plan"])
        assert saved["comparison"]["baseline_run_id"] == first["run_id"]
        assert not run["finished"]

    commit(repo, {"c.py": "c = 3\n"})
    review_commits(repo, **kwargs)
    result = review_local(repo, connection=connection, get_credentials=dict, resume=run["id"])
    assert result["head_sha"] == head and result["comparison"]["baseline_run_id"] == first["run_id"]
    assert paths(result, "completed") == ["b.py"]
