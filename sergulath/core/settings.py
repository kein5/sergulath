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


def apply(changes: dict, path: Path, snapshot_dir: Path = SNAPSHOTS) -> tuple[list[Change], Path]:
    """Write a snapshot, then the new values. Returns the changes and the snapshot file."""
    data = read(path)
    done = [Change(key, get(data, key), value) for key, value in changes.items()]
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot_dir / time.strftime("%Y%m%dT%H%M%S.json")
    snapshot.write_text(json.dumps({"file": str(path), "taken": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                    "changes": [c.to_dict() for c in done]}, indent=2), encoding="utf-8")
    for change in done:
        put(data, change.key, change.after)
    write(data, path)
    return done, snapshot


def latest_snapshot(snapshot_dir: Path = SNAPSHOTS) -> Path | None:
    found = sorted(p for p in snapshot_dir.glob("*.json") if not p.name.endswith(".restored.json"))
    return found[-1] if found else None


def restore(snapshot: Path) -> list[Change]:
    """Put back the keys a snapshot recorded, and mark the snapshot as restored."""
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


def pending(snapshot_dir: Path = SNAPSHOTS) -> list[Path]:
    """Snapshots not restored yet: a check that changed settings and did not finish."""
    return sorted(p for p in snapshot_dir.glob("*.json") if not p.name.endswith(".restored.json"))
