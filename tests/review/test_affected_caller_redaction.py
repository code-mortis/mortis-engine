import json

import pytest

from mortis_engine.review import engine, planning, preview_local, service, settings, snapshots

from .git_helpers import commit, git


def test_affected_caller_redacts_complete_blobs_before_selecting_source(repo):
    script = "scripts/charge.sh"
    caller = "payments.py"
    invoke = 'subprocess.run(["scripts/charge.sh"], check=True)'
    base = commit(repo, {
        script: "echo charge\n# padding\nremoved-credential-prefix\nremoved-credential-suffix\n",
        caller: 'padding = """unused\ncaller-credential-prefix\ncaller-credential-suffix\n"""\n\n' + invoke + "\n",
    })
    git(repo, "rm", script)
    git(repo, "commit", "-qm", "delete script")
    credentials = {"api_key": "removed-credential-prefix\nremoved-credential-suffix",
                   "redaction_values": ["caller-credential-prefix\ncaller-credential-suffix"]}
    connection = {"provider": "custom", "protocol": "openai", "model": "fixture", "options": {}}
    preview = preview_local(repo, mode="commits", base=base)
    site, = preview["affected_callers"]["sites"]
    assert (site["path"], site["start_line"], site["end_line"]) == (caller, 3, 6)

    with snapshots.snapshot(repo, mode="commits", base=base) as snap:
        options = {**settings.load(snap.repo, snap.env, snap.settings_sha), "review_mode": snap.mode}
        plan = service.plan_snapshot(snap, options)
        batch, = engine.prepare_batches(plan, connection, options, credentials, repo=snap.repo, env=snap.env)
        draft = engine._next_request(batch, [], [], remaining_source_rounds=0, repair=True)
        recovered, = engine._recovery_batches(batch, draft, batch.sources, batch.key)
        for request in (batch.request, draft, recovered.request):
            assert all("-credential-" not in message.content for message in request.messages)
            payload = json.loads(request.messages[0].content)
            unit, = payload["assigned_units"]
            assert unit["key"] == site["unit_key"]
            assert unit["source"].splitlines()[0] == "[redacted]"
            assert unit["affected_triggers"][0]["evidence"]["text"].splitlines()[-1] == "[redacted]"

        current, removed = batch.sources
        output = {"task_key": batch.key, "completed_units": [site["unit_key"]], "findings": [{
            "unit_key": site["unit_key"], "path": caller, "start_line": 6, "end_line": 6,
            "content": "The command still references the removed file",
            "trigger": "Invoke the charge command", "consequence": "The charge does not run",
            "evidence": [{"source_id": current["source_id"], "start_line": 3, "end_line": 6},
                         {"source_id": removed["source_id"], "start_line": 3, "end_line": 3}],
        }]}
        validated = engine.validate_result(output, batch, batch.sources, credentials)
        comment, = validated["comments"]
        assert comment["evidence"][0]["quote"].splitlines()[0] == "[redacted]"
        assert comment["evidence"][1]["quote"] == "[redacted]"
        assert comment["existing_code"] == invoke


@pytest.mark.parametrize("reason", ["provider_context", "source_unavailable"])
def test_unavailable_affected_context_preserves_primary_review(repo, monkeypatch, reason):
    script = "scripts/charge.sh"
    app = "payments.py"
    unchanged = "refunds.py"
    invocation = 'command = "scripts/charge.sh"\n'
    base = commit(repo, {script: "echo charge\n", app: invocation, unchanged: invocation})
    git(repo, "rm", script)
    commit(repo, {app: invocation + "live = True\n"})
    connection = {"provider": "custom", "protocol": "openai", "model": "fixture", "options": {}}

    with snapshots.snapshot(repo, mode="commits", base=base) as snap:
        options = {**settings.load(snap.repo, snap.env, snap.settings_sha), "review_mode": snap.mode}
        plan = service.plan_snapshot(snap, options)
        expected_sites = {site["path"]: site["unit_key"] for site in plan.affected_callers["sites"]}
        assert set(expected_sites) == {app, unchanged}
        if reason == "provider_context":
            def reservation(connection, request):
                payload = json.loads(request.messages[0].content)
                if any("affected_triggers" in row for row in payload["assigned_units"]):
                    raise ValueError("Fixture ceiling exceeded")
                return 1

            monkeypatch.setattr(engine.llm, "reservation", reservation)
        else:
            entry = planning._entry

            def read(*args):
                if args[3] == script:
                    raise ValueError("Fixture source unavailable")
                return entry(*args)

            monkeypatch.setattr(planning, "_entry", read)

        def prepare():
            return engine.prepare_batches(plan, connection, options, {}, repo=snap.repo, env=snap.env)

        primary, caller_only = prepare()
        assert [batch.key for batch in prepare()] == [primary.key, caller_only.key]
        assert primary.units[0].key == expected_sites[app]
        assert primary.error is None
        unit, = json.loads(primary.request.messages[0].content)["assigned_units"]
        assert "affected_triggers" not in unit and "live = True" in unit["diff"]
        assert caller_only.units[0].key == expected_sites[unchanged]
        assert caller_only.request is None and caller_only.error

        state, report, _ = engine._report(plan, [primary, caller_only], {
            expected_sites[app]: {"status": "completed", "comments": []},
            expected_sites[unchanged]: {"status": "failed", "error": caller_only.error},
        }, tokens=3)
        assert state == "partial"
        assert [row["path"] for row in report["manifest"]["coverage"]["completed"]] == [app]
        affected = report["affected_callers"]
        assert affected["status"] == "partial" and reason in affected["limits_reached"]
        assert {site["path"]: site["status"] for site in affected["sites"]} == {
            app: "incomplete", unchanged: "failed",
        }
