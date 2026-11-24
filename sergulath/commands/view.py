"""sergulath view: what the headset shows, as far as the PC can see it.

  view open        open the VR View window (the compositor's mirror) unattended: snapshot, set
                   steamvr.showMirrorView=true and restart SteamVR through Steam. Nothing changes
                   when the window is already open.
  view close       restore that snapshot and restart SteamVR (only what `view open` changed)
  view save        the VR View window: scene and overlays; with --headset, the virtual headset's
                   window (`sergulath hmd on`), which needs no window opened by hand
  view screenshot  SteamVR's screenshot API: the scene app's frame only, without overlays

SteamVR reads showMirrorView only when it starts: changing it while SteamVR runs does not open the
window, so `view open` restarts SteamVR. While the headset is not tracking (a Steam Frame lying in
standby), the VR View is an empty grey below its toolbar; `view save` reports that as blank.

Neither includes screens a standalone headset draws itself, such as a Steam Frame's
"outside the play area" camera screen: those never pass through the PC's compositor.
"""

from __future__ import annotations

import time
from pathlib import Path

from .. import output
from ..core import driver, png, screenshots, settings, steamvr, window_capture

NAME = "view"
HELP = "open the VR View, and save what the headset shows: the VR View (scene and overlays) or a screenshot"

VR_VIEW_TITLES = ("VR View", "VRビュー")
LABEL = "view"
KEY = "steamvr.showMirrorView"


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    opener = sub.add_parser("open", help="open the VR View (snapshot, showMirrorView=true, restart SteamVR)")
    opener.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for the window (default 60)")
    output.add_format(opener)
    closer = sub.add_parser("close", help="undo `view open` (restore its snapshot, restart SteamVR)")
    output.add_format(closer)
    save = sub.add_parser("save", help="save the VR View window as PNG (needs the window open, not minimized)")
    save.add_argument("--out", required=True, help="PNG file to write")
    save.add_argument("--title", action="append", help="VR View window title to look for (default: VR View, VRビュー)")
    save.add_argument("--headset", action="store_true",
                      help="save the virtual headset's own window instead (`sergulath hmd on`): what the compositor sends "
                           "to the null headset, both eyes, at the window size `hmd on --window` set; opens by itself")
    output.add_format(save)
    shot = sub.add_parser("screenshot", help="take a SteamVR stereo screenshot (scene only) into a folder")
    shot.add_argument("--dir", required=True, help="folder for the screenshot files")
    shot.add_argument("--name", default="sergulath", help="file name prefix (default sergulath)")
    output.add_format(shot)


def _find_vr_view(titles=VR_VIEW_TITLES) -> window_capture.Window | None:
    for w in window_capture.windows("vrmonitor.exe"):
        if w.visible and w.title in titles:
            return w
    return None


def _find_headset_window() -> window_capture.Window | None:
    for w in window_capture.windows("vrcompositor.exe"):
        if w.visible and w.title == "Headset Window":
            return w
    return None


def _wait_window(timeout: float) -> window_capture.Window | None:
    deadline = time.monotonic() + timeout
    while True:
        window = _find_vr_view()
        if window or time.monotonic() >= deadline:
            return window
        time.sleep(1.0)


def _open(args) -> int:
    window = _find_vr_view()
    if window:
        output.emit({"open": True, "changed": False, "window": window.title},
                    f"the VR View is already open ({window.title}); nothing changed", args.format)
        return output.OK
    if settings.latest_snapshot(label=LABEL):
        output.note("a `view open` snapshot is still pending but the window is not open; run `sergulath view close` first")
        return output.FAILED
    if "vrserver.exe" in steamvr.running_processes():
        driver.quit_steamvr()
    done, snapshot = settings.apply({KEY: True}, settings.settings_file(), label=LABEL)
    driver.start_steamvr()
    window = _wait_window(args.timeout)
    record = {"open": window is not None, "changed": True, "snapshot": str(snapshot),
              "window": window.title if window else None}
    text = f"the VR View is open ({window.title})" if window else \
        f"SteamVR restarted with {KEY}=true but no VR View window appeared within {args.timeout:.0f} s"
    output.emit(record, text + f"; undo with `sergulath view close` (snapshot {snapshot.name})", args.format)
    return output.OK if window else output.NOT_MET


def _close(args) -> int:
    snapshot = settings.latest_snapshot(label=LABEL)
    if not snapshot:
        output.note("no `view open` snapshot is pending: nothing to undo (a VR View opened by hand stays open)")
        return output.NOT_MET
    if "vrserver.exe" in steamvr.running_processes():
        driver.quit_steamvr()
    undone = settings.restore(snapshot)
    driver.start_steamvr()
    output.emit({"restored": [c.to_dict() for c in undone], "snapshot": str(snapshot)},
                f"restored {len(undone)} setting(s) from {snapshot.name}; SteamVR restarted", args.format)
    return output.OK


def _save(args) -> int:
    titles = tuple(args.title or VR_VIEW_TITLES)
    window = _find_headset_window() if args.headset else _find_vr_view(titles)
    if window is None and args.headset:
        output.note("no virtual headset window: run `sergulath hmd on` first")
        return output.NOT_MET
    if window is None:
        output.note("the VR View window is not open; run `sergulath view open`")
        return output.NOT_MET
    if window.minimized:
        output.note("the VR View window is minimized; restore it first")
        return output.NOT_MET
    width, height, rgba = window_capture.capture_client(window.hwnd)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    png.write_rgba(out, width, height, rgba)
    blank = window_capture.is_blank(rgba) if args.headset else window_capture.vr_view_is_blank(rgba, width, height)
    record = {"file": str(out), "width": width, "height": height, "blank": blank, "window": window.title}
    text = f"{out}: {width}x{height}" + (" (blank: the headset may have lost tracking or be in standby)" if blank else "")
    output.emit(record, text, args.format)
    return output.NOT_MET if blank else output.OK


def _screenshot(args) -> int:
    with steamvr.background_client() as openvr:
        files = screenshots.take_stereo(openvr, Path(args.dir), args.name)
    for f in files:
        output.emit({"file": str(f)}, str(f), args.format)
    return output.OK if files else output.NOT_MET


def run(args) -> int:
    return {"open": _open, "close": _close, "save": _save, "screenshot": _screenshot}[args.action](args)
