"""Command-line adapter and terminal output for local reviews."""

import json
import re
import sys

from mortis_engine import config
from mortis_engine.review import review_commits


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


def render(result):
    coverage = result["coverage"]
    selected = len(coverage["selected"])
    completed = len(coverage["completed"])
    tokens = result["usage"]["total_tokens"]
    lines = [
        f"Review {result['status']}  {result['merge_base'][:12]}..{result['head_sha'][:12]}",
        f"Coverage: {completed}/{selected} files fully reviewed. Excluded: {len(result['excluded_files'])}. "
        f"Findings: {len(result['findings'])}. "
        f"Tokens: {tokens if tokens is not None else 'unknown'}.",
    ]
    if not selected:
        lines.append("No eligible changed files between these commits.")
    for item in result["warnings"]:
        lines.append(item["message"])
    for item in result["errors"]:
        lines.append(f"Incomplete: {item['path']} — {item['message']}")
    for finding in result["findings"]:
        lines.extend(["", f"[{finding['severity']}] {finding['path']}:{finding['start_line']}-{finding['end_line']}",
                      finding["content"], f"Trigger: {finding['trigger']}", f"Consequence: {finding['consequence']}"])
        for evidence in finding["evidence"]:
            lines.append(f"  {evidence['path']}:{evidence['start_line']}-{evidence['end_line']}  {evidence['quote']}")
    # Terminal control characters can come from repository text or model output.
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", "", "\n".join(lines))


def run(args):
    name, profile = select_profile(args.connection, config.load()["connections"])

    def announce(count, model):
        print(f"Reviewing {count} file(s) with {name!r} / {model}…", file=sys.stderr, flush=True)

    result = review_commits(args.repo, base=args.base, head=args.head, connection=config.connection(name, profile),
                            get_credentials=lambda: config.credentials(profile), on_plan=announce)
    print(json.dumps(result, indent=2) if args.format == "json" else render(result))
    if result["status"] == "failed":
        return 1
    if result["status"] == "partial" or result["coverage"]["waived"]:
        return 2
    return 0


def add_parser(commands):
    parser = commands.add_parser("review", help="Review changes between two local Git commits")
    parser.add_argument("--base", required=True, help="Base branch, tag or commit")
    parser.add_argument("--head", default="HEAD", help="Head branch, tag or commit (default: HEAD)")
    parser.add_argument("--repo", default=".", help="Local repository directory (default: current directory)")
    parser.add_argument("--connection", help="Saved connection name (optional with one profile)")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Report format (default: text)")
    parser.set_defaults(run=run)
