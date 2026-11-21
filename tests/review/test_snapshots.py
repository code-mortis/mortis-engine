import sys

import pytest

from mortis_engine.review import engine, planning, preview, settings, snapshots, source
from mortis_engine.schemas import ToolCall

from .git_helpers import commit, git


def contents(value, path):
    return source.read_source(value.repo, value.env, value.head_sha, path)


def supporting_sources(value, paths):
    options = settings.load(value.repo, value.env, value.settings_sha)
    plan = planning.plan_review(value.repo, value.env, value.base_sha, value.head_sha, value.merge_base,
                                options, lambda: False)
    calls = [ToolCall(id=str(index), name="read_source", arguments={"path": path, "start_line": 1, "end_line": 1})
             for index, path in enumerate(paths)]
    notice, sources = engine.read_sources(calls, repo=value.repo, env=value.env, plan=plan, options=options,
                                         credentials={}, authorize=lambda: None, stopped=lambda: False)
    return plan, notice, sources


@pytest.mark.parametrize("mode,expected", [("worktree", "working"), ("staged", "staged"), ("commits", "original")])
def test_supporting_tests_read_the_selected_snapshot_without_becoming_targets(repo, mode, expected):
    path, untracked = "tests/test_retry.py", "tests/test_new.py"
    base = commit(repo, {"app.py": "value = 1\n", path: "assert original\n",
                         "tests/test_unchanged.py": "assert unchanged\n"})
    commit(repo, {"app.py": "value = 2\n"})
    (repo / path).write_text("assert staged\n")
    git(repo, "add", path)
    (repo / path).write_text("assert working\n")
    (repo / untracked).write_text("assert untracked\n")
    index = (repo / ".git/index").read_bytes()
    objects = sorted(p.relative_to(repo / ".git/objects") for p in (repo / ".git/objects").rglob("*") if p.is_file())
    arguments = {"base": base} if mode == "commits" else {}
    with snapshots.snapshot(repo, mode=mode, **arguments) as value:
        (repo / path).write_text("assert later_mutation\n")
        (repo / untracked).write_text("assert later_mutation\n")
        plan, notice, sources = supporting_sources(value, [path, untracked])
        assert all(not row.path.startswith("tests/") for row in plan.selected)
        assert notice["selections"][0]["reason"] == "available"
        assert {row["path"]: row["text"] for row in sources} == {
            path: f"assert {expected}", **({untracked: "assert untracked"} if mode == "worktree" else {})}
        assert notice["selections"][1]["reason"] == ("available" if mode == "worktree" else "unavailable")
    assert (repo / ".git/index").read_bytes() == index
    assert objects == sorted(p.relative_to(repo / ".git/objects") for p in (repo / ".git/objects").rglob("*") if p.is_file())
    if mode == "worktree":
        result = preview.preview_local(repo)
        exclusions = {row["path"]: row["reason"] for row in result["excluded_files"]}
        assert all(exclusions[name] == "default_test" for name in (path, untracked, "tests/test_unchanged.py"))


@pytest.mark.parametrize("old,new,configuration,permitted", [
    ("tests/test_old.py", "tests/test_retry.py", "", True),
    ("tests/test_privatekey.py", "tests/test_retry.py", "", False),
    ("tests/test_retry.py", "tests/test_privatekey.py", "", False),
    ("blocked/test_retry.py", "tests/test_retry.py", '[review]\nexclusions = "blocked/**"\n', False),
    ("tests/test_retry.py", "blocked/test_retry.py", '[review.rules]\nexclude = ["blocked/**"]\n', False),
    ("fixtures/test_retry.py", "tests/test_retry.py", "", False),
])
def test_worktree_supporting_test_renames_preserve_both_endpoint_exclusions(repo, old, new, configuration, permitted):
    commit(repo, {"mortis.toml": configuration, old: "assert child_count == 0\n"})
    (repo / new).parent.mkdir(parents=True, exist_ok=True)
    (repo / old).rename(repo / new)
    with snapshots.snapshot(repo, mode="worktree") as value:
        plan, notice, sources = supporting_sources(value, [new])
        assert not plan.selected
        assert notice["selections"][0]["reason"] == ("available" if permitted else "excluded")
        assert [row["text"] for row in sources] == (["assert child_count == 0"] if permitted else [])


