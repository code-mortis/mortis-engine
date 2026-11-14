import base64
from types import SimpleNamespace

import pytest
from review.git_helpers import commit, git

from mortis_engine import code_graph
from mortis_engine.code_graph import build, transport
from mortis_engine.code_graph.contracts import (
    CodeGraphRequest,
    CodeGraphResult,
    blob_sha,
    source_lines,
)
from mortis_engine.code_graph.syntax import LANGUAGES
from mortis_engine.code_graph.validation import validate_result
from mortis_engine.review import planning, settings, snapshots


def parsed(request):
    files = []
    imports = []
    for item in request.files:
        metadata = {"path": item.path, "blob_sha": item.blob_sha, "size_bytes": item.size_bytes}
        if item.exclusion:
            files.append({**metadata, "status": "excluded", "reason": item.exclusion})
        elif item.content_base64 is None:
            files.append({**metadata, "status": "unsupported", "reason": "unsupported_language"})
        else:
            files.append({**metadata, "language": LANGUAGES[item.path[item.path.rfind('.'):]][0],
                          "status": "parsed", "line_count": source_lines(item.source()), "unresolved_calls": 0})
            if item.path == "app.js" and b'"./dep.js"' in item.source():
                end = item.source().index(b"\n")
                imports.append({"file_path": item.path, "start_byte": 0, "end_byte": end,
                                "start_line": 1, "end_line": 1, "specifier": "./dep.js",
                                "target_path": "dep.js", "provenance": "relative_path"})
    return CodeGraphResult(**request.model_dump(exclude={"files"}), files=files, imports=imports)


def request_fixture():
    body = b'import { value } from "./dep.js";\nconsole.log(value);\n'
    dependency = b"export const value = 1;\n"
    return CodeGraphRequest(repository_id=1, commit_sha="a" * 40, policy_digest="b" * 64, files=[
        {"path": path, "blob_sha": blob_sha(value), "size_bytes": len(value),
         "content_base64": base64.b64encode(value).decode()}
        for path, value in (("app.js", body), ("dep.js", dependency))])


def test_validator_checks_exact_snapshot_manifest_and_import_evidence():
    request = request_fixture()
    result = parsed(request)
    assert validate_result(request, result) is result
    for field, changed in (("commit_sha", "c" * 40), ("policy_digest", "d" * 64),
                           ("extractor_version", "wrong-parser")):
        with pytest.raises(ValueError, match="identity"):
            validate_result(request, result.model_copy(update={field: changed}))
    tampered = result.model_copy(deep=True)
    tampered.imports[0].specifier = "./fake.js"
    with pytest.raises(ValueError, match="absent"):
        validate_result(request, tampered)
    tampered = result.model_copy(deep=True)
    tampered.imports[0].target_path = "app.js"
    with pytest.raises(ValueError, match="target"):
        validate_result(request, tampered)


def test_enrichment_uses_same_revision_bounded_one_hop_imports(tmp_path, monkeypatch):
    git(tmp_path, "init", "-qb", "main")
    commit(tmp_path, {"app.js": 'import { value } from "./dep.js";\nconsole.log(value);\n',
                      "dep.js": "export const value = 1;\n", "README.md": "text\n"})
    seen = []

    def extract(request, **kwargs):
        seen.append(request)
        return validate_result(request, parsed(request))

    monkeypatch.setattr(transport, "extract", extract)
    with snapshots.snapshot(tmp_path, mode="all") as value:
        options = settings.load(value.repo, value.env, value.settings_sha)
        options["_graph"] = True
        plan = planning.plan_review(value.repo, value.env, value.base_sha, value.head_sha,
                                    value.merge_base, options, lambda: False)
        graph, diagnostics = code_graph.enrich(value.repo, value.env, plan, options, {})
        assert graph["app.js"]["sources"][0]["path"] == "dep.js"
        assert graph["app.js"]["commit_sha"] == value.head_sha
        assert graph["dep.js"]["sources"][0]["path"] == "app.js"
        assert diagnostics["status"] == "ready"
        assert seen[0].commit_sha == value.head_sha
        assert next(item for item in seen[0].files if item.path == "README.md").content_base64 is None


def test_enrichment_keeps_mandatory_exclusions_and_credentials_out_of_parser(tmp_path, monkeypatch):
    git(tmp_path, "init", "-qb", "main")
    commit(tmp_path, {"app.py": "key = 'super-sensitive-value'\n", ".env": "key=secret\n", "AGENTS.md": "run instructions\n"})
    captured = []
    monkeypatch.setattr(transport, "extract", lambda request, **kwargs: captured.append(request) or parsed(request))
    with snapshots.snapshot(tmp_path, mode="all") as value:
        options = settings.load(value.repo, value.env, value.settings_sha)
        options["_graph"] = True
        plan = planning.plan_review(value.repo, value.env, value.base_sha, value.head_sha,
                                    value.merge_base, options, lambda: False)
        graph, diagnostics = code_graph.enrich(value.repo, value.env, plan, options,
                                               {"api_key": "super-sensitive-value"})
        assert graph == {}
        assert diagnostics["status"] == "ready"
        assert all(item.content_base64 is None for item in captured[0].files)
        assert all(item.exclusion == "excluded" for item in captured[0].files)
        assert "super-sensitive-value" not in captured[0].model_dump_json()


