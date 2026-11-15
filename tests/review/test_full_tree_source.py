"""Source-only full-tree requests and pre-policy saved-run compatibility."""

import hashlib
import json
from dataclasses import fields

import pytest

from mortis_engine import gateway
from mortis_engine.history import History, encode
from mortis_engine.review import engine, planning, service, settings, snapshots, workflows
from mortis_engine.schemas import Response, Usage

from .git_helpers import commit

CONNECTION = {"kind": "api", "provider": "custom", "protocol": "openai", "model": "fixture", "options": {}}


def full_plan(repo, **kwargs):
    with snapshots.snapshot(repo, mode="all") as snap:
        options = settings.load(snap.repo, snap.env, snap.settings_sha)
        options["review_mode"] = "all"
        plan = planning.plan_review(snap.repo, snap.env, snap.base_sha, snap.head_sha, snap.merge_base,
                                    options, lambda: False, trusted_rules_sha=snap.settings_sha, **kwargs)
    return plan, options


def test_full_tree_splits_serialized_source_once_and_keeps_citation_lines(repo):
    text = "".join(f'value_{number} = "é\\\\quote"\n' for number in range(80)) + "last = 0"
    commit(repo, {"app.py": text})
    plan, options = full_plan(repo, max_unit_bytes=1024, max_batch_bytes=4096)
    assert len(plan.units) > 1
    assert "".join(unit.source for unit in plan.units) == text
    assert all(unit.source_only and unit.patch == "" for unit in plan.units)
    assert all(planning._unit_bytes(unit) <= 1024 for unit in plan.units)
    assert all(sum(planning._unit_bytes(unit) + 2 for unit in batch) <= 4096 for batch in plan.batches)
    lines = text.splitlines()
    for unit in plan.units:
        assert unit.source.splitlines() == lines[unit.start_line - 1:unit.end_line]
    prepared = engine.prepare_batches(plan, CONNECTION, options, {})
    batch = prepared[-1]
    payload = json.loads(batch.request.messages[0].content)
    assert all("diff" not in unit for unit in payload["assigned_units"])
    unit = batch.units[-1]
    selected = next(source for source in batch.sources if source["start_line"] == unit.start_line)
    result = engine.validate_result({"task_key": batch.key, "completed_units": [u.key for u in batch.units],
        "findings": [{"unit_key": unit.key, "path": unit.path, "start_line": unit.end_line,
            "end_line": unit.end_line, "content": "A concrete defect", "severity": "high",
            "category": "correctness", "trigger": "Call with zero", "consequence": "Division fails",
            "evidence": [{"source_id": selected["source_id"], "start_line": unit.end_line,
                          "end_line": unit.end_line}]}]}, batch, batch.sources, {})
    assert result["comments"][0]["existing_code"] == "last = 0"
    assert result["comments"][0]["evidence"][0]["quote"] == "last = 0"


def test_followup_and_output_recovery_keep_each_full_tree_source_once(repo):
    commit(repo, {"app.py": "unique_app_value = 1\n", "other.py": "unique_other_value = 2\n"})
    plan, options = full_plan(repo)
    batch, = engine.prepare_batches(plan, CONNECTION, options, {})
    request = engine._next_request(batch, batch.sources, [], remaining_source_rounds=1)
    assert json.loads(request.messages[-1].content)["supporting_sources"] == []
    for recovered in engine._recovery_batches(batch, request, batch.sources, batch.key):
        payload = json.loads(recovered.request.messages[0].content)
        units = payload["assigned_units"] + payload["context_units"]
        assert sorted(unit["source"] for unit in units) == sorted(unit.source for unit in plan.units)
        assert all("diff" not in unit for unit in units)
        assert all(recovered.request.messages[0].content.count(unit.source.strip()) == 1 for unit in plan.units)
        assert engine.validate_result({"task_key": recovered.key,
            "completed_units": [unit.key for unit in recovered.units], "findings": []},
            recovered, recovered.sources, {})["kind"] == "final"


def test_source_only_admission_counts_source_once_and_rejects_escaped_oversized_line(repo):
    commit(repo, {"app.py": "x" * 700 + "\n"})
    plan, _ = full_plan(repo, max_source_bytes=1024)
    assert plan.units and not plan.selected[0].error
    legacy, _ = full_plan(repo, max_source_bytes=1024, full_tree_source_policy=None)
    assert legacy.selected[0].error and not legacy.units
    with pytest.raises(ValueError, match="line exceeds"):
        planning.split_units("app.py", None, "A", "b" * 40, '"' * 500, "", "Check", 1024,
                             source_only=True)


