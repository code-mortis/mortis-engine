"""Terminal workflow selection and versioned report rendering."""

import argparse
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
from mortis_engine.review import preview_local, review_local, settings

from .reports import destinations, output_options, write_outputs

LIMIT_FLAGS = ("max_output_tokens", "token_budget", "timeout_seconds", "max_files", "assessment_token_budget",
               "verification_token_budget", "summary_token_budget", "summary_timeout_seconds")
GRAPH_LIMIT_FLAGS = ("max_files", "max_file_bytes", "max_source_bytes")


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


def terminal_line(text):
    return terminal(text).replace("\n", "\\n").replace("\t", "\\t").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def terminal_block(text):
    return [f"  | {line}" for line in text.split("\n")]


@contextmanager
def progress_display(enabled, *, scan=False):
    if not enabled:
        yield lambda value: None
        return
    console = Console(stderr=True)
    display = Progress(SpinnerColumn(), TextColumn("{task.description}", markup=False), TimeElapsedColumn(),
                       console=console, auto_refresh=False)
    task = display.add_task("Preparing security scan…" if scan else "Preparing review…", total=None)
    coverage, caller_coverage = "", ""
    possible, scanner = "", ""
    assessment = []
    capacity, current = "", None
    waiting = 0
    with (Live(display, console=console, transient=True, redirect_stdout=False)
          if console.is_interactive else nullcontext()) as live:

        def update(value):
            nonlocal coverage, caller_coverage, possible, scanner, assessment, capacity, current, waiting
            if value["phase"] == "connection_capacity":
                waiting = value["waiting_requests"]
                capacity = f"Connection requests: {value['active_requests']} active, {value['waiting_requests']} waiting"
                value = {**(current or {"phase": "connection_capacity"}),
                         "active_requests": value["active_requests"], "waiting_requests": waiting}
            else:
                current = value
            phase = value["phase"]
            lines = [capacity] if capacity else []
            if "possible_findings" in value:
                possible = f"Possible findings found so far: {value['possible_findings']}"
            if "scanner_findings" in value:
                scanner = f"Trivy findings: {value['scanner_findings']}"
            if "selected_candidates" in value:
                assessment = [f"Findings assessed: {value['assessed_candidates']}/{value['selected_candidates']}",
                    f"Confirmed{' AI' if scan else ''}: {value['confirmed_findings']}    Rejected: {value['rejected_candidates']}",
                    f"Uncertain: {value['uncertain_candidates']}    Failed: {value['failed_candidates']}"]
                duplicates = value["confirmed_candidates"] - value["confirmed_findings"]
                if duplicates:
                    assessment.append(f"Duplicate confirmations: {duplicates}")
            if phase == "analysis":
                coverage = f"Files {'scanned' if scan else 'reviewed'}: {value['completed_files']}/{value['selected_files']}    Failed: {value['failed_files']}"
                if affected := value.get("affected_sites"):
                    caller_coverage = f"Affected caller sites reviewed: {affected['completed']}/{affected['selected']}"
                description = "Scanning code" if scan else "Reviewing code"
                paths = [terminal_line(path) for path in value["active_paths"]]
                lines.append(f"AI requests running: {value['active_requests']}")
                if paths:
                    lines.extend(["", "Files in active requests:", *(f"  {path}" for path in paths[:3])])
                    if len(paths) > 3:
                        lines.append(f"  +{len(paths) - 3} more files")
                    lines.extend(["", "Waiting for AI response…"])
            else:
                description = {"graph": "Building code graph…", "postprocess": "Checking findings and writing summary…",
                    "snapshot": "Preparing security snapshot…", "trivy": "Running Trivy security checks…",
                    "assessment": "Checking security findings…" if scan else "Checking findings…",
                    "summary": "Writing review summary…", "verification": "Verifying previous findings…",
                    "report": "Preparing security report…", "connection_capacity": "Preparing AI request…"}[phase]
            if waiting:
                description = "Waiting for connection capacity…"
            lines = [line for line in (coverage, caller_coverage, possible, scanner, *assessment) if line] + lines
            display.update(task, description=description)
            if live is not None:
                live.update(Group(display, Text("\n".join(lines))), refresh=True)
            else:
                print("\n".join([description, *lines]), file=sys.stderr, flush=True)

        yield update


