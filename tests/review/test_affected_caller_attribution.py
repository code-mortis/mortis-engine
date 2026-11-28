"""Send pinned dependency changes through the public review assessment request."""

import json

import pytest

from mortis_engine import gateway
from mortis_engine.history import History
from mortis_engine.review import assessment, engine, review_commits, review_local
from mortis_engine.schemas import Response, Usage

from .git_helpers import commit, git
from .test_workflows import decision, selected


@pytest.mark.parametrize(("renamed", "large_original", "legacy"), [
    pytest.param(False, False, False, id="deleted"),
    pytest.param(True, False, False, id="renamed"),
    pytest.param(False, True, False, id="deleted-with-optional-original"),
    pytest.param(False, False, True, id="resume-with-v1-attribution"),
])
def test_affected_change_attribution_in_assessment_request(repo, monkeypatch, tmp_path_factory,
                                                           renamed, large_original, legacy):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("caller-attribution-state")))
    old = "scripts/rebuild cache.sh"
    destination = "scripts/refresh cache.sh" if renamed else None
    caller = "run.sh"
    original = "printf 'ready\\n'\n"
    if large_original:
        original += ("# " + "x" * 90 + "\n") * 800
    base = commit(repo, {old: original, "version.py": "version = 1\n",
                         caller: f'#!/bin/sh\n# Run from the root.\nsh "./{old}"\n'})
    git(repo, "switch", "-qc", "topic")
    if renamed:
        git(repo, "mv", old, destination)
    else:
        git(repo, "rm", old)
    head = commit(repo, {"version.py": "version = 2\n"})
    assessments = []

    async def respond(connection, credentials, request, **kwargs):
        fields = request.output_schema["properties"]
        body = request.messages[0].content
        payload = json.loads(body.split("\n\n", 2)[1] if body.startswith("# ") else body)
        if "completed_units" in fields:
            units = payload["assigned_units"]
            caller_unit = next((unit for unit in units if unit["path"] == caller), None)
            findings = []
            if caller_unit:
                source = next(item for item in payload["citation_sources"] if item["path"] == caller)
                findings.append({"unit_key": caller_unit["key"], "path": caller, "start_line": 3, "end_line": 3,
                    "content": "The old script path is still invoked.", "severity": "high", "category": "correctness",
                    "trigger": "Run from the repository root.", "consequence": "The rebuild step fails.",
                    "evidence": [{"source_id": source["source_id"], "start_line": 3, "end_line": 3}]})
            output = {"task_key": payload["task_key"], "completed_units": [unit["key"] for unit in units],
                      "findings": findings}
        elif "verdict" in fields:
            assessments.append(payload)
            assert payload["head_sha"] == head and payload["original_sha"] == base
            assert payload["changed_paths"] == ([destination, "version.py"] if renamed else ["version.py"])
            attribution = payload["attribution"]
            assert attribution["merge_base_sha"] == base
            assert attribution["renames"] == ({old: destination} if renamed else {})
            if legacy:
                assert "affected_changes" not in attribution
                assert assessment.AFFECTED_CHANGE_INSTRUCTIONS not in request.instructions
            else:
                assert attribution["affected_changes"] == [
                    {"path": old, "new_path": destination, "status": "renamed" if renamed else "deleted"},
                ]
                assert assessment.AFFECTED_CHANGE_INSTRUCTIONS in request.instructions
            assert (f'original source: "{old}"' in body) is not large_original
            citations = [selected(payload, "current", caller, 3)]
            if not large_original:
                citations.append(selected(payload, "original", old, 1))
            output = decision(evidence=citations, explanation="The unchanged caller still invokes the old script path.")
        else:
            assert caller not in payload["paths"]
            assert any(site["path"] == caller for site in payload["affected_callers"]["sites"])
            assert ("Use only the top-level paths list" in request.instructions) is not legacy
            output = {"narrative": "The unchanged caller still invokes the old script path.", "groups": []}
        return Response(output=output, usage=Usage(total_tokens=3))

    monkeypatch.setattr(gateway, "send", respond)
    connection = {"provider": "custom", "protocol": "openai", "model": "fixture", "options": {}}
    options = {"connection": connection, "get_credentials": dict}
    if legacy:
        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt

        with monkeypatch.context() as previous:
            previous.setattr(assessment, "ATTRIBUTION_VERSION", assessment.LEGACY_ATTRIBUTION_VERSION)
            previous.setattr(engine, "run", interrupt)
            with pytest.raises(KeyboardInterrupt):
                review_commits(repo, base="main", head="topic", **options)
        with History(repo) as history:
            run_id = history.list()[0]["id"]
            assert json.loads(history.row(run_id)["plan"])["assessment_attribution"] == assessment.LEGACY_ATTRIBUTION_VERSION
        report = review_local(repo, resume=run_id, **options)
    else:
        report = review_commits(repo, base="main", head="topic", concurrency=1, **options)

    assert report["status"] == report["workflow_status"] == "completed"
    assert report["summary"]["status"] == "completed"
    candidate, = report["candidates"]
    assert candidate["snapshot"]["caller_only"]
    assert candidate["result"]["verdict"] == "confirmed"
    assert len(assessments) == 1
