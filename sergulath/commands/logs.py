"""sergulath logs: one timeline from SteamVR's logs and the app's log (read-only).

  logs [--minutes N] [--grep TEXT] [--source vrserver|vrcompositor|app ...]

Reads vrserver.txt and vrcompositor.txt from Steam's logs folder and the app's log named in the
profile ([app] log), puts every line on one clock (local time with its UTC offset) and prints
them in order. SteamVR writes local time; ASTAROT's process.log writes UTC. Lines without a time
(continuations) stay with the line before them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import output, profiles
from ..core import steamvr_status

NAME = "logs"
HELP = "one timeline from vrserver.txt, vrcompositor.txt and the app's log"

STEAMVR_TIME = re.compile(r"^\w{3} (\w{3} \d{1,2} \d{4} \d{2}:\d{2}:\d{2}\.\d{3}) ")
UTC_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) UTC ")


@dataclass
class Line:
    when: datetime
    source: str
    text: str


def parse(lines, source: str) -> list[Line]:
    """Timed lines from one log; untimed lines are joined to the line before them."""
    local = datetime.now().astimezone().tzinfo
    found: list[Line] = []
    for raw in lines:
        raw = raw.rstrip("\r\n")
        m = STEAMVR_TIME.match(raw)
        if m:
            when = datetime.strptime(m.group(1), "%b %d %Y %H:%M:%S.%f").replace(tzinfo=local)
            found.append(Line(when, source, raw[m.end():].strip()))
            continue
        m = UTC_TIME.match(raw)
        if m:
            when = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=timezone.utc).astimezone(local)
            found.append(Line(when, source, raw[m.end():].strip()))
            continue
        if found and raw.strip():
            found[-1].text += " " + raw.strip()
    return found


def merge(*logs: list[Line]) -> list[Line]:
    return sorted((line for log in logs for line in log), key=lambda line: line.when)


def _read(path: Path, since: datetime | None, source: str) -> list[Line]:
    if not path.is_file():
        output.note(f"no {source} log at {path}")
        return []
    # The app keeps its log open; reading shares it, and a large log is read from its tail.
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 8 * 1024 * 1024))
        text = f.read().decode("utf-8", "replace")
    lines = parse(text.splitlines(), source)
    return [line for line in lines if since is None or line.when >= since]


def add_arguments(parser) -> None:
    parser.add_argument("--profile", default="astarot", help="app profile for the app's log (default astarot)")
    parser.add_argument("--minutes", type=float, default=10.0, help="only the last N minutes (default 10; 0 for all)")
    parser.add_argument("--grep", help="only lines containing this text (case-insensitive)")
    parser.add_argument("--source", action="append", choices=("vrserver", "vrcompositor", "app"),
                        help="only these logs (repeatable; default all)")
    output.add_format(parser)


def run(args) -> int:
    since = datetime.now().astimezone() - timedelta(minutes=args.minutes) if args.minutes > 0 else None
    sources = set(args.source or ("vrserver", "vrcompositor", "app"))
    folder = steamvr_status.steam_logs_folder()
    logs = []
    if folder:
        for name in ("vrserver", "vrcompositor"):
            if name in sources:
                logs.append(_read(folder / f"{name}.txt", since, name))
    elif sources & {"vrserver", "vrcompositor"}:
        output.note("Steam's logs folder was not found")
    app_info = profiles.load(args.profile).get("app", {})
    app_log = app_info.get("log")
    if "app" in sources and app_log:
        log_path = Path(os.path.expandvars(app_log))
        if not log_path.is_file():
            for raw in app_info.get("legacy_log", []):
                candidate = Path(os.path.expandvars(raw))
                if candidate.is_file():
                    log_path = candidate
                    break
        logs.append(_read(log_path, since, "app"))
    lines = merge(*logs)
    if args.grep:
        lines = [line for line in lines if args.grep.lower() in line.text.lower()]
    for line in lines:
        output.emit({"time": line.when.isoformat(timespec="milliseconds"), "source": line.source, "text": line.text},
                    f"{line.when.isoformat(timespec='milliseconds')} {line.source:<12} {line.text}", args.format)
    return output.OK if lines else output.NOT_MET