def _affected_caller_lines(affected):
    if not affected:
        return []
    lines = [f"Affected callers ({affected['status']}) across {len(affected['triggers'])} deleted or renamed files"]
    for trigger in affected["triggers"]:
        replacement = f" → {trigger['new_path']}" if trigger["new_path"] else " (deleted)"
        lines.append(f"  {trigger['path']}{replacement}")
    for site in affected["sites"]:
        detail = f" — {site['error']}" if site.get("error") else ""
        lines.append(f"  {site['path']}:{site['start_line']}-{site['end_line']} ({site['status']}){detail}")
    if affected["limits_reached"]:
        lines.append("Affected caller discovery incomplete — " + ", ".join(affected["limits_reached"]))
    return lines


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
    if comparison := result.get("comparison"):
        strategy = comparison["strategy"].replace("_", " ")
        reason = comparison["reason"].replace("_", " ")
        baseline = comparison.get("baseline_run_id")
        lines.append(f"Comparison: {strategy} ({reason}). Reused coverage: {len(coverage['reused'])} files."
                     + (f" Previous run: {baseline}." if baseline else ""))
    if not selected and not result.get("affected_callers"):
        lines.append("No eligible files in the selected changes.")
    lines.extend(_affected_caller_lines(result.get("affected_callers")))
    for item in result["warnings"]:
        lines.append(item["message"])
    for item in result["errors"]:
        lines.append(f"Incomplete: {item['path']} — {item['message']}")
    for error in result.get("stage_errors", []):
        lines.append(f"Incomplete {error['stage']}: {error['error']}")
    for finding in result["findings"]:
        lines.extend(["", f"[{finding['severity']}] {finding['path']}:{finding['start_line']}-{finding['end_line']}",
                      *terminal_block(finding["content"]), f"Trigger: {finding['trigger']}",
                      f"Consequence: {finding['consequence']}"])
        for evidence in finding["evidence"]:
            lines.append(f"  {evidence['path']}:{evidence['start_line']}-{evidence['end_line']}  {evidence['quote']}")
    if result.get("summary", {}).get("result"):
        lines.extend(["", *terminal_block(result["summary"]["result"]["narrative"])])
    if result.get("verification", {}).get("verifications"):
        for row in result["verification"]["verifications"]:
            lines.append(f"{row['finding_id']}: {row['verdict']}")
    if result.get("fix_prompts"):
        lines.extend(terminal_block(result["fix_prompts"]))
    graph = result.get("code_graph", {})
    if graph:
        lines.append(f"Code graph: {graph.get('status', 'unavailable')}" + (" — " + graph["reason"] if graph.get("reason") else ""))
        for omission in graph.get("discovery_omissions", []):
            count = len(omission["unit_keys"])
            reason = omission["reason"].replace("_", " ")
            lines.append(f"Graph context omitted from discovery for {omission['path']} "
                         f"({count} review {'unit' if count == 1 else 'units'}, {reason}).")
    return "\n".join(map(terminal_line, lines))



def render_preview(result):
    totals = result["totals"]
    lines = [f"Review scope preview ({result['mode']})  {result['merge_base'][:12]}..{result['head_sha'][:12]}",
             f"Selected: {totals['selected_files']} files. Excluded: {totals['excluded_files']}. "
             f"Units: {totals['units']}. Planned batches: {totals['planned_batches']}.",
             f"Assigned source: {totals['source_bytes']} bytes. Diff: {totals['diff_bytes']} bytes. "
             f"Assigned unit JSON: {totals['assigned_unit_json_bytes']} bytes."]
    for row in result["selected_files"]:
        sizes = f"{row['source_bytes']} source bytes, {row['diff_bytes']} diff bytes" if row["source_bytes"] is not None else "bytes unavailable"
        lines.append(f"Selected: {row['path']} ({len(row['unit_keys'])} units; {sizes})")
    for row in result["excluded_files"]:
        lines.append(f"Excluded: {row['path']} ({row['reason']})")
    for batch in result["batches"]:
        lines.append(f"Batch {batch['number']}: {len(batch['unit_keys'])} units; " + ", ".join(batch["paths"]))
    lines.extend(_affected_caller_lines(result.get("affected_callers")))
    if result["limit_reason"]:
        lines.append("Blocked: " + result["limit_reason"])
    for row in result["errors"]:
        lines.append(f"Incomplete: {row['path']}: {row['message']}")
    lines.extend(result["warnings"])
    lines.extend(result["assumptions"])
    return "\n".join(map(terminal_line, lines))


