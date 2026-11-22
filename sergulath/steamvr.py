"""Connecting to a running SteamVR as a read-only background client."""

from __future__ import annotations

import subprocess
from contextlib import contextmanager

# Connecting an OpenVR client while the compositor is down makes SteamVR start it again,
# so the tools connect only when both of these already run.
REQUIRED_PROCESSES = ("vrserver.exe", "vrcompositor.exe")


def running_processes() -> set[str]:
    """Names of the running processes, lower case (Windows `tasklist`)."""
    out = subprocess.run(
        ["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True
    ).stdout
    return {line.split('","')[0].strip('"').lower() for line in out.splitlines() if line}


def missing_processes(running: set[str] | None = None) -> list[str]:
    running = running_processes() if running is None else running
    return [name for name in REQUIRED_PROCESSES if name not in running]


class SteamVRNotRunning(RuntimeError):
    pass


@contextmanager
def background_client():
    """Yield the `openvr` module after `VR_Init` as a background application; shut down after.

    A background application cannot start SteamVR and changes nothing; the callers only read.
    """
    missing = missing_processes()
    if missing:
        raise SteamVRNotRunning("SteamVR is not running (missing: " + ", ".join(missing) + ")")
    import openvr  # imported here so the rest of the package works without it

    openvr.init(openvr.VRApplication_Background)
    try:
        yield openvr
    finally:
        openvr.shutdown()
