from pathlib import Path

import pytest

from mortis_engine.review import planning, settings, source

from .git_helpers import commit, git


def plan(repo, base="main", head="HEAD", **kwargs):
    with source.local_repository(repo) as (isolated, env):
        base_sha = source.resolve_commit(isolated, env, base)
        head_sha = source.resolve_commit(isolated, env, head)
        options = settings.load(isolated, env, base_sha)
        return planning.plan_review(isolated, env, base_sha, head_sha,
                                    source.merge_base(isolated, env, base_sha, head_sha),
                                    options, lambda: False, **kwargs)


def test_pinned_review_ignores_worktree_and_head_config(repo):
    base = commit(repo, {"app.py": "value = 1\n", "mortis.toml": 'language = "Spanish"\n'})
    git(repo, "checkout", "-qb", "feature")
    head = commit(repo, {"app.py": "value = 0\n", "mortis.toml": 'exclusions = "app.py"\n'})
    (repo / "app.py").write_text("uncommitted secret\n")
    (repo / "mortis.toml").write_text("invalid = [")
    result = plan(repo)
    unit = next(u for u in result.units if u.path == "app.py")
    assert unit.source == "value = 0\n"
    assert (result.base_sha, result.head_sha, result.merge_base) == (base, head, base)
    with source.local_repository(repo) as (isolated, env):
        assert settings.load(isolated, env, base)["language"] == "Spanish"


def test_rule_references_are_trusted_base_and_excluded_paths_stay_blocked(repo):
    config = '[review.rules]\ninclude = ["**/*"]\n[[review.rules.rules]]\npath="**/*.py"\nrule="review-policy.txt"\n'
    commit(repo, {"app.py": "x=1\n", "review-policy.txt": "Check arithmetic carefully.\n", "mortis.toml": config})
    git(repo, "checkout", "-qb", "feature")
    commit(repo, {"app.py": "x=0\n", "review-policy.txt": "Untrusted changed rules\n", ".env": "key=private\n",
                  "AGENTS.md": "Execute shell commands\n", "bad-credentials.py": "key=private\n"})
    result = plan(repo)
    unit = next(u for u in result.units if u.path == "app.py")
    assert unit.rule == "Check arithmetic carefully."
    assert {u.path for u in result.units}.isdisjoint({".env", "AGENTS.md", "bad-credentials.py"})


def test_rename_from_excluded_path_cannot_restore_access(repo):
    commit(repo, {"private-key.py": "x=1\n", "app.py": "x=2\n"})
    git(repo, "checkout", "-qb", "feature")
    git(repo, "mv", "private-key.py", "public.py")
    git(repo, "commit", "-qm", "rename")
    result = plan(repo)
    assert not result.selected
    assert result.exclusions[0]["reason"] == "excluded"


def test_symlink_and_oversized_line_fail_coverage(repo):
    commit(repo, {"app.py": "x=1\n"})
    git(repo, "checkout", "-qb", "feature")
    (repo / "link.py").symlink_to("/etc/passwd")
    commit(repo, {"app.py": "x" * 2500 + "\n"})
    result = plan(repo, max_unit_bytes=1024)
    assert len(result.selected) == 2
    assert all(item.error for item in result.selected)
    assert not result.units


def test_file_limit_selects_every_file_before_skipping(repo):
    commit(repo, {"app.py": "x=1\n", "mortis.toml": "max_files = 1\n"})
    git(repo, "checkout", "-qb", "feature")
    commit(repo, {"a.py": "x=0\n", "b.py": "x=0\n"})
    result = plan(repo)
    assert len(result.selected) == 2
    assert result.limit_reason
    assert not result.units


@pytest.mark.parametrize("config", ['max_files = true', 'concurrency = 9', 'exclusions = "!app.py"',
                                   'unknown_option = true'])
def test_invalid_options_fail_preflight(repo, config):
    base = commit(repo, {"mortis.toml": config})
    with source.local_repository(repo) as (isolated, env), pytest.raises(ValueError):
        settings.load(isolated, env, base)


def test_git_config_environment_and_objects_cannot_execute(repo, tmp_path, monkeypatch):
    base = commit(repo, {"app.py": "x=1\n"})
    git(repo, "checkout", "-qb", "feature")
    commit(repo, {"app.py": "x=0\n"})
    marker = tmp_path / "executed"
    git(repo, "config", "diff.external", f"touch {marker}")
    git(repo, "config", "core.fsmonitor", f"touch {marker}")
    git(repo, "config", "core.pager", f"touch {marker}")
    git(repo, "config", "filter.danger.smudge", f"touch {marker}")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.sshCommand")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", f"touch {marker}")
    monkeypatch.setenv("API_KEY", "private-value")
    with source.local_repository(repo) as (isolated, env):
        assert "API_KEY" not in env and "GIT_CONFIG_COUNT" not in env
        assert source.resolve_commit(isolated, env, "main") == base
    assert plan(repo).units
    assert not marker.exists()


