"""sergulath frame: read the headset's own SteamVR over SSH (a Steam Frame runs SteamVR on board).

  frame overlays [--key KEY ...] [--save DIR]
      Lists the headset's own system overlays: found, shown, alpha, and the image size where
      the overlay holds raw pixels. The "outside the play space" screen is drawn on the headset
      from `system.systemui` (its button) and `XRService-CameraOverlay` (the camera view), so
      `visible` on those tells whether that screen is up. Only raw-pixel overlays can be read
      as images (`system.systemui` is one); GPU-texture overlays answer image_error 24.

Needs SSH on the headset and a registered key (default ~/.ssh/id_ed25519_steamframe for
steamos@10.35.78.1). Read-only: nothing is installed or changed on the headset; temporary
images are removed after they are copied back.
"""

from __future__ import annotations

from pathlib import Path

from .. import output
from ..core import frame

NAME = "frame"
HELP = "read a standalone headset's own SteamVR over SSH (system overlays, which the PC cannot see)"

SYSTEM_OVERLAYS = [
    "system.systemui", "XRService-CameraOverlay", "system.HeadsetView", "system.standalone",
    "system.toast", "valve.steam.gamepadui.main", "valve.steam.desktopgame.0",
]


def add_arguments(parser) -> None:
    parser.add_argument("--host", default=frame.Target.host, help=f"headset address (default {frame.Target.host})")
    parser.add_argument("--user", default=frame.Target.user, help=f"user (default {frame.Target.user})")
    parser.add_argument("--identity", default=str(frame.Target.key), help="private key file")
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    o = sub.add_parser("overlays", help="list the headset's own system overlays, optionally saving readable images")
    o.add_argument("--key", action="append", help="overlay key to read instead of the default system set (repeatable)")
    o.add_argument("--save", metavar="DIR", help="copy readable overlay images to DIR/<key>.png")
    output.add_format(o)


def run(args) -> int:
    target = frame.Target(args.host, args.user, Path(args.identity))
    records = frame.overlay_states(target, args.key or SYSTEM_OVERLAYS, Path(args.save) if args.save else None)
    for rec in records:
        if not rec.get("found", True) or "key" not in rec:
            text = f"{rec.get('key', '?')}: not found" if "key" in rec else str(rec)
        else:
            text = f"{rec['key']}: visible={rec['visible']} alpha={rec['alpha']:.2f}"
            text += f" image={rec['width']}x{rec['height']} drawn_px={rec['drawn_px']}" if not rec["image_error"] \
                else f" image_error={rec['image_error']}"
            if rec.get("file"):
                text += f" -> {rec['file']}"
        output.emit(rec, text, args.format)
    return output.OK if any(r.get("found") for r in records) else output.NOT_MET
