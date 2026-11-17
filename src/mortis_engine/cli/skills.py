"""Install the bundled portable skill without profiles or network access."""

import os
import shutil
import tempfile
from importlib.resources import as_file, files
from pathlib import Path

SKILL_NAME = "mortis-engine-use"
AGENTS = {"codex": ".agents", "claude-code": ".claude"}


def run(args):
    destination = Path.home() / AGENTS[args.agent] / "skills" / SKILL_NAME

    def check_destination():
        if os.path.lexists(destination):
            if not args.force:
                raise ValueError(f"Skill already exists at {destination}. Use --force to replace it.")
            if not destination.is_symlink() and not destination.is_dir():
                raise ValueError(f"Skill destination is not a directory: {destination}")

    check_destination()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".mortis-skill-", dir=destination.parent))
    prepared, previous = temporary / "new", temporary / "previous"
    installed = False
    try:
        with as_file(files("mortis_engine").joinpath("skills", SKILL_NAME)) as source:
            shutil.copytree(source, prepared)
        check_destination()
        if os.path.lexists(destination):
            destination.rename(previous)
        try:
            prepared.rename(destination)
        except OSError:
            if os.path.lexists(previous):
                try:
                    previous.rename(destination)
                except OSError as exc:
                    raise OSError(f"Could not restore the previous skill; it is preserved at {previous}") from exc
            raise
        installed = True
    finally:
        # Keep the recovery copy if restoring the previous installation failed.
        if installed or not os.path.lexists(previous):
            shutil.rmtree(temporary)
    print(f"Installed {SKILL_NAME} for {args.agent} at {destination}")
    return 0


def add_parser(commands):
    parser = commands.add_parser("skill", help="Manage the bundled coding-agent skill")
    install = parser.add_subparsers(dest="action", required=True).add_parser(
        "install", help="Copy the bundled skill into an agent's personal skills directory")
    install.add_argument("agent", choices=AGENTS, help="Agent receiving the skill")
    install.add_argument("--force", action="store_true", help="Replace an existing skill installation")
    install.set_defaults(run=run)
