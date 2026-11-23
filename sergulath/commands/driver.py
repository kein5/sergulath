"""sergulath driver: SERGULATH's virtual controller driver for SteamVR.

  driver build [--toolchain NAME]   Build the driver DLL with cargo and stage the folder SteamVR
                                    loads (build/driver/sergulath). Needs a Rust toolchain.
  driver register [--restart]       Add the staged driver to SteamVR's driver list (vrpathreg).
                                    SteamVR loads it at its next start; --restart quits SteamVR
                                    through its status window and starts it again through Steam.
  driver unregister [--restart]     Remove it again, and with --restart start SteamVR without it.
  driver status                     Whether it is registered, loaded, and what its controllers do.
  driver run [--restart] -- CMD ... Register, run CMD, and unregister afterwards, also when CMD
                                    fails or is interrupted (exit code: CMD's).

The driver adds its two controllers (left and right hand) only when asked
(`sergulath controller connect`), so a driver left registered adds nothing. It is meant for a
check, not for an ordinary VR session: unregister it when done.
"""

from __future__ import annotations

import subprocess
import sys

from .. import output
from ..core import driver

NAME = "driver"
HELP = "build, register, remove and check SERGULATH's virtual controller driver"


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    build = sub.add_parser("build", help="build the DLL and stage the driver folder")
    build.add_argument("--cargo", default="cargo", help="cargo to run (default: cargo on PATH)")
    build.add_argument("--toolchain", help="rustup toolchain, such as 1.100.0-x86_64-pc-windows-gnu")
    output.add_format(build)
    for name, text in (("register", "add the driver to SteamVR"), ("unregister", "remove the driver from SteamVR")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--restart", action="store_true", help="restart SteamVR so the change takes effect now")
        output.add_format(p)
    status = sub.add_parser("status", help="registered, loaded, and the controllers' state")
    output.add_format(status)
    run = sub.add_parser("run", help="register, run a command, and always unregister")
    run.add_argument("--restart", action="store_true", help="restart SteamVR before and after")
    run.add_argument("command", nargs="+", help="the command to run, after --")


def _status_record() -> dict:
    record = {"folder": str(driver.STAGE), "staged": (driver.STAGE / "bin" / "win64" / driver.DLL_NAME).is_file(),
              "registered": driver.is_registered(), "loaded": False}
    try:
        record["controllers"] = driver.state()
        record["loaded"] = True
    except (driver.DriverNotLoaded, driver.DriverError):
        pass
    return record


def _status_text(record: dict) -> str:
    text = (f"driver {'staged' if record['staged'] else 'not built'}, "
            f"{'registered' if record['registered'] else 'not registered'}, "
            f"{'loaded' if record['loaded'] else 'not loaded'} ({record['folder']})")
    for hand in ("left", "right"):
        c = record.get("controllers", {}).get(hand)
        if c:
            pressed = [name for name, down in c["buttons"].items() if down]
            text += (f"\n  {hand}: {'connected' if c['connected'] else 'not connected'}, device {c['object_id']}, "
                     f"at {c['position']}, pressed {pressed or 'none'}")
    return text


def run(args) -> int:
    if args.action == "build":
        folder = driver.build(args.cargo, args.toolchain)
        output.emit({"folder": str(folder)}, f"staged {folder}", args.format)
        return output.OK
    if args.action == "status":
        record = _status_record()
        output.emit(record, _status_text(record), args.format)
        return output.OK if record["registered"] else output.NOT_MET
    if args.action == "register":
        driver.register()
        if args.restart:
            output.note("restarting SteamVR to load the driver")
            driver.restart_steamvr()
        record = _status_record()
        output.emit(record, _status_text(record), args.format)
        return output.OK
    if args.action == "unregister":
        driver.unregister()
        if args.restart:
            output.note("restarting SteamVR without the driver")
            driver.restart_steamvr()
        record = _status_record()
        output.emit(record, _status_text(record), args.format)
        return output.OK
    # run
    command = args.command
    driver.register()
    try:
        if args.restart:
            output.note("restarting SteamVR to load the driver")
            driver.restart_steamvr()
        if command and command[0] == "sergulath":
            command = [sys.executable, "-m", "sergulath", *command[1:]]
        return subprocess.run(command).returncode
    finally:
        try:
            driver.unregister()
            if args.restart:
                output.note("restarting SteamVR without the driver")
                driver.restart_steamvr()
        except Exception as e:  # noqa: BLE001 - say so loudly: the driver must not stay registered
            output.note(f"could not remove the driver ({e}); run `sergulath driver unregister --restart`")
