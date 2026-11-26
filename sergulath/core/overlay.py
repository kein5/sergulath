"""Read an app's overlays: whether each exists and is shown, and its pixels (read-only)."""

from __future__ import annotations

import ctypes
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import png

# Large enough for the biggest overlay texture SteamVR hands out.
_MAX_BYTES = 4096 * 4096 * 4


@dataclass
class OverlayState:
    key: str
    found: bool
    visible: bool = False
    alpha: float = 0.0
    width_m: float = 0.0
    width_px: int = 0
    height_px: int = 0
    drawn_px: int = 0  # pixels with non-zero alpha
    image: Path | None = None
    error: str = ""
    placement: dict | None = None  # with read_placement: see core.placement

    def to_dict(self) -> dict:
        data = asdict(self)
        data["image"] = str(self.image) if self.image else None
        if data["placement"] is None:
            del data["placement"]
        return data


def bgra_to_rgba(pixels: bytes) -> bytes:
    data = bytearray(pixels)
    data[0::4], data[2::4] = data[2::4], data[0::4]
    return bytes(data)


def capture(openvr, keys: list[str], out_dir: Path | None = None, retries: int = 5,
            read_pixels: bool | None = None, read_placement: bool = False,
            bgra_keys: frozenset[str] | set[str] = frozenset()) -> list[OverlayState]:
    """Look up each key. With `read_pixels` (on when `out_dir` is given), count each overlay's
    drawn pixels; with `out_dir`, also save them as `<key>.png`. With `read_placement`, also
    read where each overlay sits (core.placement).

    GetOverlayImageData hands back an overlay's pixels in the order its image was given:
    RGBA for SetOverlayRaw, but the texture's own byte order for SetOverlayTexture, which is
    BGRA for a DXGI_FORMAT_B8G8R8A8 texture (ASTAROT 0.7's VR panel, launcher and popup). The
    keys in `bgra_keys` are turned back into RGBA before saving, so the PNG has the colours the
    headset shows."""
    overlay = openvr.IVROverlay()
    states = []
    poses = None
    if read_placement:
        from . import placement

        poses = openvr.VRSystem().getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding, 0, openvr.k_unMaxTrackedDeviceCount)
    if read_pixels is None:
        read_pixels = out_dir is not None
    buffer = (ctypes.c_uint8 * _MAX_BYTES)() if read_pixels else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    for key in keys:
        try:
            handle = overlay.findOverlay(key)
        except openvr.error_code.OverlayError as e:
            states.append(OverlayState(key, found=False, error=str(e) or type(e).__name__))
            continue
        state = OverlayState(
            key,
            found=True,
            visible=bool(overlay.isOverlayVisible(handle)),
            alpha=float(overlay.getOverlayAlpha(handle)),
            width_m=float(overlay.getOverlayWidthInMeters(handle)),
        )
        if read_placement:
            state.placement = placement.read(openvr, overlay, handle, poses).to_dict()
        if buffer is not None:
            # A read can fail while the app is replacing the overlay's image (a caption that
            # updates every second), so try a few times before reporting.
            for attempt in range(retries):
                try:
                    width, height = overlay.getOverlayImageData(handle, buffer, _MAX_BYTES)[:2]
                    rgba = bytes(buffer[: width * height * 4])
                    alpha = rgba[3::4]
                    state.width_px, state.height_px = width, height
                    state.drawn_px = len(alpha) - alpha.count(0)
                    if out_dir:
                        state.image = out_dir / f"{key}.png"
                        png.write_rgba(state.image, width, height, bgra_to_rgba(rgba) if key in bgra_keys else rgba)
                    state.error = ""
                    break
                except Exception as e:  # noqa: BLE001 - report and go on with the other overlays
                    state.error = f"image: {type(e).__name__} {e}".rstrip()
                    time.sleep(0.2)
        states.append(state)
    return states


def describe(state: OverlayState, label: str = "") -> str:
    name = f"{state.key} ({label})" if label else state.key
    if not state.found:
        return f"{name}: not found"
    text = f"{name}: visible={state.visible} alpha={state.alpha:.2f} width={state.width_m:.2f} m"
    if state.width_px:
        text += f" image={state.width_px}x{state.height_px} drawn_px={state.drawn_px} -> {state.image}"
    if state.error:
        text += f" ({state.error})"
    if state.placement is not None:
        from . import placement

        text += "\n  " + placement.describe(placement.Placement(**state.placement))
    return text
