"""SERGULATH's virtual controller driver: build it, register it with SteamVR, talk to it.

The driver (driver/ in this repository) is registered with SteamVR's own `vrpathreg` for a check
and removed afterwards, so it never stays in an ordinary VR session. SteamVR loads drivers when
it starts, so registering or removing takes effect at the next start; `restart_steamvr` quits
SteamVR through its status window and starts it again through Steam.

Even while registered, the driver adds no device until asked (`connect`).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import steamvr

PORT = 39581
NAME = "sergulath"
REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "driver"
STAGE = REPO / "build" / "driver" / NAME
TARGET = REPO / "build" / "driver-target"
DLL_NAME = "driver_sergulath.dll"
STEAMVR_APP_ID = "250820"


class DriverNotLoaded(RuntimeError):
    pass


class DriverError(RuntimeError):
    pass


# ---- talking to the loaded driver --------------------------------------------------------------

def send(line: str, timeout: float = 1.0, attempts: int = 3) -> str:
    """Send one command and return the reply's text after "ok" (see driver/src/control.rs).

    UDP may drop a datagram, and the driver answers from vrserver's process, which can be busy
    for a moment; every command is idempotent, so a lost one is simply sent again."""
    reply = None
    for _ in range(attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(line.encode("utf-8"), ("127.0.0.1", PORT))
            try:
                reply = s.recv(65536).decode("utf-8", "replace").strip()
                break
            except (TimeoutError, ConnectionResetError, socket.timeout):
                continue
    if reply is None:
        raise DriverNotLoaded("the virtual controller driver does not answer: is it registered and "
                              "SteamVR restarted? (`sergulath driver register --restart`)")
    if reply == "ok" or reply.startswith("ok "):
        return reply[3:]
    raise DriverError(reply.removeprefix("error ").strip() or reply)


def state() -> dict:
    return json.loads(send("state"))


def set_pose(openvr, hand: str, standing) -> list[float]:
    """Put a controller at a pose given in the standing universe (what apps read).

    The driver's space is SteamVR's raw tracking space; the standing universe sits above it by the
    play area's calibration (`GetRawZeroPoseToStandingAbsoluteTrackingPose`)."""
    from . import aim
    from .placement import multiply, to_rows

    raw_to_standing = to_rows(openvr.VRSystem().getRawZeroPoseToStandingAbsoluteTrackingPose())
    raw = multiply(aim.inverse(raw_to_standing), standing)
    q = aim.quaternion(raw)
    position = [raw[0][3], raw[1][3], raw[2][3]]
    send("pose %s %s" % (hand, " ".join(f"{v:.6f}" for v in [*position, *q])))
    return position


def read_pose(openvr, hand: str):
    """The controller's pose as apps see it (standing), or None without a valid pose."""
    from .placement import to_rows

    system = openvr.VRSystem()
    role = openvr.TrackedControllerRole_LeftHand if hand == "left" else openvr.TrackedControllerRole_RightHand
    index = system.getTrackedDeviceIndexForControllerRole(role)
    if index >= openvr.k_unMaxTrackedDeviceCount:
        return None
    pose = system.getDeviceToAbsoluteTrackingPose(openvr.TrackingUniverseStanding, 0, openvr.k_unMaxTrackedDeviceCount)[index]
    return to_rows(pose.mDeviceToAbsoluteTracking) if pose.bPoseIsValid else None


def wait_loaded(timeout: float = 30.0) -> None:
    """Wait until a freshly started SteamVR has loaded the driver and its listener answers."""
    if not _wait(loaded, timeout):
        raise DriverNotLoaded("SteamVR started, but the virtual controller driver does not answer")


def loaded() -> bool:
    try:
        send("hello", timeout=0.5)
        return True
    except (DriverNotLoaded, DriverError):
        return False


# ---- SteamVR's driver registry -----------------------------------------------------------------

def openvr_paths_file() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if sys.platform == "win32" and base:
        return Path(base) / "openvr" / "openvrpaths.vrpath"
    return Path.home() / ".config" / "openvr" / "openvrpaths.vrpath"


def openvr_paths() -> dict:
    path = openvr_paths_file()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def registered_folders(paths: dict | None = None) -> list[str]:
    paths = openvr_paths() if paths is None else paths
    return list(paths.get("external_drivers") or [])


def is_registered(folder: Path = STAGE, paths: dict | None = None) -> bool:
    want = os.path.normcase(os.path.normpath(str(folder)))
    return any(os.path.normcase(os.path.normpath(f)) == want for f in registered_folders(paths))


def vrpathreg() -> Path:
    runtimes = openvr_paths().get("runtime") or []
    for runtime in runtimes:
        exe = Path(runtime) / "bin" / ("win64/vrpathreg.exe" if sys.platform == "win32" else "linux64/vrpathreg")
        if exe.is_file():
            return exe
    raise DriverError("SteamVR's vrpathreg was not found (no runtime in openvrpaths.vrpath)")


