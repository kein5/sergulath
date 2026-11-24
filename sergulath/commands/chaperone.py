"""sergulath chaperone: the play area and its walls, read, changed with a snapshot, put back.

  chaperone show                          Calibration state, play area size and corners, number of
                                          wall quads, whether the bounds are showing. Read-only.
  chaperone apply --size W D [--walls]    Snapshot chaperone_info.vrchap, then set a W x D metre
                                          play area (and four walls around it with --walls) through
                                          the chaperone setup API. It takes effect at once.
  chaperone restore                       Stop SteamVR, write the file back as the snapshot kept it,
                                          start SteamVR through Steam, and compare.

The API's "temporary" commit is meant to last for the session only, but SteamVR 2.19.4 writes it
to chaperone_info.vrchap at once, so a change lasts until `chaperone restore`; a snapshot left
pending is reported by `chaperone show`.

A real headset's play area is refused unless --real-headset says so: a standalone headset that
streams from the PC (a Steam Frame) keeps its own boundary, and what this does to it has not been
checked. On the virtual headset (`sergulath hmd on`) only the virtual headset's universe changes.
"""

from __future__ import annotations

import json

from .. import output
from ..core import chaperone, driver, steamvr

NAME = "chaperone"
HELP = "show the play area, change it with a snapshot, and restore it"


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    show = sub.add_parser("show", help="print the play area and bounds (read-only)")
    output.add_format(show)
    change = sub.add_parser("apply", help="snapshot, then set a play area")
    change.add_argument("--size", nargs=2, type=float, required=True, metavar=("WIDTH", "DEPTH"),
                        help="play area in metres (x, z)")
    change.add_argument("--walls", action="store_true", help="also set four walls around it")
    change.add_argument("--real-headset", action="store_true",
                        help="allow it while a real headset is SteamVR's headset (unchecked on a Steam Frame)")
    output.add_format(change)
    restore = sub.add_parser("restore", help="put the chaperone file back (restarts SteamVR)")
    output.add_format(restore)


def _headset(openvr) -> str | None:
    system = openvr.VRSystem()
    if not system.isTrackedDeviceConnected(openvr.k_unTrackedDeviceIndex_Hmd):
        return None
    return system.getStringTrackedDeviceProperty(openvr.k_unTrackedDeviceIndex_Hmd,
                                                 openvr.Prop_TrackingSystemName_String)


def _text(state: chaperone.State) -> str:
    area = f"{state.play_area[0]} x {state.play_area[1]} m" if state.play_area else "none"
    return (f"play area {area}; {state.bounds_quads} wall quad(s); calibration {state.calibration}; "
            f"bounds {'showing' if state.bounds_visible else 'hidden'}")


def _show(args) -> int:
    with steamvr.background_client() as openvr:
        state = chaperone.read(openvr)
        headset = _headset(openvr)
    pending = chaperone.pending()
    record = {**state.to_dict(), "headset": headset, "pending_snapshot": str(pending[-1]) if pending else None}
    output.emit(record, _text(state) + (f"; snapshot pending: {pending[-1].name}" if pending else ""), args.format)
    return output.OK if state.play_area else output.NOT_MET


def _apply(args) -> int:
    if chaperone.pending():
        output.note("a chaperone snapshot is still pending; run `sergulath chaperone restore` first")
        return output.FAILED
    with steamvr.background_client() as openvr:
        headset = _headset(openvr)
        if headset != "null" and not args.real_headset:
            output.note(f"SteamVR's headset is {headset or 'none'}, not the virtual one; "
                        "use `sergulath hmd on` or pass --real-headset")
            return output.FAILED
        snapshot = chaperone.snapshot(openvr)
        width, depth = args.size
        try:
            chaperone.apply(openvr, width, depth, args.walls)
        except Exception as e:  # noqa: BLE001 - the snapshot stays pending; say how to undo
            output.note(f"{e}; the file may have changed: run `sergulath chaperone restore`")
            return output.FAILED
        state = chaperone.read(openvr)
    applied = state.play_area == (round(width, 3), round(depth, 3))
    output.emit({**state.to_dict(), "applied": applied, "snapshot": str(snapshot)},
                _text(state) + f"; snapshot {snapshot.name}; undo with `sergulath chaperone restore`", args.format)
    return output.OK if applied else output.NOT_MET


def _restore(args) -> int:
    pending = chaperone.pending()
    if not pending:
        output.note("no chaperone snapshot is pending: nothing to restore")
        return output.NOT_MET
    snapshot = pending[-1]
    kept = json.loads(snapshot.read_text(encoding="utf-8"))
    if "vrserver.exe" in steamvr.running_processes():
        driver.quit_steamvr()
    path = chaperone.write_back(snapshot)
    driver.start_steamvr()
    now = path.read_text(encoding="utf-8") if path.is_file() else None
    same = chaperone.universes(now) == chaperone.universes(kept["content"])
    done = chaperone.mark_restored(snapshot)
    output.emit({"file": str(path), "snapshot": str(done), "matches_snapshot": same},
                f"{path} written back from {snapshot.name}; SteamVR restarted; "
                + ("same as the snapshot" if same else "DIFFERS from the snapshot"), args.format)
    return output.OK if same else output.NOT_MET


def run(args) -> int:
    return {"show": _show, "apply": _apply, "restore": _restore}[args.action](args)
