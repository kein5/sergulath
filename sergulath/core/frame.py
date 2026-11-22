"""Reach a standalone headset (a Steam Frame) over SSH with a dedicated key. Read-only use:
scripts are fed to the headset's own python3 on stdin, files go to a private temporary folder
there and are removed after they are copied back."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path


@dataclass
class Target:
    host: str = "10.35.78.1"  # the Frame on its USB Wi-Fi link
    user: str = "steamos"  # fixed by Valve for the Steam Frame
    key: Path = Path(os.path.expanduser("~/.ssh/id_ed25519_steamframe"))

    def ssh_base(self, program: str = "ssh") -> list[str]:
        return [program, "-i", str(self.key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=8"]

    @property
    def login(self) -> str:
        return f"{self.user}@{self.host}"


def _run(args: list[str], stdin: str | None = None, timeout: float = 90) -> subprocess.CompletedProcess:
    return subprocess.run(args, input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def overlay_states(target: Target, keys: list[str], save_to: Path | None) -> list[dict]:
    """Run the overlay probe on the headset; with `save_to`, copy drawn images back there."""
    script = resources.files(__package__).joinpath("frame_probe.py").read_text(encoding="utf-8")
    remote_dir = ""
    if save_to:
        made = _run(target.ssh_base() + [target.login, "mktemp -d /tmp/sergulath.XXXXXX"])
        if made.returncode:
            raise RuntimeError(f"ssh failed: {made.stderr.strip()}")
        remote_dir = made.stdout.strip()
    env = f"SERGULATH_KEYS={','.join(keys)} SERGULATH_SAVE_DIR={remote_dir} "
    command = env + "LD_LIBRARY_PATH=/opt/steamvr/bin/linuxarm64 timeout 60 python3 -"
    try:
        done = _run(target.ssh_base() + [target.login, command], stdin=script)
        if done.returncode and not done.stdout:
            raise RuntimeError(f"probe failed on the headset: {(done.stderr or done.stdout).strip()[:300]}")
        records = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
        if save_to:
            save_to.mkdir(parents=True, exist_ok=True)
            for rec in records:
                if rec.get("remote_file"):
                    local = save_to / Path(rec["remote_file"]).name
                    copied = _run(target.ssh_base("scp") + [f"{target.login}:{rec['remote_file']}", str(local)])
                    rec["file"] = str(local) if copied.returncode == 0 else None
        return records
    finally:
        if remote_dir:
            _run(target.ssh_base() + [target.login, f"rm -rf {remote_dir}"])
