"""sergulath view: what the headset shows, as far as the PC can see it.

  view save        the VR View window (the compositor's mirror): scene and overlays; with
                   --headset, the virtual headset's window (`sergulath hmd on`), which needs no
                   window opened by hand
  view screenshot  SteamVR's screenshot API: the scene app's frame only, without overlays

Neither includes screens a standalone headset draws itself, such as a Steam Frame's
"outside the play area" camera screen: those never pass through the PC's compositor.
"""

from __future__ import annotations

from pathlib import Path

from .. import output
from ..core import png, screenshots, steamvr, window_capture

NAME = "view"
HELP = "save what the headset shows: the VR View (scene and overlays) or a SteamVR screenshot (scene only)"

VR_VIEW_TITLES = ("VR View", "VRビュー")


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
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


def _find_vr_view(titles) -> window_capture.Window | None:
    for w in window_capture.windows("vrmonitor.exe"):
        if w.visible and w.title in titles:
            return w
    return None


def _find_headset_window() -> window_capture.Window | None:
    for w in window_capture.windows("vrcompositor.exe"):
        if w.visible and w.title == "Headset Window":
            return w
    return None


def _save(args) -> int:
    titles = tuple(args.title or VR_VIEW_TITLES)
    window = _find_headset_window() if args.headset else _find_vr_view(titles)
    if window is None and args.headset:
        output.note("no virtual headset window: run `sergulath hmd on` first")
        return output.NOT_MET
    if window is None:
        output.note("the VR View window is not open; open it from the SteamVR status window's menu")
        return output.NOT_MET
    if window.minimized:
        output.note("the VR View window is minimized; restore it first")
        return output.NOT_MET
    width, height, rgba = window_capture.capture_client(window.hwnd)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    png.write_rgba(out, width, height, rgba)
    blank = window_capture.is_blank(rgba)
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
    return _save(args) if args.action == "save" else _screenshot(args)
