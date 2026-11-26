"""One SteamVR, one user of it at a time: a lease shared by every sergulath on this PC.

Commands that start, quit or restart SteamVR, or switch its headset or drivers, run inside
`steamvr(...)`. The lease lives in a per-user state folder, not in the checkout, so two sessions
working from different checkouts see each other. It belongs to an *owner*, the working
folder (or SERGULATH_OWNER): child commands of a suite, or the `hmd off` that follows an `hmd on`
from the same folder, share it; another folder is refused with exit code 4 and who holds it.

A lease is kept while either
- a process of the owner is inside a locked command (its pid is alive), or
- the owner left SteamVR changed on purpose (`hold`): `hmd on`, `view open` and `chaperone
  apply` hold it until `hmd off`, `view close` or `chaperone restore`. A hold names its snapshot
  file; once that snapshot is restored (renamed) or gone, the hold no longer counts.

A lease left by a session that ended without undoing its change can be released with
`sergulath lock release --force` after checking the other session is really gone.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

LEASE_NAME = "steamvr-lease.json"
GUARD_WAIT_S = 5.0
GUARD_STALE_S = 30.0


class Busy(RuntimeError):
    """Another owner is using SteamVR."""

    def __init__(self, lease: dict):
        self.lease = lease
        holds = ", ".join(h["label"] for h in lease.get("holds", [])) or "none"
        super().__init__(
            f"SteamVR is in use by another sergulath (owner {lease.get('owner')}, command "
            f"{lease.get('command')!r} since {lease.get('since')}, running pids {lease.get('pids') or 'none'}, "
            f"holds {holds}); nothing was changed. Wait for it, or undo its change there "
            "(`sergulath hmd off`, `view close`, `chaperone restore`); if that session is gone, "
            "`sergulath lock release --force`")


def state_dir() -> Path:
    if os.environ.get("SERGULATH_STATE_DIR"):
        return Path(os.environ["SERGULATH_STATE_DIR"])
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "sergulath"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "sergulath"


def lease_file() -> Path:
    return state_dir() / LEASE_NAME


def owner() -> str:
    return os.environ.get("SERGULATH_OWNER") or os.path.normcase(str(Path.cwd().resolve()))


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_uint32()
            return bool(kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read() -> dict | None:
    try:
        return json.loads(lease_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def live(lease: dict | None) -> dict | None:
    """The lease with dead pids and spent holds dropped, or None when nothing is left."""
    if not lease:
        return None
    pids = [p for p in lease.get("pids", []) if alive(int(p))]
    holds = [h for h in lease.get("holds", []) if h.get("snapshot") and Path(h["snapshot"]).is_file()]
    if not pids and not holds:
        return None
    return {**lease, "pids": pids, "holds": holds}


def _write(lease: dict | None) -> None:
    path = lease_file()
    if lease is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(lease, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


@contextmanager
def _guard():
    """A short critical section around reading and writing the lease (an O_EXCL file)."""
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    guard = folder / (LEASE_NAME + ".guard")
    deadline = time.monotonic() + GUARD_WAIT_S
    while True:
        try:
            fd = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - guard.stat().st_mtime > GUARD_STALE_S:
                    guard.unlink(missing_ok=True)  # left by a process that died inside the section
                    continue
            except OSError:
                continue
            if time.monotonic() > deadline:
                raise Busy({"owner": "(another sergulath is updating the lease)", "command": "?", "since": "?"})
            time.sleep(0.05)
    try:
        yield
    finally:
        guard.unlink(missing_ok=True)


def acquire(command: str) -> dict:
    me = owner()
    with _guard():
        lease = live(read())
        if lease and lease.get("owner") != me:
            raise Busy(lease)
        if lease is None:
            lease = {"owner": me, "command": command, "since": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                     "pids": [], "holds": []}
        lease["pids"] = [*lease["pids"], os.getpid()]
        lease["command"] = command
        _write(lease)
        return lease


def release() -> None:
    me = owner()
    with _guard():
        lease = read()
        if not lease or lease.get("owner") != me:
            return
        lease["pids"] = [p for p in lease.get("pids", []) if int(p) != os.getpid()]
        _write(live(lease))


@contextmanager
def steamvr(command: str):
    """Hold the lease while a command changes SteamVR; raises Busy when another owner has it."""
    acquire(command)
    try:
        yield
    finally:
        release()


def hold(label: str, snapshot: Path) -> None:
    """Keep the lease after this command: SteamVR stays changed until `snapshot` is restored."""
    me = owner()
    with _guard():
        lease = read() or {"owner": me, "command": label, "since": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                           "pids": [], "holds": []}
        if lease.get("owner") != me:
            return  # acquire() refused another owner already; nothing to add here
        lease["holds"] = [h for h in lease.get("holds", []) if h.get("label") != label] + [
            {"label": label, "snapshot": str(Path(snapshot).resolve()), "since": time.strftime("%Y-%m-%dT%H:%M:%S%z")}]
        _write(lease)


def unhold(label: str) -> None:
    me = owner()
    with _guard():
        lease = read()
        if not lease or lease.get("owner") != me:
            return
        lease["holds"] = [h for h in lease.get("holds", []) if h.get("label") != label]
        _write(live(lease))


def force_release() -> dict | None:
    with _guard():
        lease = read()
        _write(None)
        return lease