@pytest.mark.parametrize("limit,message", [("MAX_SOURCE_BYTES", "20 MB read limit"),
                                        ("MAX_SNAPSHOT_BYTES", "100 MB snapshot admission limit")])
def test_worktree_supporting_tests_keep_snapshot_admission_limits(repo, monkeypatch, limit, message):
    commit(repo, {"app.py": "value = 1\n"})
    (repo / "test_new.py").write_text("assert True\n")
    monkeypatch.setattr(snapshots, limit, 8)
    with pytest.raises(ValueError, match=message), snapshots.snapshot(repo, mode="worktree"):
        pass


def test_worktree_includes_staged_unstaged_and_untracked_with_immutable_sources(repo, tmp_path):
    original = commit(repo, {"app.py": "original\n", "removed.py": "gone\n"})
    (repo / "app.py").write_text("staged\n")
    git(repo, "add", "app.py")
    (repo / "app.py").write_text("working\n")
    (repo / "extra.py").write_text("untracked\n")
    (repo / "removed.py").unlink()
    index = (repo / ".git/index").read_bytes()
    objects_before = sorted(p.relative_to(repo / ".git/objects") for p in (repo / ".git/objects").rglob("*") if p.is_file())
    with snapshots.snapshot(repo, mode="worktree") as value:
        assert value.base_sha == value.settings_sha == value.merge_base == original
        assert contents(value, "app.py") == "working\n"
        assert contents(value, "extra.py") == "untracked\n"
        (repo / "app.py").write_text("later mutation\n")
        assert contents(value, "app.py") == "working\n"
        with pytest.raises(ValueError, match="missing"):
            contents(value, "removed.py")
    assert (repo / ".git/index").read_bytes() == index
    assert git(repo, "rev-parse", "HEAD") == original
    assert objects_before == sorted(p.relative_to(repo / ".git/objects") for p in (repo / ".git/objects").rglob("*") if p.is_file())


def test_staged_snapshot_uses_index_only_and_retains_renames(repo):
    commit(repo, {"old.py": "original\n", "keep.py": "keep\n"})
    git(repo, "mv", "old.py", "new.py")
    (repo / "new.py").write_text("staged\n")
    git(repo, "add", "new.py")
    (repo / "new.py").write_text("working\n")
    (repo / "untracked.py").write_text("new\n")
    with snapshots.snapshot(repo, mode="staged") as value:
        assert contents(value, "new.py") == "staged\n"
        assert contents(value, "keep.py") == "keep\n"
        for missing in ("old.py", "untracked.py"):
            with pytest.raises(ValueError, match="missing"):
                contents(value, missing)


def test_full_tree_uses_target_ref_and_empty_ancestor(repo):
    base = commit(repo, {"main.py": "main\n"})
    git(repo, "checkout", "-qb", "feature")
    target = commit(repo, {"feature.py": "feature\n"})
    (repo / "feature.py").write_text("uncommitted\n")
    with snapshots.snapshot(repo, mode="all", head="feature", base="main") as value:
        assert value.settings_sha == base
        assert value.base_sha == value.merge_base
        assert source.merge_base(value.repo, value.env, value.base_sha, value.head_sha) == value.base_sha
        assert contents(value, "main.py") == "main\n"
        assert contents(value, "feature.py") == "feature\n"
        assert source.git(value.repo, value.env, "ls-tree", value.base_sha) == ""
        assert source.git(value.repo, value.env, "rev-parse", value.head_sha + "^{tree}").strip() == git(repo, "rev-parse", target + "^{tree}")


def test_snapshot_cache_retains_synthetic_objects_without_mutating_original(repo, tmp_path):
    commit(repo, {"app.py": "original\n"})
    (repo / "app.py").write_text("snapshot\n")
    cache = tmp_path / "private-cache"
    with snapshots.snapshot(repo, mode="worktree", object_cache=cache) as value:
        pinned = value.head_sha
        with source.local_repository(repo, object_cache=cache) as (other, other_env):
            assert source.read_source(other, other_env, pinned, "app.py") == "snapshot\n"
    (repo / "app.py").write_text("later\n")
    with source.local_repository(repo, object_cache=cache) as (isolated, env):
        assert source.resolve_commit(isolated, env, pinned) == pinned
        assert source.read_source(isolated, env, pinned, "app.py") == "snapshot\n"
    assert cache.stat().st_mode & 0o777 == 0o700