def test_optional_failure_has_explicit_omission(tmp_path, monkeypatch):
    git(tmp_path, "init", "-qb", "main")
    commit(tmp_path, {"app.py": "pass\n"})
    monkeypatch.setattr(transport, "extract", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("offline parser unavailable")))
    with snapshots.snapshot(tmp_path, mode="all") as value:
        options = settings.load(value.repo, value.env, value.settings_sha)
        options["_graph"] = True
        plan = planning.plan_review(value.repo, value.env, value.base_sha, value.head_sha, value.merge_base, options, lambda: False)
        graph, diagnostics = code_graph.enrich(value.repo, value.env, plan, options, {})
        assert graph == {}
        assert diagnostics == {"status": "unavailable", "reason": "offline parser unavailable"}


def test_docker_boundary_disables_network_mounts_credentials_and_automatic_pulls(tmp_path, monkeypatch):
    request = request_fixture()
    seen = []
    monkeypatch.setenv("CM_DATABASE_URL", "postgresql://private-db")
    monkeypatch.setenv("API_KEY", "private-key")
    monkeypatch.setattr(transport.shutil, "which", lambda *args, **kwargs: "/trusted/docker")

    def spawn(command, **kwargs):
        seen.append((command, kwargs))
        payload = CodeGraphRequest.model_validate_json(kwargs["stdin"].read())
        kwargs["stdout"].write(parsed(payload).model_dump_json().encode())
        return SimpleNamespace(poll=lambda: 0, returncode=0)

    monkeypatch.setattr(transport.subprocess, "Popen", spawn)
    monkeypatch.setattr(transport.subprocess, "run", lambda *args, **kwargs: None)
    assert transport.extract(request).commit_sha == request.commit_sha
    command, kwargs = seen[0]
    for flag in ("--network=none", "--read-only", "--pull=never", "--memory=512m", "--memory-swap=512m",
                 "--cpus=1", "--pids-limit=64", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--user=65534:65534"):
        assert flag in command
    assert not any(flag in command for flag in ("--volume", "-v", "--mount", "--env", "-e", "--env-file"))
    assert "API_KEY" not in kwargs["env"] and "CM_DATABASE_URL" not in kwargs["env"]
    assert kwargs["env"]["DOCKER_CONFIG"] != str(tmp_path)


def test_docker_cleanup_after_cancellation(monkeypatch):
    request = request_fixture()
    removed = []
    process = SimpleNamespace(returncode=None, poll=lambda: None)
    process.terminate = lambda: setattr(process, "returncode", -15)
    process.wait = lambda **kwargs: process.returncode
    monkeypatch.setattr(transport.shutil, "which", lambda *args, **kwargs: "/trusted/docker")
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(transport.subprocess, "run", lambda command, **kwargs: removed.append(command))
    calls = iter((False, True))
    with pytest.raises(Exception, match="cancelled"):
        transport.extract(request, stopped=lambda: next(calls))
    assert process.returncode == -15
    assert removed[0][1:3] == ["rm", "--force"]
    assert removed[0][-1].startswith("mortis-parser-")


def test_docker_rejects_remote_endpoint_and_invalid_images(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.example:2375")
    with pytest.raises(ValueError, match="local Docker"):
        transport.docker_environment(tmp_path)
    for value in ("--privileged", "image; touch /tmp/bad", "image\n--network=host"):
        with pytest.raises(ValueError):
            transport.image_name(value)


def test_builder_copies_only_bundled_parser_files(monkeypatch):
    captured = []
    def invoke(command, **kwargs):
        from pathlib import Path
        root = Path(command[-1])
        captured.append((command, sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())))

    monkeypatch.setattr(build.shutil, "which", lambda *args, **kwargs: "/trusted/docker")
    monkeypatch.setattr(build.subprocess, "run", invoke)
    assert build.main(["--tag", "parser:test"]) == 0
    command, files = captured[0]
    assert command[:4] == ["/trusted/docker", "build", "--tag", "parser:test"]
    assert files == ["Dockerfile", "mortis_engine/__init__.py", "mortis_engine/code_graph/__init__.py",
                     "mortis_engine/code_graph/contracts.py", "mortis_engine/code_graph/extract.py",
                     "mortis_engine/code_graph/syntax.py", "mortis_engine/review/__init__.py",
                     "mortis_engine/review/file_policy.py", "requirements.txt"]


def test_native_extractor_retains_real_source_contract():
    pytest.importorskip("tree_sitter")
    from mortis_engine.code_graph.extract import extract
    request = request_fixture()
    result = validate_result(request, extract(request))
    assert result.imports[0].target_path == "dep.js"
    assert result.files[0].unresolved_calls == 1


def test_graph_excludes_renamed_secret_before_parser_and_related_context(tmp_path, monkeypatch):
    git(tmp_path, "init", "-qb", "main")
    commit(tmp_path, {".env": "config = 'repository-secret'\n", "app.py": "pass\n"})
    git(tmp_path, "checkout", "-qb", "feature")
    git(tmp_path, "mv", ".env", "public.py")
    commit(tmp_path, {"app.py": "from .public import config\n"})
    captured = []
    monkeypatch.setattr(transport, "extract", lambda request, **kwargs: captured.append(request) or parsed(request))
    with snapshots.snapshot(tmp_path, mode="commits", base="main", head="feature") as value:
        options = settings.load(value.repo, value.env, value.settings_sha)
        options["_graph"] = True
        plan = planning.plan_review(value.repo, value.env, value.base_sha, value.head_sha, value.merge_base, options, lambda: False)
        assert all(item.path != "public.py" for item in plan.selected)
        graph, diagnostics = code_graph.enrich(value.repo, value.env, plan, options, {})
        hidden = next(item for item in captured[0].files if item.path == "public.py")
        assert hidden.exclusion == "excluded"
        assert hidden.content_base64 is None
        assert diagnostics["status"] == "ready"
        assert all(item["path"] != "public.py" for value in graph.values() for item in value["sources"])
