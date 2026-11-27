"""Connection-free discovery of callers affected by pinned deletions and renames."""

import pytest

from mortis_engine.review import affected_callers, planning, preview_local, settings, snapshots

from .git_helpers import commit, git


@pytest.mark.parametrize("mode", ["commits", "worktree", "staged"])
def test_deleted_executable_with_spaces_plans_cross_language_caller(repo, mode):
    executable = "tools/legacy runner"
    caller = "jobs/start.py"
    base = commit(repo, {executable: "#!/bin/sh\nexit 0\n",
                         caller: 'import subprocess\nsubprocess.run(["../tools/legacy runner"])\n'})
    if mode == "worktree":
        (repo / executable).unlink()
    else:
        git(repo, "rm", executable)
        if mode == "commits":
            git(repo, "commit", "-qm", "remove executable")
    preview = preview_local(repo, mode=mode, base=base if mode == "commits" else None)
    scope = preview["affected_callers"]
    trigger, = scope["triggers"]
    site, = scope["sites"]
    unit, = preview["units"]
    assert trigger["path"] == executable and trigger["source_sha"] == base
    assert site["path"] == caller and site["start_line"] <= 2 <= site["end_line"]
    assert site["unit_key"] == unit["key"]
    assert not preview["selected_files"]
    assert not scope["limits_reached"]


def test_explicit_paths_can_resolve_from_repository_root_without_leaving_it(repo):
    executable = "scripts/charge.sh"
    base = commit(repo, {executable: "#!/bin/sh\nexit 0\n",
                         "backend/run.py": 'subprocess.run(["./scripts/charge.sh"])\n',
                         "backend/outside.py": 'subprocess.run(["../../scripts/charge.sh"])\n'})
    git(repo, "rm", executable)
    git(repo, "commit", "-qm", "remove executable")
    preview = preview_local(repo, mode="commits", base=base)
    site, = preview["affected_callers"]["sites"]
    assert site["path"] == "backend/run.py" and site["triggers"] == [executable]


@pytest.mark.parametrize(("suffix", "unavailable"), [(b"\xff", True), (b"\0", False)])
def test_caller_discovery_omits_undecodable_and_binary_blobs(repo, suffix, unavailable):
    (repo / "caller.py").write_bytes(b'run("tool.sh")  # ' + suffix + b"\n")
    base = commit(repo, {"tool.sh": "exit 0\n"})
    git(repo, "rm", "tool.sh")
    git(repo, "commit", "-qm", "remove tool")
    scope = preview_local(repo, mode="commits", base=base)["affected_callers"]
    assert scope["sites"] == []
    assert scope["limits_reached"] == (["source_unavailable"] if unavailable else [])


@pytest.mark.parametrize("policy", [affected_callers.POLICY, affected_callers.ABSOLUTE_IMPORT_POLICY, "affected-callers/v3"])
def test_literal_replacement_character_preserves_saved_plan_scope(repo, policy):
    base = commit(repo, {"tool.sh": "exit 0\n", "caller.py": 'run("tool.sh")\n'})
    git(repo, "rm", "tool.sh")
    commit(repo, {"caller.py": 'run("tool.sh")  # \ufffd\n'})
    with snapshots.snapshot(repo, mode="commits", base=base) as snap:
        args = (snap.repo, snap.env, snap.base_sha, snap.head_sha, snap.merge_base)
        options = settings.load(snap.repo, snap.env, snap.settings_sha)
        current = planning.plan_review(*args, {**options, "_affected_caller_policy": policy}, lambda: False)
        legacy = planning.plan_review(*args, {**options, "_affected_caller_policy": "affected-callers/v2"}, lambda: False)
    assert current.selected[0].error is None
    assert current.affected_callers["sites"][0]["unit_key"] == current.selected[0].units[0].key
    assert legacy.selected[0].error and not legacy.units
    assert legacy.affected_callers["limits_reached"] == ["source_unavailable"]


def test_callers_across_languages_share_one_discovery_pass(repo):
    callers = {
        "client.ts": ("lib/old_client.ts", "import { send } from './lib/old_client';"),
        "main.rs": ("support/legacy.rs", "use crate::support::legacy;"),
        "worker.c": ("headers/retired.h", '#include "headers/retired.h"'),
        "job.rb": ("tasks/old_job.rb", "require_relative 'tasks/old_job'"),
        "launch.go": ("bin/old-helper", 'cmd := exec.Command("bin/old-helper")'),
        "script.custom": ("assets/retired.dat", 'read("assets/retired.dat")'),
    }
    files = {path: line + "\n" for path, (_, line) in callers.items()}
    files.update({target: "retired\n" for target, _ in callers.values()})
    files["mortis.toml"] = '[review.rules]\ninclude = ["**/*.custom", "**/*.dat"]\n'
    base = commit(repo, files)
    git(repo, "rm", *(target for target, _ in callers.values()))
    git(repo, "commit", "-qm", "retire dependencies")
    preview = preview_local(repo, mode="commits", base=base)
    scope = preview["affected_callers"]
    assert {site["path"]: site["triggers"] for site in scope["sites"]} == {
        path: [target] for path, (target, _) in callers.items()
    }
    assert not scope["limits_reached"]