def test_untracked_exclusions_happen_before_source_reads_and_git_ignores_are_respected(repo, monkeypatch):
    commit(repo, {"app.py": "x=1\n", ".gitignore": "ignored.py\n"})
    for name in (".env", "AGENTS.md", "ssh-private-key.py", "ignored.py", "info-excluded.py"):
        (repo / name).write_text("must not be ingested\n")
    (repo / ".git/info/exclude").write_text("info-excluded.py\n")
    seen = []
    original_read = snapshots._worktree_bytes

    def observed_read(root, path):
        seen.append(path)
        return original_read(root, path)

    monkeypatch.setattr(snapshots, "_worktree_bytes", observed_read)
    with snapshots.snapshot(repo, mode="worktree") as value:
        assert contents(value, "app.py") == "x=1\n"
        assert set(seen).isdisjoint({".env", "AGENTS.md", "ssh-private-key.py", "ignored.py", "info-excluded.py"})
        assert {e["path"] for e in value.excluded} >= {".env", "AGENTS.md", "ssh-private-key.py"}
        for path in (".env", "AGENTS.md", "ssh-private-key.py", "ignored.py", "info-excluded.py"):
            with pytest.raises(ValueError):
                contents(value, path)


def test_filters_hooks_fsmonitor_and_environment_do_not_execute(repo, tmp_path, monkeypatch):
    commit(repo, {"app.py": "initial\n", ".gitattributes": "*.py filter=unsafe\n"})
    marker = tmp_path / "executed"
    for option in ("filter.unsafe.clean", "filter.unsafe.smudge", "core.fsmonitor", "diff.external"):
        git(repo, "config", option, f"touch {marker}")
    hook = repo / ".git/hooks/post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o755)
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "not-the-index"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", f"touch {marker}")
    (repo / "app.py").write_text("working\n")
    with snapshots.snapshot(repo, mode="worktree") as value:
        assert contents(value, "app.py") == "working\n"
    assert not marker.exists()


def test_symlink_targets_are_never_read(repo, tmp_path):
    commit(repo, {"app.py": "initial\n"})
    outside = tmp_path / "outside"
    outside.write_text("outside secret\n")
    (repo / "app.py").unlink()
    (repo / "app.py").symlink_to(outside)
    with snapshots.snapshot(repo, mode="worktree") as value, pytest.raises(ValueError, match="regular"):
        contents(value, "app.py")


def test_symlink_directory_components_reject(repo, tmp_path):
    commit(repo, {"dir/app.py": "initial\n"})
    (repo / "dir/app.py").unlink()
    (repo / "dir").rmdir()
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (outside / "app.py").write_text("outside secret\n")
    (repo / "dir").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError), snapshots.snapshot(repo, mode="worktree"):
        pass


def test_worktree_requires_no_follow_support_but_staged_remains_available(repo, monkeypatch):
    commit(repo, {"app.py": "value = 1\n"})
    monkeypatch.delattr(snapshots.os, "O_NOFOLLOW", raising=False)
    with pytest.raises(ValueError, match="safe no-follow file access"), snapshots.snapshot(repo, mode="worktree"):
        pass
    with snapshots.snapshot(repo, mode="staged") as value:
        assert contents(value, "app.py") == "value = 1\n"


def test_snapshot_cache_rejects_symlinked_parent(tmp_path):
    destination = tmp_path / "destination"
    destination.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(destination, target_is_directory=True)

    with pytest.raises(ValueError, match="parent directories without symbolic links"):
        source._object_cache(alias / "objects")
    assert list(destination.iterdir()) == []


def test_worktree_subdirectory_and_linked_worktree_use_correct_root(repo, tmp_path):
    commit(repo, {"dir/app.py": "initial\n"})
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-qb", "feature", str(linked))
    (linked / "dir/app.py").write_text("linked working\n")
    with snapshots.snapshot(linked / "dir", mode="worktree") as value:
        assert contents(value, "dir/app.py") == "linked working\n"


