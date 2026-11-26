"""Cut one overlay out of the virtual headset's window (`sergulath hmd on`).

The null driver's "Headset Window" shows what the compositor sends to the headset: both eyes,
side by side, scene and overlays together. Where an overlay lands there follows from geometry:
its pose and size (SteamVR tells both), the head's pose, each eye's offset and its projection
(`GetProjectionRaw`). Where each eye sits in the window does not: on 2026-11-23, in a 480 x 270
window on a 125% display, the eyes took 180 x 150 pixels each at the top left and the rest of the
window stayed black. So each capture is calibrated first: a small magenta probe overlay is shown
at two spots in front of the head, found in the window, and the two spots give each eye's pixel
scale and offset. The probe is destroyed right after.

This is the route for overlays whose pixels SteamVR will not hand out (`GetOverlayImageData`
answers InvalidTexture for GPU textures, and for every overlay while a real headset is in
standby): the compositor has drawn them, so the window has them.
"""

from __future__ import annotations

import ctypes
import time
from dataclasses import asdict, dataclass

from . import placement

PROBE_KEY = "sergulath.headset-view.probe"
PROBE_COLOUR = (255, 0, 255)
PROBE_WIDTH_M = 0.08
# Two spots in front of the head (head space, for a probe 1 m away; scaled with its depth): far
# enough apart for a good scale, close enough to the centre to stay inside both eyes.
PROBE_SPOTS = ((0.0, 0.0, -1.0), (0.25, 0.2, -1.0))
PROBE_DEPTHS = (1.0, 0.2)  # farthest and nearest the probe goes

Matrix = placement.Matrix


@dataclass
class EyeMap:
    """Pixel = scale * (u or v) + offset, for one eye (u, v in 0..1 over the eye's projection)."""
    sx: float
    ox: float
    sy: float
    oy: float

    def pixel(self, u: float, v: float) -> tuple[float, float]:
        return self.sx * u + self.ox, self.sy * v + self.oy


@dataclass
class Crop:
    eye: str
    box: list[int]  # left, top, right, bottom (right and bottom exclusive), clamped to the eye
    inside: float  # share of the overlay's projected area that falls inside the eye's view

    def to_dict(self) -> dict:
        return asdict(self)


def invert(m: Matrix) -> Matrix:
    """The inverse of a rigid 3x4 transform."""
    r = [[m[c][r] for c in range(3)] for r in range(3)]
    t = [-sum(r[i][k] * m[k][3] for k in range(3)) for i in range(3)]
    return [r[i] + [t[i]] for i in range(3)]


def apply(m: Matrix, p) -> list[float]:
    return [sum(m[r][k] * p[k] for k in range(3)) + m[r][3] for r in range(3)]


def project(point_eye, raw) -> tuple[float, float] | None:
    """A point in eye space to (u, v) over the eye's projection; None behind the eye.

    `raw` is GetProjectionRaw's (left, right, top, bottom) tangents; OpenVR's top is the smaller
    one (image y grows downwards), and eye space looks along -z with +y up.
    """
    x, y, z = point_eye
    if z >= -1e-6:
        return None
    left, right, top, bottom = raw
    tx, ty = x / -z, y / -z
    return (tx - left) / (right - left), (-ty - top) / (bottom - top)


def overlay_size(openvr, overlay, handle) -> tuple[float, float]:
    """Width and height in metres: the width SteamVR has, the height from the texture's aspect."""
    width = overlay.getOverlayWidthInMeters(handle)
    tw, th = 1, 1  # unknown texture: assume square
    # The first call of a client can answer InvalidTexture for another app's GPU texture and the
    # next one the size (seen with ASTAROT0 on 2026-11-23), so ask a few times.
    for _ in range(5):
        try:
            tw, th = overlay.getOverlayTextureSize(handle)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    du, dv = 1.0, 1.0
    try:
        b = overlay.getOverlayTextureBounds(handle)
        du, dv = abs(b.uMax - b.uMin) or 1.0, abs(b.vMax - b.vMin) or 1.0
    except Exception:  # noqa: BLE001
        pass
    try:
        texel = overlay.getOverlayTexelAspect(handle) or 1.0
    except Exception:  # noqa: BLE001
        texel = 1.0
    aspect = (th * dv) / (tw * du) / texel if tw and th else 1.0
    return width, width * aspect


