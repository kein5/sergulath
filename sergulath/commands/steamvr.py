"""sergulath steamvr: SteamVR on this PC.

  steamvr status   Whether SteamVR runs (vrserver and vrcompositor), whether its status window
                   (vrmonitor) runs, its version from its own log, and, while it runs, the
                   headset's and controllers' state. Read-only; it never starts SteamVR.
  steamvr start    Start SteamVR through Steam (as Steam's child, the way it normally runs) and
                   wait until it takes clients. Does nothing when it already runs.
  steamvr wait     Wait until the headset is connected (--until connected, the default) or has a
                   pose (--until tracking; a headset on a desk never gets one). Exit 1 on timeout.
  steamvr stop     Quit SteamVR the way closing its status window does.

start and stop change what runs on the PC; status and wait only read. start warns when a
settings snapshot was never restored (`sergulath settings pending`).
"""

from __future__ import annotations

import time
from dataclasses import asdict

from .. import output
from ..core import driver, settings
from ..core import steamvr as steamvr_core
from ..core import steamvr_status, watch

NAME = "steamvr"
HELP = "SteamVR on this PC: status, start, wait for the headset, stop"


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    status = sub.add_parser("status", help="report SteamVR's state (read-only)")
    status.add_argument("--no-devices", action="store_true", help="do not connect to read the headset and controllers")
    output.add_format(status)
    start = sub.add_parser("start", help="start SteamVR through Steam")
    start.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for it (default 90)")
    output.add_format(start)
    wait = sub.add_parser("wait", help="wait for the headset")
    wait.add_argument("--until", choices=("connected", "tracking"), default="connected")
    wait.add_argument("--timeout", type=float, default=60.0, help="seconds (default 60)")
    output.add_format(wait)
    stop = sub.add_parser("stop", help="quit SteamVR")
    output.add_format(stop)


def _status(args) -> int:
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


def _headset(openvr) -> watch.DeviceSample | None:
    return next((d for d in watch.sample_devices(openvr, 0) if d.device_class == "hmd"), None)


def _wait(args) -> int:
    deadline = time.monotonic() + args.timeout
    last = None
    while time.monotonic() < deadline:
        if not steamvr_core.missing_processes():
            with steamvr_core.background_client() as openvr:
                last = _headset(openvr)
            if last and last.connected and (args.until == "connected" or last.pose_valid):
                output.emit({"ready": True, "headset": asdict(last)}, f"headset {args.until}: {watch.summary([last])}", args.format)
                return output.OK
        time.sleep(2)
    output.emit({"ready": False, "headset": asdict(last) if last else None},
                f"not {args.until} after {args.timeout:.0f} s" + (f": {watch.summary([last])}" if last else ""), args.format)
    return output.NOT_MET


def run(args) -> int:
    if args.action == "status":
        return _status(args)
    if args.action == "wait":
        return _wait(args)
    if args.action == "stop":
        driver.quit_steamvr()
        output.emit({"running": False}, "SteamVR stopped", args.format)
        return output.OK
    # start
    for snapshot in settings.pending():
        output.note(f"settings snapshot not restored: {snapshot} (`sergulath settings restore`)")
    if not steamvr_core.missing_processes():
        output.emit({"running": True, "started": False}, "SteamVR already runs", args.format)
        return output.OK
    driver.start_steamvr(args.timeout)
    output.emit({"running": True, "started": True}, "SteamVR started", args.format)
    return output.OK
