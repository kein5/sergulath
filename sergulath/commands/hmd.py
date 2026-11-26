"""sergulath hmd: a virtual headset (SteamVR's bundled null driver) for checks nobody wears.

  hmd on [--with-driver] [--window X Y W H]
      Snapshot, then set steamvr.forcedDriver="null" and driver_null.enable=true, and restart
      SteamVR through Steam. The null headset reports a fixed, valid head pose at the standing
      origin, looking along -z. With --with-driver the virtual controllers are registered in the
      same restart. The null driver shows what the headset sees in a small desktop window
      ("Headset Window", 480x270 at the top left by default). SteamVR keeps a play area per
      tracking universe and has none for the null headset's, so it would open Room Setup at
      every start: `hmd on` also adds a 2 x 2 m standing play area for that universe to
      chaperone_info.vrchap (after a snapshot), and `hmd off` takes it out again.
  hmd off       Restore the snapshots, remove the virtual controllers, restart SteamVR.
  hmd status    Which headset SteamVR uses, and whether a virtual-headset snapshot is pending.
  hmd run [--with-driver] -- CMD ...
      hmd on, run CMD, hmd off, also when CMD fails or is interrupted (exit code: CMD's).

While the virtual headset is on, the real headset is not SteamVR's headset: a Steam Frame shows
nothing from the PC. `hmd off` (or `settings restore --restart`) brings it back.
"""

from __future__ import annotations

import subprocess
import sys

from .. import output
from ..core import chaperone, driver, host, settings, steamvr

NAME = "hmd"
HELP = "a virtual headset (the null driver) for unattended checks, with a snapshot and restore"
LABEL = "hmd"


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    for name, text in (("on", "switch to the virtual headset"), ("run", "on, run a command, off")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--with-driver", action="store_true", help="also register the virtual controllers")
        p.add_argument("--window", nargs=4, type=int, default=[0, 0, 480, 270], metavar=("X", "Y", "W", "H"),
                       help="the null driver's desktop window (default 0 0 480 270)")
        if name == "run":
            p.add_argument("command", nargs="+", help="the command to run, after --")
        else:
            output.add_format(p)
    off = sub.add_parser("off", help="restore the real headset")
    output.add_format(off)
    status = sub.add_parser("status", help="which headset SteamVR uses")
    output.add_format(status)


def changes(window: list[int]) -> dict:
    x, y, w, h = window
    return {"steamvr.forcedDriver": "null", "driver_null.enable": True,
            "driver_null.windowX": x, "driver_null.windowY": y,
            "driver_null.windowWidth": w, "driver_null.windowHeight": h}


def switch_on(window: list[int], with_driver: bool) -> None:
    if with_driver:  # before anything changes
        host.require_windows("--with-driver", driver.WINDOWS_ONLY)
    if settings.latest_snapshot(label=LABEL):
        raise RuntimeError("a virtual-headset snapshot is still pending; run `sergulath hmd off` first")
    driver.quit_steamvr()
    settings.apply(changes(window), settings.settings_file(), label=LABEL)
    # A play area for the null headset's universe, so SteamVR does not open Room Setup.
    chaperone.ensure_universe(chaperone.NULL_UNIVERSE, label=LABEL)
    if with_driver:
        driver.register()
    driver.start_steamvr()
    if with_driver:
        driver.wait_loaded()


def switch_off() -> tuple[list, list[dict]]:
    """Restore the real headset; return the settings and the chaperone universes put back."""
    driver.quit_steamvr()
    undone = []
    snapshot = settings.latest_snapshot(label=LABEL)
    if snapshot:
        undone = settings.restore(snapshot)
    universes = [chaperone.restore_universe(p) for p in reversed(chaperone.universe_pending(label=LABEL))]
    if driver.is_registered():
        driver.unregister()
    driver.start_steamvr()
    return undone, universes


def _headset() -> dict:
    universe_snapshots = chaperone.universe_pending(label=LABEL)
    record = {"pending_snapshot": str(settings.latest_snapshot(label=LABEL) or "") or None,
              "pending_chaperone_snapshot": str(universe_snapshots[-1]) if universe_snapshots else None,
              "forced_driver": None, "headset": None, "universe": None, "calibration": None}
    value = settings.get(settings.read(), "steamvr.forcedDriver")
    record["forced_driver"] = None if value == settings.MISSING else value
    if not steamvr.missing_processes():
        with steamvr.background_client() as openvr:
            system = openvr.VRSystem()
            hmd = openvr.k_unTrackedDeviceIndex_Hmd
            if system.isTrackedDeviceConnected(hmd):
                record["headset"] = system.getStringTrackedDeviceProperty(hmd, openvr.Prop_TrackingSystemName_String)
                try:
                    record["universe"] = str(system.getUint64TrackedDeviceProperty(hmd, openvr.Prop_CurrentUniverseId_Uint64))
                except Exception:  # noqa: BLE001 - pyopenvr raises when the headset does not report one
                    pass
                state = int(openvr.VRChaperone().getCalibrationState())
                record["calibration"] = chaperone.CALIBRATION.get(state, str(state))
    return record


def run(args) -> int:
    if args.action == "status":
        record = _headset()
        virtual = record["headset"] == "null"
        output.emit(record, f"headset: {record['headset'] or 'none'} ({'virtual' if virtual else 'real or none'}); "
                            f"forcedDriver {record['forced_driver']!r}; pending snapshot {record['pending_snapshot']}",
                    args.format)
        return output.OK if virtual else output.NOT_MET
    if args.action == "on":
        switch_on(args.window, args.with_driver)
        record = _headset()
        if record["headset"] == "null" and record["universe"] not in (None, chaperone.NULL_UNIVERSE):
            output.note(f"the null headset reports universe {record['universe']}, not {chaperone.NULL_UNIVERSE}: "
                        "SteamVR may open Room Setup (the play area went to the wrong universe)")
        output.emit(record, f"headset: {record['headset']}; universe {record['universe']}, "
                            f"calibration {record['calibration']}", args.format)
        return output.OK if record["headset"] == "null" else output.FAILED
    if args.action == "off":
        undone, universes = switch_off()
        record = _headset()
        output.emit({**record, "restored": [c.to_dict() for c in undone], "restored_universes": universes},
                    f"restored {len(undone)} settings and {sum(u['removed'] for u in universes)} chaperone "
                    f"universe(s); headset: {record['headset'] or 'none'}", args.format)
        return output.OK
    # run
    command = args.command
    if command and command[0] == "sergulath":
        command = [sys.executable, "-m", "sergulath", *command[1:]]
    if settings.latest_snapshot(label=LABEL):
        output.note("a virtual-headset snapshot is still pending; run `sergulath hmd off` first")
        return output.FAILED
    try:
        switch_on(args.window, args.with_driver)
        return subprocess.run(command).returncode
    finally:
        try:
            switch_off()
        except Exception as e:  # noqa: BLE001 - say so loudly: the real headset must come back
            output.note(f"could not restore the real headset ({e}); run `sergulath hmd off`")
