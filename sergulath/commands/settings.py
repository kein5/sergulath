"""sergulath settings: SteamVR's settings, changed only with a snapshot and restored after.

  settings show [KEY ...]                 Print keys (section.name) from steamvr.vrsettings,
                                          all of them when none are named. Read-only.
  settings set KEY=VALUE ... [--restart]  Snapshot the keys, then write the new values. SteamVR
                                          must be stopped; --restart stops it through its status
                                          window first and starts it through Steam after.
  settings restore [--snapshot FILE] [--restart]
                                          Put back the keys of the latest (or the named) snapshot.
  settings pending                        Snapshots not restored yet (a check that did not finish).

Values are JSON (300, false, "null"); other text is a string. Snapshots are kept in
records/settings/ (ignored by git); a restored one is renamed *.restored.json.

Example: keep the screens on during a check, then put it back.
  sergulath settings set power.turnOffScreensTimeout=1800 --restart
  sergulath settings restore --restart
"""

from __future__ import annotations

from pathlib import Path

from .. import output
from ..core import driver, settings, steamvr

NAME = "settings"
HELP = "show SteamVR settings, change them with a snapshot, and restore them"


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    show = sub.add_parser("show", help="print settings (read-only)")
    show.add_argument("keys", nargs="*", metavar="KEY")
    output.add_format(show)
    change = sub.add_parser("set", help="snapshot, then change settings")
    change.add_argument("pairs", nargs="+", metavar="KEY=VALUE")
    change.add_argument("--restart", action="store_true", help="stop SteamVR first and start it after")
    output.add_format(change)
    restore = sub.add_parser("restore", help="put back a snapshot's keys")
    restore.add_argument("--snapshot", help="snapshot file (default: the latest not restored)")
    restore.add_argument("--restart", action="store_true", help="stop SteamVR first and start it after")
    output.add_format(restore)
    pending = sub.add_parser("pending", help="snapshots not restored yet")
    output.add_format(pending)


def _guard_running(restart: bool) -> bool:
    if "vrserver.exe" in steamvr.running_processes():
        if not restart:
            output.note("SteamVR is running and rewrites its settings file; pass --restart (or quit SteamVR)")
            return False
        driver.quit_steamvr()
    return True


def _emit_changes(changes, fmt, snapshot=None) -> None:
    for change in changes:
        record = {**change.to_dict(), "snapshot": str(snapshot) if snapshot else None}
        before = "(absent)" if change.before == settings.MISSING else change.before
        after = "(absent)" if change.after == settings.MISSING else change.after
        output.emit(record, f"{change.key}: {before} -> {after}", fmt)


def run(args) -> int:
    if args.action == "show":
        data = settings.read()
        keys = args.keys or [f"{s}.{n}" for s, values in data.items() if isinstance(values, dict) for n in values]
        for key in keys:
            value = settings.get(data, key)
            output.emit({"key": key, "value": None if value == settings.MISSING else value,
                         "present": value != settings.MISSING},
                        f"{key} = {'(absent)' if value == settings.MISSING else value}", args.format)
        return output.OK
    if args.action == "pending":
        found = settings.pending()
        for path in found:
            output.emit({"snapshot": str(path)}, str(path), args.format)
        return output.OK if found else output.NOT_MET
    if not _guard_running(args.restart):
        return output.FAILED
    if args.action == "set":
        try:
            changes = {pair.partition("=")[0]: settings.parse_value(pair.partition("=")[2]) for pair in args.pairs}
            for key in changes:
                settings.split(key)
        except ValueError as e:
            output.note(str(e))
            return output.USAGE
        done, snapshot = settings.apply(changes, settings.settings_file())
        _emit_changes(done, args.format, snapshot)
    else:
        snapshot = Path(args.snapshot) if args.snapshot else settings.latest_snapshot()
        if snapshot is None:
            output.note("no snapshot to restore")
            return output.NOT_MET
        _emit_changes(settings.restore(snapshot), args.format, snapshot)
    if args.restart:
        driver.start_steamvr()
    return output.OK
