"""What differs between Windows and Linux: the process list, Steam's folders, starting Steam and
ending processes. The rest of the package asks here instead of calling `tasklist` or the registry.

Process names keep the Windows spelling on both systems (lower case, ending in `.exe`), so
callers compare one name: on Linux `vrserver` is listed as `vrserver.exe`.

Features that only exist on Windows (UI Automation, WASAPI loopback, window capture, the virtual
controller DLL) call `require_windows`, which raises `NotSupportedHere`. The command line turns
that into a one-line message and exit code 1, with `"status": "unsupported"` in JSON output.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"


def system_name() -> str:
    if WINDOWS:
        return "Windows"
    if sys.platform.startswith("linux"):
        return "Linux"
    return sys.platform


class NotSupportedHere(RuntimeError):
    """A feature this operating system does not have."""

    def __init__(self, feature: str, why: str):
        self.feature = feature
        self.why = why
        super().__init__(f"{feature} is not available on {system_name()}: {why}")


def require_windows(feature: str, why: str) -> None:
    if not WINDOWS:
        raise NotSupportedHere(feature, why)


# ---- processes -----------------------------------------------------------------------------

def normalize(name: str) -> str:
    """A process name in the form the package compares: lower case, ending in `.exe`."""
    name = name.strip().lower()
    return name if name.endswith(".exe") else name + ".exe"


def _tasklist() -> list[tuple[str, int]]:
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    found = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            found.append((parts[0].lower(), int(parts[1])))
    return found


def _linux_name(proc: Path) -> str | None:
    """The program's file name: from /proc/PID/exe when readable, else argv[0], else comm (which
    the kernel cuts to 15 characters)."""
    try:
        target = os.readlink(proc / "exe")
        return os.path.basename(target.removesuffix(" (deleted)"))
    except OSError:
        pass
    try:
        argv0 = (proc / "cmdline").read_bytes().split(b"\0", 1)[0].decode("utf-8", "replace")
        if argv0:
            return os.path.basename(argv0.split(" ", 1)[0])
    except OSError:
        pass
    try:
        return (proc / "comm").read_text(encoding="utf-8", errors="replace").strip() or None
    except OSError:
        return None


def _proc_table(root: Path = Path("/proc")) -> list[tuple[str, int]]:
    found = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        name = _linux_name(Path(entry.path))
        if name:
            found.append((normalize(name), int(entry.name)))
    return found


def process_table() -> list[tuple[str, int]]:
    """(name, pid) of every running process, names normalized (see `normalize`)."""
    return _tasklist() if WINDOWS else _proc_table()


def running_names() -> set[str]:
    return {name for name, _ in process_table()}


def pids(name: str) -> list[int]:
    want = normalize(name)
    return [pid for found, pid in process_table() if found == want]


def parent_map(root: Path = Path("/proc")) -> dict[int, int]:
    """pid -> parent pid on Linux, read from /proc/PID/stat."""
    parents = {}
    try:
        entries = list(os.scandir(root))
    except OSError:
        return parents
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (Path(entry.path) / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # The second field is the name in parentheses and may hold spaces: split after its last ')'.
        rest = stat[stat.rfind(")") + 2:].split()
        if len(rest) > 1 and rest[1].isdigit():
            parents[int(entry.name)] = int(rest[1])
    return parents


def tree(root_pid: int, parents: dict[int, int]) -> list[int]:
    """[root_pid, ...descendants] from a pid -> parent map, or [] when root_pid is not in it."""
    if root_pid not in parents:
        return []
    children: dict[int, list[int]] = {}
    for pid, ppid in parents.items():
        children.setdefault(ppid, []).append(pid)
    found, stack = [root_pid], [root_pid]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in found:
                found.append(child)
                stack.append(child)
    return found


def process_path(pid: int) -> Path | None:
    """The executable of a running process, or None when it cannot be read."""
    if WINDOWS:
        from . import window_capture

        name = window_capture.full_process_path(pid)
        return Path(name) if name else None
    try:
        return Path(os.readlink(f"/proc/{pid}/exe").removesuffix(" (deleted)"))
    except OSError:
        return None


def end(names: list[str], force: bool = False, with_children: bool = False) -> None:
    """Ask processes to end by name (Windows: taskkill; Linux: SIGTERM, or SIGKILL with force)."""
    if WINDOWS:
        for name in names:
            subprocess.run(["taskkill", *(["/F"] if force else []), *(["/T"] if with_children else []), "/IM", name],
                           capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return
    wanted = {normalize(n) for n in names}
    targets = [pid for name, pid in _proc_table() if name in wanted]
    if with_children and targets:
        parents = parent_map()
        targets = list(dict.fromkeys(p for root in targets for p in tree(root, parents) or [root]))
    for pid in targets:
        try:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


# ---- Steam -----------------------------------------------------------------------------------

def _registry_value(name: str) -> str | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            return winreg.QueryValueEx(key, name)[0] or None
    except (ImportError, OSError):
        return None


def linux_steam_roots(home: Path | None = None) -> list[Path]:
    """Where Steam keeps its folder on Linux, most usual first (native, then Flatpak)."""
    home = home or Path.home()
    return [home / ".steam" / "steam", home / ".local" / "share" / "Steam",
            home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam"]


def steam_root() -> Path | None:
    """Steam's own folder (holding logs/ and config/), or None when not found."""
    if WINDOWS:
        value = _registry_value("SteamPath")
        return Path(value) if value else None
    for candidate in linux_steam_roots():
        if candidate.is_dir():
            return candidate.resolve()
    return None


def steam_launcher() -> list[str] | None:
    """The command that runs Steam (to hand it `-applaunch`), or None when Steam is not found."""
    if WINDOWS:
        value = _registry_value("SteamExe")
        return [value] if value and Path(value).is_file() else None
    found = shutil.which("steam")
    return [found] if found else None


def steam_not_found_hint() -> str:
    return ("HKCU\\Software\\Valve\\Steam\\SteamExe" if WINDOWS else "`steam` on PATH")