def corners(world: Matrix, width: float, height: float) -> list[list[float]]:
    w, h = width / 2, height / 2
    return [apply(world, p) for p in ((-w, -h, 0), (w, -h, 0), (w, h, 0), (-w, h, 0))]


def eye_views(openvr) -> dict:
    """Per eye: (eye-from-standing transform, projection raw) for the head's current pose."""
    system = openvr.VRSystem()
    poses = system.getDeviceToAbsoluteTrackingPose(openvr.TrackingUniverseStanding, 0, openvr.k_unMaxTrackedDeviceCount)
    head = poses[openvr.k_unTrackedDeviceIndex_Hmd]
    if not head.bPoseIsValid:
        raise RuntimeError("the headset has no pose")
    head_from_world = invert(placement.to_rows(head.mDeviceToAbsoluteTracking))
    views = {}
    for name, eye in (("left", openvr.Eye_Left), ("right", openvr.Eye_Right)):
        eye_from_head = invert(placement.to_rows(system.getEyeToHeadTransform(eye)))
        views[name] = (placement.multiply(eye_from_head, head_from_world), tuple(system.getProjectionRaw(eye)))
    return views


def find_colour(width: int, height: int, rgba: bytes, colour=PROBE_COLOUR, tolerance: int = 40) -> list[list[tuple]]:
    """Pixels of one colour, grouped into clusters by gaps along x (one cluster per eye)."""
    r0, g0, b0 = colour
    hits = []
    for y in range(height):
        row = y * width * 4
        for x in range(width):
            i = row + x * 4
            if abs(rgba[i] - r0) <= tolerance and abs(rgba[i + 1] - g0) <= tolerance and abs(rgba[i + 2] - b0) <= tolerance:
                hits.append((x, y))
    if not hits:
        return []
    xs = sorted({x for x, _ in hits})
    gaps = [(xs[i + 1] - xs[i], xs[i]) for i in range(len(xs) - 1)]
    split = max(gaps)[1] if gaps and max(gaps)[0] > 3 else None
    if split is None:
        return [hits]
    return [[p for p in hits if p[0] <= split], [p for p in hits if p[0] > split]]


def centre(points) -> tuple[float, float]:
    return (min(p[0] for p in points) + max(p[0] for p in points) + 1) / 2, \
           (min(p[1] for p in points) + max(p[1] for p in points) + 1) / 2


def solve(spots_uv: dict, spots_px: dict) -> dict:
    """Each eye's map from two spots: {eye: [(u, v), (u, v)]} and {eye: [(x, y), (x, y)]}."""
    maps = {}
    for eye, ((u1, v1), (u2, v2)) in spots_uv.items():
        (x1, y1), (x2, y2) = spots_px[eye]
        sx = (x2 - x1) / (u2 - u1)
        sy = (y2 - y1) / (v2 - v1)
        maps[eye] = EyeMap(sx, x1 - sx * u1, sy, y1 - sy * v1)
    return maps


def probe_depth(head_distance_m: float | None) -> float:
    """How far in front of the head to show the probe: in front of the overlay being cut out.

    SteamVR draws a nearer overlay over a farther one whatever their sort orders, so a probe at
    1 m behind a panel at 0.7 m (ASTAROT2, the VR panel) never shows in the window (found 0
    times, 2026-11-26). Half the overlay's distance, between 0.2 m and 1 m."""
    if not head_distance_m or head_distance_m <= 0:
        return PROBE_DEPTHS[0]
    return max(PROBE_DEPTHS[-1], min(PROBE_DEPTHS[0], head_distance_m / 2))


def calibrate(openvr, capture, settle: float = 1.0, depth: float | None = None) -> dict:
    """Show the probe at PROBE_SPOTS (scaled to `depth` metres), find it in `capture()` (width,
    height, rgba), return the eye maps. When something nearer hides it, try once more at the
    nearest depth."""
    depths = [depth or PROBE_DEPTHS[0]]
    if depths[0] > PROBE_DEPTHS[-1]:
        depths.append(PROBE_DEPTHS[-1])
    for i, d in enumerate(depths):
        try:
            return _calibrate_at(openvr, capture, settle, d)
        except ProbeNotFound:
            if i == len(depths) - 1:
                raise