def test_index_conflicts_fail_explicitly(repo):
    commit(repo, {"app.py": "original\n"})
    blob = git(repo, "rev-parse", "HEAD:app.py")
    import subprocess
    subprocess.run(["git", "update-index", "--index-info"], cwd=repo,
                   input=f"0 {'0' * 40}\tapp.py\n100644 {blob} 1\tapp.py\n100644 {blob} 2\tapp.py\n".encode(), check=True)
    with pytest.raises(ValueError, match="unmerged"), snapshots.snapshot(repo, mode="staged"):
        pass


def test_private_cache_permissions_are_required(repo, tmp_path):
    commit(repo, {"app.py": "initial\n"})
    cache = tmp_path / "public-cache"
    cache.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private"), snapshots.snapshot(repo, mode="worktree", object_cache=cache):
        pass


def test_split_index_is_read_from_private_copy(repo):
    commit(repo, {"app.py": "initial\n"})
    git(repo, "update-index", "--split-index")
    index = (repo / ".git/index").read_bytes()
    (repo / "app.py").write_text("working\n")
    with snapshots.snapshot(repo, mode="worktree") as value:
        assert contents(value, "app.py") == "working\n"
    assert (repo / ".git/index").read_bytes() == index


def test_sparse_checkout_omissions_preserve_committed_files(repo):
    commit(repo, {"keep/app.py": "keep\n", "omitted/app.py": "omitted\n"})
    git(repo, "sparse-checkout", "init", "--cone")
    git(repo, "sparse-checkout", "set", "keep")
    assert not (repo / "omitted/app.py").exists()
    (repo / "keep/app.py").write_text("modified\n")
    with snapshots.snapshot(repo, mode="worktree") as value:
        assert contents(value, "keep/app.py") == "modified\n"
        assert contents(value, "omitted/app.py") == "omitted\n"


def test_commit_comparison_preserves_merge_base_and_ref_inputs(repo):
    ancestor = commit(repo, {"app.py": "ancestor\n"})
    git(repo, "checkout", "-qb", "feature")
    head = commit(repo, {"app.py": "feature\n"})
    git(repo, "checkout", "main")
    base = commit(repo, {"main.py": "main-only\n"})
    with snapshots.snapshot(repo, mode="commits", base="main", head="feature") as value:
        assert value.base_sha == value.settings_sha == base
        assert value.head_sha == head
        assert value.merge_base == ancestor


@pytest.mark.parametrize("arguments", [
    {"mode": "unknown"}, {"mode": "commits"},
    {"mode": "worktree", "base": "main"}, {"mode": "staged", "head": "other"},
])
def test_invalid_mode_combinations_fail_before_snapshot(repo, arguments):
    commit(repo, {"app.py": "initial\n"})
    with pytest.raises(ValueError), snapshots.snapshot(repo, **arguments):
        pass


def test_only_changed_tracked_bodies_are_written_to_the_snapshot(repo, monkeypatch):
    commit(repo, {"unchanged.py": "same\n", "changed.py": "old\n"})
    (repo / "changed.py").write_text("new\n")
    seen, read_paths = [], []
    run_git = source.git
    read_file = snapshots._worktree_bytes

    def observed(*args, **kwargs):
        if "hash-object" in args:
            seen.append(kwargs["input_bytes"])
        return run_git(*args, **kwargs)

    def read(root, path):
        read_paths.append(path)
        return read_file(root, path)

    monkeypatch.setattr(source, "git", observed)
    monkeypatch.setattr(snapshots, "_worktree_bytes", read)
    with snapshots.snapshot(repo, mode="worktree") as value:
        assert contents(value, "unchanged.py") == "same\n"
        assert contents(value, "changed.py") == "new\n"
    assert read_paths == ["changed.py", "unchanged.py"]
    assert seen == [b"new\n"]


def test_excluded_file_rename_preserves_origin_for_planner_scope(repo):
    from mortis_engine.review import planning, settings

    commit(repo, {"private-key.py": "hidden = 'original'\n"})
    (repo / "private-key.py").rename(repo / "public.py")
    with snapshots.snapshot(repo, mode="worktree") as value:
        options = settings.load(value.repo, value.env, value.settings_sha)
        plan = planning.plan_review(value.repo, value.env, value.base_sha, value.head_sha, value.merge_base, options, lambda: False)
        assert not plan.selected
        assert any(item.get("old_path") == "private-key.py" for item in plan.exclusions)


