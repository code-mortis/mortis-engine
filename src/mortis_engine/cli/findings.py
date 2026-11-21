"""Credential-free local finding views and explicit decisions."""

import json

from mortis_engine import findings

from .review import terminal_block, terminal_line


def run(args):
    if args.command == "triage":
        result = findings.triage(args.repo, run_id=args.run_id, finding_id=args.finding,
                                 state=args.state, version=args.version, reason=args.reason)
        lines = [f"{result['finding']}: {result['state']} (version {result['version']})",
                 *terminal_block(result["reason"])]
    else:
        result = findings.list_findings(args.repo, run_id=args.run_id, include_history=args.history)
        lines = [f"Current findings: {len(result['findings'])}  Scope: {result['scope']}"]
        for item in result["findings"]:
            title = item.get("title") or item.get("content", "")
            lines.append(f"{item['id']}  {item.get('path', '')}  {item['decision']} (version {item['version']})  {title}")
            if item.get("outcome"):
                lines.append(f"  Evidence: {item['outcome']}")
            elif item.get("evidence_status"):
                lines.append(f"  Recorded status: {item['evidence_status']}")
        for event in result.get("history", []):
            lines.append(f"{event['finding']} v{event['version']}: {event['previous']} -> {event['state']}: {event['reason']}")
    print(json.dumps(result, indent=2) if args.format == "json" else "\n".join(map(terminal_line, lines)))
    return 0


def add_parser(commands):
    for command, help_text in (("findings", "Show current local findings and decisions"),
                               ("triage", "Record a local finding decision with an audit trail")):
        parser = commands.add_parser(command, help=help_text)
        parser.add_argument("--repo", default=".")
        parser.add_argument("--run", dest="run_id", required=True, help="Saved review or scan ID")
        parser.add_argument("--format", choices=("text", "json"), default="text")
        if command == "findings":
            parser.add_argument("--history", action="store_true", help="Include the decision history")
        else:
            parser.add_argument("--finding", required=True, help="Finding ID belonging to the saved run")
            parser.add_argument("--state", required=True, choices=("open", "dismissed", "false_positive", "accepted_risk"))
            parser.add_argument("--version", required=True, type=int, help="Current version from mortis findings")
            parser.add_argument("--reason", required=True, help="Reason for this decision")
        parser.set_defaults(run=run)
