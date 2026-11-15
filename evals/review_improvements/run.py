"""Offline replay of the real review pipeline; never evidence of live model quality."""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
BASELINE_REVISION = "35fbc999dd7fd4bf42251b22c04059dfdbc51c22"


def read(path):
    return json.loads(Path(path).read_text())


def git(repo, *args, input=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="Mortis Eval", GIT_COMMITTER_NAME="Mortis Eval",
               GIT_AUTHOR_EMAIL="eval@localhost", GIT_COMMITTER_EMAIL="eval@localhost",
               GIT_AUTHOR_DATE="946684800 +0000", GIT_COMMITTER_DATE="946684800 +0000")
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                           *args], cwd=repo, input=input, env=env, text=True, capture_output=True,
                          check=True).stdout.strip()


def materialize(repo, case):
    """Build only evaluator-owned fixtures, never execute reviewed code or its tests."""
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=main", "--object-format=sha1", "--template=")
    parent = None
    for label, files in (("base", case["base"]), ("head", case["changes"])):
        for name, content in files.items():
            # The fixture format deliberately allows only flat Python source files.
            if Path(name).name != name or not name.endswith(".py"):
                raise ValueError("Fixture paths must be flat Python files")
            (repo / name).write_text(content)
        git(repo, "add", ".")
        tree = git(repo, "write-tree")
        args = ["commit-tree", tree, *(["-p", parent] if parent else [])]
        parent = git(repo, *args, input=label + "\n")
        if parent != case[label + "_sha"]:
            raise ValueError(f"Pinned {label} revision changed for {case['id']}")
    git(repo, "update-ref", "refs/heads/main", parent)


def verify_inputs():
    manifest = read(ROOT / "baseline.json")
    for name, expected in manifest["inputs_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frozen baseline input changed: {name}")
    cases = read(ROOT / "fixtures/cases.json")
    pairs = [{key: row[key] for key in ("id", "base_sha", "head_sha")} for row in cases]
    if pairs != manifest["case_pairs"] or manifest["engine_revision"] != BASELINE_REVISION:
        raise ValueError("Frozen baseline revision pairs changed")
    return manifest, cases


class Replay:
    """Adapt scripted responses to actual task/citation IDs, then let Mortis validate them."""

    def __init__(self, case_id, recording):
        self.case_id, self.recording = case_id, recording
        self.calls = []

    async def send(self, connection, credentials, request, **kwargs):
        from mortis_engine.schemas import Response, Usage

        content = request.messages[0].content
        context, _ = json.JSONDecoder().raw_decode(content[content.index("{"):])
        properties = request.output_schema["properties"]
        stage = "analysis" if "task_key" in properties else "assessment" if "verdict" in properties else "summary"
        self.calls.append({"stage": stage, "request": request.model_dump(mode="json")})
        rows = [row for row in self.recording["responses"] if row["case"] == self.case_id]
        if stage == "analysis":
            findings = []
            discovery = (self.recording.get("second_discovery", {}) if context.get("discovery_pass") == 2
                         else self.recording["discovery"])
            for index in discovery.get(self.case_id, []):
                row = self.recording["responses"][index]
                if row["case"] != self.case_id:
                    raise ValueError("Replay response belongs to another case")
                unit = next((unit for unit in context["assigned_units"] if unit["path"] == row["path"]
                             and unit["start_line"] <= row["line"] <= unit["end_line"]), None)
                if unit is None:
                    continue
                citation = next(source for source in context["citation_sources"] if source["path"] == row["path"]
                                and source["start_line"] <= row["line"] <= source["end_line"])
                findings.append({"unit_key": unit["key"], "path": row["path"], "start_line": row["line"],
                    "end_line": row["line"], "content": row["content"], "category": "correctness",
                    "severity": "high", "suggestion_code": "", "trigger": row["trigger"],
                    "consequence": row["consequence"], "evidence": [{"source_id": citation["source_id"],
                        "start_line": row["line"], "end_line": row["line"]}]})
            value = {"task_key": context["task_key"],
                     "completed_units": [unit["key"] for unit in context["assigned_units"]], "findings": findings}
        elif stage == "assessment":
            row = next(row for row in rows if row["content"] == context["finding"]["content"])
            available = {item["path"]: identity for identity, item in context["citation_sources"].items()
                         if item["revision"] == "current"}
            missing = sorted({path for path, _ in row["evidence"]} - available.keys())
            value = {"verdict": "uncertain" if missing else row["verdict"], "relationship": "new",
                "finding_id": None, "explanation": row["explanation"], "cause": row["content"],
                "trigger": row["trigger"], "consequence": row["consequence"], "history_explanation": "",
                "contradicts_prior": False, "changed_conditions": "", "suggestion_supported": False,
                "required_paths": missing, "evidence": [{"source_id": available[path], "start_line": line,
                    "end_line": line} for path, line in row["evidence"] if path in available]}
        else:
            value = {"narrative": "Scripted offline summary of the pinned source change.", "groups": []}
        tokens = None if self.case_id in self.recording.get("unknown_usage_cases", []) else 10
        return Response(output=value, usage=Usage(total_tokens=tokens))


