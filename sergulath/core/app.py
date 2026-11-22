"""The app under check: find its process and bring its main window up.

A tray-resident app (ASTAROT with the tray companion on) has no main window to press buttons in.
Starting its executable a second time hands over to the running instance, which shows its
main window; it does not start a second app.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from . import window_capture


def running_pid(process: str) -> int | None:
    for line in subprocess.run(["tasklist", "/fo", "csv", "/nh", "/fi", f"IMAGENAME eq {process}"],
                               capture_output=True, text=True, check=True).stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == process.lower():
            return int(parts[1])
    return None


def executable(profile_app: dict) -> Path:
    return Path(os.path.expandvars(profile_app["exe"]))


def main_window_shown(pid: int, title: str) -> bool:
    return any(w.pid == pid and w.title == title and w.visible and not w.minimized for w in window_capture.windows())


def show_main_window(profile_app: dict, wait_s: float = 10.0) -> int:
    """Bring the running app's main window up; return its pid. Raises if the app does not run."""
    pid = running_pid(profile_app["process"])
    if pid is None:
        raise RuntimeError(f"{profile_app['process']} is not running; start the app first")
    title = profile_app["main_window_title"]
    if main_window_shown(pid, title):
        return pid
    exe = executable(profile_app)
    subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if main_window_shown(pid, title):
            return pid
        time.sleep(0.3)
    raise RuntimeError(f"the main window '{title}' did not appear within {wait_s:.0f} s")
