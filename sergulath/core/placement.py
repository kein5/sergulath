"""Where an overlay sits: what it follows, its offset from that, and where that puts it (read-only).

An app places each overlay either in the room (absolute) or relative to a tracked device (the
headset, a controller). For a device-relative overlay the world position exists only while that
device has a valid pose; without one SteamVR has nowhere to draw it, whatever its alpha.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

# VROverlayTransformType
TRANSFORM = {
    0: "absolute", 1: "tracked_device_relative", 2: "system_overlay", 3: "tracked_component",
    4: "cursor", 5: "dashboard_tab", 6: "dashboard_thumb", 7: "mountable", 8: "projection",
    9: "subview",
}
# ETrackingUniverseOrigin
UNIVERSE = {0: "seated", 1: "standing", 2: "raw"}
DEVICE_CLASS = {0: "invalid", 1: "hmd", 2: "controller", 3: "tracker", 4: "base_station", 5: "display_redirect"}
ROLE = {1: "left", 2: "right"}
NO_DEVICE = 0xFFFFFFFF

Matrix = list[list[float]]  # 3 rows of 4: rotation in the first three columns, translation in the last


@dataclass
class Placement:
    transform: str
    device_index: int | None = None
    device: str = ""  # "hmd", "controller/left", ...; empty for an absolute overlay
    device_pose_valid: bool | None = None
    universe: str = ""  # for an absolute overlay
    offset_m: list[float] = field(default_factory=list)  # translation from the device (or origin)
    offset_distance_m: float | None = None
    rotation_deg: list[float] = field(default_factory=list)  # yaw, pitch, roll of the overlay relative to it
    world_m: list[float] | None = None  # position in the standing universe, when known
    head_distance_m: float | None = None
    texture_bounds: list[float] = field(default_factory=list)  # uMin, vMin, uMax, vMax
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def to_rows(m) -> Matrix:
    """An HmdMatrix34_t (or anything indexable as [r][c]) as plain lists."""
    return [[float(m.m[r][c]) if hasattr(m, "m") else float(m[r][c]) for c in range(4)] for r in range(3)]


def multiply(a: Matrix, b: Matrix) -> Matrix:
    """a × b for two 3×4 rigid transforms (an implied bottom row 0 0 0 1)."""
    out = []
    for r in range(3):
        row = [sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)]
        row.append(sum(a[r][k] * b[k][3] for k in range(3)) + a[r][3])
        out.append(row)
    return out


def translation(m: Matrix) -> list[float]:
    return [m[0][3], m[1][3], m[2][3]]


def distance(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def yaw_pitch_roll(m: Matrix) -> list[float]:
    """The rotation as yaw (about y), pitch (about x) and roll (about z) in degrees, Y-X-Z order."""
    pitch = math.asin(max(-1.0, min(1.0, -m[1][2])))
    yaw = math.atan2(m[0][2], m[2][2])
    roll = math.atan2(m[1][0], m[1][1])
    return [round(math.degrees(v), 1) for v in (yaw, pitch, roll)]


def _rounded(values: list[float]) -> list[float]:
    return [round(v, 4) for v in values]


def device_name(system, index: int) -> str:
    device_class = DEVICE_CLASS.get(system.getTrackedDeviceClass(index), "?")
    if device_class == "controller":
        return f"controller/{ROLE.get(system.getControllerRoleForTrackedDeviceIndex(index), '-')}"
    return device_class


def read(openvr, overlay, handle, poses=None) -> Placement:
    """Read one overlay's placement. `poses` (standing universe) may be passed to share one read."""
    system = openvr.VRSystem()
    if poses is None:
        poses = system.getDeviceToAbsoluteTrackingPose(openvr.TrackingUniverseStanding, 0, openvr.k_unMaxTrackedDeviceCount)
    kind = overlay.getOverlayTransformType(handle)
    kind = int(getattr(kind, "value", kind))
    place = Placement(transform=TRANSFORM.get(kind, str(kind)))
    try:
        bounds = overlay.getOverlayTextureBounds(handle)
        place.texture_bounds = _rounded([bounds.uMin, bounds.vMin, bounds.uMax, bounds.vMax])
    except Exception:  # noqa: BLE001 - optional detail
        pass
    head = poses[openvr.k_unTrackedDeviceIndex_Hmd]
    head_at = translation(to_rows(head.mDeviceToAbsoluteTracking)) if head.bPoseIsValid else None
    try:
        if place.transform == "tracked_device_relative":
            index, relative = overlay.getOverlayTransformTrackedDeviceRelative(handle)
            index = int(getattr(index, "value", index))
            relative = to_rows(relative)
            place.device_index = None if index == NO_DEVICE else index
            place.device = device_name(system, index) if index != NO_DEVICE else "none"
            device_pose = poses[index] if index != NO_DEVICE and index < len(poses) else None
            place.device_pose_valid = bool(device_pose and device_pose.bPoseIsValid)
            if place.device_pose_valid:
                world = multiply(to_rows(device_pose.mDeviceToAbsoluteTracking), relative)
                place.world_m = _rounded(translation(world))
        elif place.transform == "absolute":
            universe, relative = overlay.getOverlayTransformAbsolute(handle)
            universe = int(getattr(universe, "value", universe))
            place.universe = UNIVERSE.get(universe, str(universe))
            relative = to_rows(relative)
            if universe == 1:
                place.world_m = _rounded(translation(relative))
        else:
            return place
    except Exception as e:  # noqa: BLE001 - report it with the rest of the record
        place.error = f"{type(e).__name__} {e}".rstrip()
        return place
    place.offset_m = _rounded(translation(relative))
    place.offset_distance_m = round(distance(place.offset_m, [0.0, 0.0, 0.0]), 4)
    place.rotation_deg = yaw_pitch_roll(relative)
    if place.world_m is not None and head_at is not None:
        place.head_distance_m = round(distance(place.world_m, head_at), 3)
    return place


