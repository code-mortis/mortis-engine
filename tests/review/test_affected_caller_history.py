import json

import pytest

from mortis_engine.history import History
from mortis_engine.review import affected_callers, planning, review_commits, review_local, service

from .git_helpers import commit, git
from .test_incremental import connection as connection
from .test_incremental import paths
from .test_incremental import provider as provider

CALLER = 'subprocess.run(["scripts/legacy.sh"], check=True)\n'


@pytest.fixture
def deletion_repo(repo, tmp_path_factory, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("affected-caller-state")))
    commit(repo, {"scripts/legacy.sh": "exit 0\n", "app.py": "import subprocess\n" + CALLER})
    git(repo, "switch", "-qc", "topic")
    git(repo, "rm", "scripts/legacy.sh")
    git(repo, "commit", "-qm", "remove script")
    return repo


@pytest.mark.parametrize("changed_caller", [False, True])
def test_identical_tree_reuses_affected_caller_coverage(deletion_repo, provider, connection, changed_caller, monkeypatch):
    repo = deletion_repo
    if changed_caller:
        commit(repo, {"app.py": "import subprocess\n" + CALLER + "done = True\n"})
    options = {"incremental": True, "skip_unchanged": True}
    announced = []
    first = review_commits(repo, base="main", connection=connection, get_credentials=dict, overrides=options,
                           on_plan=lambda count, model: announced.append((count, model)))
    assert first["status"] == "completed"
    assert announced == [(1, connection["model"])]
    assert paths(first, "completed") == (["app.py"] if changed_caller else [])
    assert {site["path"] for site in first["affected_callers"]["sites"]} == {"app.py"}

    git(repo, "commit", "--allow-empty", "-qm", "same tree")
    requests_before = sum(map(len, provider[0].values()))
    with monkeypatch.context() as patch:
        patch.setattr(planning, "split_units", lambda *a, **kw: pytest.fail("Re-split a covered file"))
        patch.setattr(affected_callers, "discover", lambda *a, **kw: pytest.fail("Re-read covered caller sources"))
        skipped = review_commits(repo, base="main", connection=connection, overrides=options,
                                 get_credentials=lambda: pytest.fail("Identical review loaded credentials"))
    assert skipped["status"] == "skipped"
    assert skipped["head_sha"] != first["head_sha"]
    assert skipped["comparison"]["baseline_run_id"] == first["run_id"]
    assert paths(skipped, "reused") == (["app.py"] if changed_caller else [])
    assert skipped["affected_callers"]["status"] == "reused"
    units = {unit["key"]: unit["status"] for unit in skipped["units"]}
    assert set(units) == {unit["key"] for unit in first["units"]}
    for site in skipped["affected_callers"]["sites"]:
        assert site["status"] == units[site["unit_key"]] == "reused"
    assert sum(map(len, provider[0].values())) == requests_before

    commit(repo, {"app.py": "import subprocess\n" + CALLER.replace("legacy", "replacement")})
    changed = review_commits(repo, base="main", connection=connection, get_credentials=dict, overrides=options)
    assert changed["comparison"]["strategy"] == "full"
    assert changed["comparison"]["reason"] == "affected_callers_require_full_review"
    assert changed["affected_callers"]["sites"] == []


@pytest.mark.parametrize("legacy", [False, True])
def test_resume_restores_saved_affected_caller_scope(deletion_repo, provider, connection, monkeypatch, legacy):
    repo = deletion_repo
    head = git(repo, "rev-parse", "HEAD")
    captured = []

    def interrupt(_repo, _env, plan, *_args, **_kwargs):
        captured.extend(unit.key for unit in plan.units)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch, pytest.raises(KeyboardInterrupt):
        patch.setattr(service.engine, "run", interrupt)
        review_commits(repo, base="main", connection=connection, get_credentials=dict)

    with History(repo) as history:
        run_id = history.list()[0]["id"]
        saved = json.loads(history.row(run_id)["plan"])
        assert saved["affected_caller_policy"] == planning.AFFECTED_CALLER_POLICY
        if legacy:
            del saved["affected_caller_policy"]
            with history.db:
                history.db.execute("UPDATE runs SET plan=? WHERE id=?", (json.dumps(saved), run_id))

    commit(repo, {"app.py": "done = True\n"})
    result = review_local(repo, connection=connection, get_credentials=dict, resume=run_id)
    assert result["head_sha"] == head
    assert captured
    if legacy:
        assert "affected_callers" not in result
        assert result["units"] == []
        assert provider[0]["analysis"] == []
    else:
        assert result["status"] == "completed"
        assert [site["unit_key"] for site in result["affected_callers"]["sites"]] == captured
        assert [unit["key"] for unit in result["units"]] == captured


def test_partial_caller_discovery_cannot_baseline_an_identical_tree(deletion_repo, provider, connection, monkeypatch):
    repo = deletion_repo
    kwargs = {"base": "main", "connection": connection, "overrides": {"skip_unchanged": True}}
    with monkeypatch.context() as patch:
        patch.setattr(affected_callers, "MAX_FILES", 0)
        first = review_commits(repo, **kwargs, get_credentials=lambda: pytest.fail("No units were planned"))
    assert first["status"] == first["workflow_status"] == "partial"
    assert first["affected_callers"]["limits_reached"] == ["scanned_files"]
    assert first["affected_callers"]["sites"] == []

    git(repo, "commit", "--allow-empty", "-qm", "same tree")
    full = review_commits(repo, **kwargs, get_credentials=dict)
    assert full["status"] == "completed"
    assert full["comparison"]["strategy"] == "full"
    assert full["comparison"]["reason"] == "incompatible_baseline"
    assert provider[0]["analysis"]
