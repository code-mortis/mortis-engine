"""Inert exact-revision navigation is never citation evidence by itself."""

import json
from types import SimpleNamespace

import pytest

from mortis_engine import gateway
from mortis_engine.review import context, engine, planning, source, source_navigation
from mortis_engine.review.errors import ReviewStopped
from mortis_engine.review.settings import RepoSettings
from mortis_engine.schemas import Response, ToolCall, Usage

from .git_helpers import commit, git
from .test_execution import CONNECTION, final


def search(repo, env, plan, symbol="divide", options=None, credentials=None, **kwargs):
    return source_navigation.search(repo, env, plan, options or {}, symbol, credentials or {},
                                    kwargs.get("authorize", lambda: None), kwargs.get("stopped", lambda: False))


def scope(base, head=None, rules=None):
    return SimpleNamespace(base_sha=base, head_sha=head or base,
                           rules=rules or {"layers": [{"include": [], "exclude": []}]})


def test_search_finds_unchanged_caller_homonym_and_tests_without_executing(repo, tmp_path):
    marker = tmp_path / "executed"
    sha = commit(repo, {"app.py": "def divide(value):\n    return 1 / value\n",
                        "caller.py": "from app import divide\nresult = divide(0)\n",
                        "unrelated.py": "def divide(label):\n    return label.upper()\n",
                        "tests/test_app.py": f"from pathlib import Path\nPath({str(marker)!r}).touch()\nassert divide(1) == 1\n",
                        ".env": "divide SECRET\n", "privatekey.py": "divide SECRET\n",
                        "hidden.py": "divide SECRET\n", "uv.lock": "divide SECRET\n"})
    (repo / "caller.py").write_text("uncommitted = 'must not be read'\n")
    with source.local_repository(repo) as (isolated, env):
        result = search(isolated, env, scope(sha), options={"exclusions": "hidden.py"})
    paths = {item["path"] for item in result["matches"]}
    assert paths == {"app.py", "caller.py", "unrelated.py", "tests/test_app.py"}
    assert result["revision"] == sha
    assert {item["relation"] for item in result["matches"]} == {"identifier_occurrence"}
    assert "Homonyms" in result["notice"] and "never evidence" in result["notice"]
    assert "SECRET" not in json.dumps(result) and "source_id" not in json.dumps(result)
    assert not marker.exists()


@pytest.mark.parametrize("options", [{"exclusions": "tests/**"}, {"rules": {"exclude": ["tests/**"]}}])
def test_search_preserves_explicit_test_exclusions(repo, options):
    sha = commit(repo, {"tests/test_app.py": "assert divide(1) == 1\n"})
    rules = {"layers": [planning._rule_layer(options.get("rules"))]}
    with source.local_repository(repo) as (isolated, env):
        assert not search(isolated, env, scope(sha, rules=rules), options=options)["matches"]


def test_search_rejects_excluded_rename_origin_and_symlinks(repo):
    base = commit(repo, {"privatekey.py": "result = divide(0)\n", "app.py": "other = 1\n"})
    git(repo, "mv", "privatekey.py", "caller.py")
    (repo / "linked.py").symlink_to("caller.py")
    head = commit(repo, {})
    with source.local_repository(repo) as (isolated, env):
        result = search(isolated, env, scope(base, head))
    assert result["matches"] == []


def test_search_redacts_complete_source_and_uses_identifier_boundaries(repo):
    sha = commit(repo, {"app.py": "prefixdivide = 1\ndivideSuffix = 2\nsecret_divide = 3\nresult = divide(0)\n"})
    with source.local_repository(repo) as (isolated, env):
        result = search(isolated, env, scope(sha), credentials={"api_key": "secret_divide"})
        assert [item["line"] for item in result["matches"]] == [4]
        with pytest.raises(ValueError, match="credential"):
            search(isolated, env, scope(sha), "secret_divide", credentials={"api_key": "secret_divide"})


@pytest.mark.parametrize("symbol", ["", "a.*", "../app", "x\n", 123, "a" * 129])
def test_search_rejects_patterns_and_invalid_identifiers(repo, symbol):
    sha = commit(repo, {"app.py": "divide(0)\n"})
    with source.local_repository(repo) as (isolated, env), pytest.raises(ValueError, match="literal identifier"):
        search(isolated, env, scope(sha), symbol)


@pytest.mark.parametrize("limit", ["files", "bytes", "file_bytes", "results", "result_bytes"])
def test_search_bounds_inventory_reads_and_results(repo, monkeypatch, limit):
    sha = commit(repo, {f"file{index}.py": "divide(0)\n" * 4 for index in range(4)})
    setting, value = {"files": ("MAX_FILES", 1), "bytes": ("MAX_BYTES", 45),
                      "file_bytes": ("MAX_FILE_BYTES", 2), "results": ("MAX_RESULTS", 1),
                      "result_bytes": ("MAX_RESULT_BYTES", 900)}[limit]
    monkeypatch.setattr(source_navigation, setting, value)
    with source.local_repository(repo) as (isolated, env):
        result = search(isolated, env, scope(sha))
    assert result["truncated"]
    assert result["scanned_files"] <= source_navigation.MAX_FILES
    assert result["scanned_bytes"] <= source_navigation.MAX_BYTES
    assert len(result["matches"]) <= source_navigation.MAX_RESULTS
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= source_navigation.MAX_RESULT_BYTES


def test_search_cancellation_and_revoked_authorization(repo):
    sha = commit(repo, {"app.py": "divide(0)\n"})
    with source.local_repository(repo) as (isolated, env):
        with pytest.raises(ReviewStopped):
            search(isolated, env, scope(sha), stopped=lambda: True)

        def revoked():
            raise PermissionError("revoked")

        with pytest.raises(PermissionError, match="revoked"):
            search(isolated, env, scope(sha), authorize=revoked)


