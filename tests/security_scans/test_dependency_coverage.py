import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mortis_engine.security_scans import engine, scanner, service


def normalize(paths, *results):
    item = SimpleNamespace(files=Path("/snapshot"), inventory=dict.fromkeys(paths))
    return engine.normalize_trivy(item, {"Results": list(results)})


def target(path, package_type, **fields):
    return {"Target": path, "Class": "lang-pkgs", "Type": package_type, **fields}


def test_dependency_coverage_requires_a_matching_parser_for_each_manifest():
    paths = ["package.json", "package-lock.json", "pyproject.toml", "nested/pyproject.toml", "nested/uv.lock"]
    result = normalize(paths,
        target("package-lock.json", "npm", Packages=[{"Name": "shared", "Version": "1.0"}]),
        target("nested/uv.lock", "uv", Packages=[{"Name": "shared", "Version": "1.0"}]))

    assert result["problems"] == [
        "Dependency inventory is unavailable for pyproject.toml; a supported resolved dependency file is required.",
    ]
    assert set(result["dependency_targets"]["package-lock.json"]) == {"npm"}
    assert set(result["dependency_targets"]["nested/uv.lock"]) == {"uv"}


@pytest.mark.parametrize("row", [
    target("package-lock.json", "npm"),
    target("package-lock.json", "npm", Packages=None),
    target("package-lock.json", "pip", Packages=[]),
    {"Target": "package-lock.json", "Class": "secret", "Type": "npm", "Packages": []},
])
def test_dependency_coverage_rejects_missing_or_mismatched_inventory(row):
    result = normalize(["package.json", "package-lock.json"], row)

    assert result["problems"] == [
        "Dependency inventory is unavailable for package.json; a supported resolved dependency file is required.",
        "Dependency target was not successfully analyzed: package-lock.json",
    ]


def test_explicit_empty_inventory_remains_covered_when_trivy_adds_a_secret_row():
    result = normalize(["package.json", "package-lock.json"],
        target("package-lock.json", "npm", Packages=[]), {"Target": "package-lock.json", "Class": "secret"})

    assert result["problems"] == []
    assert result["dependency_targets"] == {"package-lock.json": {"npm": []}}


def npm_snapshot(tmp_path, *, lock_overrides=None, manifest_overrides=None, extra=None):
    identity = {"name": "test-project", "version": "1.0.0"}
    lock = {**identity, "lockfileVersion": 3, "requires": True, "packages": {"": identity}}
    files = {"nested/package-lock.json": {**lock, **(lock_overrides or {})},
             "nested/package.json": {**identity, **(manifest_overrides or {})}, **(extra or {})}
    inventory = {}
    for path, value in files.items():
        if value is None:
            continue
        content = value if isinstance(value, str) else json.dumps(value)
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
        inventory[path] = {"hash": engine.digest(content)}
    return SimpleNamespace(files=tmp_path, inventory=inventory, stopped=lambda: False)


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("reported", [False, True])
def test_empty_npm_lockfile_verified_when_trivy_omits_packages(tmp_path, version, reported):
    path = "nested/package-lock.json"
    item = npm_snapshot(tmp_path, lock_overrides={"lockfileVersion": version})
    results = [target(path, "npm")] if reported else []

    result = engine.normalize_trivy(item, {"Results": results})

    assert result["problems"] == []
    assert result["dependency_targets"] == {path: {"npm": []}}
    assert (path in result["targets"]) is reported


@pytest.mark.parametrize("manifest_overrides", [
    {"dependencies": {"pkg": "1.0.0"}},
    {"devDependencies": {"pkg": "1.0.0"}},
    {"optionalDependencies": {"pkg": "1.0.0"}},
    {"peerDependencies": {"pkg": "1.0.0"}},
    {"bundledDependencies": ["pkg"]},
    {"workspaces": []},
    {"name": "other-project"},
])
def test_empty_npm_lockfile_requires_matching_empty_manifest(tmp_path, manifest_overrides):
    item = npm_snapshot(tmp_path, manifest_overrides=manifest_overrides)

    result = engine.normalize_trivy(item, {"Results": []})

    assert result["dependency_targets"] == {}
    assert len(result["dependency_problems"]) == 2


@pytest.mark.parametrize("lock_overrides", [
    {"lockfileVersion": 1},
    {"lockfileVersion": "3"},
    {"packages": {}},
    {"packages": {"": {}, "node_modules/pkg": {"version": "1.0.0"}}},
    {"packages": {"": {"name": "test-project", "version": "1.0.0", "link": True}}},
    {"dependencies": {"pkg": {"version": "1.0.0"}}},
    {"workspaces": ["packages/*"]},
])
def test_empty_manifest_requires_supported_root_only_lockfile(tmp_path, lock_overrides):
    item = npm_snapshot(tmp_path, lock_overrides=lock_overrides)

    result = engine.normalize_trivy(item, {"Results": []})

    assert result["dependency_targets"] == {}
    assert len(result["dependency_problems"]) == 2


@pytest.mark.parametrize("extra", [
    {"nested/package.json": None},
    {"nested/package-lock.json": None},
    {"nested/package-lock.json": "{"},
    {"nested/package-lock.json": '{"lockfileVersion": 3, "packages": {}, "packages": {"": '
                                 '{"name": "test-project", "version": "1.0.0"}}}'},
    {"nested/npm-shrinkwrap.json": {}},
    {"nested/pnpm-lock.yaml": ""},
    {"nested/package-lock.json": " " * engine.NPM_INVENTORY_FILE_LIMIT + "{}"},
])
def test_empty_npm_inventory_requires_both_valid_bounded_unambiguous_files(tmp_path, extra):
    item = npm_snapshot(tmp_path, extra=extra)

    result = engine.normalize_trivy(item, {"Results": []})

    assert result["dependency_targets"] == {}
    assert result["dependency_problems"]


