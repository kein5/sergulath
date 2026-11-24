"""Reach a standalone headset (a Steam Frame) over SSH with a dedicated key. Read-only use:
scripts are fed to the headset's own python3 on stdin, files go to a private temporary folder
there and are removed after they are copied back."""

from __future__ import annotations

import json
import os
import subprocess
import time
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


def _remote_dir(target: Target) -> str:
    made = _run(target.ssh_base() + [target.login, "mktemp -d /tmp/sergulath.XXXXXX"])
    if made.returncode:
        raise RuntimeError(f"ssh failed: {made.stderr.strip()}")
    return made.stdout.strip()


def scanout_shot(target: Target, out: Path, password: str | None) -> dict:
    """Save what the headset's panel shows (frame_scanout.py, run once on the headset).

    The script goes to a private temporary folder there and runs under `sudo -S` when a password
    is given: the password travels only on that ssh command's standard input, is never put in a
    command line, an environment variable or a file on the headset, and is never printed. The
    folder is removed afterwards, also after a failure. Returns the script's records, the
    command's exit code and the wall time.
    """
    script = resources.files(__package__).joinpath("frame_scanout.py").read_text(encoding="utf-8")
    remote_dir = _remote_dir(target)
    try:
        uploaded = _run(target.ssh_base() + [target.login, f"cat > {remote_dir}/capture.py"], stdin=script)
        if uploaded.returncode:
            raise RuntimeError(f"could not copy the capture script: {uploaded.stderr.strip()[:200]}")
        # Mesa would otherwise keep a shader cache in the user's home (root's, under sudo): keep it
        # off, and anything else it writes in the temporary folder.
        run = (f"timeout 120 env SERGULATH_SAVE_DIR={remote_dir} SERGULATH_NAME=frame-scanout "
               f"MESA_SHADER_CACHE_DISABLE=true XDG_CACHE_HOME={remote_dir}/cache HOME={remote_dir} "
               f"python3 {remote_dir}/capture.py")
        if password is not None:
            # -S: password from stdin, -p '': no prompt text, -k: never reuse a cached sudo ticket.
            run = f"sudo -S -k -p '' {run}"
        started = time.monotonic()
        done = _run(target.ssh_base() + [target.login, run],
                    stdin=(password + "\n") if password is not None else None, timeout=180)
        seconds = round(time.monotonic() - started, 2)
        records = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
        result = {"exit": done.returncode, "seconds": seconds, "records": records, "file": None,
                  "stderr": done.stderr.strip()[-300:]}
        shot = next((r for r in records if r.get("remote_file")), None)
        if shot:
            out.parent.mkdir(parents=True, exist_ok=True)
            copied = _run(target.ssh_base("scp") + [f"{target.login}:{shot['remote_file']}", str(out)], timeout=180)
            result["file"] = str(out) if copied.returncode == 0 else None
        return result
    finally:
        _run(target.ssh_base() + [target.login, f"rm -rf {remote_dir}"])


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
