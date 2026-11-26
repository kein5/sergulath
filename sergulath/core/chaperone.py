"""SteamVR's chaperone (play area and collision bounds): read it, change it, put it back.

Changes go through IVRChaperoneSetup's working copy and are committed with
EChaperoneConfigFile_Temp, which the API documents as session-only. On SteamVR 2.19.4 it is not:
SteamVR writes the committed universe to chaperone_info.vrchap at once (found 2026-11-23), so it
outlives the session. That is why a change starts with a snapshot of the whole file, and the
restore stops SteamVR, writes the file back as it was (or removes it if there was none) and
starts SteamVR again through Steam.
"""

from __future__ import annotations

import ctypes
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import driver

SNAPSHOTS = Path("records") / "chaperone"
WALL_HEIGHT = 2.43  # SteamVR's room setup draws walls this high

CALIBRATION = {
    1: "ok", 100: "warning", 101: "warning_base_station_may_have_moved", 102: "warning_base_station_removed",
    103: "warning_seated_bounds_invalid", 200: "error", 201: "error_base_station_uninitialized",
    202: "error_base_station_conflict", 203: "error_play_area_invalid", 204: "error_collision_bounds_invalid",
}


@dataclass
class State:
    calibration: str
    play_area: tuple[float, float] | None  # width (x), depth (z) in metres
    corners: list[list[float]] = field(default_factory=list)  # play area corners, standing space
    bounds_quads: int = 0  # collision bound walls
    bounds_visible: bool = False

    def to_dict(self) -> dict:
        return {"calibration": self.calibration, "play_area": list(self.play_area) if self.play_area else None,
                "corners": self.corners, "bounds_quads": self.bounds_quads, "bounds_visible": self.bounds_visible}


def chaperone_file() -> Path:
    configs = driver.openvr_paths().get("config") or []
    if not configs:
        raise RuntimeError("SteamVR's config folder is not in openvrpaths.vrpath")
    return Path(configs[0]) / "chaperone_info.vrchap"


def _vec(v) -> list[float]:
    return [round(v.v[0], 3), round(v.v[1], 3), round(v.v[2], 3)]


def live_bounds(openvr) -> list:
    """The live collision bounds as HmdQuad_t objects (pyopenvr's wrapper reads one quad only)."""
    fn = openvr.VRChaperoneSetup().function_table.getLiveCollisionBoundsInfo
    count = ctypes.c_uint32(0)
    fn(None, ctypes.byref(count))
    if not count.value:
        return []
    quads = (openvr.HmdQuad_t * count.value)()
    fn(ctypes.byref(quads[0]), ctypes.byref(count))
    return list(quads[: count.value])


def export_live(openvr) -> str:
    """The live chaperone as SteamVR's JSON text (pyopenvr's wrapper passes the length by value)."""
    fn = openvr.VRChaperoneSetup().function_table.exportLiveToBuffer
    length = ctypes.c_uint32(0)
    fn(None, ctypes.byref(length))
    if not length.value:
        return ""
    buffer = ctypes.create_string_buffer(length.value)
    if not fn(buffer, ctypes.byref(length)):
        return ""
    return buffer.value.decode("utf-8")


def read(openvr) -> State:
    chaperone = openvr.VRChaperone()
    status = chaperone.getCalibrationState()
    ok, width, depth = chaperone.getPlayAreaSize()
    ok_rect, rect = chaperone.getPlayAreaRect()
    return State(calibration=CALIBRATION.get(status, str(status)),
                 play_area=(round(width, 3), round(depth, 3)) if ok else None,
                 corners=[_vec(c) for c in rect.vCorners] if ok_rect else [],
                 bounds_quads=len(live_bounds(openvr)),
                 bounds_visible=bool(chaperone.areBoundsVisible()))


