"""Terminal workflow selection and versioned report rendering."""

import json
import re
import sys
import time
from contextlib import contextmanager, nullcontext

from rich.console import Console, Group
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.text import Text

from mortis_engine import config
from mortis_engine.history import History
from mortis_engine.review import review_local, settings

LIMIT_FLAGS = ("max_output_tokens", "token_budget", "timeout_seconds", "max_files", "assessment_token_budget",
               "verification_token_budget", "summary_token_budget", "summary_timeout_seconds")


def limit_overrides(args):
    return {key: getattr(args, key) for key in LIMIT_FLAGS if getattr(args, key, None) is not None}


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


@contextmanager
def progress_display(enabled, *, scan=False):
    if not enabled:
        yield lambda value: None
        return
    console = Console(stderr=True)
    display = Progress(SpinnerColumn(), TextColumn("{task.description}", markup=False), TimeElapsedColumn(),
                       console=console, auto_refresh=False)
    task = display.add_task("Preparing security scan…" if scan else "Preparing review…", total=None)
    coverage = ""
    with (Live(display, console=console, transient=True, redirect_stdout=False)
          if console.is_interactive else nullcontext()) as live:

        def update(value):
            nonlocal coverage
            phase = value["phase"]
            lines = []
            if phase == "analysis":
                coverage = f"Files {'scanned' if scan else 'reviewed'}: {value['completed_files']}/{value['selected_files']}    Failed: {value['failed_files']}"
                description = "Scanning code" if scan else "Reviewing code"
                paths = [terminal(path).replace("\n", "\\n").replace("\t", "\\t") for path in value["active_paths"]]
                lines.append(f"AI requests running: {value['active_requests']}")
                if paths:
                    lines.extend(["", "Files in active requests:", *(f"  {path}" for path in paths[:3])])
                    if len(paths) > 3:
                        lines.append(f"  +{len(paths) - 3} more files")
                    lines.extend(["", "Waiting for AI response…"])
            else:
                description = {"graph": "Building code graph…", "postprocess": "Checking findings and writing summary…",
                    "snapshot": "Preparing security snapshot…", "trivy": "Running Trivy security checks…",
                    "assessment": "Checking security findings…", "verification": "Verifying previous findings…",
                    "report": "Preparing security report…"}[phase]
            if coverage:
                lines.insert(0, coverage)
            display.update(task, description=description)
            if live is not None:
                live.update(Group(display, Text("\n".join(lines))), refresh=True)
            else:
                print("\n".join([description, *lines]), file=sys.stderr, flush=True)

        yield update


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
    overrides = limit_overrides(args)
    settings.apply_overrides({}, overrides)
    if args.resume and (args.base or args.head or args.staged or args.all or args.effort or args.graph
                        or args.concurrency is not None or overrides):
        raise ValueError("--resume uses the inputs and options of the saved run.")
    if args.head and not (args.base or args.all):
        raise ValueError("Use --head with --base for a comparison or with --all for a full-branch review.")
    mode = "all" if args.all else "staged" if args.staged else "commits" if args.base else "worktree"
    name, profile = selected(args)

    def announce(count, model):
        print(terminal(f"Reviewing {count} file(s) with {name!r} / {model}…"), file=sys.stderr, flush=True)

    started = time.monotonic()
    try:
        with progress_display(args.progress) as progress:
            result = review_local(args.repo, mode=mode, base=args.base, head=args.head or "HEAD",
                                  connection=config.connection(name, profile), get_credentials=lambda: config.credentials(profile),
                                  on_plan=announce, profile=name, effort=args.effort, graph=args.graph, resume=args.resume,
                                  progress=progress, concurrency=args.concurrency, overrides=overrides)
        print(json.dumps(result, indent=2) if args.format == "json" else render(result))
    finally:
        print(f"Elapsed: {time.monotonic() - started:.1f}s", file=sys.stderr, flush=True)
    return 1 if result["status"] == "failed" else 2 if result["status"] == "partial" or result["coverage"]["waived"] else 0


def options(parser, *, kind="review"):
    parser.add_argument("--repo", default=".", help="Local repository (default: current directory)")
    parser.add_argument("--connection", help="Saved connection name (optional with one profile)")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Report output format")
    parser.add_argument("--effort", choices=("low", "medium", "high"), help="Override repository reasoning effort")
    parser.add_argument("--resume", metavar="RUN_ID", help="Continue an interrupted run with its original inputs")
    fields = {"max_output_tokens"}
    if kind != "summary":
        fields.add("timeout_seconds")
    if kind in {"review", "scan", "background"}:
        fields.add("token_budget")
    if kind in {"review", "scan"}:
        fields.update(("assessment_token_budget", "verification_token_budget"))
    if kind == "verify":
        fields.add("verification_token_budget")
    if kind in {"review", "summary"}:
        fields.update(("summary_token_budget", "summary_timeout_seconds"))
    if kind == "review":
        fields.add("max_files")
    for field in LIMIT_FLAGS:
        if field in fields:
            parser.add_argument("--" + field.replace("_", "-"), type=int,
                                help=f"Override {field} for this run (0: no Mortis limit)")


def execution_options(parser):
    parser.add_argument("--concurrency", type=int, choices=range(1, 9), metavar="1-8",
                        help="Override repository AI concurrency for this run")
    parser.add_argument("--progress", action="store_true", help="Show live progress and active file names on stderr")


def add_parser(commands):
    parser = commands.add_parser("review", help="Review local changes (default), staged changes, a branch or a comparison")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--base", help="Review changes from the common ancestor of this ref and --head")
    mode.add_argument("--staged", action="store_true", help="Review the index against HEAD")
    mode.add_argument("--all", action="store_true", help="Review all eligible tracked files at --head")
    parser.add_argument("--head", help="Branch, tag or commit (default: HEAD)")
    parser.add_argument("--graph", action="store_true", help="Include related source from the isolated local code graph parser")
    execution_options(parser)
    options(parser)
    parser.set_defaults(run=run)
