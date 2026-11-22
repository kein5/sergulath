"""sergulath watch: sample the headset and controllers at a fixed interval (read-only).

With --frame, each sample also reads a standalone headset's own system overlays over SSH (see
`sergulath frame`), so a record shows when the headset put up its own screens. On a Steam
Frame, `XRService-CameraOverlay` shown means the "outside the play space" camera screen is up.

With --overlay KEY, each sample also reads that overlay of the app: shown, alpha, and what it
follows and where (see `sergulath overlay --placement`), so a record shows whether, say, a
launcher on the wrist ever attaches to the hand and fades in while the headset is worn.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from .. import output
from ..core import frame, overlay, steamvr, watch

NAME = "watch"
HELP = "log headset and controller connection, pose, tracking result and headset activity"

# The headset's own screens worth watching: the camera view (outside the play space), its
# button sheet, and Steam's UI.
FRAME_OVERLAYS = ["XRService-CameraOverlay", "system.systemui", "valve.steam.gamepadui.main"]


def add_arguments(parser) -> None:
    parser.add_argument("--count", type=int, default=21,
                        help="number of samples (default 21: 20 minutes at the default interval)")
    parser.add_argument("--interval", type=float, default=60.0, help="seconds between samples (default 60)")
    parser.add_argument("--csv", metavar="FILE", help="also write every sample to this CSV file")
    parser.add_argument("--frame", action="store_true",
                        help="also read the headset's own system overlays over SSH in every sample")
    parser.add_argument("--frame-host", default=frame.Target.host, help=f"headset address (default {frame.Target.host})")
    parser.add_argument("--overlay", metavar="KEY", action="append",
                        help="also read this overlay in every sample: shown, alpha and where it sits (repeatable), "
                             "e.g. --overlay ASTAROT3 for ASTAROT's launcher")
    output.add_format(parser)


def _overlay_part(openvr, keys: list[str], sample: int, when: str, fmt: str) -> str:
    states = overlay.capture(openvr, keys, read_placement=True)
    parts = []
    for s in states:
        if fmt == "json":
            output.emit({"source": "overlay", "time": when, "sample": sample, **s.to_dict()}, "", "json")
        if not s.found:
            parts.append(f"{s.key} not found")
            continue
        place = s.placement or {}
        if place.get("transform") == "tracked_device_relative":
            where = f"on {place.get('device')}#{place.get('device_index')}"
        else:
            where = place.get("transform", "?")
        if place.get("world_m") is not None:
            where += f" at {place['world_m']}"
        if place.get("head_distance_m") is not None:
            where += f" {place['head_distance_m']} m from head"
        parts.append(f"{s.key} visible={s.visible} alpha={s.alpha:.2f} {where}")
    return "; ".join(parts)


def _frame_part(target: frame.Target, sample: int, when: str, fmt: str) -> str:
    try:
        states = frame.overlay_states(target, FRAME_OVERLAYS, None)
    except Exception as e:  # noqa: BLE001 - an unreachable headset must not end the watch
        if fmt == "json":
            output.emit({"source": "frame", "time": when, "sample": sample, "error": str(e)}, "", "json")
        return f"frame: unreachable ({e})"
    shown = [s["key"] for s in states if s.get("visible")]
    if fmt == "json":
        for s in states:
            output.emit({"source": "frame", "time": when, "sample": sample, **s}, "", "json")
    return "frame: " + (", ".join(shown) + " shown" if shown else "no system screen shown")


def run(args) -> int:
    if args.count < 1:
        output.note("--count must be 1 or more")
        return output.USAGE
    record = watch.CsvRecord(Path(args.csv)) if args.csv else None
    target = frame.Target(host=args.frame_host) if args.frame else None
    try:
        with steamvr.background_client() as openvr:
            for samples in watch.sample_series(openvr, args.count, args.interval):
                if record:
                    record.add(samples)
                when = samples[0].time if samples else ""
                if args.format == "json":
                    for s in samples:
                        output.emit({"source": "steamvr", **asdict(s)}, "", "json")
                number = samples[0].sample if samples else 0
                overlay_text = _overlay_part(openvr, args.overlay, number, when, args.format) if args.overlay else ""
                frame_text = _frame_part(target, number, when, args.format) if target else ""
                if args.format != "json":
                    line = "; ".join(part for part in (f"{when} {watch.summary(samples)}".strip(), overlay_text, frame_text) if part)
                    output.emit({}, line, "text")
    finally:
        if record:
            record.close()
    return output.OK
