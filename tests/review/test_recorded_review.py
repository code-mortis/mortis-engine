"""Replay the same provider responses used for Code Mortis initial-review parity."""

import json
import os
from pathlib import Path

from mortis_engine import gateway
from mortis_engine.llm_http import InferenceError
from mortis_engine.review import engine, planning, settings, source
from mortis_engine.schemas import Response

from .git_helpers import commit, git

RECORDING = Path(__file__).with_name("fixtures") / "partial_review.json"


def revisions(repo, monkeypatch):
    # Fixed commit metadata keeps Git objects, source IDs and task keys reproducible.
    for key in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(key, "2001-01-01T00:00:00Z")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git(repo, "init", "--quiet", "--initial-branch=main", "--object-format=sha1", "--template=")
    rows = "".join(f"item_{i} = {i}\n" for i in range(150))
    base = commit(repo, {"pricing.py": "price = 100\n" + rows})
    head = commit(repo, {"pricing.py": "price = 0\n" + rows})
    return base, head


def test_recorded_partial_file_keeps_the_app_finding_and_coverage(tmp_path, monkeypatch):
    base, head = revisions(tmp_path, monkeypatch)
    recording = json.loads(RECORDING.read_text())
    replies = recording["replies"].copy()

    async def replay(connection, credentials, request, **kwargs):
        task = json.loads(request.messages[0].content)["task_key"]
        reply = replies.pop(task)
        if "error" in reply:
            raise InferenceError(reply["error"], "Recorded provider failure", consumption_unknown=False)
        return Response.model_validate(reply)

    monkeypatch.setattr(gateway, "send", replay)
    with source.local_repository(tmp_path) as (repo, env):
        options = settings.load(repo, env, base)
        connection = {"kind": "api", "provider": "custom", "protocol": "openai", "model": "recorded"}
        plan = planning.plan_review(repo, env, base, head, base, options, lambda: False,
                                    max_unit_bytes=2048, max_batch_bytes=2048)
        state, report, findings = engine.run(repo, env, plan, connection, options, {})

    assert not replies
    assert state == recording["state"] == "partial"
    assert findings == recording["findings"]
    assert report["manifest"]["coverage"] == recording["coverage"]
    assert report["summary"]["total_tokens"] == recording["tokens"]
    assert [unit["status"] for unit in report["units"]] == recording["unit_statuses"]
