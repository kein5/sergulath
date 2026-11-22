"""Log the headset's and controllers' connection, pose, tracking result and activity (read-only)."""

from __future__ import annotations

import csv
import time
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


def run(openvr, out: Path, minutes: int, interval_s: float = 60.0, echo=print) -> None:
    """Take `minutes` + 1 samples, `interval_s` apart, into the CSV file `out`."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(FIELDS)
        for n in range(minutes + 1):
            samples = sample_devices(openvr, n)
            writer.writerows(s.row() for s in samples)
            f.flush()
            echo(f"{samples[0].time if samples else datetime.now().astimezone().isoformat(timespec='seconds')} {summary(samples)}")
            if n < minutes:
                time.sleep(interval_s)