@pytest.mark.parametrize("row", [
    {"Target": "nested/package-lock.json", "Class": "secret", "Packages": [{"Name": "pkg", "Version": "1"}]},
    target("nested/package-lock.json", "npm", Packages=None),
    target("nested/package-lock.json", "pip"),
])
def test_conflicting_scanner_inventory_blocks_empty_npm_fallback(tmp_path, row):
    result = engine.normalize_trivy(npm_snapshot(tmp_path), {"Results": [row]})

    assert result["dependency_targets"] == {}
    assert len(result["dependency_problems"]) == 2


@pytest.mark.parametrize("row", [{"Class": "secret"}, {"Class": "config", "Packages": []}])
def test_empty_npm_fallback_ignores_unrelated_scanner_rows(tmp_path, row):
    path = "nested/package-lock.json"
    result = engine.normalize_trivy(npm_snapshot(tmp_path), {"Results": [{"Target": path, **row}]})

    assert result["dependency_targets"] == {path: {"npm": []}}
    assert result["problems"] == []


def test_empty_npm_fallback_accepts_no_cancellation_callback(tmp_path):
    item = npm_snapshot(tmp_path)
    item.stopped = None
    assert engine.normalize_trivy(item, {})["problems"] == []


def test_trivy_propagates_empty_npm_inventory_cancellation(tmp_path, monkeypatch):
    item = npm_snapshot(tmp_path)
    item.root = tmp_path
    item.stopped = lambda: True
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(scanner.shutil, "which", lambda *args, **kwargs: "/test/trivy")
    monkeypatch.setattr(scanner, "offline_prefix", lambda: [])

    def command(args, *unused):
        return 0, scanner.VERSION if "--version" in args else '{"SchemaVersion": 2}', ""

    monkeypatch.setattr(scanner, "command", command)
    with pytest.raises(engine.ReviewStopped, match="cancelled"):
        scanner.trivy(item)


def test_empty_npm_fallback_verifies_snapshot_hash_before_using_it(tmp_path):
    path = "nested/package-lock.json"
    item = npm_snapshot(tmp_path)
    file = tmp_path / path
    file.write_text(file.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = engine.normalize_trivy(item, {"Results": []})

    assert result["dependency_targets"] == {}
    assert len(result["dependency_problems"]) == 2


def test_empty_npm_fallback_rejects_an_excluded_competing_lock(tmp_path):
    item = npm_snapshot(tmp_path)
    item.excluded = [{"path": "nested/npm-shrinkwrap.json", "reason": "Symlink or submodule is not followed"}]

    result = engine.normalize_trivy(item, {"Results": []})

    assert result["dependency_targets"] == {}
    assert len(result["dependency_problems"]) == 2


@pytest.fixture
def changed_dependency():
    path = "package-lock.json"
    item = SimpleNamespace(sha="b" * 40, inventory={path: {}}, renamed=lambda *args: path,
        read=lambda *args: "before", current_source=lambda *args: "after", diff=lambda *args: "-before\n+after")
    evidence = {"kind": "dependency", "path": path, "source_sha": "a" * 40, "package": "shared",
                "package_type": "npm", "installed_version": "1.0.0", "fixed_version": "2.0.0"}
    return item, evidence


def test_dependency_fix_matches_the_same_project_and_ecosystem(changed_dependency):
    item, evidence = changed_dependency
    result = {"metadata": {"database_fresh": True}, "dependency_targets": {
        "package-lock.json": {"npm": [{"name": "shared", "version": "2.0.0"}]},
        "uv.lock": {"uv": [{"name": "shared", "version": "1.0.0"}]},
    }}

    assert service.deterministic_fix(item, evidence, result)["verdict"] == "fixed"
    result["dependency_targets"]["nested/yarn.lock"] = {"yarn": [{"name": "shared", "version": "1.0.0"}]}
    assert service.deterministic_fix(item, evidence, result)["verdict"] == "fixed"
    result["dependency_targets"]["yarn.lock"] = {"yarn": [{"name": "shared", "version": "1.0.0"}]}
    assert service.deterministic_fix(item, evidence, result)["verdict"] == "still_present"


@pytest.mark.parametrize(("inventory", "expected"), [
    ({"npm": []}, "fixed"),
    ({}, "uncertain"),
    ({"pip": []}, "uncertain"),
])
def test_dependency_fix_requires_verified_inventory(changed_dependency, inventory, expected):
    item, evidence = changed_dependency
    result = {"metadata": {"database_fresh": True}, "dependency_targets": {"package-lock.json": inventory}}

    assert service.deterministic_fix(item, evidence, result)["verdict"] == expected


def test_legacy_untyped_inventory_can_prove_a_same_target_version_only(changed_dependency):
    item, evidence = changed_dependency
    packages = [{"name": "shared", "version": "2.0.0"}]
    result = {"metadata": {"database_fresh": True}, "targets": {
        "package-lock.json": {"packages": []},
        "nested/package-lock.json": {"packages": packages},
    }}

    assert service.deterministic_fix(item, evidence, result)["verdict"] == "uncertain"
    result["targets"]["package-lock.json"]["packages"] = packages
    assert service.deterministic_fix(item, evidence, result)["verdict"] == "fixed"