def test_changed_file_reviews_retain_the_original_diff_and_identity(repo):
    base = commit(repo, {"app.py": "value = 1\n"})
    head = commit(repo, {"app.py": "value = 2\n"})
    with snapshots.snapshot(repo, mode="commits", base=base, head=head) as snap:
        options = settings.load(snap.repo, snap.env, snap.settings_sha)
        arguments = (snap.repo, snap.env, base, head, snap.merge_base, options, lambda: False)
        current = planning.plan_review(*arguments)
        legacy = planning.plan_review(*arguments, full_tree_source_policy=None)
    assert current.snapshot() == legacy.snapshot()
    assert "-value = 1" in current.units[0].patch and "+value = 2" in current.units[0].patch
    assert not current.units[0].source_only


@pytest.mark.parametrize("source_policy", [None, planning.FULL_TREE_SOURCE_POLICY])
def test_resume_reuses_cached_analysis_with_historical_plan_layout(repo, tmp_path, monkeypatch, source_policy):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path.parent / (tmp_path.name + "-state")))
    commit(repo, {"app.py": "value = 1\n"})
    connection = service.effective_connection(CONNECTION, {})
    with History(repo) as history, snapshots.snapshot(repo, mode="all", object_cache=history.objects) as snap:
        options = settings.load(snap.repo, snap.env, snap.settings_sha)
        options.update(review_mode="all", _settings_sha=snap.settings_sha, _triage_snapshot={})
        saved = {"connection": planning.fingerprint(connection), "profile": None,
            "snapshot": {f.name: getattr(snap, f.name) for f in fields(snap) if f.name not in {"env", "repo"}},
            "history": [], "effort": None, "graph": False, "concurrency": None, "overrides": None,
            "code_graph_limits": options["code_graph_limits"], "triage_versions": {}, "assessment_runs": []}
        if source_policy is not None:
            saved["full_tree_source_policy"] = source_policy
        run_id = history.begin("review", "all refs/heads/main", saved)
        saved_before = history.row(run_id)["plan"]
        plan = planning.plan_review(snap.repo, snap.env, snap.base_sha, snap.head_sha, snap.merge_base,
            options, lambda: False, trusted_rules_sha=snap.settings_sha, full_tree_source_policy=source_policy)
        batch, = engine.prepare_batches(plan, connection, options, {}, source_tool_policy="bounded-reads/v2")
        assert [tool.name for tool in batch.request.tools] == ["read_source"]
        payload = json.loads(batch.request.messages[0].content)
        assert ("diff" in payload["assigned_units"][0]) == (source_policy is None)
        request = batch.request.model_copy(update={"session_id": planning.fingerprint([run_id, batch.key])})
        digest = hashlib.sha256(encode(request.model_dump()).encode()).hexdigest()
        row = {"task_key": batch.key + ":round:0", "stage": "analysis", "reserved_tokens": 20000,
               "usage": {"total_tokens": 3}, "consumption_unknown": False, "status": "completed"}
        history.start_attempt(run_id, row, digest)
        history.settle(run_id, row, Response(output={"task_key": batch.key,
            "completed_units": [unit.key for unit in batch.units], "findings": []},
            usage=Usage(total_tokens=3)).model_dump(include={"usage", "output", "tool_calls"}))
        attempts_before = history.attempts(run_id)

    async def no_dispatch(*args, **kwargs):
        pytest.fail("A saved analysis must be reused without a provider call")

    async def postprocess(repo, env, result, *args, **kwargs):
        return result

    monkeypatch.setattr(gateway, "send", no_dispatch)
    monkeypatch.setattr(workflows, "postprocess", postprocess)
    result = service.review_local(repo, connection=CONNECTION, get_credentials=lambda: {}, resume=run_id)
    assert result["status"] == "completed"
    assert result["usage"]["total_tokens"] == 3
    with History(repo) as history:
        assert history.row(run_id)["plan"] == saved_before
        assert history.attempts(run_id) == attempts_before


def test_new_runs_persist_source_policy_before_dispatch(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path.parent / (tmp_path.name + "-state")))
    commit(repo, {"app.py": "value = 1\n"})

    def interrupted(*args, **kwargs):
        assert all(unit.source_only for unit in args[2].units)
        raise KeyboardInterrupt

    monkeypatch.setattr(engine, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.review_local(repo, mode="all", connection=CONNECTION, get_credentials=lambda: {})
    with History(repo) as history:
        saved = json.loads(history.row(history.list()[0]["id"])["plan"])
    assert saved["full_tree_source_policy"] == planning.FULL_TREE_SOURCE_POLICY
