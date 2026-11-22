"""sergulath caption: send the app's sample caption and check that it renders.

  caption sample start [--line one|multi]
      Brings the running app's main window up (a second start of its executable hands over to
      the running app), opens the captions settings page and starts the app's own sample,
      which it sends once a second while that page stays open. Changes no setting.
  caption sample stop
      Stops the sample and leaves the settings page.
  caption check [--line one|multi] [--save DIR] [--view FILE]
      Reads the caption overlay: shown, not transparent and drawn. With --view, also saves the
      VR View (the caption is in it only while the headset tracks).
"""

from __future__ import annotations

from pathlib import Path

from .. import output, profiles
from ..core import app, overlay, png, steamvr, uia, window_capture

NAME = "caption"
HELP = "send the app's sample caption, and check that the caption overlay renders"


def add_arguments(parser) -> None:
    parser.add_argument("--profile", default="astarot", help="app profile (default astarot)")
    parser.add_argument("--lang", default="ja", help="the app's UI language in the profile (default ja)")
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    sample = sub.add_parser("sample", help="start or stop the app's caption sample")
    sample.add_argument("state", choices=("start", "stop"))
    sample.add_argument("--line", choices=("one", "multi"), default="one", help="which caption (default one)")
    output.add_format(sample)
    check = sub.add_parser("check", help="check that the caption overlay is shown and drawn")
    check.add_argument("--line", choices=("one", "multi"), default="one", help="which caption (default one)")
    check.add_argument("--save", metavar="DIR", help="save the caption overlay's pixels as DIR/<key>.png")
    check.add_argument("--view", metavar="FILE", help="also save the VR View as this PNG file")
    output.add_format(check)


def _sample(args, profile) -> int:
    labels = profile["ui"][args.lang]
    pid = app.show_main_window(profile["app"])
    title = profile["app"]["main_window_title"]
    if args.state == "start":
        steps = [
            uia.Step(labels["sample_stop"], optional=True, timeout=0.5),  # already sending: stop first
            uia.Step(labels["open_settings"], optional=True, timeout=2),  # absent when settings are open
            uia.Step(labels["page"]),
            uia.Step(labels["details_prefix"], prefix=True),
            uia.Step(labels["tab_one" if args.line == "one" else "tab_multi"]),
            uia.Step(labels["sample_start"]),
            uia.Step(labels["sample_stop"], check_only=True),
        ]
    else:
        steps = [uia.Step(labels["sample_stop"], optional=True, timeout=2), uia.Step(labels["back"], optional=True, timeout=2)]
    result = uia.press(pid, title, steps)
    text = ("sample " + ("sending" if args.state == "start" else "stopped")) if result.get("ok") else f"failed: {result}"
    output.emit({"action": args.state, "line": args.line, **result}, text, args.format)
    return output.OK if result.get("ok") else output.NOT_MET


def _check(args, profile) -> int:
    key = profile["captions"][args.line]
    with steamvr.background_client() as openvr:
        state = overlay.capture(openvr, [key], Path(args.save) if args.save else None, read_pixels=True)[0]
    rendered = state.found and state.visible and state.alpha > 0 and state.drawn_px > 0
    record = {"line": args.line, "rendered": rendered, **state.to_dict()}
    if args.view:
        window = next((w for w in window_capture.windows("vrmonitor.exe")
                       if w.visible and not w.minimized and w.title in ("VR View", "VRビュー")), None)
        if window:
            width, height, rgba = window_capture.capture_client(window.hwnd)
            Path(args.view).parent.mkdir(parents=True, exist_ok=True)
            png.write_rgba(Path(args.view), width, height, rgba)
            record["view"] = {"file": args.view, "blank": window_capture.is_blank(rgba)}
        else:
            record["view"] = {"file": None, "error": "the VR View window is not open"}
    text = f"{args.line}-line caption ({key}): " + ("rendered" if rendered else "not rendered") + \
        f" (visible={state.visible} alpha={state.alpha:.2f} drawn_px={state.drawn_px})"
    if "view" in record:
        text += f"; VR View -> {record['view'].get('file') or record['view'].get('error')}"
    output.emit(record, text, args.format)
    return output.OK if rendered else output.NOT_MET


def run(args) -> int:
    profile = profiles.load(args.profile)
    return _sample(args, profile) if args.action == "sample" else _check(args, profile)
