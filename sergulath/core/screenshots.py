"""SteamVR's screenshot API (IVRScreenshots). It captures the scene app's frame only: overlays
(captions, the dashboard) and the headset's own screens are not in it."""

from __future__ import annotations

import time
from pathlib import Path


def take_stereo(openvr, folder: Path, name: str, wait_s: float = 10.0) -> list[Path]:
    """Ask the compositor for a stereo screenshot; return the files that appeared."""
    # The compositor writes the files from its own process, so the path must be absolute.
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    preview, vr = folder / f"{name}_preview", folder / f"{name}_vr"
    before = set(folder.iterdir())
    openvr.VRScreenshots().takeStereoScreenshot(str(preview), str(vr))
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        new = set(folder.iterdir()) - before
        if all(any(p.name.startswith(prefix) for p in new) for prefix in (preview.name, vr.name)):
            time.sleep(0.5)  # let the last file finish writing
            break
        time.sleep(0.2)
    return sorted(set(folder.iterdir()) - before)
