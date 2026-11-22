"""Point a virtual controller's laser at a spot on an overlay and pull the trigger.

The overlay's real pose and size are read through OpenVR (as `overlay --placement` does), the
controller is placed `distance_m` in front of the spot so the app's laser (see `aim`) meets it,
SteamVR's own intersection test confirms the hit, and the trigger is clicked.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from . import aim, driver, placement


@dataclass
class PressResult:
    key: str
    hand: str
    at: list[float]
    target_m: list[float] | None = None
    controller_m: list[float] | None = None
    hit: bool = False
    hit_uv: list[float] | None = None
    hit_distance_m: float | None = None
    pressed: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def overlay_size(overlay, handle) -> tuple[float, float]:
    """Width and height in metres of what the overlay shows (its texture bounds' share)."""
    width = float(overlay.getOverlayWidthInMeters(handle))
    try:
        tw, th = overlay.getOverlayTextureSize(handle)
        b = overlay.getOverlayTextureBounds(handle)
        shown_w = abs(b.uMax - b.uMin) * tw
        shown_h = abs(b.vMax - b.vMin) * th
        if shown_w > 0 and shown_h > 0:
            return width, width * shown_h / shown_w
    except Exception:  # noqa: BLE001 - a raw-pixel overlay has no texture size; assume square
        pass
    return width, width


def intersect(openvr, overlay, handle, ray: placement.Matrix):
    """SteamVR's own test of a laser along the ray's -z (ComputeOverlayIntersection)."""
    params = openvr.VROverlayIntersectionParams_t()
    origin = aim.axis(ray, 3)
    direction = [-c for c in aim.axis(ray, 2)]
    params.vSource = openvr.HmdVector3_t(*origin)
    params.vDirection = openvr.HmdVector3_t(*direction)
    params.eOrigin = openvr.TrackingUniverseStanding
    ok, results = overlay.computeOverlayIntersection(handle, params)
    if not ok:
        return None
    return [results.vUVs.v[0], results.vUVs.v[1]], float(results.fDistance)


def press(openvr, key: str, u: float, v: float, *, hand: str = "right", distance_m: float = 0.12,
          pitch_up_deg: float = 0.0, hold_s: float = 0.15, settle_s: float = 0.4, dry_run: bool = False) -> PressResult:
    result = PressResult(key, hand, [u, v])
    overlay = openvr.IVROverlay()
    try:
        handle = overlay.findOverlay(key)
    except openvr.error_code.OverlayError as e:
        result.error = f"overlay not found ({e})"
        return result
    world = placement.world_matrix(openvr, overlay, handle)
    if world is None:
        result.error = "the overlay has no pose now (it follows a device that is not tracked?)"
        return result
    width, height = overlay_size(overlay, handle)
    target = aim.point_on(world, width, height, u, v)
    ray = aim.ray_at(world, target, distance_m)
    body = aim.body_for_ray(ray, pitch_up_deg)
    result.target_m = [round(c, 4) for c in target]
    driver.send(f"connect {hand}")
    driver.set_pose(openvr, hand, body)
    time.sleep(settle_s)
    seen = driver.read_pose(openvr, hand)
    if seen is None:
        result.error = "the controller has no pose in SteamVR"
        return result
    result.controller_m = [round(seen[r][3], 4) for r in range(3)]
    laser = placement.multiply(seen, aim.laser_tilt(pitch_up_deg))
    hit = intersect(openvr, overlay, handle, laser)
    if hit:
        result.hit = True
        result.hit_uv = [round(c, 4) for c in hit[0]]
        result.hit_distance_m = round(hit[1], 4)
    if dry_run:
        return result
    driver.send(f"button {hand} trigger 1")
    time.sleep(hold_s)
    driver.send(f"button {hand} trigger 0")
    result.pressed = True
    return result
