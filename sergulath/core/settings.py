"""SteamVR's settings file (steamvr.vrsettings): read, change with a snapshot, and restore.

A key is written `section.name`, such as `power.turnOffScreensTimeout`. Values are JSON
(`300`, `false`, `"null"`); anything that is not JSON is taken as a string.

SteamVR reads most settings when it starts and writes the file itself while it runs, so the
file is changed only while SteamVR is stopped (`--restart` stops it first and starts it after).
Every change first writes a snapshot of the keys it touches (their earlier value, or that they
were absent) to records/settings/; `restore` puts exactly those keys back and leaves the rest of
the file as SteamVR left it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from . import driver

SNAPSHOTS = Path("records") / "settings"
MISSING = {"__absent__": True}


@dataclass
class Change:
    key: str
    before: object  # MISSING when the key was not in the file
    after: object

    def to_dict(self) -> dict:
        return {"key": self.key, "before": self.before, "after": self.after}


def settings_file() -> Path:
    configs = driver.openvr_paths().get("config") or []
    if not configs:
        raise RuntimeError("SteamVR's config folder is not in openvrpaths.vrpath")
    return Path(configs[0]) / "steamvr.vrsettings"


def split(key: str) -> tuple[str, str]:
    section, _, name = key.partition(".")
    if not section or not name:
        raise ValueError(f"a key is section.name, not {key!r}")
    return section, name


def parse_value(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def read(path: Path | None = None) -> dict:
    path = path or settings_file()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def get(data: dict, key: str):
    section, name = split(key)
    return data.get(section, {}).get(name, MISSING)


def put(data: dict, key: str, value) -> None:
    section, name = split(key)
    if value == MISSING:
        data.get(section, {}).pop(name, None)
        if section in data and not data[section]:
            del data[section]
    else:
        data.setdefault(section, {})[name] = value


def write(data: dict, path: Path) -> None:
    text = json.dumps(data, indent=3, ensure_ascii=False) + "\n"
    temporary = path.with_suffix(".sergulath-tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


# Values only a virtual headset puts there (`hmd on`: the null driver; driver_asvr when tried). A
# snapshot whose "before" holds one was taken while a virtual headset was already on (another
# session's `hmd on`, or one that never finished): restoring it would put the virtual headset
# back while saying "restored". Such a snapshot is marked and refused by `restore`.
VIRTUAL_HEADSET = {"steamvr.forcedDriver": ("null", "asvr"), "driver_null.enable": (True,)}


class SuspectSnapshot(RuntimeError):
    pass


def virtual_keys(values: dict) -> list[str]:
    """The keys of {key: value} that hold a virtual headset's value."""
    return [k for k, v in values.items() if k in VIRTUAL_HEADSET and v in VIRTUAL_HEADSET[k]]


def virtual_now(data: dict) -> list[str]:
    return virtual_keys({k: get(data, k) for k in VIRTUAL_HEADSET})


def virtual_before(record: dict) -> list[str]:
    return virtual_keys({c["key"]: c["before"] for c in record.get("changes", [])})


def apply(changes: dict, path: Path, snapshot_dir: Path | None = None, label: str = "") -> tuple[list[Change], Path]:
    """Write a snapshot, then the new values. Returns the changes and the snapshot file."""
    snapshot_dir = snapshot_dir or SNAPSHOTS
    data = read(path)
    done = [Change(key, get(data, key), value) for key, value in changes.items()]
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot_dir / (time.strftime("%Y%m%dT%H%M%S") + (f"-{label}" if label else "") + ".json")
    record = {"file": str(path), "taken": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "changes": [c.to_dict() for c in done]}
    suspect = virtual_before(record)
    if suspect:
        record["virtual_before"] = suspect
    snapshot.write_text(json.dumps(record, indent=2), encoding="utf-8")
    for change in done:
        put(data, change.key, change.after)
    write(data, path)
    return done, snapshot


def latest_snapshot(snapshot_dir: Path | None = None, label: str = "") -> Path | None:
    found = [p for p in pending(snapshot_dir) if not label or p.stem.endswith(f"-{label}")]
    return found[-1] if found else None


def clean_restored(label: str, snapshot_dir: Path | None = None) -> Path | None:
    """The newest restored snapshot with this label whose "before" has no virtual headset values:
    the one to restore from when the pending one is suspect."""
    snapshot_dir = snapshot_dir or SNAPSHOTS
    found = sorted(snapshot_dir.glob(f"*-{label}.restored.json"))
    for path in reversed(found):
        try:
            if not virtual_before(json.loads(path.read_text(encoding="utf-8"))):
                return path
        except (OSError, ValueError):
            continue
    return None


def check_restorable(snapshot: Path) -> None:
    """Raise SuspectSnapshot when the snapshot's "before" holds a virtual headset's values."""
    record = json.loads(snapshot.read_text(encoding="utf-8"))
    suspect = virtual_before(record)
    if suspect:
        label = snapshot.stem.removesuffix(".restored").rpartition("-")[2] if "-" in snapshot.stem else ""
        clean = clean_restored(label, snapshot.parent) if label else None
        hint = (f"`sergulath settings restore --snapshot {clean}` puts back the values from before the "
                "virtual headset" if clean else "set those keys back by hand (`sergulath settings show`)")
        raise SuspectSnapshot(
            f"{snapshot.name} was taken while a virtual headset was already on ({', '.join(suspect)} held "
            f"its value before): restoring it would keep the virtual headset. Not restored; {hint}. "
            "To restore it anyway: `sergulath settings restore --snapshot FILE --allow-virtual-before`")


def restore(snapshot: Path, allow_virtual_before: bool = False) -> list[Change]:
    """Put back the keys a snapshot recorded, and mark the snapshot as restored."""
    if not allow_virtual_before:
        check_restorable(snapshot)
    record = json.loads(snapshot.read_text(encoding="utf-8"))
    path = Path(record["file"])
    data = read(path)
    undone = []
    for item in record["changes"]:
        now = get(data, item["key"])
        put(data, item["key"], item["before"])
        undone.append(Change(item["key"], now, item["before"]))
    write(data, path)
    snapshot.replace(snapshot.with_name(snapshot.stem + ".restored.json"))
    return undone


def pending(snapshot_dir: Path | None = None) -> list[Path]:
    """Snapshots not restored yet: a check that changed settings and did not finish."""
    snapshot_dir = snapshot_dir or SNAPSHOTS
    return sorted(p for p in snapshot_dir.glob("*.json") if not p.name.endswith(".restored.json"))
