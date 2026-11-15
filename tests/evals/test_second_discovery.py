"""The second-pass experiment is offline wiring evidence, never live quality."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("second_discovery_eval", ROOT / "evals/review_improvements/run.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def test_opt_in_second_discovery_uses_normal_assessment_and_cannot_pass_quality_acceptance():
    recording = evaluation.ROOT / "fixtures/second-pass.json"
    baseline = evaluation.replay_all(recording)
    experiment = evaluation.replay_all(recording, second_pass=True)
    assert baseline["metrics"]["discovery_recall"] == 0.5
    assert experiment["metrics"]["discovery_recall"] == 1
    assert experiment["metrics"]["confirmed_precision"] == 1
    assert experiment["metrics"]["duplicate_candidates"] == 0
    assert experiment["metrics"]["duplicate_confirmations"] == 0
    assert experiment["metrics"]["unknown_usage_requests"] >= 1
    assert experiment["metrics"]["total_tokens"] is None
    case = next(case for case in experiment["cases"] if case["id"] == "c03")
    analysis = [row for row in case["requests"] if row["stage"] == "analysis"]
    assert len(analysis) == 2
    assert "discovery_pass" not in json.loads(analysis[0]["request"]["messages"][0]["content"])
    assert json.loads(analysis[1]["request"]["messages"][0]["content"])["discovery_pass"] == 2
    candidate, = case["report"]["candidates"]
    assert candidate["result"]["verdict"] == "confirmed"
    assert {item["path"] for item in candidate["result"]["evidence"]} == {"app.py", "maths.py"}
    assert any(row["stage"] == "assessment" for row in case["requests"])
    comparison = evaluation.compare(baseline, experiment, evaluation.read(evaluation.ROOT / "acceptance.json"))
    assert comparison["discovery_recall_delta"] == 0.5
    assert comparison["quality_accepted"] is False
    assert comparison["checks"]["quality_evidence"] is False
    assert comparison["checks"]["known_usage"] is False
    assert experiment["quality_evidence"] is False
