"""Install the bundled portable skill without profiles or network access."""

import os
import shutil
import tempfile
from importlib.resources import as_file, files
from pathlib import Path

SKILL_NAME = "mortis-engine-use"
MANAGED = b"mortis-engine-use\n"


def run(args):
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", ""))
    opencode = (config if config.is_absolute() else home / ".config") / "opencode"
    canonical = home / ".agents/skills" / SKILL_NAME
    roots = [home / name for name in (".claude", ".cursor", ".codex") if (home / name).is_dir()]
    if opencode.is_dir() or (home / ".opencode").is_dir():
        roots.append(opencode)
    destinations = {canonical.parent.resolve() / SKILL_NAME: canonical}
    for root in roots:
        destination = root / "skills" / SKILL_NAME
        destinations.setdefault(destination.parent.resolve() / SKILL_NAME, destination)

    for destination in destinations.values():
        check_destination(destination, canonical, args.force)

    source = files("mortis_engine").joinpath("_skill")
    if not source.is_dir():
        source = Path(__file__).resolve().parents[3] / ".agents/skills" / SKILL_NAME
    with as_file(source) as source:
        for destination in destinations.values():
            install(source, destination, canonical, args.force)
            print(f"Installed {SKILL_NAME} at {destination}")
    return 0


def check_destination(destination, canonical, force):
    if not os.path.lexists(destination):
        return
    if destination.is_symlink():
        if force or (destination != canonical and destination.resolve() == canonical.resolve()):
            return
    elif destination.is_dir():
        if force:
            return
        marker = destination / ".mortis-managed"
        if not marker.is_symlink() and marker.is_file():
            with marker.open("rb") as file:
                if file.read(len(MANAGED) + 1) == MANAGED:
                    return
    else:
        raise ValueError(f"Skill destination is not a directory: {destination}")
    raise ValueError(f"Skill already exists at {destination}. Use --force to replace it.")


def install(source, destination, canonical, force):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".mortis-skill-", dir=destination.parent))
    prepared, previous = temporary / "new", temporary / "previous"
    installed = False
    copy = destination == canonical or os.name == "nt"
    try:
        if copy:
            shutil.copytree(source, prepared)
            (prepared / ".mortis-managed").write_bytes(MANAGED)
        check_destination(destination, canonical, force)
        if os.path.lexists(destination):
            destination.rename(previous)
        try:
            if copy:
                prepared.rename(destination)
            else:
                destination.symlink_to(canonical, target_is_directory=True)
        except OSError:
            if os.path.lexists(previous):
                try:
                    if os.path.lexists(destination):
                        raise FileExistsError(destination)
                    previous.rename(destination)
                except OSError as exc:
                    raise OSError(f"Could not restore the previous skill; it is preserved at {previous}") from exc
            raise
        installed = True
    finally:
        # Keep the recovery copy if restoring the previous installation failed.
        if installed or not os.path.lexists(previous):
            shutil.rmtree(temporary)


def add_parser(commands):
    parser = commands.add_parser("agent", help="Manage the bundled coding-agent skill")
    install = parser.add_subparsers(dest="action", required=True).add_parser(
        "install", help="Install or update the bundled skill for detected coding agents")
    install.add_argument("--force", action="store_true", help="Replace an existing skill installation")
    install.set_defaults(run=run)
