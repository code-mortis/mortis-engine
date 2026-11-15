"""Command-line entry point for Mortis Engine."""

import argparse
import sys

from keyring.errors import KeyringError
from pydantic import ValidationError

from mortis_engine import __version__
from mortis_engine.llm_http import InferenceError

from . import analysis, config, connect, findings, followups, reports, review


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mortis", description="Review local Git changes with your AI connection.")
    parser.add_argument("--version", action="version", version=f"mortis {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    connect.add_parser(commands)
    review.add_parser(commands)
    config.add_parser(commands)
    analysis.add_parser(commands)
    findings.add_parser(commands)
    followups.add_parser(commands)
    reports.add_parser(commands)
    args = parser.parse_args(argv)
    try:
        return args.run(args) or 0
    except ValidationError as exc:
        errors = [".".join(map(str, error["loc"])) + ": " + error["msg"] for error in exc.errors(include_input=False)]
        print("Invalid configuration. " + "; ".join(errors), file=sys.stderr)
    except KeyringError:
        print("The OS credential vault could not be accessed. Use environment variable references or unlock the vault.", file=sys.stderr)
    except (ValueError, OSError, InferenceError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("Command cancelled.", file=sys.stderr)
        return 130
    return 1