def test_analysis_search_then_requests_real_caller_source(repo, monkeypatch):
    base = commit(repo, {"app.py": "def divide(value):\n    return 1 / value\n",
                         "caller.py": "from app import divide\nresult = divide(0)\n"})
    head = commit(repo, {"app.py": "def divide(value):\n    return 2 / value\n"})
    options = RepoSettings().model_dump()
    options.update(effort="medium", concurrency=1)
    calls = []

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        if len(calls) == 1:
            assert {tool.name for tool in request.tools} == {"read_source", "search_source"}
            return Response(tool_calls=[ToolCall(id="search", name="search_source", arguments={"symbol": "divide"})],
                            usage=Usage(total_tokens=10))
        payload = json.loads(request.messages[-1].content)
        if len(calls) == 2:
            assert payload["supporting_sources"] == []
            hints = payload["source_read_results"][0]
            assert "caller.py" in {item["path"] for item in hints["matches"]}
            return Response(tool_calls=[ToolCall(id="read", name="read_source",
                arguments={"path": "caller.py", "start_line": 1, "end_line": 2})], usage=Usage(total_tokens=10))
        evidence = payload["supporting_sources"]
        assert evidence[0]["text"] == "from app import divide\nresult = divide(0)"
        assert evidence[0]["revision"] == head and not request.tools
        return final(request)

    monkeypatch.setattr(gateway, "send", send)
    with source.local_repository(repo) as (isolated, env):
        plan = planning.plan_review(isolated, env, base, head, base, options, lambda: False)
        status, report, _ = engine.run(isolated, env, plan, CONNECTION, options, {})
    assert status == "completed" and len(calls) == 3
    assert report["summary"]["total_tokens"] == 30


def test_search_hints_cannot_be_cited_and_legacy_requests_keep_read_only_tools(repo):
    base = commit(repo, {"app.py": "result = divide(1)\n", "caller.py": "divide(0)\n"})
    head = commit(repo, {"app.py": "result = divide(0)\n"})
    options = RepoSettings().model_dump()
    with source.local_repository(repo) as (isolated, env):
        plan = planning.plan_review(isolated, env, base, head, base, options, lambda: False)
        batch = engine.prepare_batches(plan, CONNECTION, options, {})[0]
        result, additional = engine.read_sources([ToolCall(id="s", name="search_source", arguments={"symbol": "divide"})],
            repo=isolated, env=env, plan=plan, options=options, credentials={}, authorize=lambda: None, stopped=lambda: False)
        assert result["selections"][0]["matches"] and additional == []
        legacy = engine.prepare_batches(plan, CONNECTION, options, {}, source_tool_policy="bounded-reads/v2")[0]
        assert [tool.name for tool in legacy.request.tools] == ["read_source"]
        assert source_navigation.NOTICE not in legacy.request.instructions
        with pytest.raises(engine.ResultValidationError, match="supplied source tools"):
            engine.read_sources([ToolCall(id="s", name="search_source", arguments={"symbol": "divide"})],
                repo=isolated, env=env, plan=plan, options=options, credentials={}, authorize=lambda: None,
                stopped=lambda: False, source_tool_policy="bounded-reads/v2")
        candidate = {"task_key": batch.key, "completed_units": [unit.key for unit in batch.units], "findings": [{
            "unit_key": batch.units[0].key, "path": batch.units[0].path, "start_line": 1, "end_line": 1,
            "content": "Division by zero", "trigger": "Zero argument", "consequence": "Exception",
            "evidence": [{"source_id": context.source_id(head, "caller.py"), "start_line": 1, "end_line": 1}]}]}
        with pytest.raises(engine.ResultValidationError, match="supplied citation sources"):
            engine.validate_result(candidate, batch, batch.sources, {})


@pytest.mark.parametrize("legacy", [False, True])
def test_service_pins_search_policy_and_preserves_legacy_request_hash(repo, tmp_path_factory, monkeypatch, legacy):
    from mortis_engine.history import History, encode
    from mortis_engine.review import service

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("navigation-state")))
    commit(repo, {"app.py": "value = 1\n"})
    observed = []

    def interrupted(repo, env, plan, connection, options, credentials, **kwargs):
        selected = kwargs["source_tool_policy"]
        batch = engine.prepare_batches(plan, connection, options, credentials, source_tool_policy=selected)[0]
        observed.append((selected, planning.fingerprint(batch.request.model_dump())))
        if legacy:
            old = engine.prepare_batches(plan, connection, options, credentials, source_tool_policy="bounded-reads/v2")[0]
            old_hashes.append(planning.fingerprint(old.request.model_dump()))
        raise KeyboardInterrupt

    old_hashes = []
    monkeypatch.setattr(engine, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.review_local(repo, mode="all", connection=CONNECTION, get_credentials=lambda: {})
    with History(repo) as history:
        identity = history.list()[0]["id"]
        saved = json.loads(history.row(identity)["plan"])
        assert saved["source_tool_policy"] == engine.SOURCE_TOOL_POLICY
        if legacy:
            saved.pop("source_tool_policy")
            history.db.execute("UPDATE runs SET plan=? WHERE id=?", (encode(saved), identity))
            history.db.commit()
        original_plan = history.row(identity)["plan"]
    with pytest.raises(KeyboardInterrupt):
        service.review_local(repo, resume=identity, connection=CONNECTION, get_credentials=lambda: {})
    assert observed[1][0] == ("bounded-reads/v2" if legacy else engine.SOURCE_TOOL_POLICY)
    assert observed[1][1] == (old_hashes[0] if legacy else observed[0][1])
    with History(repo) as history:
        assert history.row(identity)["plan"] == original_plan