def walls(openvr, width: float, depth: float, height: float = WALL_HEIGHT):
    """Four collision-bound walls around a width x depth rectangle centred on the origin."""
    x, z = width / 2, depth / 2
    floor = [(-x, -z), (x, -z), (x, z), (-x, z)]
    quads = (openvr.HmdQuad_t * 4)()
    for i in range(4):
        (ax, az), (bx, bz) = floor[i], floor[(i + 1) % 4]
        for j, (px, py, pz) in enumerate(((ax, 0, az), (bx, 0, bz), (bx, height, bz), (ax, height, az))):
            quads[i].vCorners[j].v[0], quads[i].vCorners[j].v[1], quads[i].vCorners[j].v[2] = px, py, pz
    return quads


def snapshot(openvr, path: Path | None = None, snapshot_dir: Path | None = None) -> Path:
    """Keep the chaperone file as it is now (None when absent) and the live chaperone's export."""
    path = path or chaperone_file()
    snapshot_dir = snapshot_dir or SNAPSHOTS
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    out = snapshot_dir / (time.strftime("%Y%m%dT%H%M%S") + ".json")
    record = {"taken": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "file": str(path),
              "content": path.read_text(encoding="utf-8") if path.is_file() else None,
              "state": read(openvr).to_dict(), "live": export_live(openvr)}
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def apply(openvr, width: float, depth: float, with_walls: bool) -> None:
    """Set the play area (and walls) in the working copy and commit it."""
    setup = openvr.VRChaperoneSetup()
    setup.revertWorkingCopy()
    if not setup.getWorkingStandingZeroPoseToRawTrackingPose()[0]:
        # An uncalibrated universe (the virtual headset has none) has no standing origin, and
        # SteamVR refuses the commit without one: use the raw origin for standing and seated.
        identity = openvr.HmdMatrix34_t()
        for i in range(3):
            identity.m[i][i] = 1.0
        setup.setWorkingStandingZeroPoseToRawTrackingPose(identity)
        setup.setWorkingSeatedZeroPoseToRawTrackingPose(identity)
    setup.setWorkingPlayAreaSize(width, depth)
    if with_walls:
        quads = walls(openvr, width, depth)
        setup.function_table.setWorkingCollisionBoundsInfo(ctypes.byref(quads[0]), len(quads))
    if not setup.commitWorkingCopy(openvr.EChaperoneConfigFile_Temp):
        raise RuntimeError("SteamVR refused to commit the working chaperone")


def write_back(snapshot_path: Path) -> Path:
    """Put the chaperone file back as the snapshot kept it. SteamVR must be stopped."""
    record = json.loads(snapshot_path.read_text(encoding="utf-8"))
    path = Path(record["file"])
    if record["content"] is None:
        path.unlink(missing_ok=True)
    else:
        temporary = path.with_suffix(".sergulath-tmp")
        temporary.write_text(record["content"], encoding="utf-8")
        temporary.replace(path)
    return path


def universes(text: str | None) -> dict:
    """universeID -> the universe's play area and bounds, without SteamVR's time stamp."""
    if not text:
        return {}
    found = {}
    for universe in json.loads(text).get("universes", []):
        found[universe.get("universeID")] = {k: v for k, v in universe.items() if k not in ("time", "universeID")}
    return found


# ---- a calibrated universe for the virtual headset -------------------------------------------
#
# SteamVR keeps one play area per tracking universe (chaperone_info.vrchap, keyed by the headset's
# Prop_CurrentUniverseId). The null driver reports universe 2 and SteamVR has no play area for it,
# so every start with the virtual headset is uncalibrated (calibration state 201,
# error_base_station_uninitialized) and SteamVR opens Room Setup ("Welcome to Room Setup!").
# `hmd on` adds a standing play area for that universe while SteamVR is stopped, and `hmd off`
# takes it out again, so the file ends as it began. A Steam Frame brings its own universe
# (424242, from the headset over Steam Link), which this never touches.

NULL_UNIVERSE = "2"  # the null driver's Prop_CurrentUniverseId_Uint64 (SteamVR 2.19.5, 2026-11-26)
UNIVERSE_SNAPSHOTS = SNAPSHOTS / "universe"  # apart from `chaperone apply`'s whole-file snapshots
PLAY_AREA = (2.0, 2.0)  # metres; standing at the raw origin, where the null headset sits


