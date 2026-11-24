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

from . import host, window_capture


def process_names(profile_app: dict) -> list[str]:
    """Current process name first, followed by any older names (0.6.x: vrct-vox.exe)."""
    names = [profile_app["process"]]
    for legacy in profile_app.get("legacy_process", []):
        if legacy not in names:
            names.append(legacy)
    return names


def running_pid(process: str) -> int | None:
    found = host.pids(process)
    return found[0] if found else None


def running_pid_for(profile_app: dict) -> int | None:
    """Return the first running PID for the app under its current or legacy process names."""
    for name in process_names(profile_app):
        pid = running_pid(name)
        if pid is not None:
            return pid
    return None


def executable(profile_app: dict) -> Path:
    """Primary executable path, falling back to an existing legacy path when 0.6.x is installed."""
    primary = Path(os.path.expandvars(profile_app["exe"]))
    if primary.is_file():
        return primary
    for raw in profile_app.get("legacy_exe", []):
        candidate = Path(os.path.expandvars(raw))
        if candidate.is_file():
            return candidate
    return primary


def main_window_shown(pid: int, title: str) -> bool:
    return any(w.pid == pid and w.title == title and w.visible and not w.minimized for w in window_capture.windows())


def show_main_window(profile_app: dict, wait_s: float = 10.0) -> int:
    """Bring the running app's main window up; return its pid. Raises if the app does not run."""
    pid = running_pid_for(profile_app)
    if pid is None:
        names = " or ".join(process_names(profile_app))
        raise RuntimeError(f"{names} is not running; start the app first")
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
