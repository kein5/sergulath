"""SERGULATH's command line, its primary interface.

Every capability is a subcommand that can be scripted: data on stdout (text or JSON Lines),
messages on stderr, no prompts, and documented exit codes (`sergulath.output`).
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, output
from .commands import COMMANDS
from .core import host, lock
from .core.steamvr import SteamVRNotRunning

EPILOG = """exit codes: 0 done, 1 ran but found nothing (or not available on this OS),
2 usage error, 3 SteamVR is not running (the tools never start it), 4 failed"""


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
        p = sub.add_parser(command.NAME, help=command.HELP, description=command.__doc__,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
        command.add_arguments(p)
        p.set_defaults(run=command.run, tool=command.NAME)  # `command` is taken: hmd/driver run use it for CMD
    return parser


# Commands that start, quit or restart SteamVR, or change its headset, drivers or settings: they
# take the SteamVR lease (`core.lock`), so two sessions on one PC cannot run them over each other.
LOCKED = {
    "steamvr": {"start", "stop"},
    "settings": {"set", "restore"},
    "hmd": {"on", "off", "run"},
    "driver": {"register", "unregister", "run"},
    "view": {"open", "close"},
    "chaperone": {"apply", "restore"},
    "suite": {"run"},
}


def _run(args) -> int:
    if getattr(args, "action", None) in LOCKED.get(args.tool, ()):
        with lock.steamvr(f"{args.tool} {args.action}"):
            return args.run(args)
    return args.run(args)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except lock.Busy as e:
        output.note(str(e))
        if getattr(args, "format", "text") == "json":
            output.emit({"source": args.tool, "status": "busy", "lease": e.lease, "reason": str(e)}, "", "json")
        return output.FAILED
    except SteamVRNotRunning as e:
        output.note(f"{e}. Start SteamVR first; the tools do not start it.")
        return output.STEAMVR_NOT_RUNNING
    except host.NotSupportedHere as e:
        # Reported like other unsupported checks (`upgrade`): exit 1 and, in JSON, status "unsupported",
        # so a suite step with `unsupported_when` counts it apart from failures.
        output.note(str(e))
        if getattr(args, "format", "text") == "json":
            output.emit({"source": args.tool, "status": "unsupported", "unsupported_on": host.system_name(),
                         "feature": e.feature, "reason": str(e)}, "", "json")
        return output.NOT_MET
    except KeyboardInterrupt:
        output.note("interrupted")
        return output.FAILED
    except Exception as e:  # noqa: BLE001 - one line on stderr and a stable exit code
        output.note(f"{type(e).__name__}: {e}")
        return output.FAILED


if __name__ == "__main__":
    sys.exit(main())
