"""Connection-free inspection of the actual local review plan."""

from . import planning, settings, snapshots
from .service import plan_snapshot, unit_byte_limit

VERSION = "mortis.review-preview/v1"


def _sizes(units):
    return {"source_bytes": sum(len(unit.source.encode()) for unit in units),
            "diff_bytes": sum(len(unit.patch.encode()) for unit in units),
            "assigned_unit_json_bytes": sum(planning._unit_bytes(unit) for unit in units)}


def _exclusions(snap, plan, options):
    result = {}
    for row in [*snap.excluded, *plan.exclusions]:
        row = dict(row)
        if row["reason"] == "excluded":
            row["reason"] = next((reason for path in (row["path"], row.get("old_path")) if path
                if (reason := planning.exclusion_reason(path, options, plan.rules))), "excluded")
        result[row["path"]] = row
    return list(result.values())


def preview_local(repository, *, mode="worktree", base=None, head="HEAD", effort=None,
                  overrides=None, prompt_token_ceiling=None):
    """Return a versioned real plan without profiles, credentials, history or inference.

    The optional explicit ceiling reproduces a connection's unit bound without reading
    that connection. Batch counts exclude provider-envelope splitting and follow-ups.
    """
    maximum = unit_byte_limit(prompt_token_ceiling)
    with snapshots.snapshot(repository, mode=mode, base=base, head=head) as snap:
        options = settings.load(snap.repo, snap.env, snap.settings_sha)
        options.update(effort=effort or options["effort"], review_mode=snap.mode, _settings_sha=snap.settings_sha)
        settings.apply_overrides(options, overrides)
        plan = plan_snapshot(snap, options, prompt_token_ceiling=prompt_token_ceiling)
        selected = [{"path": row.path, "old_path": row.old_path, "status": row.status,
                     "source_sha": row.source_sha, "blob_sha": row.blob_sha,
                     "unit_keys": [unit.key for unit in row.units], "error": row.error,
                     **(_sizes(row.units) if row.units else dict.fromkeys(_sizes([])))} for row in plan.selected]
        units = [{"key": unit.key, "path": unit.path, "old_path": unit.old_path,
                  "source_sha": unit.source_sha, "start_line": unit.start_line, "end_line": unit.end_line,
                  "source_only": unit.source_only, **_sizes([unit])} for unit in plan.units]
        batches = [{"number": number, "unit_keys": [unit.key for unit in batch],
                    "paths": list(dict.fromkeys(unit.path for unit in batch)), **_sizes(batch)}
                   for number, batch in enumerate(plan.batches, 1)]
        exclusions = _exclusions(snap, plan, options)
        return {"schema_version": VERSION, "plan_version": planning.VERSION,
                "mode": snap.mode, "base_sha": snap.base_sha, "head_sha": snap.head_sha,
                "merge_base": snap.merge_base, "settings_sha": snap.settings_sha,
                "revision": snap.settings_sha if snap.mode == "all" else snap.head_sha,
                "full_tree_source_policy": planning.FULL_TREE_SOURCE_POLICY,
                "policy_digest": plan.policy_digest, "rules_digest": plan.rules_digest,
                "selected_files": selected, "excluded_files": exclusions, "units": units, "batches": batches,
                "totals": {"selected_files": len(selected), "excluded_files": len(exclusions),
                           "units": len(units), "planned_batches": len(batches), **_sizes(plan.units)},
                "bounds": {"prompt_token_ceiling": prompt_token_ceiling, "max_unit_bytes": maximum,
                           "max_batch_bytes": 98304, "max_source_bytes": 20_000_000,
                           "max_files": options["max_files"]},
                "limit_reason": plan.limit_reason,
                "errors": [{"path": row.path, "message": row.error} for row in plan.selected if row.error],
                "warnings": [*snap.warnings, *plan.rules["warnings"]],
                "assumptions": ["No saved connection was read; unit sizing uses only the explicit ceiling or the default bound.",
                                "Bytes describe assigned source, diff and unit JSON, not tokens or a complete provider request.",
                                "Planned batches exclude provider-envelope splitting, graph context, source follow-ups and later stages."]}