def review_case(repo, case, recording, *, second_pass=False):
    from mortis_engine import gateway
    from mortis_engine.review import engine, planning, settings, source, workflows
    from mortis_engine.workflow import Session

    replay = Replay(case["id"], recording)
    connection = {"kind": "api", "provider": "custom", "protocol": "openai", "model": "offline-replay",
                  "base_url": "https://offline.invalid", "options": {}}
    with source.local_repository(repo) as (isolated, env), patch.object(gateway, "send", replay.send):
        options = settings.load(isolated, env, case["base_sha"])
        options.update(concurrency=1, auto_resolve=False)
        if second_pass:
            options["second_pass"] = True
        plan = planning.plan_review(isolated, env, case["base_sha"], case["head_sha"], case["base_sha"],
                                    options, lambda: False)
        session = Session(connection, {})
        status, raw, candidates = engine.run(isolated, env, plan, connection, options, {}, session=session)
        report = {"status": status, "base_sha": case["base_sha"], "head_sha": case["head_sha"],
                  "findings": candidates, "coverage": raw["manifest"]["coverage"], "errors": raw["errors"]}

        async def finish():
            async with session:
                return await workflows.postprocess(isolated, env, report, options, session)

        report = asyncio.run(finish())
        report["usage"] = session.usage()
    return {"id": case["id"], "report": report, "requests": replay.calls}


def matched(snapshot, defects):
    return next((item["id"] for item in defects if snapshot["path"] == item["path"]
                 and snapshot["start_line"] <= item["end_line"]
                 and snapshot["end_line"] >= item["start_line"]), None)


def score(results, expected):
    """Match curated defect anchors, not titles; count raw duplicates separately."""
    # shortcut: anchors identify these isolated defects; manually map overlapping cases before expanding.
    discovered, confirmed, false_confirmed = set(), set(), 0
    raw_candidates, duplicate_candidates, duplicate_confirmations = 0, 0, 0
    selected, completed, incomplete, known_tokens, unknown_requests = 0, 0, 0, 0, 0
    for row in results:
        case_id, report = row["id"], row["report"]
        defects = expected[case_id]["defects"]
        observed = set()
        for candidate in report.get("candidates", []):
            raw_candidates += 1
            snapshot = candidate["snapshot"]
            anchor = (snapshot["path"], snapshot["start_line"], snapshot["end_line"])
            duplicate_candidates += int(anchor in observed)
            observed.add(anchor)
            defect = matched(snapshot, defects)
            if defect:
                discovered.add((case_id, defect))
        published = set()
        for finding in report.get("findings", []):
            defect = matched(finding, defects)
            assessment = finding.get("assessment", finding)
            identity = ((case_id, defect) if defect else
                        (case_id, finding["path"], finding["start_line"], finding["end_line"],
                         assessment.get("cause", ""), assessment.get("trigger", "")))
            duplicate_confirmations += int(identity in published)
            if identity not in published:
                if defect:
                    confirmed.add(identity)
                else:
                    false_confirmed += 1
            published.add(identity)
        coverage = report["coverage"]
        selected += len(coverage.get("selected", []))
        completed += len(coverage.get("completed", []))
        incomplete += int(report["status"] != "completed" or report.get("workflow_status") != "completed"
                          or len(coverage.get("completed", [])) != len(coverage.get("selected", []))
                          or bool(coverage.get("failed") or coverage.get("waived")))
        for request in report["usage"]["requests"]:
            if request["consumption_unknown"]:
                unknown_requests += 1
            else:
                known_tokens += request["usage"].get("total_tokens") or 0
    expected_count = sum(len(row["defects"]) for row in expected.values())
    denominator = len(confirmed) + false_confirmed
    return {"expected_defects": expected_count, "discovered_defects": len(discovered),
            "discovery_recall": len(discovered) / expected_count if expected_count else None,
            "confirmed_true": len(confirmed), "confirmed_false": false_confirmed,
            "confirmed_precision": len(confirmed) / denominator if denominator else None,
            "raw_candidates": raw_candidates, "duplicate_candidates": duplicate_candidates,
            "duplicate_confirmations": duplicate_confirmations, "incomplete_cases": incomplete,
            "selected_files": selected, "completed_files": completed,
            "known_tokens": known_tokens, "unknown_usage_requests": unknown_requests,
            "total_tokens": None if unknown_requests else known_tokens}


