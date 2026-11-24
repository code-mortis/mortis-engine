"""Terminal commands for grounded PR questions and descriptions."""

import json
import re
from pathlib import Path

from mortis_engine import config
from mortis_engine.review import pr_commands

from .review import select_profile, terminal_line


def run(args):
    question, location = None, None
    if args.command == "ask":
        with args.question_file.open(encoding="utf-8") as stream:
            question = stream.read(8001)
        if args.path or args.line or args.side:
            match = re.fullmatch(r"([1-9]\d*)(?::([1-9]\d*))?", args.line or "")
            if not (args.path and match and args.side):
                raise ValueError("Use --path, --line START[:END] and --side together.")
            location = (args.path, int(match[1]), int(match[2] or match[1]), args.side)
    name, profile = select_profile(args.connection, config.load()["connections"])
    result = pr_commands.run(args.repo, command=args.command, base=args.base, head=args.head,
                             question=question, location=location, connection=config.connection(name, profile),
                             get_credentials=lambda: config.credentials(profile))
    if args.format == "json":
        print(json.dumps(result))
    elif result["status"] == "insufficient_context":
        print(terminal_line(result["message"]))
    else:
        data = result["result"]
        if args.command == "ask":
            print(terminal_line(data["answer"]))
            for citation in data["citations"]:
                print(terminal_line(f"{citation['path']}:{citation['start_line']}-{citation['end_line']}"))
        else:
            print(terminal_line(data["title"]))
            print(terminal_line(data["narrative"]))
            for group in data["groups"]:
                print(terminal_line(f"- {group['title']}: {group['description']}"))
    return 0


def add_parser(commands):
    for command, help_text in (("ask", "Answer a question about a pinned Git comparison"),
                               ("describe", "Draft a title and description for a pinned Git comparison")):
        parser = commands.add_parser(command, help=help_text)
        parser.add_argument("--repo", default=".")
        parser.add_argument("--base", required=True)
        parser.add_argument("--head", default="HEAD")
        parser.add_argument("--connection")
        parser.add_argument("--format", choices=("text", "json"), default="text")
        if command == "ask":
            parser.add_argument("--question-file", type=Path, required=True)
            parser.add_argument("--path")
            parser.add_argument("--line", metavar="START[:END]")
            parser.add_argument("--side", choices=("left", "right"))
        parser.set_defaults(run=run)