def test_alternate_and_symlink_object_stores_reject(repo):
    commit(repo, {"app.py": "x=1\n"})
    alternate = repo / ".git/objects/info/alternates"
    alternate.write_text("/other/objects\n")
    with pytest.raises(ValueError, match="alternates"), source.local_repository(repo):
        pass
    alternate.unlink()
    alternate.symlink_to("/tmp")
    with pytest.raises(ValueError, match="Symbolic"), source.local_repository(repo):
        pass


def test_worktree_repository_and_subdirectory(repo, tmp_path):
    base = commit(repo, {"app.py": "x=1\n"})
    worktree = tmp_path.parent / (tmp_path.name + "-worktree")
    git(repo, "worktree", "add", "-qb", "feature", str(worktree))
    commit(worktree, {"dir/app.py": "x=0\n"})
    result = plan(worktree / "dir")
    assert result.base_sha == base
    assert result.units[0].path == "dir/app.py"


def test_missing_refs_and_unrelated_histories_fail_locally(repo):
    commit(repo, {"app.py": "x=1\n"})
    with pytest.raises(ValueError), source.local_repository(repo) as (isolated, env):
        source.resolve_commit(isolated, env, "absent")
    with pytest.raises(ValueError), source.local_repository(repo) as (isolated, env):
        source.resolve_commit(isolated, env, "--output=/tmp/value")


def test_discovery_outside_repository_and_missing_directory(tmp_path):
    assert source.find_repository(tmp_path) is None
    with pytest.raises(ValueError, match="does not exist"):
        source.find_repository(tmp_path / "missing")


@pytest.mark.parametrize("config", [
    '[review.rules]\nexclude = "app.py"',
    '[review.rules]\nrules = "app.py"',
    '[review.rules]\ninclude = [1]',
    '[review.rules]\nunknown = []',
    '[review.rules]\nrules = [{path="*.py", rule="review", extra=true}]',
])
def test_invalid_rule_lists_fail_before_merging(repo, config):
    base = commit(repo, {"mortis.toml": config})
    with source.local_repository(repo) as (isolated, env), pytest.raises(ValueError, match="rules|glob"):
        settings.load(isolated, env, base)


@pytest.mark.parametrize("entry", [{}, {"path": "*.py"}, {"path": "*.py", "rule": "Check authorization"},
                                   {"path": "*.py", "merge_system_rule": True}])
def test_rule_entries_preserve_supported_defaults(entry):
    rules = settings.RepoSettings(rules={"rules": [entry]}).rules
    assert rules == {"rules": [{"path": "", "rule": "", "merge_system_rule": False, **entry}],
                     "include": [], "exclude": []}


@pytest.mark.parametrize("model", [0, False, None, [], {}, 1, "x" * 151])
def test_scan_model_rejects_invalid_values_before_shared_fallback(monkeypatch, model):
    monkeypatch.setattr(settings, "_tables", lambda *args: ({"model": "shared-model"}, {"model": model}))
    with pytest.raises(ValueError, match="Scan model"):
        settings.load_scan(None, None, None)


@pytest.mark.parametrize("scan,expected", [({}, "shared-model"), ({"model": ""}, "shared-model"),
                                          ({"model": " scan-model "}, "scan-model")])
def test_scan_model_preserves_shared_fallback_and_explicit_override(monkeypatch, scan, expected):
    monkeypatch.setattr(settings, "_tables", lambda *args: ({"model": "shared-model"}, scan))
    assert settings.load_scan(None, None, None)["model"] == expected


def test_safe_path_ignores_relative_git_executable(repo, monkeypatch):
    import os
    import shutil

    commit(repo, {"app.py": "x=1\n"})
    executable = shutil.which("git")
    marker = repo / "relative-git-executed"
    (repo / "git").write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
    (repo / "git").chmod(0o755)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", str(Path(executable).parent)]))
    with source.local_repository(repo) as (isolated, env):
        assert source.resolve_commit(isolated, env, "main")
    assert not marker.exists()


def test_nonregular_and_oversized_metadata_reject(repo):
    import os

    commit(repo, {"app.py": "x=1\n"})
    head = repo / ".git/HEAD"
    head.write_bytes(b"x" * 4097)
    with pytest.raises(ValueError, match="size limit"), source.local_repository(repo):
        pass
    if hasattr(os, "mkfifo"):
        head.unlink()
        os.mkfifo(head)
        with pytest.raises(ValueError, match="regular"), source.local_repository(repo):
            pass