def compare(baseline, experiment, criteria=None):
    if baseline["input_manifest"] != experiment["input_manifest"]:
        raise ValueError("Comparisons require the same frozen cases and grader expectations")
    old, new = baseline["metrics"], experiment["metrics"]
    result = {"discovery_recall_delta": (new["discovery_recall"] - old["discovery_recall"]),
              "confirmed_true_delta": new["confirmed_true"] - old["confirmed_true"],
              "confirmed_precision_delta": (new["confirmed_precision"] - old["confirmed_precision"])
              if old["confirmed_precision"] is not None and new["confirmed_precision"] is not None else None,
              "quality_accepted": False, "acceptance_requested": criteria is not None}
    if criteria is not None:
        known_usage = all(metrics["unknown_usage_requests"] == 0 and metrics["total_tokens"] is not None
                          for metrics in (old, new))
        checks = {"quality_evidence": all(row.get("quality_evidence") is True for row in (baseline, experiment)),
                  "recall_gain": result["discovery_recall_delta"] >= criteria["min_discovery_recall_gain"],
                  "confirmed_gain": result["confirmed_true_delta"] > 0,
                  "precision": new["confirmed_precision"] is not None
                  and new["confirmed_precision"] >= criteria["min_confirmed_precision"],
                  "coverage": new["incomplete_cases"] <= criteria["max_incomplete_cases"],
                  "duplicates": new["duplicate_confirmations"] <= criteria["max_duplicate_confirmations"],
                  "known_usage": known_usage,
                  "token_limit": known_usage and within_ratio(old["total_tokens"], new["total_tokens"],
                                                              criteria.get("max_total_token_ratio")),
                  "cost_limit": within_ratio(old.get("total_cost_usd"), new.get("total_cost_usd"),
                                             criteria.get("max_cost_ratio"))}
        result.update(checks=checks, quality_accepted=all(checks.values()))
    return result


def within_ratio(baseline, experiment, maximum):
    if not all(type(value) in (float, int) for value in (baseline, experiment, maximum)):
        return False
    before, after, limit = (Decimal(str(value)) for value in (baseline, experiment, maximum))
    return all(value.is_finite() and value >= 0 for value in (before, after, limit)) and after <= before * limit


def replay_all(recording_path, *, second_pass=False):
    import mortis_engine.review.engine as engine

    manifest, cases = verify_inputs()
    recording = read(recording_path)
    engine_root = Path(engine.__file__).resolve().parents[3]
    revision = git(engine_root, "rev-parse", "HEAD")
    with tempfile.TemporaryDirectory(prefix="mortis-review-eval-") as temporary:
        root = Path(temporary)
        environment = {name: str(root / name) for name in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")}
        with patch.dict(os.environ, environment):
            results = []
            for case in cases:
                repo = root / case["id"]
                materialize(repo, case)
                results.append(review_case(repo, case, recording, second_pass=second_pass))
    # Expected labels are loaded only after every review request has completed.
    metrics = score(results, read(ROOT / "expected.json"))
    return {"schema_version": "mortis.review-eval/v1", "evidence_kind": "offline_scripted_replay",
            "second_pass": second_pass,
            "quality_evidence": False, "engine_revision": revision,
            "engine_dirty": bool(git(engine_root, "status", "--porcelain", "--", "src")),
            "recording_sha256": hashlib.sha256(Path(recording_path).read_bytes()).hexdigest(),
            "input_manifest": manifest, "metrics": metrics, "cases": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser("replay")
    replay.add_argument("--recording", type=Path, default=ROOT / "fixtures/replay.json")
    replay.add_argument("--second-pass", action="store_true")
    replay.add_argument("--output", type=Path, required=True)
    comparison = commands.add_parser("compare")
    comparison.add_argument("--baseline", type=Path, required=True)
    comparison.add_argument("--experiment", type=Path, required=True)
    comparison.add_argument("--acceptance", type=Path)
    args = parser.parse_args()
    if args.command == "replay":
        result = replay_all(args.recording, second_pass=args.second_pass)
        with args.output.open("x") as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write("\n")
    else:
        result = compare(read(args.baseline), read(args.experiment), read(args.acceptance) if args.acceptance else None)
        print(json.dumps(result, indent=2))
        if args.acceptance and not result["quality_accepted"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
