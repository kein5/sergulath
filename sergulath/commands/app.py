"""sergulath app: the app under check (from its profile): status, start, stop, press.

  app status                       Whether it runs, its process ids and executable.
  app start [--exe FILE] [--settings-dir DIR [--fresh] [--set KEY=VALUE ...]]
                                   Start it. With --settings-dir it runs with a settings folder of
                                   its own (ASTAROT_CONFIG_DIR / VRCT_CONFIG_DIR) and a WebView2
                                   folder inside it, so a check never touches the user's settings
                                   (ground rule 4). --fresh writes DIR/config.json from the
                                   profile's [app.check_settings] (plus --set) first. Waits until
                                   its main process runs.
  app stop                         End every process of the app, with its WebView2 children.
  app type --field NAME --text TEXT [--then BUTTON ...]
                                   Set a field's text (such as the message box), then press buttons.
  app press NAME [NAME ...] [--prefix]
                                   Press buttons or check boxes in its main window, in order, by
                                   accessible name (Windows UI Automation; no mouse, no focus).
  app home [--lang ja]             Bring it back to its main page: stop a caption sample that is
                                   still sending, press "back" while there is one, and check that
                                   the main page's settings button is there (the profile's
                                   [ui.LANG] labels). A suite runs it after a failed case, so a
                                   case that stopped half-way on the settings page does not take
                                   the next cases down with it. Exit 1 when the main page is not
                                   reached.

Pressing can change the app's settings: do it on an app started with --settings-dir.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from .. import output, profiles
from ..core import app as app_core
from ..core import settings as settings_core
from ..core import host, uia

NAME = "app"
HELP = "the app under check: status, start (with its own settings folder), stop, press buttons"


def add_arguments(parser) -> None:
    parser.add_argument("--profile", default="astarot", help="app profile (default astarot)")
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    status = sub.add_parser("status", help="whether the app runs")
    output.add_format(status)
    start = sub.add_parser("start", help="start the app")
    start.add_argument("--exe", metavar="FILE", help="executable to start instead of the profile's")
    start.add_argument("--settings-dir", metavar="DIR", help="run with this settings folder instead of the user's")
    start.add_argument("--fresh", action="store_true", help="write DIR/config.json from the profile's check settings")
    start.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="a setting for --fresh (JSON value)")
    start.add_argument("--wait", type=float, default=20.0, help="seconds to wait for it (default 20)")
    output.add_format(start)
    stop = sub.add_parser("stop", help="end the app")
    output.add_format(stop)
    press = sub.add_parser("press", help="press buttons or check boxes by accessible name")
    press.add_argument("names", nargs="+", metavar="NAME")
    press.add_argument("--prefix", action="store_true", help="match names that start with each NAME")
    press.add_argument("--timeout", type=float, default=8.0, help="seconds to wait for each control (default 8)")
    output.add_format(press)
    home = sub.add_parser("home", help="bring the app back to its main page (a suite's reset between cases)")
    home.add_argument("--lang", default="ja", help="the app's UI language in the profile (default ja)")
    output.add_format(home)
    typing = sub.add_parser("type", help="set the text of a field by its accessible name")
    typing.add_argument("--field", required=True, help="the field's accessible name")
    typing.add_argument("--text", required=True)
    typing.add_argument("--then", action="append", default=[], metavar="NAME", help="press this button after (repeatable)")
    output.add_format(typing)


def home_steps(labels: dict) -> list:
    """Back to the main page from wherever a case stopped: every press is optional, then the main
    page's settings button must be there. Two "back" presses cover a page opened from a page."""
    return [uia.Step(labels["sample_stop"], optional=True, timeout=0.5),
            uia.Step(labels["back"], optional=True, timeout=1.5),
            uia.Step(labels["back"], optional=True, timeout=0.5),
            uia.Step(labels["open_settings"], check_only=True, timeout=5)]


def pids(process: str) -> list[int]:
    return host.pids(process)


def _all_pids(names: list[str]) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for name in names:
        for pid in pids(name):
            found.append((name, pid))
    return found


