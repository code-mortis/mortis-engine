"""Regressions for the isolated evaluator; no live provider or personal state."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("review_improvement_eval", ROOT / "evals/review_improvements/run.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def test_frozen_replay_uses_real_pipeline_without_grader_leakage():
    result = evaluation.replay_all(evaluation.ROOT / "fixtures/replay.json")
    assert result["evidence_kind"] == "offline_scripted_replay"
    assert result["quality_evidence"] is False
    metrics = result["metrics"]
    assert metrics["expected_defects"] == 2
    assert metrics["discovery_recall"] == 0.5
    assert metrics["confirmed_precision"] == 1
    assert metrics["confirmed_true"] == 1 and metrics["confirmed_false"] == 0
    assert metrics["raw_candidates"] == 3
    assert metrics["incomplete_cases"] in {0, 1}
    assert metrics["unknown_usage_requests"] >= 1
    assert metrics["total_tokens"] is None and metrics["known_tokens"] > 0
    reports = {row["id"]: row for row in result["cases"]}
    assert reports["c02"]["report"]["candidates"][0]["result"]["verdict"] == "rejected"
    assert reports["c03"]["report"]["candidates"] == []
    assert reports["c04"]["report"]["candidates"][0]["result"]["verdict"] in {"rejected", "uncertain"}
    for row in result["cases"]:
        for request in row["requests"]:
            encoded = json.dumps(request["request"])
            assert "expected.json" not in encoded
            assert "introduced_bug" not in encoded
            assert "candidate_refuted_by_test" not in encoded
            assert "zero-rate" not in encoded and "zero-caller" not in encoded
            assert "quality_evidence" not in encoded


def test_experiment_discovers_caller_and_cannot_pass_live_quality_acceptance(tmp_path):
    recording = evaluation.read(evaluation.ROOT / "fixtures/replay.json")
    recording["discovery"]["c03"] = [2]
    recording["unknown_usage_cases"] = []
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(recording))
    experiment = evaluation.replay_all(path)
    baseline = evaluation.replay_all(evaluation.ROOT / "fixtures/replay.json")
    assert experiment["metrics"]["discovery_recall"] == 1
    assert experiment["metrics"]["confirmed_precision"] == 1
    assert experiment["metrics"]["unknown_usage_requests"] == 0
    assert experiment["metrics"]["total_tokens"] > 0
    candidate = experiment["cases"][2]["report"]["candidates"][0]
    assert candidate["result"]["verdict"] == "confirmed"
    assert {item["path"] for item in candidate["result"]["evidence"]} == {"app.py", "maths.py"}
    comparison = evaluation.compare(baseline, experiment, evaluation.read(evaluation.ROOT / "acceptance.json"))
    assert comparison["discovery_recall_delta"] == 0.5
    assert comparison["quality_accepted"] is False
    assert comparison["checks"]["quality_evidence"] is False
    assert all(value for key, value in comparison["checks"].items() if key not in {"quality_evidence", "coverage"})


def test_metrics_keep_duplicates_incomplete_coverage_and_unknown_usage():
    finding = {"id": "first", "path": "app.py", "start_line": 2, "end_line": 2}
    report = {"status": "partial", "workflow_status": "partial", "candidates": [
        {"snapshot": finding, "status": "failed"}, {"snapshot": finding, "result": {"verdict": "rejected"}}],
        "findings": [finding, {**finding, "id": "duplicate"},
                     {"id": "false", "path": "other.py", "start_line": 1, "end_line": 1}],
        "coverage": {"selected": [{"path": "app.py"}, {"path": "other.py"}],
                     "completed": [{"path": "app.py"}], "failed": [{"path": "other.py"}]},
        "usage": {"requests": [{"consumption_unknown": False, "usage": {"total_tokens": 20}},
                               {"consumption_unknown": True, "usage": {"total_tokens": 5},
                                "reserved_tokens": 1000}]}}
    expected = {"c01": {"defects": [{"id": "a", "path": "app.py", "start_line": 2, "end_line": 2}]}}
    metrics = evaluation.score([{"id": "c01", "report": report}], expected)
    assert metrics["discovery_recall"] == 1
    assert metrics["confirmed_precision"] == 0.5
    assert metrics["duplicate_candidates"] == metrics["duplicate_confirmations"] == 1
    assert metrics["incomplete_cases"] == 1
    assert metrics["selected_files"] == 2 and metrics["completed_files"] == 1
    assert metrics["known_tokens"] == 20 and metrics["unknown_usage_requests"] == 1
    assert metrics["total_tokens"] is None


def test_no_confirmations_has_unavailable_precision_and_wrong_corpus_is_rejected():
    expected = {"c01": {"defects": []}}
    report = {"status": "completed", "workflow_status": "completed", "candidates": [], "findings": [],
              "coverage": {"selected": [], "completed": []}, "usage": {"requests": []}}
    assert evaluation.score([{"id": "c01", "report": report}], expected)["confirmed_precision"] is None
    baseline = {"input_manifest": {"id": "first"}, "metrics": {}}
    experiment = copy.deepcopy(baseline)
    experiment["input_manifest"]["id"] = "different"
    with pytest.raises(ValueError, match="same frozen cases"):
        evaluation.compare(baseline, experiment)


@pytest.mark.parametrize("case_id", ["c02", "c04"])
def test_duplicate_candidates_count_preexisting_and_refuted_allegations(case_id):
    baseline = evaluation.read(evaluation.ROOT / "baseline-result.json")
    row = copy.deepcopy(next(row for row in baseline["cases"] if row["id"] == case_id))
    duplicate = copy.deepcopy(row["report"]["candidates"][0])
    duplicate["snapshot"]["content"] = "Reworded allegation at the same source anchor"
    row["report"]["candidates"].append(duplicate)
    expected = {case_id: {"defects": []}}
    metrics = evaluation.score([row], expected)
    assert metrics["raw_candidates"] == 2
    assert metrics["duplicate_candidates"] == 1
    assert metrics["discovered_defects"] == 0
    assert metrics["confirmed_true"] == metrics["confirmed_false"] == 0
    assert metrics["confirmed_precision"] is None


def test_duplicate_false_confirmations_ignore_ids_but_keep_distinct_causes():
    first = {"id": "first", "path": "app.py", "start_line": 2, "end_line": 2,
             "assessment": {"cause": "Unguarded division", "trigger": "Zero input"}}
    duplicate = {**first, "id": "different-id", "content": "Reworded finding"}
    distinct = {**first, "id": "distinct-cause", "assessment": {"cause": "Truncated input", "trigger": "Fraction"}}
    report = {"status": "completed", "workflow_status": "completed", "candidates": [],
              "findings": [first, duplicate, distinct], "coverage": {"selected": [], "completed": []},
              "usage": {"requests": []}}
    metrics = evaluation.score([{"id": "clean", "report": report}], {"clean": {"defects": []}})
    assert metrics["duplicate_confirmations"] == 1
    assert metrics["confirmed_false"] == 2
    assert metrics["confirmed_precision"] == 0


def test_pinned_git_identity_and_paths_fail_closed(tmp_path):
    _, cases = evaluation.verify_inputs()
    changed = copy.deepcopy(cases[0])
    changed["head_sha"] = "0" * 40
    with pytest.raises(ValueError, match="Pinned head"):
        evaluation.materialize(tmp_path / "pinned", changed)
    changed["base"] = {"../outside.py": "raise AssertionError\n"}
    with pytest.raises(ValueError, match="flat Python"):
        evaluation.materialize(tmp_path / "paths", changed)
    assert not (tmp_path / "outside.py").exists()


def test_guard_counterexample_covers_the_claimed_zero_behavior():
    def inverse(value):
        return 1 / value if value else 0

    assert inverse(0) == 0
    assert inverse(1) == 1


def test_frozen_baseline_keeps_the_measured_excluded_test_limit():
    baseline = evaluation.read(evaluation.ROOT / "baseline-result.json")
    assert baseline["engine_revision"] == evaluation.BASELINE_REVISION
    assert baseline["engine_dirty"] is False
    assert baseline["input_manifest"] == evaluation.verify_inputs()[0]
    assert baseline["metrics"]["incomplete_cases"] == 1
    assert baseline["cases"][3]["report"]["candidates"][0]["result"]["verdict"] == "uncertain"
