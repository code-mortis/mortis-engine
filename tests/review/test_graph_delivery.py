import json

import pytest
from code_graph.test_graph import parsed

from mortis_engine import gateway
from mortis_engine.code_graph import transport
from mortis_engine.history import History
from mortis_engine.llm_http import InferenceError
from mortis_engine.review import engine, review_local
from mortis_engine.schemas import Response, Usage

from .git_helpers import commit, git
from .test_execution import CONNECTION, OPTIONS, final, plan


def test_public_report_records_graph_omission_when_resuming(repo, tmp_path_factory, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("graph-delivery-state")))
    dependency = "// Context for the review.\n" * 430 + "export const value = 1;\n"
    commit(repo, {"app.js": 'import { value } from "./dep.js";\nconsole.log(value);\n', "dep.js": dependency})
    git(repo, "switch", "-qc", "topic")
    commit(repo, {"app.js": 'import { value } from "./dep.js";\nconsole.log(2 * value);\n'})
    monkeypatch.setattr(transport, "extract", lambda request, **kwargs: parsed(request))
    run = engine.run
    prepared_omissions = []

    def interrupt_after_analysis(*args, **kwargs):
        status, report, _ = run(*args, **kwargs)
        assert status == "completed"
        prepared_omissions.extend(report["graph_discovery_omissions"])
        raise KeyboardInterrupt

    sent = []

    async def send(connection, credentials, request, **kwargs):
        if "task_key" in request.output_schema["properties"]:
            payload = json.loads(request.messages[0].content)
            assert "code_graph" not in payload
            assert {row["path"] for row in payload["citation_sources"]} == {"app.js"}
            sent.append(payload["task_key"])
            return final(request)
        return Response(output={"narrative": "Fixture summary", "groups": []}, usage=Usage(total_tokens=1))

    monkeypatch.setattr(engine, "run", interrupt_after_analysis)
    monkeypatch.setattr(gateway, "send", send)
    kwargs = {"connection": {**CONNECTION, "options": {"prompt_token_ceiling": 15000}}, "get_credentials": dict}
    with pytest.raises(KeyboardInterrupt):
        review_local(repo, mode="commits", base="main", head="topic", graph=True, **kwargs)
    with History(repo) as history:
        run_id = history.list()[0]["id"]

    monkeypatch.setattr(engine, "run", run)
    monkeypatch.setattr(transport, "extract", lambda *args, **kwargs: pytest.fail("Resume started the parser"))
    report = review_local(repo, resume=run_id, **kwargs)

    assert len(sent) == 1
    assert report["status"] == "completed"
    assert [item["path"] for item in report["coverage"]["completed"]] == ["app.js"]
    assert report["code_graph"]["status"] == "ready"
    assert report["code_graph"]["enriched_paths"] == 1
    assert report["code_graph"]["discovery_omissions"] == prepared_omissions == [
        {"path": "app.js", "reason": "provider_context_limit", "unit_keys": [report["units"][0]["key"]]}
    ]
    with History(repo) as history:
        assert history.report(run_id)["code_graph"] == report["code_graph"]


def test_graph_omission_tracks_final_split_units_and_recovery(monkeypatch):
    value = plan(2, same_file=True)
    value.batches = [value.units]
    graph = {"app.py": {"commit_sha": value.head_sha, "sources": [{"path": "dep.py", "text": "value = 1\n"}]}}
    reserve = gateway.reservation

    def reservation(connection, request):
        payload = json.loads(request.messages[0].content)
        keys = [unit["key"] for unit in payload["assigned_units"]]
        if len(keys) > 1 or (keys == ["unit-1"] and "code_graph" in payload):
            raise ValueError("Request exceeds the fixture ceiling")
        return reserve(connection, request)

    dispatched = {}

    async def send(connection, credentials, request, **kwargs):
        payload = json.loads(request.messages[0].content)
        unit, = payload["assigned_units"]
        recovered = ":unit:" in payload["task_key"]
        dispatched[unit["key"], recovered] = "code_graph" in payload
        if not recovered:
            raise InferenceError("output_limit", "Fixture output limit", consumption_unknown=False,
                                 usage=Usage(total_tokens=1))
        return final(request)

    monkeypatch.setattr(gateway, "reservation", reservation)
    monkeypatch.setattr(gateway, "send", send)
    status, report, _ = engine.run(None, {}, value, CONNECTION, OPTIONS, {}, graph_by_path=graph)

    assert status == "completed"
    assert dispatched == {("unit-0", False): True, ("unit-0", True): True,
                          ("unit-1", False): False, ("unit-1", True): False}
    assert report["graph_discovery_omissions"] == [
        {"path": "app.py", "reason": "provider_context_limit", "unit_keys": ["unit-1"]}
    ]