class ProbeNotFound(RuntimeError):
    pass


def _calibrate_at(openvr, capture, settle: float, depth: float) -> dict:
    overlay = openvr.VROverlay()
    handle = overlay.createOverlay(PROBE_KEY, "SERGULATH probe")
    try:
        size = 16
        pixels = (ctypes.c_uint8 * (size * size * 4))(*([*PROBE_COLOUR, 255] * size * size))
        overlay.setOverlayRaw(handle, pixels, size, size, 4)
        overlay.setOverlayWidthInMeters(handle, PROBE_WIDTH_M * depth)  # the same angular size at any depth
        overlay.setOverlaySortOrder(handle, 1000)
        overlay.showOverlay(handle)
        system = openvr.VRSystem()
        # The probe follows the head, so where it falls in each eye does not depend on the pose.
        eyes = {name: (invert(placement.to_rows(system.getEyeToHeadTransform(eye))), tuple(system.getProjectionRaw(eye)))
                for name, eye in (("left", openvr.Eye_Left), ("right", openvr.Eye_Right))}
        uv = {"left": [], "right": []}
        px = {"left": [], "right": []}
        for spot in [tuple(c * depth for c in s) for s in PROBE_SPOTS]:
            m = openvr.HmdMatrix34_t()
            for i in range(3):
                m.m[i][i] = 1.0
            m.m[0][3], m.m[1][3], m.m[2][3] = spot
            overlay.setOverlayTransformTrackedDeviceRelative(handle, openvr.k_unTrackedDeviceIndex_Hmd, m)
            time.sleep(settle)
            width, height, rgba = capture()
            clusters = find_colour(width, height, rgba)
            if len(clusters) != 2:
                raise ProbeNotFound(f"the calibration probe ({depth:.2f} m in front of the head) was found "
                                    f"{len(clusters)} time(s), not once per eye")
            clusters.sort(key=lambda c: centre(c)[0])
            for eye, cluster in zip(("left", "right"), clusters):
                eye_from_head, raw = eyes[eye]
                uv[eye].append(project(apply(eye_from_head, spot), raw))
                px[eye].append(centre(cluster))
        return solve(uv, px)
    finally:
        overlay.destroyOverlay(handle)


def crop_box(points_world, eye_from_world: Matrix, raw, eye_map: EyeMap, eye_box: list[float]) -> Crop | None:
    """The pixel box the overlay's corners cover in one eye, clamped to that eye's area."""
    projected = [project(apply(eye_from_world, p), raw) for p in points_world]
    if any(p is None for p in projected):
        return None
    pixels = [eye_map.pixel(u, v) for u, v in projected]
    left, top = min(p[0] for p in pixels), min(p[1] for p in pixels)
    right, bottom = max(p[0] for p in pixels), max(p[1] for p in pixels)
    area = max(0.0, right - left) * max(0.0, bottom - top)
    cl, ct = max(left, eye_box[0]), max(top, eye_box[1])
    cr, cb = min(right, eye_box[2]), min(bottom, eye_box[3])
    inside_area = max(0.0, cr - cl) * max(0.0, cb - ct)
    if inside_area <= 0:
        return Crop("", [0, 0, 0, 0], 0.0)
    box = [int(cl), int(ct), int(cr + 0.999), int(cb + 0.999)]
    return Crop("", box, round(inside_area / area, 3) if area else 0.0)


def eye_area(eye_map: EyeMap) -> list[float]:
    """The pixels of one eye's whole projection (u, v from 0 to 1)."""
    x0, y0 = eye_map.pixel(0.0, 0.0)
    x1, y1 = eye_map.pixel(1.0, 1.0)
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def cut(width: int, rgba: bytes, box: list[int]) -> tuple[int, int, bytes]:
    left, top, right, bottom = box
    rows = [rgba[(y * width + left) * 4:(y * width + right) * 4] for y in range(top, bottom)]
    return right - left, bottom - top, b"".join(rows)