def world_matrix(openvr, overlay, handle, poses=None) -> Matrix | None:
    """The overlay's pose in the standing universe, or None when it cannot be known now
    (a device-relative overlay whose device has no pose, or a kind not handled here)."""
    system = openvr.VRSystem()
    if poses is None:
        poses = system.getDeviceToAbsoluteTrackingPose(openvr.TrackingUniverseStanding, 0, openvr.k_unMaxTrackedDeviceCount)
    kind = overlay.getOverlayTransformType(handle)
    kind = TRANSFORM.get(int(getattr(kind, "value", kind)))
    if kind == "tracked_device_relative":
        index, relative = overlay.getOverlayTransformTrackedDeviceRelative(handle)
        index = int(getattr(index, "value", index))
        if index == NO_DEVICE or index >= len(poses) or not poses[index].bPoseIsValid:
            return None
        return multiply(to_rows(poses[index].mDeviceToAbsoluteTracking), to_rows(relative))
    if kind == "absolute":
        universe, relative = overlay.getOverlayTransformAbsolute(handle)
        if int(getattr(universe, "value", universe)) != 1:
            return None
        return to_rows(relative)
    return None


def describe(place: Placement) -> str:
    if place.error:
        return f"placement: {place.transform} ({place.error})"
    if place.transform == "tracked_device_relative":
        text = f"follows {place.device}#{place.device_index} (pose={place.device_pose_valid})"
    elif place.transform == "absolute":
        text = f"absolute in {place.universe}"
    else:
        return f"placement: {place.transform}"
    if place.offset_m:
        text += f", offset {place.offset_m} m ({place.offset_distance_m} m), rotation {place.rotation_deg} deg"
    text += f", world {place.world_m}" if place.world_m is not None else ", world unknown (no pose)"
    if place.head_distance_m is not None:
        text += f", {place.head_distance_m} m from the head"
    return "placement: " + text
