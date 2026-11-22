"""Shared command-line conventions: output formats and exit codes.

- Data goes to stdout; progress, warnings and errors go to stderr.
- `--format text` (default) is for people; `--format json` prints JSON Lines, one object per
  record, so commands compose with `jq` and with each other.
- Commands never prompt.
"""

from __future__ import annotations

import json
import sys
from typing import Any

# Exit codes. argparse already uses 2 for usage errors.
OK = 0
NOT_MET = 1  # the command ran, but what it looked for was not there
USAGE = 2
STEAMVR_NOT_RUNNING = 3
FAILED = 4

FORMATS = ("text", "json")


def add_format(parser) -> None:
    parser.add_argument("--format", choices=FORMATS, default="text",
                        help="text for people (default) or json (JSON Lines, one object per record)")


def emit(record: dict[str, Any], text: str, fmt: str, stream=None) -> None:
    stream = stream or sys.stdout
    if fmt == "json":
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    else:
        stream.write(text + "\n")
    stream.flush()


def note(message: str) -> None:
    sys.stderr.write(f"sergulath: {message}\n")