def run(args):
    outputs = destinations(args.output)
    overrides = limit_overrides(args)
    if args.preview and args.second_pass:
        raise ValueError("--preview shows primary scope only; omit --second-pass.")
    if args.second_pass:
        overrides["second_pass"] = True
    comparison = {key: value for key in settings.COMPARISON_FLAGS if (value := getattr(args, key)) is not None}
    if comparison and (args.preview or not (args.base or args.resume)):
        raise ValueError("Incremental and unchanged-review flags require --base and cannot be used with --preview.")
    overrides.update(comparison)
    settings.apply_overrides({}, overrides)
    graph_limits = {key: getattr(args, "graph_" + key) for key in GRAPH_LIMIT_FLAGS
                    if getattr(args, "graph_" + key, None) is not None}
    if graph_limits:
        overrides["code_graph_limits"] = graph_limits
    if args.resume and (args.base or args.head or args.staged or args.all or args.effort or args.graph
                        or args.concurrency is not None or overrides):
        raise ValueError("--resume uses the inputs and options of the saved run.")
    if graph_limits and not args.graph:
        raise ValueError("Graph limit overrides require --graph.")
    if args.head and not (args.base or args.all):
        raise ValueError("Use --head with --base for a comparison or with --all for a full-branch review.")
    mode = "all" if args.all else "staged" if args.staged else "commits" if args.base else "worktree"
    if args.preview:
        if args.resume or args.connection or args.graph or args.progress or args.output or args.second_pass or args.concurrency is not None:
            raise ValueError("--preview inspects local scope only; omit --resume, --connection, --graph, --progress, --output, --second-pass and --concurrency.")
        result = preview_local(args.repo, mode=mode, base=args.base, head=args.head or "HEAD",
                               effort=args.effort, overrides=overrides, prompt_token_ceiling=args.prompt_token_ceiling)
        print(json.dumps(result, indent=2) if args.format == "json" else render_preview(result))
        partial_callers = result.get("affected_callers", {}).get("status") == "partial"
        return 2 if result["limit_reason"] or result["errors"] or partial_callers else 0
    if args.prompt_token_ceiling is not None:
        raise ValueError("--prompt-token-ceiling is a preview sizing assumption and requires --preview.")
    name, profile = selected(args)

    def announce(count, model):
        print(terminal_line(f"Reviewing {count} file(s) with {name!r} / {model}…"), file=sys.stderr, flush=True)

    started = time.monotonic()
    try:
        with progress_display(args.progress) as progress:
            result = review_local(args.repo, mode=mode, base=args.base, head=args.head or "HEAD",
                                  connection=config.connection(name, profile), get_credentials=lambda: config.credentials(profile),
                                  on_plan=announce, profile=name, effort=args.effort, graph=args.graph, resume=args.resume,
                                  progress=progress, concurrency=args.concurrency, overrides=overrides)
        write_outputs(result, outputs)
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
    parser.add_argument("--second-pass", action="store_true", help="Opt into a bounded complementary discovery pass after primary analysis")
    parser.add_argument("--incremental", action=argparse.BooleanOptionalAction,
                        help="Review files changed since the previous complete branch review")
    parser.add_argument("--skip-unchanged", action=argparse.BooleanOptionalAction,
                        help="Skip inference when a compatible branch review already covered identical content")
    parser.add_argument("--preview", action="store_true", help="Show local files, exclusions, bytes, units and planned batches without a connection or saved run")
    parser.add_argument("--prompt-token-ceiling", type=int, help="Explicit preview-only sizing assumption (4096–2000000); never reads a saved connection")
    parser.add_argument("--graph", action="store_true", help="Include related source from the isolated local code graph parser")
    for field in GRAPH_LIMIT_FLAGS:
        parser.add_argument("--graph-" + field.replace("_", "-"), type=int,
                            help=f"Override graph {field} for this run (requires --graph; positive integer)")
    execution_options(parser)
    options(parser)
    output_options(parser)
    parser.set_defaults(run=run)
