"""What SteamVR is doing on this PC, read from its processes and its own log (no OpenVR client)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import host

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
    found: dict[str, int | None] = {n: None for n in names}
    for name, pid in host.process_table():
        if name in found and found[name] is None:
            found[name] = pid
    return found


def process_path(pid: int) -> Path | None:
    return host.process_path(pid)


def logs_folder(vrserver_exe: Path) -> Path | None:
    """Steam's logs folder, found by walking up from vrserver (…/steamapps/common/SteamVR/bin/win64
    or bin/linux64)."""
    for parent in vrserver_exe.parents:
        candidate = parent / "logs" / "vrserver.txt"
        if candidate.exists():
            return candidate.parent
    return None


def steam_logs_folder() -> Path | None:
    """Steam's logs folder under Steam's own folder (Windows: HKCU\\Software\\Valve\\Steam\\SteamPath;
    Linux: ~/.steam/steam and the other usual places). Needed when SteamVR's library sits
    elsewhere than Steam itself (another library folder, or a linked folder)."""
    steam = host.steam_root()
    if steam is None:
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
    folder = (logs_folder(exe) if exe else None) or steam_logs_folder()
    if folder:
        log = folder / "vrserver.txt"
        status.log = str(log)
        status.version = version_from_log(log.read_text(encoding="utf-8", errors="replace"))
    return status