@pytest.mark.parametrize(("statement", "legacy_match"), [
    ("import acme.old", False), ("from acme.old import apply", False), ("from acme import old", True),
    ("from acme import (\n    old,\n)", False), ("if enabled: import acme.old", False),
    ("value = 1; import acme.old", False), ("from acme.old import apply\nunfinished = (", False),
])
def test_deleted_src_layout_module_has_absolute_python_callers(repo, statement, legacy_match):
    target, caller = "src/acme/old.py", "jobs/run.py"
    base = commit(repo, {target: "def apply():\n    return 1\n", caller: statement + "\n"})
    git(repo, "rm", target)
    git(repo, "commit", "-qm", "remove module")
    preview = preview_local(repo, mode="commits", base=base)
    site, = preview["affected_callers"]["sites"]
    assert site["path"] == caller and site["triggers"] == [target]
    with snapshots.snapshot(repo, mode="commits", base=base) as snap:
        options = settings.load(snap.repo, snap.env, snap.settings_sha)
        legacy = planning.plan_review(snap.repo, snap.env, snap.base_sha, snap.head_sha, snap.merge_base,
                                      {**options, "_affected_caller_policy": "affected-callers/v3"}, lambda: False)
    assert bool(legacy.affected_callers["sites"]) == legacy_match


@pytest.mark.parametrize("imported", ["apply", "(\n    apply,\n)"])
def test_renamed_src_layout_module_tracks_stale_import_only(repo, imported):
    old, new = "src/acme/old.py", "src/acme/new.py"
    original = f"from acme.old import {imported}\napply()\n"
    base = commit(repo, {old: "def apply():\n    return 1\n", "jobs/stale.py": original, "jobs/updated.py": original})
    git(repo, "mv", old, new)
    commit(repo, {"jobs/updated.py": f"from acme.new import {imported}\napply()\n"})
    scope = preview_local(repo, mode="commits", base=base)["affected_callers"]
    assert scope["triggers"][0]["new_path"] == new
    site, = scope["sites"]
    assert site["path"] == "jobs/stale.py" and site["triggers"] == [old]


def test_caller_ranges_count_only_git_line_separators(repo):
    base = commit(repo, {"old.sh": "exit 0\n",
                         "run.py": 'label = "left\u2028right"\na = 1\nb = 2\nc = 3\nrun("old.sh")\n'})
    git(repo, "rm", "old.sh")
    git(repo, "commit", "-qm", "remove script")
    preview = preview_local(repo, mode="commits", base=base)
    site, = preview["affected_callers"]["sites"]
    assert (site["start_line"], site["end_line"]) == (2, 5)


@pytest.mark.parametrize(("package", "caller"), [("acme", "jobs/run.py"), (".", "src/acme/run.py")])
def test_multiline_src_import_preserves_git_line_coordinates(repo, package, caller):
    module = "src/acme/old.py"
    base = commit(repo, {module: "pass\n",
                         caller: f'label = "left\u2028right"\rvalue = 1\na = 2\nb = 3\nfrom {package} import (\n    old,\n)'})
    git(repo, "rm", module)
    git(repo, "commit", "-qm", "remove module")
    preview = preview_local(repo, mode="commits", base=base)
    site, = preview["affected_callers"]["sites"]
    assert (site["path"], site["start_line"], site["end_line"]) == (caller, 2, 6)


@pytest.mark.parametrize(("prefix", "package", "nesting"), [("", ".", ""), ("src/", "..", "nested/")])
def test_deleted_module_matches_multiline_relative_alias_only_in_its_package(repo, prefix, package, nesting):
    module, caller = f"{prefix}acme/old.py", f"{prefix}acme/{nesting}run.py"
    statement = f"from {package} import (\n    old as api,\n)\n\napi.apply()\n"
    base = commit(repo, {module: "def apply(): return 1\n", caller: statement,
                         f"{prefix}other/old.py": "def apply(): return 2\n",
                         f"{prefix}other/{nesting}run.py": statement})
    git(repo, "rm", module)
    git(repo, "commit", "-qm", "remove module")
    preview = preview_local(repo, mode="commits", base=base)
    scope = preview["affected_callers"]
    site, = scope["sites"]
    assert site["path"] == caller and site["triggers"] == [module]
    assert not scope["limits_reached"]


