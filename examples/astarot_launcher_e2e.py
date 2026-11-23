"""ASTAROT, unattended: show the VR UI's launcher, press its "log" button several times, and check that
the VR panel (ASTAROT2) opens and closes with each press. Nobody wears the headset.

Run it inside a virtual-headset session, which also registers the virtual controllers and puts
everything back afterwards, also when the check fails:

    sergulath hmd run --with-driver -- python examples/astarot_launcher_e2e.py [PRESSES]

Needs ASTAROT running with "Show VR UI" on (an app setting this script does not change).
Exit code 0 when every press toggled the panel.
"""
import json, subprocess, sys, time

PY = [sys.executable, "-m", "sergulath"]
PRESSES = int(sys.argv[1]) if len(sys.argv) > 1 else 4


def sg(*args, check=True):
    done = subprocess.run([*PY, *args], capture_output=True, text=True)
    sys.stderr.write(done.stderr)
    if check and done.returncode != 0:
        raise SystemExit(f"sergulath {' '.join(args)} -> {done.returncode}: {done.stdout}")
    return done


def visible(key):
    return json.loads(sg("overlay", "--key", key, "--format", "json").stdout.splitlines()[0])["visible"]


def wait_visible(key, want, timeout=3.0):
    deadline = time.monotonic() + timeout
    while True:
        now = visible(key)
        if now == want or time.monotonic() > deadline:
            return now
        time.sleep(0.3)


sg("steamvr", "wait", "--until", "tracking", "--timeout", "60")
sg("controller", "connect", "--hand", "left")
sg("controller", "connect", "--hand", "right")
sg("controller", "dashboard", "close")  # SteamVR opens it when no game runs; apps get no buttons then
time.sleep(2)
sg("controller", "pose", "--hand", "left", "--show", "ASTAROT3")
time.sleep(6)  # the launcher shows after the wrist is looked at, then plays its intro
steps = []
for n in range(PRESSES):
    before = visible("ASTAROT2")
    press = json.loads(sg("overlay", "press", "--overlay", "ASTAROT3", "--button", "log", "--format", "json").stdout)
    after = wait_visible("ASTAROT2", not before)
    steps.append({"before": before, "after": after, "toggled": after != before, "hit_uv": press["hit_uv"]})
print(json.dumps({"toggled": sum(s["toggled"] for s in steps), "presses": len(steps), "steps": steps}))
sys.exit(0 if all(s["toggled"] for s in steps) else 1)
