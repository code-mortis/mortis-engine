"""Terminal workflow selection and versioned report rendering."""

import json
import re
import sys

from mortis_engine import config
from mortis_engine.history import History
from mortis_engine.review import review_local


def select_profile(name, profiles):
    if name is None:
        if not profiles:
            raise ValueError("Set up an AI connection first with mortis connect.")
        if len(profiles) > 1:
            raise ValueError("Select a connection with --connection. Saved profiles: " + ", ".join(sorted(profiles)))
        name = next(iter(profiles))
    if name not in profiles:
        raise ValueError(f"Unknown connection {name!r}. Saved profiles: " + ", ".join(sorted(profiles)))
    return name, profiles[name]


def selected(args):
    name = args.connection
    if args.resume and name is None:
        with History(args.repo) as history:
            name = json.loads(history.row(args.resume, args.command)["plan"]).get("profile")
    return select_profile(name, config.load()["connections"])


def terminal(text):
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", "", text)


def render(result):
    coverage = result["coverage"]
    selected = len(coverage["selected"])
    completed = len(coverage["completed"])
    tokens = result["usage"]["total_tokens"]
    lines = [
        f"Review {result['status']}  {result['merge_base'][:12]}..{result['head_sha'][:12]}",
        f"Coverage: {completed}/{selected} files fully reviewed. Excluded: {len(result['excluded_files'])}. "
        f"Findings: {len(result['findings'])}. Tokens: {tokens if tokens is not None else 'unknown'}.",
        f"Run: {result['run_id']}",
    ]
    if result.get("mode") == "all":
        lines[0] = f"Review {result['status']}  full tree at {result['revision'][:12]}"
    if not selected:
        lines.append("No eligible files in the selected changes.")
    for item in result["warnings"]:
        lines.append(item["message"])
    for item in result["errors"]:
        lines.append(f"Incomplete: {item['path']} — {item['message']}")
    for error in result.get("stage_errors", []):
        lines.append(f"Incomplete {error['stage']}: {error['error']}")
    for finding in result["findings"]:
        lines.extend(["", f"[{finding['severity']}] {finding['path']}:{finding['start_line']}-{finding['end_line']}",
                      finding["content"], f"Trigger: {finding['trigger']}", f"Consequence: {finding['consequence']}"])
        for evidence in finding["evidence"]:
            lines.append(f"  {evidence['path']}:{evidence['start_line']}-{evidence['end_line']}  {evidence['quote']}")
    if result.get("summary", {}).get("result"):
        lines.extend(["", result["summary"]["result"]["narrative"]])
    if result.get("verification", {}).get("verifications"):
        for row in result["verification"]["verifications"]:
            lines.append(f"{row['finding_id']}: {row['verdict']}")
    if result.get("fix_prompts"):
        lines.append(result["fix_prompts"])
    graph = result.get("code_graph", {})
    if graph:
        lines.append(f"Code graph: {graph.get('status', 'unavailable')}" + (" — " + graph["reason"] if graph.get("reason") else ""))
    return terminal("\n".join(lines))


def run(args):
    if args.resume and (args.base or args.head or args.staged or args.all or args.effort or args.graph):
        raise ValueError("--resume uses the inputs and options of the saved run.")
    if args.head and not (args.base or args.all):
        raise ValueError("Use --head with --base for a comparison or with --all for a full-branch review.")
    mode = "all" if args.all else "staged" if args.staged else "commits" if args.base else "worktree"
    name, profile = selected(args)

    def announce(count, model):
        print(terminal(f"Reviewing {count} file(s) with {name!r} / {model}…"), file=sys.stderr, flush=True)

    result = review_local(args.repo, mode=mode, base=args.base, head=args.head or "HEAD",
                          connection=config.connection(name, profile), get_credentials=lambda: config.credentials(profile),
                          on_plan=announce, profile=name, effort=args.effort, graph=args.graph, resume=args.resume)
    print(json.dumps(result, indent=2) if args.format == "json" else render(result))
    return 1 if result["status"] == "failed" else 2 if result["status"] == "partial" or result["coverage"]["waived"] else 0


def options(parser):
    parser.add_argument("--repo", default=".", help="Local repository (default: current directory)")
    parser.add_argument("--connection", help="Saved connection name (optional with one profile)")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Report output format")
    parser.add_argument("--effort", choices=("low", "medium", "high"), help="Override repository reasoning effort")
    parser.add_argument("--resume", metavar="RUN_ID", help="Continue an interrupted run with its original inputs")


def add_parser(commands):
    parser = commands.add_parser("review", help="Review local changes (default), staged changes, a branch or a comparison")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--base", help="Review changes from the common ancestor of this ref and --head")
    mode.add_argument("--staged", action="store_true", help="Review the index against HEAD")
    mode.add_argument("--all", action="store_true", help="Review all eligible tracked files at --head")
    parser.add_argument("--head", help="Branch, tag or commit (default: HEAD)")
    parser.add_argument("--graph", action="store_true", help="Include related source from the isolated local code graph parser")
    options(parser)
    parser.set_defaults(run=run)
