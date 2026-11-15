"""Compare full-tree request representations offline, without credentials or run history."""

import argparse
import hashlib
import json
from pathlib import Path

from mortis_engine import llm_http
from mortis_engine.review import engine, planning, settings, snapshots, source


def measure(repository, head, protocol="openai"):
    connection = {"kind": "api", "provider": "custom", "protocol": protocol, "model": "measurement", "options": {}}
    measurements = {}
    with snapshots.snapshot(repository, mode="all", head=head) as snap:
        revision = source.resolve_commit(snap.repo, snap.env, head)
        options = settings.load(snap.repo, snap.env, snap.settings_sha)
        options["review_mode"] = "all"
        # Use the pinned settings, including the same unit limit as the service without a prompt ceiling.
        selected = None
        for label, source_policy in (("before", None), ("after", planning.FULL_TREE_SOURCE_POLICY)):
            plan = planning.plan_review(snap.repo, snap.env, snap.base_sha, snap.head_sha, snap.merge_base,
                options, lambda: False, trusted_rules_sha=snap.settings_sha, full_tree_source_policy=source_policy)
            assert not plan.limit_reason and plan.units, "Pinned settings must admit a nonempty review"
            assert not any(item.error for item in plan.selected), "Incomplete source admission"
            inventory = [(item.path, item.blob_sha) for item in plan.selected]
            assert selected is None or selected == inventory, "The measured file inventory changed"
            selected = inventory
            for item in plan.selected:
                assert "".join(unit.source for unit in item.units) == source.read_source(
                    snap.repo, snap.env, snap.head_sha, item.path), "Splitting changed source coverage"
            batches = engine.prepare_batches(plan, connection, options, {})
            assert not any(batch.error for batch in batches), "Incomplete request admission"
            encoded = [json.dumps(llm_http.encode_request(protocol, connection, batch.request),
                                  ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
                       for batch in batches]
            measurements[label] = {"files": len(plan.selected), "units": len(plan.units),
                "planned_batches": len(plan.batches), "requests": len(batches),
                "source_bytes": sum(len(unit.source.encode()) for unit in plan.units),
                "diff_bytes": sum(len(unit.patch.encode()) for unit in plan.units),
                "serialized_request_bytes": sum(map(len, encoded)), "largest_request_bytes": max(map(len, encoded)),
                "request_sha256": [hashlib.sha256(value).hexdigest() for value in encoded]}
    before, after = measurements["before"], measurements["after"]
    assert before["source_bytes"] == after["source_bytes"] and after["diff_bytes"] == 0
    return {"revision": revision, "protocol": protocol, "model": connection["model"], "graph": False,
            "unit_byte_limit": 49152, "batch_byte_limit": 98304,
            "settings": {key: options[key] for key in ("effort", "max_files", "max_output_tokens")},
            **measurements,
            "saved_request_bytes": before["serialized_request_bytes"] - after["serialized_request_bytes"],
            "request_byte_reduction_percent": round(100 * (1 - after["serialized_request_bytes"]
                                                          / before["serialized_request_bytes"]), 2)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--head", default="205b77eae8a20a4c4f76d62d9c9e362c90c58197")
    parser.add_argument("--protocol", choices=("openai", "openai-responses", "anthropic"), default="openai")
    arguments = parser.parse_args()
    print(json.dumps(measure(arguments.repo, arguments.head, arguments.protocol), indent=2))