def run(args) -> int:
    profile = profiles.load(args.profile)
    info = profile["app"]
    names = app_core.process_names(info)
    process = info["process"]
    if args.action == "status":
        matches = _all_pids(names)
        running = [pid for _, pid in matches]
        active_name = matches[0][0] if matches else process
        output.emit({"running": bool(running), "pids": running, "process": active_name, "exe": str(app_core.executable(info))},
                    f"{active_name}: " + (f"running ({', '.join(map(str, running))})" if running else "not running"), args.format)
        return output.OK if running else output.NOT_MET
    if args.action == "stop":
        before_matches = _all_pids(names)
        before = [pid for _, pid in before_matches]
        if before_matches:
            host.end(list(dict.fromkeys(n for n, _ in before_matches)), force=True, with_children=True)
            deadline = time.monotonic() + 10
            while _all_pids(names) and time.monotonic() < deadline:
                time.sleep(0.3)
        left = [pid for _, pid in _all_pids(names)]
        output.emit({"stopped": before, "running": left}, f"stopped {len(before)}" + (f"; still running {left}" if left else ""),
                    args.format)
        return output.FAILED if left else output.OK
    if args.action in ("press", "type", "home"):
        host.require_windows(f"app {args.action}", uia.WINDOWS_ONLY)
    if args.action == "home":
        labels = profile.get("ui", {}).get(args.lang)
        if not labels:
            output.note(f"the profile has no [ui.{args.lang}] labels")
            return output.USAGE
        pid = app_core.show_main_window(info)
        result = uia.press(pid, info["main_window_title"], home_steps(labels))
        output.emit(result, "on the main page" if result.get("ok") else f"not back on the main page: {result}",
                    args.format)
        return output.OK if result.get("ok") else output.NOT_MET
    if args.action == "press":
        pid = app_core.show_main_window(info)
        result = uia.press(pid, info["main_window_title"],
                           [uia.Step(name, prefix=args.prefix, timeout=args.timeout) for name in args.names])
        output.emit(result, ("pressed " + ", ".join(args.names)) if result.get("ok") else f"failed: {result}", args.format)
        return output.OK if result.get("ok") else output.NOT_MET
    if args.action == "type":
        pid = app_core.show_main_window(info)
        steps = [uia.Step(args.field, value=args.text)] + [uia.Step(name) for name in args.then]
        result = uia.press(pid, info["main_window_title"], steps)
        output.emit(result, f"typed into {args.field}" if result.get("ok") else f"failed: {result}", args.format)
        return output.OK if result.get("ok") else output.NOT_MET
    # start
    exe = Path(args.exe).resolve() if getattr(args, "exe", None) else app_core.executable(info)
    target_process = exe.name
    env = dict(os.environ)
    if args.settings_dir:
        folder = Path(args.settings_dir).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        config = folder / "config.json"
        if args.fresh:
            values = dict(info.get("check_settings", {}))
            for pair in args.set:
                key, _, value = pair.partition("=")
                values[key] = settings_core.parse_value(value)
            config.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        env.update(
            ASTAROT_CONFIG_DIR=str(folder),
            VRCT_CONFIG_DIR=str(folder),
            WEBVIEW2_USER_DATA_FOLDER=str(folder / "webview"),
        )
    elif args.fresh or args.set:
        output.note("--fresh and --set need --settings-dir: the user's own settings are never written")
        return output.USAGE
    if not exe.is_file():
        hint = "" if host.WINDOWS else " (the profile's install path is a Windows one; pass --exe)"
        output.note(f"no executable at {exe}{hint}")
        return output.FAILED
    existing = [pid for _, pid in _all_pids(names)]
    if existing and not args.settings_dir:
        output.emit({"started": False, "pids": existing}, f"{target_process} already runs", args.format)
        return output.OK
    started = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env,
                               creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline and started.pid not in pids(target_process):
        time.sleep(0.3)
    running = started.pid in pids(target_process)
    output.emit({"started": running, "pid": started.pid, "process": target_process, "settings_dir": args.settings_dir},
                f"{'started' if running else 'did not start'} {target_process} ({started.pid})"
                + (f" with settings in {args.settings_dir}" if args.settings_dir else ""), args.format)
    return output.OK if running else output.FAILED
