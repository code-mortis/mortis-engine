"""Read candidate eligibility and retry one saved failed assessment."""

import json
import sys
import time

from mortis_engine import config, operations
from mortis_engine.history import History

from .review import progress_display, select_profile, terminal


def assessments(args):
    result = operations.list_assessments(args.repo, run_id=args.run_id)
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        lines = [f"{row['candidate_id']}  {row['path']}  {row['status']}  "
                 + ("retry eligible" if row["retry_eligible"] else row["blocked_reason"])
                 + ("\n  " + row["error"] if row.get("error") else "") for row in result["candidates"]]
        print(terminal("\n".join(lines) or result.get("blocked_reason", "No saved assessment candidates.")))
    return 0


def assess(args):
    started = time.monotonic()
    try:
        if args.resume and (args.run_id or args.candidate):
            raise ValueError("--resume uses the parent and candidate of the saved retry.")
        if not args.resume and not (args.run_id and args.candidate):
            raise ValueError("mortis assess requires --run RUN_ID and --candidate CANDIDATE_ID.")
        with History(args.repo) as history:
            row = history.row(args.resume, "assessment") if args.resume else history.row(args.run_id)
            saved = json.loads(row["plan"])
            name = args.connection or saved.get("profile")
        name, profile = select_profile(name, config.load()["connections"])
        with progress_display(args.progress) as progress:
            result = operations.retry_assessment(args.repo, run_id=args.run_id, candidate_id=args.candidate,
                resume=args.resume, connection=config.connection(name, profile),
                get_credentials=lambda: config.credentials(profile), profile=name, progress=progress)
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            verdict = (result.get("result") or {}).get("verdict", result["status"])
            lines = [f"Assessment {verdict}", f"Run: {result['run_id']}"]
            if result.get("finding"):
                finding = result["finding"]
                lines.append(f"{finding['path']}: {finding.get('title') or finding.get('content', '')} ({finding['id']})")
            lines.append((result.get("result") or {}).get("explanation", result.get("error", "")))
            print(terminal("\n".join(lines)))
        return 0 if result["status"] == "completed" else 1
    finally:
        print(f"Elapsed: {time.monotonic() - started:.1f}s", file=sys.stderr, flush=True)


def add_parser(commands):
    parser = commands.add_parser("assessments", help="List saved candidates and assessment retry eligibility")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--run", dest="run_id", required=True, help="Saved review or security scan run ID")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.set_defaults(run=assessments)
    parser = commands.add_parser("assess", help="Retry one failed assessment using its exact saved inputs")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--run", dest="run_id", help="Original review or security scan run ID")
    parser.add_argument("--candidate", help="Failed candidate ID from mortis assessments")
    parser.add_argument("--resume", help="Resume an interrupted assessment retry run")
    parser.add_argument("--connection", help="Original saved connection name")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--progress", action="store_true")
    parser.set_defaults(run=assess)