def test_worktree_includes_changes_hidden_by_assume_unchanged(repo):
    commit(repo, {"app.py": "original\n"})
    git(repo, "update-index", "--assume-unchanged", "app.py")
    index = (repo / ".git/index").read_bytes()
    (repo / "app.py").write_text("hidden modification\n")
    with snapshots.snapshot(repo, mode="worktree") as value:
        assert contents(value, "app.py") == "hidden modification\n"
    assert (repo / ".git/index").read_bytes() == index


def test_object_store_and_cache_paths_support_unicode_spaces_and_colons(tmp_path):
    repository = tmp_path / "proyecto ñ: con espacios"
    repository.mkdir()
    git(repository, "init", "-qb", "main")
    commit(repository, {"app.py": "original\n"})
    (repository / "app.py").write_text("working\n")
    cache = tmp_path / "caché: privado"
    with snapshots.snapshot(repository, mode="worktree", object_cache=cache) as value:
        pinned = value.head_sha
        assert contents(value, "app.py") == "working\n"
    with source.local_repository(repository, object_cache=cache) as (repo, env):
        assert source.read_source(repo, env, pinned, "app.py") == "working\n"


def test_trusted_custom_exclusions_block_reads_and_snapshot_cache_ingestion(repo, tmp_path, monkeypatch):
    import zlib

    configuration = '[review]\nexclusions = "skip.py"\n[review.rules]\nexclude = ["blocked/**"]\n'
    commit(repo, {"mortis.toml": configuration, "skip.py": "old excluded reference\n", "app.py": "old\n"})
    (repo / "skip.py").write_text("must-never-be-cached-modified-excluded\n")
    (repo / "blocked").mkdir()
    (repo / "blocked/new.py").write_text("must-never-be-cached-untracked-excluded\n")
    (repo / "app.py").write_text("new\n")
    (repo / "mortis.toml").write_text('[review]\nexclusions = ""\n')
    original = snapshots._worktree_bytes
    reads = []

    def observed(root, path):
        reads.append(path)
        return original(root, path)

    monkeypatch.setattr(snapshots, "_worktree_bytes", observed)
    cache = tmp_path / "cache"
    with snapshots.snapshot(repo, mode="worktree", object_cache=cache) as value:
        assert contents(value, "app.py") == "new\n"
        assert contents(value, "skip.py") == "old excluded reference\n"
        assert {"skip.py", "blocked/new.py"}.isdisjoint(reads)
        assert {item["path"] for item in value.excluded} >= {"skip.py", "blocked/new.py"}
        assert all(b"must-never-be-cached" not in zlib.decompress(path.read_bytes())
                   for path in cache.glob("??/*") if path.is_file())


def test_worktree_snapshot_detects_same_size_content_change(repo):
    commit(repo, {"app.py": "value = 1\n"})
    (repo / "app.py").write_text("value = 2\n")
    with snapshots.snapshot(repo, mode="worktree") as value:
        assert contents(value, "app.py") == "value = 2\n"


@pytest.mark.parametrize(("mode", "flag"), [("worktree", "--others"), ("worktree", "--stage"), ("staged", "--stage")])
def test_local_snapshot_rejects_non_utf8_filenames(repo, monkeypatch, mode, flag):
    commit(repo, {"app.py": "value = 1\n"})
    run = source.subprocess.Popen

    def git_with_non_utf8_path(command, **kwargs):
        if "ls-files" in command and flag in command:
            record = b"invalid-\xff.py\0"
            if flag == "--stage":
                record = b"100644 " + b"a" * 40 + b" 0\t" + record
            command = [sys.executable, "-c", f"import sys; sys.stdout.buffer.write({record!r})"]
        return run(command, **kwargs)

    monkeypatch.setattr(source.subprocess, "Popen", git_with_non_utf8_path)
    with pytest.raises(ValueError, match="path that is not valid UTF-8"), snapshots.snapshot(repo, mode=mode):
        pass