def register(folder: Path = STAGE) -> None:
    if not (folder / "bin" / "win64" / DLL_NAME).is_file():
        raise DriverError(f"no driver at {folder}: run `sergulath driver build` first")
    subprocess.run([str(vrpathreg()), "adddriver", str(folder)], check=True, capture_output=True, timeout=30)
    if not is_registered(folder):
        raise DriverError("vrpathreg did not record the driver")


def unregister(folder: Path = STAGE) -> None:
    subprocess.run([str(vrpathreg()), "removedriver", str(folder)], check=True, capture_output=True, timeout=30)
    if is_registered(folder):
        raise DriverError("vrpathreg did not remove the driver")


# ---- building ------------------------------------------------------------------------------------

def build(cargo: str = "cargo", toolchain: str | None = None) -> Path:
    """Build the driver DLL with cargo and stage the driver folder SteamVR loads (build/driver)."""
    if not SOURCE.is_dir():
        raise DriverError(f"no driver sources at {SOURCE}: run from a checkout of the repository")
    if steamvr_holds(STAGE):
        raise DriverError("SteamVR has the staged driver loaded; quit SteamVR (or `driver unregister --restart`) first")
    command = [cargo] + ([f"+{toolchain}"] if toolchain else []) + [
        "build", "--release", "--lib", "--manifest-path", str(SOURCE / "Cargo.toml")]
    subprocess.run(command, check=True, env=dict(os.environ, CARGO_TARGET_DIR=str(TARGET)))
    dll = TARGET / "release" / DLL_NAME
    if not dll.is_file():
        raise DriverError(f"cargo did not produce {dll}")
    if STAGE.exists():
        shutil.rmtree(STAGE)
    shutil.copytree(SOURCE / NAME, STAGE)
    (STAGE / "bin" / "win64").mkdir(parents=True, exist_ok=True)
    shutil.copy2(dll, STAGE / "bin" / "win64" / DLL_NAME)
    return STAGE


def steamvr_holds(folder: Path) -> bool:
    """Whether a running vrserver has this driver's DLL loaded (it cannot be replaced then)."""
    dll = folder / "bin" / "win64" / DLL_NAME
    if not dll.is_file() or "vrserver.exe" not in steamvr.running_processes():
        return False
    try:
        with dll.open("r+b"):
            return False
    except PermissionError:
        return True


# ---- restarting SteamVR ----------------------------------------------------------------------

def steam_exe() -> Path | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value = winreg.QueryValueEx(key, "SteamExe")[0]
            return Path(value) if value else None
    except OSError:
        return None


def _wait(predicate, timeout: float, step: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def quit_steamvr(timeout: float = 30.0) -> None:
    """Quit SteamVR the way closing its status window does; force it only if that hangs."""
    running = steamvr.running_processes
    if "vrserver.exe" not in running():
        return
    subprocess.run(["taskkill", "/IM", "vrmonitor.exe"], capture_output=True, timeout=15)
    if _wait(lambda: "vrserver.exe" not in running(), timeout):
        return
    for name in ("vrmonitor.exe", "vrcompositor.exe", "vrdashboard.exe", "vrwebhelper.exe", "vrserver.exe"):
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, timeout=15)
    if not _wait(lambda: "vrserver.exe" not in running(), 10):
        raise DriverError("SteamVR did not quit")


def start_steamvr(timeout: float = 90.0, attempts: int = 2) -> None:
    """Start SteamVR through Steam (so it runs as Steam's child, as it normally does).

    A wireless headset (a Steam Frame over Steam Link) sometimes does not come back to a SteamVR
    started moments after the previous one quit: vrserver runs, but the compositor never starts
    (vrserver.txt: VRInitError_Driver_WirelessHmdNotConnected). Quitting and starting once more
    brought it back (2026-11-23), so a start that times out is tried again.
    """
    exe = steam_exe()
    if exe is None or not exe.is_file():
        raise DriverError("Steam was not found (HKCU\\Software\\Valve\\Steam\\SteamExe)")
    for attempt in range(1, attempts + 1):
        subprocess.Popen([str(exe), "-applaunch", STEAMVR_APP_ID], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if _wait(lambda: not steamvr.missing_processes(), timeout):
            break
        if attempt == attempts:
            raise DriverError("SteamVR did not start within %d s (%d attempts)" % (timeout, attempts))
        quit_steamvr()
        time.sleep(5)
    # The server takes a few seconds more before clients and drivers are ready.
    time.sleep(8)


def restart_steamvr() -> None:
    quit_steamvr()
    start_steamvr()
