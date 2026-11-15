"""Export immutable review and security reports without another analysis."""

import json
import os
import sys
import tempfile
from pathlib import Path

from mortis_engine.history import History
from mortis_engine.reporting import markdown


def output_options(parser, *, required=False):
    parser.add_argument("--output", action="append", default=[], required=required, metavar="FILE",
                        help="Save a report as .md, .markdown or .json (repeat for multiple files; new files only)")


def destinations(values):
    paths = []
    for value in values:
        path = Path(value).expanduser()
        if path.suffix.lower() not in {".md", ".markdown", ".json"}:
            raise ValueError(f"Unsupported report extension for {str(path)!r}. Use .md, .markdown or .json.")
        path = path.parent.resolve() / path.name
        if path in paths:
            raise ValueError(f"Duplicate report destination: {str(path)!r}.")
        if any(path.is_relative_to(other) or other.is_relative_to(path) for other in paths):
            raise ValueError(f"Report destinations cannot also be parent directories: {str(path)!r}.")
        if os.path.lexists(path):
            raise ValueError(f"Report destination already exists: {str(path)!r}. Choose a new filename.")
        parent = path.parent
        while not os.path.lexists(parent):
            parent = parent.parent
        if not parent.is_dir():
            raise ValueError(f"Report parent is not a directory: {str(parent)!r}.")
        if not os.access(parent, os.W_OK | os.X_OK):
            raise ValueError(f"Report parent is not writable: {str(parent)!r}.")
        paths.append(path)
    return paths


def write_outputs(result, paths):
    if not paths:
        return
    written = []
    try:
        if result.get("schema_version") not in {"mortis.review/v1", "mortis.security/v1"}:
            raise ValueError("Only saved review and security reports can be exported.")
        contents = [(path, json.dumps(result, indent=2) + "\n" if path.suffix.lower() == ".json"
                     else markdown(result)) for path in paths]
        for path, content in contents:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".mortis-report-", dir=path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Publishing a complete inode with link refuses even a concurrently created target.
                os.link(temporary, path)
                written.append(path)
            finally:
                os.unlink(temporary)
    except (OSError, ValueError) as exc:
        saved = ", ".join(repr(str(path)) for path in written) or "none"
        raise ValueError(f"Report export failed: {exc}. Files written: {saved}. "
                         f"Saved run: {result['run_id']}. Export it with mortis report --run {result['run_id']} "
                         "--repo REPOSITORY --output NEW_FILE; analysis does not need to be repeated.") from exc
    for path in written:
        print(f"Saved report: {str(path)!r}", file=sys.stderr, flush=True)


def run(args):
    paths = destinations(args.output)
    with History(args.repo) as history:
        row = history.row(args.run_id)
        if row["kind"] not in {"review", "scan"}:
            raise ValueError("Only saved review and security reports can be exported.")
        result = history.report(args.run_id)
    write_outputs(result, paths)
    return 0


def add_parser(commands):
    parser = commands.add_parser("report", help="Export an original saved review or security report without AI")
    parser.add_argument("--run", dest="run_id", required=True, metavar="RUN_ID", help="Saved run ID from mortis runs")
    parser.add_argument("--repo", default=".", help="Local repository (default: current directory)")
    output_options(parser, required=True)
    parser.set_defaults(run=run)
