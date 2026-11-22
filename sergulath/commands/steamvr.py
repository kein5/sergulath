"""sergulath steamvr: SteamVR on this PC.

  steamvr status   Whether SteamVR runs (vrserver and vrcompositor), whether its status window
                   (vrmonitor) runs, its version from its own log, and, while it runs, the
                   headset's and controllers' state. Read-only; it never starts SteamVR.
"""

from __future__ import annotations

from dataclasses import asdict

from .. import output
from ..core import steamvr as steamvr_core
from ..core import steamvr_status, watch

NAME = "steamvr"
HELP = "SteamVR on this PC: running or not, status window, version, headset state"


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    status = sub.add_parser("status", help="report SteamVR's state (read-only)")
    status.add_argument("--no-devices", action="store_true", help="do not connect to read the headset and controllers")
    output.add_format(status)


def run(args) -> int:
    status = steamvr_status.read_status()
    record = status.to_dict()
    text = ("SteamVR " + (status.version or "(version unknown)") + ": " +
            ("running" if status.running else "not running") +
            (", status window open" if status.vrmonitor else ", no status window"))
    if status.running and not args.no_devices:
        with steamvr_core.background_client() as openvr:
            devices = watch.sample_devices(openvr, 0)
        record["devices"] = [asdict(d) for d in devices]
        text += "\n  " + watch.summary(devices)
    output.emit(record, text, args.format)
    return output.OK if status.running else output.NOT_MET
