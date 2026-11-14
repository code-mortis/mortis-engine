"""Terminal commands for saved runs and local repository workflows."""

import json

from mortis_engine import config, operations
from mortis_engine.history import History

from .review import options, selected, terminal


def run(args):
    if args.resume and (getattr(args, "ref", None) or args.effort or getattr(args, "run_id", None) or getattr(args, "rotations", None)):
        raise ValueError("--resume uses the inputs and options of the saved run.")
    if args.command in {"verify", "summary"} and not (args.run_id or args.resume):
        raise ValueError(f"mortis {args.command} requires --run RUN_ID. Use mortis runs to see the available IDs.")
    name, profile = selected(args)
    result = operations.run(args.repo, kind=args.command, connection=config.connection(name, profile),
        get_credentials=lambda: config.credentials(profile), profile=name, ref=getattr(args, "ref", None) or "HEAD",
        finding_run=getattr(args, "run_id", None), resume=args.resume, effort=args.effort, rotations=getattr(args, "rotations", ()))
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        lines = [f"{args.command.capitalize()} {result['status']}  {result['head_sha'][:12]}", f"Run: {result['run_id']}"]
        if args.command == "scan":
            coverage = result["coverage"]
            lines.append(f"Coverage: {coverage['completed_files']}/{coverage['selected_files']} files. Grade: {result['grade'] or 'unavailable'}")
            lines.extend(f"[{r['severity']}] {r['path']} — {r['title']} ({r['outcome']}, {r['id']})" for r in result["findings"])
            lines.extend(result["problems"])
            if error := result["stages"]["trivy"].get("error"):
                lines.append(error)
        elif args.command == "verify":
            lines.extend(f"{r['finding_id']} — {r['verdict']}: " + ((r.get("result") or {}).get("explanation") or r.get("error", ""))
                         for r in result["verifications"])
        elif args.command == "summary":
            lines.append(result["result"]["narrative"])
            lines.extend(f"{g['title']} — {g['description']} ({', '.join(g['paths'])})" for g in result["result"]["groups"])
            if result["result"].get("diagram"):
                from mortis_engine.review.summary import diagram_section
                lines.append(diagram_section(result["result"]["diagram"]))
        else:
            lines.append(result["background"])
        print(terminal("\n".join(lines)))
    return 0 if result["status"] == "completed" else 2 if result["status"] == "partial" else 1


def runs(args):
    with History(args.repo) as history:
        if args.id:
            row = history.row(args.id)
            if row["report"] is None:
                records = [record for _, record, _ in history.attempts(args.id).values()]
                value = {"run_id": args.id, "status": "unfinished", "attempts": records}
            else:
                value = history.report(args.id)
            print(json.dumps(value, indent=2))
        else:
            rows = history.list()
            print(terminal("\n".join(f"{r['id']}  {r['kind']}  {r['scope']}  " +
                ("finished" if r["finished"] else "unfinished") for r in rows)) or "No saved runs.")


def build_graph(args):
    from mortis_engine.code_graph.build import main

    return main([])


def add_parser(commands):
    graph = commands.add_parser("graph", help="Manage the isolated local source parser")
    build = graph.add_subparsers(dest="action", required=True).add_parser("build", help="Build the local Docker parser image")
    build.set_defaults(run=build_graph)
    for command, description in (("scan", "Scan a local revision with Trivy and your AI connection"),
                                 ("verify", "Check saved findings against a local revision"),
                                 ("summary", "Generate a summary for a saved review"),
                                 ("background", "Generate repository background from a pinned revision")):
        parser = commands.add_parser(command, help=description)
        options(parser)
        if command == "scan":
            parser.add_argument("--acknowledge-rotation", metavar="FINDING_ID", dest="rotations", action="append",
                                default=[], help="Record that this retained secret has been rotated or revoked")
        if command != "summary":
            parser.add_argument("--ref", help="Local branch, tag or commit (default: HEAD)")
        if command in {"summary", "verify"}:
            parser.add_argument("--run", dest="run_id", help="Saved review ID from mortis runs")
        parser.set_defaults(run=run)
    parser = commands.add_parser("runs", help="List saved local runs or print a saved report")
    parser.add_argument("id", nargs="?", help="Print the report or progress for this run")
    parser.add_argument("--repo", default=".")
    parser.set_defaults(run=runs)
