import json

import pytest

from mortis_engine.history import History
from mortis_engine.review import engine, evidence, service, snapshots, source_lines, verification
from mortis_engine.workflow import Session

from .git_helpers import commit
from .test_execution import CONNECTION, OPTIONS, draft_finding


def test_unicode_separator_keeps_the_git_line_through_planning_and_citation(repo):
    text = 'label = "left\u2028right"\nresult = 1 / 0\n'
    base = commit(repo, {"app.py": "result = 1\n"})
    head = commit(repo, {"app.py": text})
    with snapshots.snapshot(repo, mode="commits", base=base, head=head) as snap:
        plan = service.plan_snapshot(snap, OPTIONS)
        batch, = engine.prepare_batches(plan, CONNECTION, OPTIONS, {})
    unit, = plan.units
    assert unit.end_line == 2
    assert '+label = "left\u2028right"\n' in unit.patch

    finding = draft_finding(batch.request)
    citation, = finding["evidence"]
    finding.update(start_line=2, end_line=2)
    citation.update(start_line=2, end_line=2)
    result = {"task_key": batch.key, "completed_units": [unit.key], "findings": [finding]}
    comment, = engine.validate_result(result, batch, batch.sources, {})["comments"]
    assert comment["existing_code"] == comment["evidence"][0]["quote"] == "result = 1 / 0"
    assert comment["inline_line"] == 2
    citation.update(start_line=3, end_line=3)
    with pytest.raises(engine.ResultValidationError) as error:
        engine.validate_result(result, batch, batch.sources, {})
    assert error.value.issue["code"] == "citation_range_out_of_bounds"

    ctx = {"sources": {"current": {"app.py": text}}}
    supplied, _, history = evidence.prepare(ctx, verification.output_schema())
    identity, = supplied["citation_sources"]
    assert supplied["citation_sources"][identity]["line_count"] == 2
    ref = {"source_id": identity, "start_line": 2, "end_line": 2}
    assert evidence.resolve({"evidence": [ref]}, supplied, history)["evidence"][0]["quote"] == "result = 1 / 0\n"
    with pytest.raises(verification.ResultValidationError):
        evidence.resolve({"evidence": [{**ref, "end_line": 3}]}, supplied, history)


@pytest.mark.parametrize(("text", "visible", "raw"), [
    ("", [], []),
    ("\n", [""], ["\n"]),
    ("a\r\n\r\nb", ["a", "", "b"], ["a\r\n", "\r\n", "b"]),
    ("a\rb\r", ["a\rb\r"], ["a\rb\r"]),
    ("a\u2028b\x0cc\n", ["a\u2028b\x0cc"], ["a\u2028b\x0cc\n"]),
])
def test_git_delimiters(text, visible, raw):
    assert source_lines.split(text) == visible
    assert source_lines.split(text, keepends=True) == raw
    assert "".join(raw) == text


@pytest.mark.parametrize("legacy", [False, True])
def test_resume_uses_pinned_line_coordinates(repo, tmp_path_factory, monkeypatch, legacy):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("line-policy-state")))
    base = commit(repo, {"app.py": "result = 1\n"})
    head = commit(repo, {"app.py": 'label = "left\u2028right"\nresult = 0\n'})

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt), monkeypatch.context() as patch:
        patch.setattr(engine, "run", interrupt)
        service.review_commits(repo, base=base, head=head, connection=CONNECTION, get_credentials=dict)
    with History(repo) as history:
        run_id = history.list()[0]["id"]
        saved = json.loads(history.row(run_id)["plan"])
        assert saved["source_line_policy"] == source_lines.POLICY
        if legacy:
            del saved["source_line_policy"]
            with history.db:
                history.db.execute("UPDATE runs SET plan=? WHERE id=?", (json.dumps(saved), run_id))

    def resumed(_repo, _env, plan, *args, **kwargs):
        unit, = plan.units
        assert unit.end_line == (3 if legacy else 2)
        raise KeyboardInterrupt

    monkeypatch.setattr(engine, "run", resumed)
    with pytest.raises(KeyboardInterrupt):
        service.review_local(repo, connection=CONNECTION, get_credentials=dict, resume=run_id)
    assert source_lines.split("x\u2028y") == ["x\u2028y"]


def test_prompt_numbers_match_its_review_or_scan_evidence_contract():
    session = Session(CONNECTION, {})
    context = {"sources": {"current": {"app.py": "left\u2028right\nnext\n"}}, "diff": ""}

    def render(stage):
        req = session.request(stage, "fixture", instructions="", context=context, schema={})
        return req.messages[0].content

    assert "1 | left\u2028right\n2 | next" in render("verification")
    historical = "1 | left\n2 | right\n3 | next"
    assert historical in render("security_verification")
    with source_lines.use(None):
        assert historical in render("verification")
