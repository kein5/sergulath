"""Log the headset's and controllers' connection, pose, tracking result and activity (read-only)."""

from __future__ import annotations

import csv
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEVICE_CLASS = {1: "hmd", 2: "controller", 3: "tracker", 4: "base_station", 5: "display_redirect"}
ROLE = {1: "left", 2: "right"}
# EDeviceActivityLevel
ACTIVITY = {-1: "unknown", 0: "idle", 1: "user_interaction", 2: "user_interaction_timeout", 3: "standby", 4: "idle_timeout"}
# ETrackingResult
TRACKING = {
    0: "none",  # what SteamVR reports for a device that is not connected
    1: "uninitialized",
    100: "calibrating_in_progress",
    101: "calibrating_out_of_range",
    200: "running_ok",
    201: "running_out_of_range",
    300: "fallback_rotation_only",
}
FIELDS = ["time", "sample", "index", "class", "role", "connected", "pose_valid", "tracking", "activity"]


@dataclass
class DeviceSample:
    time: str
    sample: int
    index: int
    device_class: str
    role: str
    connected: bool
    pose_valid: bool
    tracking: str
    activity: str  # the headset's activity level; empty for other devices

    def row(self) -> list:
        return [self.time, self.sample, self.index, self.device_class, self.role,
                self.connected, self.pose_valid, self.tracking, self.activity]


def sample_devices(openvr, sample: int) -> list[DeviceSample]:
    system = openvr.VRSystem()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    poses = system.getDeviceToAbsoluteTrackingPose(
        openvr.TrackingUniverseStanding, 0, openvr.k_unMaxTrackedDeviceCount
    )
    activity = ACTIVITY.get(system.getTrackedDeviceActivityLevel(openvr.k_unTrackedDeviceIndex_Hmd), "?")
    samples = []
    for index in range(openvr.k_unMaxTrackedDeviceCount):
        device_class = system.getTrackedDeviceClass(index)
        if device_class not in (1, 2):
            continue
        pose = poses[index]
        role = ROLE.get(system.getControllerRoleForTrackedDeviceIndex(index), "-") if device_class == 2 else "-"
        tracking = int(pose.eTrackingResult)
        samples.append(DeviceSample(
            time=now,
            sample=sample,
            index=index,
            device_class=DEVICE_CLASS[device_class],
            role=role,
            connected=bool(system.isTrackedDeviceConnected(index)),
            pose_valid=bool(pose.bPoseIsValid),
            tracking=TRACKING.get(tracking, str(tracking)),
            activity=activity if device_class == 1 else "",
        ))
    return samples


def summary(samples: list[DeviceSample]) -> str:
    if not samples:
        return "no headset or controllers"
    parts = []
    for s in samples:
        name = s.device_class if s.role == "-" else f"{s.device_class}/{s.role}"
        part = f"{name}#{s.index} connected={s.connected} pose={s.pose_valid} tracking={s.tracking}"
        if s.activity:
            part += f" activity={s.activity}"
        parts.append(part)
    return "; ".join(parts)


def sample_series(openvr, count: int, interval_s: float, sleep=time.sleep) -> Iterator[list[DeviceSample]]:
    """Yield `count` rounds of samples, `interval_s` apart (no wait after the last)."""
    for n in range(count):
        yield sample_devices(openvr, n)
        if n < count - 1:
            sleep(interval_s)


class CsvRecord:
    """Appends samples to a CSV file, flushing each round so an interrupted watch keeps its rows."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(FIELDS)

    def add(self, samples: list[DeviceSample]) -> None:
        self._writer.writerows(s.row() for s in samples)
        self._file.flush()

    def close(self) -> None:
        self._file.close()