@pytest.mark.parametrize("prefix", ["", "src/"])
def test_renamed_module_rebinds_multiline_relative_alias(repo, prefix):
    old, new = f"{prefix}legacy/tool.py", f"{prefix}acme/tool.py"
    fixed, stale = f"{prefix}acme/fixed.py", f"{prefix}acme/stale.py"
    original = "from ..legacy import (\n    tool as api,\n)\n\napi.apply()\n"
    base = commit(repo, {old: "def apply(): return 1\n", fixed: original, stale: original})
    git(repo, "mv", old, new)
    commit(repo, {fixed: original.replace("..legacy", ".")})
    scope = preview_local(repo, mode="commits", base=base)["affected_callers"]
    assert scope["triggers"][0]["new_path"] == new
    site, = scope["sites"]
    assert site["path"] == stale and site["triggers"] == [old]


@pytest.mark.parametrize("stale_call", ["", "total(1)\n", "; total(1)"])
def test_rename_distinguishes_relative_symbol_alias_from_stale_use(repo, stale_call):
    old, new = "legacy/billing.py", "shop/billing.py"
    stale, updated = "shop/stale.py", "shop/updated.py"
    original = 'from ..legacy.billing import (\n    total as quote)\nquote(1)\n'
    module = "def total(amount): return amount\n\ndef quote(amount): return amount\n"
    base = commit(repo, {old: module, stale: original, updated: original})
    git(repo, "mv", old, new)
    migrated = original.replace("..legacy.billing", ".billing")
    if stale_call.startswith(";"):
        migrated = migrated.replace("quote)\n", f"quote){stale_call}\n")
    else:
        migrated += stale_call
    commit(repo, {updated: migrated})
    scope = preview_local(repo, mode="commits", base=base)["affected_callers"]
    assert scope["triggers"][0]["new_path"] == new
    assert {site["path"] for site in scope["sites"]} == ({stale, updated} if stale_call else {stale})
    assert all(site["triggers"] == [old] for site in scope["sites"])


@pytest.mark.parametrize(("binding", "call"), [
    ("from . import (\n        new as api,\n    )", "api.apply()"),
    ("from . import new as api", "api.apply()"),
    ("from .new import apply", "apply()"),
])
def test_rename_only_rebinds_callers_in_import_scope(repo, binding, call):
    caller = "pkg/caller.py"
    functions = f"def first():\n    return {call}\n\ndef second():\n    return {call}\n"
    base = commit(repo, {"pkg/__init__.py": "", "pkg/old.py": "def apply(): return 1\n",
                         caller: binding.replace("new", "old") + "\n" + functions})
    git(repo, "mv", "pkg/old.py", "pkg/new.py")
    # The nested function inherits first's binding. The sibling has lost the module-level import.
    updated = (f"def first():\n    {binding}\n    def nested():\n        return {call}\n    return nested()\n"
               + "\n" * 8 + f"def second():\n    return {call}\n")
    commit(repo, {caller: updated})
    with snapshots.snapshot(repo, mode="commits", base=base) as snap:
        options = settings.load(snap.repo, snap.env, snap.settings_sha)
        args = (snap.repo, snap.env, snap.base_sha, snap.head_sha, snap.merge_base)
        plan = planning.plan_review(*args, options, lambda: False)
        site, = plan.affected_callers["sites"]
        assert site["path"] == caller and site["triggers"] == ["pkg/old.py"]
        assert site["start_line"] > updated.splitlines().index("    return nested()") + 1
        assert site["end_line"] == len(updated.splitlines())
        saved = planning.plan_review(*args, {**options, "_affected_caller_policy": "affected-callers/v5"},
                                     lambda: False)
        assert saved.affected_callers["sites"] == []


def test_rename_updates_relative_import_of_same_basename(repo):
    base = commit(repo, {"legacy/tool.py": "def apply():\n    return 1\n",
                         "backend/run.py": "from ..legacy.tool import apply\napply()\n"})
    git(repo, "mv", "legacy/tool.py", "backend/tool.py")
    commit(repo, {"backend/run.py": "from .tool import apply\napply()\n"})
    preview = preview_local(repo, mode="commits", base=base)
    assert not preview["affected_callers"]["sites"]


