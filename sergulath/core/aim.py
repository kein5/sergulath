"""Where to hold a controller so its laser meets a point on an overlay (pure math, no SteamVR).

Matrices are 3 rows of 4 (rotation, then translation), like OpenVR's HmdMatrix34_t. An overlay's
quad lies in its own x-y plane, centred on its origin, facing +z. A point on it is given as
(u, v): 0..1 from the left and from the top of what it shows.

An app's laser leaves the controller's tip and may be tilted; `body_for_ray` undoes that tilt so
the laser, not the controller body, points at the target. ASTAROT tilts its laser up by 12
degrees and, for a controller without a render model, takes the body as the tip.
"""

from __future__ import annotations

import math

from .placement import Matrix, multiply

IDENTITY: Matrix = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]


def inverse(m: Matrix) -> Matrix:
    """The inverse of a rigid transform (rotation and translation only)."""
    rt = [[m[c][r] for c in range(3)] for r in range(3)]
    t = [-sum(rt[r][k] * m[k][3] for k in range(3)) for r in range(3)]
    return [rt[r] + [t[r]] for r in range(3)]


def apply(m: Matrix, point: list[float]) -> list[float]:
    return [sum(m[r][k] * point[k] for k in range(3)) + m[r][3] for r in range(3)]


def axis(m: Matrix, column: int) -> list[float]:
    return [m[0][column], m[1][column], m[2][column]]


def laser_tilt(degrees: float) -> Matrix:
    """A rotation about x that turns -z up by `degrees` (ASTAROT's `pose::laser_tilt`)."""
    a = math.radians(degrees)
    return [[1.0, 0.0, 0.0, 0.0], [0.0, math.cos(a), -math.sin(a), 0.0], [0.0, math.sin(a), math.cos(a), 0.0]]


def point_on(overlay: Matrix, width_m: float, height_m: float, u: float, v: float) -> list[float]:
    """The world position of (u, v) on an overlay `width_m` wide and `height_m` tall."""
    return apply(overlay, [(u - 0.5) * width_m, (0.5 - v) * height_m, 0.0])


def ray_at(overlay: Matrix, target: list[float], distance_m: float) -> Matrix:
    """A ray pose `distance_m` in front of the overlay that looks straight at `target`."""
    normal = axis(overlay, 2)
    start = [target[i] + normal[i] * distance_m for i in range(3)]
    return [overlay[r][:3] + [start[r]] for r in range(3)]


def body_for_ray(ray: Matrix, pitch_up_deg: float, tip: Matrix = IDENTITY) -> Matrix:
    """The controller body pose whose laser (body x tip x tilt) is `ray`."""
    return multiply(ray, inverse(multiply(tip, laser_tilt(pitch_up_deg))))


def quaternion(m: Matrix) -> list[float]:
    """The rotation of `m` as (w, x, y, z)."""
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        q = [0.25 * s, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s]
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        q = [(m[2][1] - m[1][2]) / s, 0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s]
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        q = [(m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s]
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        q = [(m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s]
    length = math.sqrt(sum(c * c for c in q))
    return [c / length for c in q]


def from_quaternion(position: list[float], q: list[float]) -> Matrix:
    w, x, y, z = q
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), position[0]],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), position[1]],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), position[2]],
    ]


def look_at(position: list[float], target: list[float]) -> Matrix:
    """A pose at `position` whose -z points at `target`, keeping +y as close to up as it can."""
    forward = [target[i] - position[i] for i in range(3)]
    length = math.sqrt(sum(c * c for c in forward))
    if length < 1e-9:
        return [IDENTITY[r][:3] + [position[r]] for r in range(3)]
    back = [-c / length for c in forward]  # +z
    up = [0.0, 1.0, 0.0] if abs(back[1]) < 0.99 else [0.0, 0.0, -1.0]
    right = [up[1] * back[2] - up[2] * back[1], up[2] * back[0] - up[0] * back[2], up[0] * back[1] - up[1] * back[0]]
    norm = math.sqrt(sum(c * c for c in right))
    right = [c / norm for c in right]
    true_up = [back[1] * right[2] - back[2] * right[1], back[2] * right[0] - back[0] * right[2], back[0] * right[1] - back[1] * right[0]]
    return [[right[r], true_up[r], back[r], position[r]] for r in range(3)]
