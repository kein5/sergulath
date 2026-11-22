"""SERGULATH's command line, its primary interface.

Every capability is a subcommand that can be scripted: data on stdout (text or JSON Lines),
messages on stderr, no prompts, and documented exit codes (`sergulath.output`).
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, output
from .commands import COMMANDS
from .core.steamvr import SteamVRNotRunning

EPILOG = """exit codes: 0 done, 1 ran but found nothing, 2 usage error,
3 SteamVR is not running (the tools never start it), 4 failed"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sergulath",
        description="Operate and check SteamVR overlay apps on a real headset.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"sergulath {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for command in COMMANDS:
        p = sub.add_parser(command.NAME, help=command.HELP, description=command.__doc__)
        command.add_arguments(p)
        p.set_defaults(run=command.run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except SteamVRNotRunning as e:
        output.note(f"{e}. Start SteamVR first; the tools do not start it.")
        return output.STEAMVR_NOT_RUNNING
    except KeyboardInterrupt:
        output.note("interrupted")
        return output.FAILED
    except Exception as e:  # noqa: BLE001 - one line on stderr and a stable exit code
        output.note(f"{type(e).__name__}: {e}")
        return output.FAILED


if __name__ == "__main__":
    sys.exit(main())