@pytest.mark.parametrize("binding", ["import new as replacement\nreplacement.apply()", "from new import apply\napply()"])
def test_bare_import_of_renamed_sibling_rebinds_only_in_its_directory(repo, binding):
    original = "from src.old import apply\napply()\n"
    base = commit(repo, {"src/old.py": "def apply():\n    return 1\n", "src/caller.py": original,
                         "other/caller.py": original, "other/new.py": "other = 1\n"})
    git(repo, "mv", "src/old.py", "src/new.py")
    commit(repo, {"src/caller.py": binding + "\n", "other/caller.py": binding + "\n"})
    preview = preview_local(repo, mode="commits", base=base)
    assert {site["path"] for site in preview["affected_callers"]["sites"]} == {"other/caller.py"}
    with snapshots.snapshot(repo, mode="commits", base=base) as snap:
        options = {**settings.load(snap.repo, snap.env, snap.settings_sha), "_affected_caller_policy": "affected-callers/v1"}
        legacy = planning.plan_review(snap.repo, snap.env, snap.base_sha, snap.head_sha, snap.merge_base,
                                      options, lambda: False)
    assert legacy.affected_callers["policy"] == "affected-callers/v1"
    assert {site["path"] for site in legacy.affected_callers["sites"]} == {"src/caller.py", "other/caller.py"}


@pytest.mark.parametrize("stale_symbol", [False, True])
def test_rename_rebinds_only_the_symbol_actually_imported(repo, stale_symbol):
    module = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
    base = commit(repo, {"old/tool.py": module,
                         "checkout.py": "from old.tool import foo, bar\nfoo()\nbar()\n",
                         "unrelated.rs": "fn main() { foo(); bar(); }\n"})
    (repo / "new").mkdir()
    git(repo, "mv", "old/tool.py", "new/tool.py")
    commit(repo, {"checkout.py": "from new.tool import foo\nfoo()\n" + ("bar()\n" if stale_symbol else "")})
    preview = preview_local(repo, mode="commits", base=base)
    scope = preview["affected_callers"]
    assert scope["triggers"][0]["new_path"] == "new/tool.py"
    assert not scope["limits_reached"]
    if stale_symbol:
        site, = scope["sites"]
        assert site["path"] == "checkout.py"
        checkout = next(row for row in preview["selected_files"] if row["path"] == "checkout.py")
        assert site["unit_key"] in checkout["unit_keys"]
    else:
        assert not scope["sites"]


def test_legacy_policy_keeps_primary_unit_identity_and_disabled_scope(repo):
    base = commit(repo, {"old.sh": "#!/bin/sh\nexit 0\n", "main.py": 'command = "old.sh"\n'})
    git(repo, "rm", "old.sh")
    commit(repo, {"main.py": 'command = "old.sh"\nrun(command)\n'})
    with snapshots.snapshot(repo, mode="commits", base=base) as snap:
        options = settings.load(snap.repo, snap.env, snap.settings_sha)
        args = (snap.repo, snap.env, snap.base_sha, snap.head_sha, snap.merge_base)
        current = planning.plan_review(*args, options, lambda: False)
        legacy = planning.plan_review(*args, {**options, "_affected_caller_policy": None}, lambda: False)
    unit, = current.units
    original, = legacy.units
    assert current.affected_callers["sites"][0]["unit_key"] == unit.key
    assert unit.key != original.key and unit.path == original.path
    assert unit.affected_triggers[0]["evidence"]["revision"] == base
    assert legacy.affected_callers is None and not original.affected_triggers


def test_discovery_limit_preserves_primary_units_and_marks_omission(repo, monkeypatch):
    base = commit(repo, {"old.sh": "#!/bin/sh\nexit 0\n", "a.py": "value = 1\n", "z.py": 'run("old.sh")\n'})
    git(repo, "rm", "old.sh")
    commit(repo, {"a.py": "value = 2\n"})
    monkeypatch.setattr(affected_callers, "MAX_FILES", 1)
    preview = preview_local(repo, mode="commits", base=base)
    assert [unit["path"] for unit in preview["units"]] == ["a.py"]
    assert preview["affected_callers"]["limits_reached"] == ["scanned_files"]


def test_deleted_symlink_does_not_enable_caller_discovery(repo):
    (repo / "old.sh").symlink_to("tool.sh")
    base = commit(repo, {"tool.sh": "#!/bin/sh\nexit 0\n", "job.py": 'run("old.sh")\n'})
    git(repo, "rm", "old.sh")
    git(repo, "commit", "-qm", "remove symlink")
    preview = preview_local(repo, mode="commits", base=base)
    assert "affected_callers" not in preview
    assert not preview["units"]


def test_file_limit_retains_matched_unassigned_caller_location(repo):
    base = commit(repo, {"old.sh": "exit 0\n", "caller.py": 'run("old.sh")\n', "app.py": "version = 1\n"})
    git(repo, "rm", "old.sh")
    commit(repo, {"app.py": "version = 2\n"})
    preview = preview_local(repo, mode="commits", base=base, overrides={"max_files": 1})
    site, = preview["affected_callers"]["sites"]
    assert (site["path"], site["start_line"], site["status"]) == ("caller.py", 1, "incomplete")
    assert site["unit_key"] is None and site["error"] == "max_files"
    assert [unit["path"] for unit in preview["units"]] == ["app.py"]
