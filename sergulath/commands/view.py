"""sergulath view: what the headset shows, as far as the PC can see it.

  view open        open the VR View window (the compositor's mirror) unattended: snapshot, set
                   steamvr.showMirrorView=true and restart SteamVR through Steam. Nothing changes
                   when the window is already open.
  view close       restore that snapshot and restart SteamVR (only what `view open` changed)
  view save        the VR View window: scene and overlays; with --headset, the virtual headset's
                   window (`sergulath hmd on`), which needs no window opened by hand; with
                   --headset --overlay KEY, only where that overlay is in one eye
                   (docs/checks/headset-view.md)
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
from ..core import driver, headset_view, host, lock, placement, png, screenshots, settings, steamvr, window_capture

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
    save.add_argument("--overlay", metavar="KEY",
                      help="with --headset: save only the part of one eye where this overlay is (from its pose and "
                           "size, after a calibration with a probe overlay); the whole window is saved next to it")
    save.add_argument("--eye", choices=("left", "right"), default="left", help="with --overlay: which eye (default left)")
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
    lock.hold(LABEL, snapshot)  # until `view close` restores it
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
    lock.unhold(LABEL)
    driver.start_steamvr()
    output.emit({"restored": [c.to_dict() for c in undone], "snapshot": str(snapshot)},
                f"restored {len(undone)} setting(s) from {snapshot.name}; SteamVR restarted", args.format)
    return output.OK


def _save_overlay(args, window) -> int:
    """The part of the virtual headset's window where one overlay is."""
    capture = lambda: window_capture.capture_client(window.hwnd)  # noqa: E731
    with steamvr.background_client() as openvr:
        overlay = openvr.VROverlay()
        try:
            handle = overlay.findOverlay(args.overlay)
        except Exception:  # noqa: BLE001 - pyopenvr raises on an unknown key
            output.note(f"no overlay {args.overlay!r}")
            return output.NOT_MET
        visible = bool(overlay.isOverlayVisible(handle))
        world = placement.world_matrix(openvr, overlay, handle)
        if world is None:
            output.note(f"{args.overlay}: its position is not known now (it follows a device without a pose, "
                        "or is not placed in the room)")
            return output.NOT_MET
        width_m, height_m = headset_view.overlay_size(openvr, overlay, handle)
        head = openvr.VRSystem().getDeviceToAbsoluteTrackingPose(openvr.TrackingUniverseStanding, 0, 1)[0]
        head_distance = placement.distance(placement.translation(world),
                                           placement.translation(placement.to_rows(head.mDeviceToAbsoluteTracking))) \
            if head.bPoseIsValid else None
        # The probe goes in front of the overlay: a nearer overlay hides a farther one.
        maps = headset_view.calibrate(openvr, capture, depth=headset_view.probe_depth(head_distance))
        time.sleep(1.0)  # the window shows the probe for a few frames after it is destroyed
        eye_from_world, raw = headset_view.eye_views(openvr)[args.eye]
        width, height, rgba = capture()
    eye_map = maps[args.eye]
    area = headset_view.eye_area(eye_map)
    area = [max(0.0, area[0]), max(0.0, area[1]), min(float(width), area[2]), min(float(height), area[3])]
    crop = headset_view.crop_box(headset_view.corners(world, width_m, height_m), eye_from_world, raw, eye_map, area)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    whole = out.with_name(out.stem + "-window" + out.suffix)
    png.write_rgba(whole, width, height, rgba)
    record = {"file": None, "window_file": str(whole), "overlay": args.overlay, "visible": visible, "eye": args.eye,
              "size_m": [round(width_m, 4), round(height_m, 4)], "eye_area": [round(v, 1) for v in area],
              "box": None, "inside": 0.0, "blank": None}
    if crop is None or crop.inside == 0.0:
        text = f"{args.overlay}: not in the {args.eye} eye's view" + (" (behind the head)" if crop is None else "")
        output.emit(record, text + f"; whole window -> {whole}", args.format)
        return output.NOT_MET
    cw, ch, part = headset_view.cut(width, rgba, crop.box)
    png.write_rgba(out, cw, ch, part)
    blank = window_capture.is_blank(part)
    record.update({"file": str(out), "box": crop.box, "inside": crop.inside, "blank": blank,
                   "width": cw, "height": ch})
    text = (f"{out}: {args.overlay} in the {args.eye} eye, {cw}x{ch} px at {crop.box[:2]}"
            + (f", {crop.inside:.0%} of it in view" if crop.inside < 1 else "")
            + (" (blank)" if blank else "") + ("" if visible else " (the overlay is hidden)"))
    output.emit(record, text, args.format)
    return output.NOT_MET if blank or not visible else output.OK


def _save(args) -> int:
    if args.overlay and not args.headset:
        output.note("--overlay works with --headset (the virtual headset's window) only")
        return output.USAGE
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
    if args.overlay:
        return _save_overlay(args, window)
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
    if args.action != "screenshot":  # before anything changes: open restarts SteamVR, then looks for the window
        host.require_windows(f"view {args.action}", window_capture.WINDOWS_ONLY)
    return {"open": _open, "close": _close, "save": _save, "screenshot": _screenshot}[args.action](args)
