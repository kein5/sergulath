"""Read an app's overlays: whether each exists and is shown, and its pixels (read-only)."""

from __future__ import annotations

import ctypes
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

    def to_dict(self) -> dict:
        data = asdict(self)
        data["image"] = str(self.image) if self.image else None
        return data


def capture(openvr, keys: list[str], out_dir: Path | None) -> list[OverlayState]:
    """Look up each key; with `out_dir`, also save each overlay's pixels as `<key>.png`."""
    overlay = openvr.IVROverlay()
    states = []
    buffer = (ctypes.c_uint8 * _MAX_BYTES)() if out_dir else None
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
        if buffer is not None:
            try:
                width, height = overlay.getOverlayImageData(handle, buffer, _MAX_BYTES)[:2]
                rgba = bytes(buffer[: width * height * 4])
                alpha = rgba[3::4]
                state.width_px, state.height_px = width, height
                state.drawn_px = len(alpha) - alpha.count(0)
                state.image = out_dir / f"{key}.png"
                png.write_rgba(state.image, width, height, rgba)
            except Exception as e:  # noqa: BLE001 - report and go on with the other overlays
                state.error = f"image: {e}"
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
    return text
