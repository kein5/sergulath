"""sergulath frame: read the headset's own SteamVR over SSH (a Steam Frame runs SteamVR on board).

  frame overlays [--key KEY ...] [--save DIR]
      Lists the headset's own system overlays: found, shown, alpha, and the image size where
      the overlay holds raw pixels. The "outside the play space" screen is drawn on the headset
      from `system.systemui` (its button) and `XRService-CameraOverlay` (the camera view), so
      `visible` on those tells whether that screen is up. Only raw-pixel overlays can be read
      as images (`system.systemui` is one); GPU-texture overlays answer image_error 24.

  frame shot --out PNG --password-file FILE
      Saves what the headset's panel shows: both eyes side by side, after the lens correction,
      with the screens the headset draws itself (the outside-the-play-area camera view). One
      step needs root (the panel framebuffer's memory), so the capture runs once under sudo.
      The password is read from FILE (a one-time file written by the session tool's secret
      prompt) and goes only to that sudo, on the ssh command's standard input. It is never
      printed, put on a command line, or written on the headset. Without --password-file the
      capture runs as the normal user and stops before that step (a check that everything else
      is in place). See docs/checks/frame-shot.md.

Needs SSH on the headset and a registered key (default ~/.ssh/id_ed25519_steamframe for
steamos@10.35.78.1). Read-only: nothing is installed or changed on the headset; temporary
files are removed after they are copied back.
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
    s = sub.add_parser("shot", help="save what the headset's panel shows (needs sudo once, password from a file)")
    s.add_argument("--out", required=True, help="PNG file to write")
    s.add_argument("--password-file", metavar="FILE",
                   help="file holding the headset user's password for sudo (first line); without it, a dry run "
                        "that stops before the step needing root")
    output.add_format(s)


def _shot(args, target) -> int:
    password = None
    if args.password_file:
        try:
            with open(args.password_file, encoding="utf-8") as f:
                password = f.readline().rstrip("\r\n")
        except OSError as e:
            output.note(f"cannot read the password file ({e.strerror})")
            return output.USAGE
        if not password:
            output.note("the password file is empty")
            return output.USAGE
    result = frame.scanout_shot(target, Path(args.out), password)
    password = None
    steps = {r.get("step"): r for r in result["records"]}
    plane, handle, read = steps.get("plane"), steps.get("handle"), steps.get("read")
    record = {"file": result["file"], "seconds": result["seconds"], "root": bool(plane and plane.get("root")),
              "plane": plane, "handle_ok": bool(handle and handle.get("ok")), "read": read}
    if result["file"] and read and read.get("ok"):
        text = (f"{result['file']}: {read['width']}x{read['height']}, lit {read['lit_share']:.0%} of sampled pixels, "
                f"{result['seconds']} s in all (read {read.get('read_ms')} ms, PNG {read.get('png_ms')} ms on the headset)")
        output.emit(record, text, args.format)
        return output.OK
    if plane is None:
        # Nothing ran: sudo refused (wrong password) or ssh failed. stderr never holds the password.
        refused = bool(args.password_file) and ("incorrect password" in result["stderr"]
                                                or "Sorry" in result["stderr"])
        output.note("sudo refused the password" if refused else f"the capture did not run: {result['stderr'] or result['exit']}")
        output.emit(record, "no capture", args.format)
        return output.FAILED
    if not plane.get("ok"):
        output.note(plane.get("error", "no plane is scanning out"))
        output.emit(record, "no capture", args.format)
        return output.NOT_MET
    if handle and not handle.get("ok"):
        if args.password_file:
            output.note(handle.get("error", "no handle"))
            output.emit(record, "no capture", args.format)
            return output.FAILED
        text = (f"dry run: panel {plane['width']}x{plane['height']} {plane['format']} (modifier {plane['modifier']}) "
                f"found; the capture stops before the step needing root, as expected without --password-file")
        output.emit(record, text, args.format)
        return output.NOT_MET
    output.note(f"the capture failed: {read or result['stderr']}")
    output.emit(record, "no capture", args.format)
    return output.FAILED


def run(args) -> int:
    target = frame.Target(args.host, args.user, Path(args.identity))
    if args.action == "shot":
        return _shot(args, target)
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
