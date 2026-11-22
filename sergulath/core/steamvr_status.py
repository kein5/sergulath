"""What SteamVR is doing on this PC, read from its processes and its own log (no OpenVR client)."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import window_capture

PROCESSES = ("vrserver.exe", "vrcompositor.exe", "vrmonitor.exe", "vrdashboard.exe", "vrstartup.exe")
_VERSION = re.compile(r"vrserver (\d+(?:\.\d+)+) startup")


@dataclass
class Status:
    processes: dict[str, int | None] = field(default_factory=dict)  # name -> pid
    running: bool = False  # vrserver and vrcompositor both run
    vrmonitor: bool = False  # the status window runs (some apps detect SteamVR only by it)
    version: str | None = None
    log: str | None = None

    def to_dict(self) -> dict:
        return {"running": self.running, "vrmonitor": self.vrmonitor, "version": self.version,
                "processes": self.processes, "log": self.log}


def process_pids(names=PROCESSES) -> dict[str, int | None]:
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True).stdout
    found: dict[str, int | None] = {n: None for n in names}
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower() in found and found[parts[0].lower()] is None:
            found[parts[0].lower()] = int(parts[1])
    return found


def process_path(pid: int) -> Path | None:
    name = window_capture.full_process_path(pid) if window_capture.kernel32 else None
    return Path(name) if name else None


def logs_folder(vrserver_exe: Path) -> Path | None:
    """Steam's logs folder, found by walking up from vrserver.exe (…/steamapps/common/SteamVR/bin/win64)."""
    for parent in vrserver_exe.parents:
        candidate = parent / "logs" / "vrserver.txt"
        if candidate.exists():
            return candidate.parent
    return None


def steam_logs_from_registry() -> Path | None:
    """Steam's logs folder from HKCU\\Software\\Valve\\Steam\\SteamPath. Needed when SteamVR's
    library sits elsewhere than Steam itself (another library folder, or a linked folder)."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except (ImportError, OSError):
        return None
    folder = steam / "logs"
    return folder if (folder / "vrserver.txt").exists() else None


def version_from_log(text: str) -> str | None:
    """The last `vrserver <version> startup` line's version."""
    found = _VERSION.findall(text)
    return found[-1] if found else None


def read_status() -> Status:
    status = Status(processes=process_pids())
    status.running = bool(status.processes["vrserver.exe"] and status.processes["vrcompositor.exe"])
    status.vrmonitor = bool(status.processes["vrmonitor.exe"])
    pid = status.processes["vrserver.exe"]
    exe = process_path(pid) if pid else None
    folder = (logs_folder(exe) if exe else None) or steam_logs_from_registry()
    if folder:
        log = folder / "vrserver.txt"
        status.log = str(log)
        status.version = version_from_log(log.read_text(encoding="utf-8", errors="replace"))
    return status