def standing_universe(universe_id: str, width: float = PLAY_AREA[0], depth: float = PLAY_AREA[1],
                      height: float = WALL_HEIGHT) -> dict:
    """A universe as Room Setup writes it: standing and seated at the raw origin, a width x depth
    play area centred there, and four walls around it."""
    x, z = width / 2, depth / 2
    floor = [(-x, -z), (x, -z), (x, z), (-x, z)]
    bounds = []
    for i in range(4):
        (ax, az), (bx, bz) = floor[i], floor[(i + 1) % 4]
        bounds.append([[ax, 0, az], [bx, 0, bz], [bx, height, bz], [ax, height, az]])
    return {"collision_bounds": bounds, "play_area": [width, depth],
            "seated": {"translation": [0, 0, 0], "yaw": 0}, "standing": {"translation": [0, 0, 0], "yaw": 0},
            "time": time.strftime("%a %b %d %H:%M:%S %Y"), "universeID": universe_id}


def _read_file(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else \
        {"jsonid": "chaperone_info", "universes": [], "version": 5}


def _write_file(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".sergulath-tmp")
    temporary.write_text(json.dumps(data, indent=3) + "\n", encoding="utf-8")
    temporary.replace(path)


def ensure_universe(universe_id: str = NULL_UNIVERSE, path: Path | None = None, snapshot_dir: Path | None = None,
                    label: str = "hmd") -> Path:
    """Give `universe_id` a standing play area unless the file has one. SteamVR must be stopped.

    Writes a snapshot first (whether the file existed, and the universe's entry before) and
    returns it; `restore_universe` puts back exactly that."""
    path = path or chaperone_file()
    snapshot_dir = snapshot_dir or UNIVERSE_SNAPSHOTS
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    existed = path.is_file()
    data = _read_file(path)
    before = next((u for u in data.get("universes", []) if str(u.get("universeID")) == universe_id), None)
    out = snapshot_dir / (time.strftime("%Y%m%dT%H%M%S") + (f"-{label}" if label else "") + ".json")
    out.write_text(json.dumps({"taken": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "file": str(path),
                               "file_existed": existed, "universe": universe_id, "before": before,
                               "added": before is None}, indent=2), encoding="utf-8")
    if before is None:
        data.setdefault("universes", []).append(standing_universe(universe_id))
        _write_file(path, data)
    return out


def restore_universe(snapshot: Path) -> dict:
    """Undo `ensure_universe`: take out the universe it added (or the file it created).
    SteamVR must be stopped. Returns what was done and marks the snapshot restored."""
    record = json.loads(snapshot.read_text(encoding="utf-8"))
    path, universe_id = Path(record["file"]), record["universe"]
    done = {"universe": universe_id, "removed": False, "file_removed": False}
    if record.get("added") and path.is_file():
        data = _read_file(path)
        kept = [u for u in data.get("universes", []) if str(u.get("universeID")) != universe_id]
        done["removed"] = len(kept) != len(data.get("universes", []))
        if not kept and not record.get("file_existed"):
            path.unlink()
            done["file_removed"] = True
        else:
            data["universes"] = kept
            _write_file(path, data)
    mark_restored(snapshot)
    return done


def universe_pending(snapshot_dir: Path | None = None, label: str = "") -> list[Path]:
    found = pending(snapshot_dir or UNIVERSE_SNAPSHOTS)
    return [p for p in found if not label or p.stem.endswith(f"-{label}")]


def pending(snapshot_dir: Path | None = None) -> list[Path]:
    snapshot_dir = snapshot_dir or SNAPSHOTS
    return sorted(p for p in snapshot_dir.glob("*.json") if not p.name.endswith(".restored.json"))


def mark_restored(path: Path) -> Path:
    done = path.with_name(path.stem + ".restored.json")
    path.replace(done)
    return done
