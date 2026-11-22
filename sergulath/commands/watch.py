"""sergulath watch: sample the headset and controllers at a fixed interval (read-only)."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from .. import output
from ..core import steamvr, watch

NAME = "watch"
HELP = "log headset and controller connection, pose, tracking result and headset activity"


def add_arguments(parser) -> None:
    parser.add_argument("--count", type=int, default=21,
                        help="number of samples (default 21: 20 minutes at the default interval)")
    parser.add_argument("--interval", type=float, default=60.0, help="seconds between samples (default 60)")
    parser.add_argument("--csv", metavar="FILE", help="also write every sample to this CSV file")
    output.add_format(parser)


def run(args) -> int:
    if args.count < 1:
        output.note("--count must be 1 or more")
        return output.USAGE
    record = watch.CsvRecord(Path(args.csv)) if args.csv else None
    try:
        with steamvr.background_client() as openvr:
            for samples in watch.sample_series(openvr, args.count, args.interval):
                if record:
                    record.add(samples)
                if args.format == "json":
                    for s in samples:
                        output.emit(asdict(s), "", "json")
                else:
                    when = samples[0].time if samples else ""
                    output.emit({}, f"{when} {watch.summary(samples)}".strip(), "text")
    finally:
        if record:
            record.close()
    return output.OK