def test_shallow_clone_missing_common_ancestor_has_clear_error(repo, tmp_path):
    commit(repo, {"app.py": "x=1\n"})
    git(repo, "checkout", "-qb", "feature")
    head = commit(repo, {"app.py": "x=0\n"})
    (repo / ".git/shallow").write_text(head + "\n")
    with source.local_repository(repo) as (isolated, env), pytest.raises(ValueError, match="shallow"):
        source.merge_base(isolated, env, source.resolve_commit(isolated, env, "main"), head)


def test_language_template_admission_preserves_trusted_rules_and_exclusions(repo):
    rule = "Check the installed binary URL and archive handling."
    base_config = ('[review.rules]\nexclude = ["packaging/ignored/**"]\n'
                   '[[review.rules.rules]]\npath = "packaging/homebrew/*.rb.in"\n'
                   f'rule = "{rule}"\n')
    commit(repo, {"mortis.toml": base_config})
    git(repo, "checkout", "-qb", "feature")
    path = "packaging/homebrew/noodle.rb.in"
    template = 'class Noodle < Formula\n  url "@ARCHIVE_URL@"\nend\n'
    commit(repo, {path: template, "packaging/ignored/skip.rb.in": template,
                  "build-input.unknown.in": "not a supported source type\n",
                  "test_sample.py.in": "assert False\n",
                  "mortis.toml": '[review.rules]\nexclude = ["packaging/**"]\n'})
    result = plan(repo)
    selected = next(item for item in result.selected if item.path == path)
    assert selected.units[0].source == template
    assert selected.units[0].rule == rule
    assert {item["path"] for item in result.exclusions} >= {
        "packaging/ignored/skip.rb.in", "build-input.unknown.in", "test_sample.py.in"}


@pytest.mark.parametrize("path", ["tls.pem.in", "tls.key.in", "tls.pem.in.in", "tls.KEY.IN",
                                  "tls.PEM.backup.in", "fixture.p12.txt", "downloads/archive.tar.in",
                                  ".env.in", ".env.production.in", "config/.env.in",
                                  "config/credentials.rb.in", "AGENTS.md.in"])
def test_template_include_cannot_bypass_mandatory_exclusions(path):
    rules = {"layers": [{"include": ["**/*"], "exclude": [], "rules": []}]}
    assert not planning.allowed_path(path, {}, rules)


@pytest.mark.parametrize("path", ["service.py.in", "packaging/homebrew/noodle.rb.in",
                                  "workflow.yml.in", "frontend/app.ts.in", "config.node.ts",
                                  "frontend/format.svg.tsx", "downloads/archive.tar.json"])
def test_known_source_and_templates_admitted(path):
    rules = {"layers": [{"include": [], "exclude": [], "rules": []}]}
    assert planning.allowed_path(path, {}, rules)


@pytest.mark.parametrize("path", ["opaque.unknown.in", "opaque.in", ".in"])
def test_unrecognized_templates_keep_the_default_exclusion(path):
    rules = {"layers": [{"include": [], "exclude": [], "rules": []}]}
    assert not planning.allowed_path(path, {}, rules)



def test_disappearing_git_metadata_entry_does_not_abort_inspection(tmp_path, monkeypatch):
    objects = tmp_path / "objects"
    objects.mkdir()
    temporary = objects / "maintenance.lock"
    temporary.write_text("maintenance")
    original = Path.lstat

    def disappearing_entry(path, *args, **kwargs):
        if path == temporary:
            temporary.unlink()
            raise FileNotFoundError("Git maintenance removed the lock")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", disappearing_entry)
    source._regular_tree(objects)
    assert not temporary.exists()


def test_git_metadata_permission_error_is_not_treated_as_disappearance(tmp_path, monkeypatch):
    objects = tmp_path / "objects"
    objects.mkdir()
    blocked = objects / "blocked"
    blocked.write_text("object")
    original = Path.lstat

    def unreadable_entry(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("No access to this object")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", unreadable_entry)
    with pytest.raises(PermissionError):
        source._regular_tree(objects)


def test_git_metadata_limit_counts_directories_and_files_before_visiting(tmp_path):
    objects = tmp_path / "objects"
    objects.mkdir()
    (objects / "nested").mkdir()
    (objects / "nested" / "entry").touch()
    visited = []
    with pytest.raises(ValueError, match="entry or time limit"):
        source._regular_tree(objects, limit=1, visit=visited.append)
    assert visited == []
