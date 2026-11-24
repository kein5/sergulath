"""sergulath soak: keep watch on a running app over time (read-only).

Samples the app's process tree (liveness and private/working-set memory) and, with --overlay,
each named OpenVR overlay (found, visible, alpha) at a fixed interval.

  sergulath soak --duration 1800 --interval 60 --overlay ASTAROT0 --min-alpha 0.01 --csv records/soak.csv

Pass criteria:
- The initial PID stays alive across every sample (no crash or restart).
- Post-warmup process-tree private memory growth stays within --max-growth-mb, and its linear
  trend stays within --max-slope-mb-per-hour (when net growth is at least 16 MiB).
- Every --overlay key stays found, visible, and at alpha >= --min-alpha in every sample.

Exit codes: 0 passed, 1 app not running or a check failed, 2 usage error, 3 SteamVR is not
running (when --overlay is used), 4 failed.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from .. import output, profiles
from ..core import app as app_core
from ..core import soak as soak_core
from ..core import steamvr

NAME = "soak"
HELP = "check a running app over time: process stays alive, memory stays bounded, overlays stay shown"


def add_arguments(parser) -> None:
    parser.add_argument("--profile", default="astarot", help="app profile (default astarot)")
    parser.add_argument("--pid", type=int, help="process id to watch instead of looking up the profile's process")
    parser.add_argument("--duration", type=float, default=1800.0, help="total seconds to run (default 1800: 30 minutes)")
    parser.add_argument("--interval", type=float, default=60.0, help="seconds between samples (default 60)")
    parser.add_argument("--warmup", type=float, default=120.0,
                        help="seconds before the memory baseline (default 120)")
    parser.add_argument("--max-growth-mb", type=float, default=100.0,
                        help="maximum allowed post-warmup private memory growth in MiB (default 100)")
    parser.add_argument("--max-slope-mb-per-hour", type=float, default=120.0,
                        help="maximum allowed post-warmup private memory trend in MiB/h (default 120)")
    parser.add_argument("--overlay", action="append", metavar="KEY",
                        help="also check this OpenVR overlay on every sample (repeatable), e.g. --overlay ASTAROT0")
    parser.add_argument("--min-alpha", type=float, default=0.0,
                        help="minimum overlay alpha required on every sample (default 0.0; use 0.01 with caption sample)")
    parser.add_argument("--csv", metavar="FILE", help="also write every sample to this CSV file")
    output.add_format(parser)


def _format_sample(sample: soak_core.SoakSample) -> str:
    if not sample.alive:
        return f"{sample.time} #{sample.sample} +{sample.elapsed_s:.0f}s pid={sample.pid} EXITED"
    line = (
        f"{sample.time} #{sample.sample} +{sample.elapsed_s:.0f}s pid={sample.pid} "
        f"procs={sample.process_count} private={sample.tree_private_mb:.1f} MiB "
        f"(main {sample.main_private_mb:.1f} MiB) ws={sample.tree_working_set_mb:.1f} MiB"
    )
    if sample.overlays is not None:
        parts = [f"{o['key']}:{'ok' if (o['found'] and o['visible']) else 'missing'}(a={o['alpha']:.2f})"
                 for o in sample.overlays]
        line += "; " + ", ".join(parts)
    return line


def _format_summary(summary: soak_core.SoakSummary) -> str:
    verdict = "PASS" if summary.ok else "FAIL"
    text = (
        f"soak {verdict}: pid={summary.pid} samples={summary.samples} elapsed={summary.elapsed_s:.0f}s "
        f"alive={summary.alive} private={summary.baseline_mb:.1f}->{summary.final_mb:.1f} MiB "
        f"(growth {summary.growth_mb:+.1f} MiB, slope {summary.slope_mb_per_hour:+.1f} MiB/h, peak {summary.peak_mb:.1f} MiB) "
        f"overlay_ok={summary.overlay_ok}"
    )
    if summary.reason:
        text += f" ({summary.reason})"
    return text


def run(args) -> int:
    if args.duration < 0:
        output.note("--duration must be 0 or more")
        return output.USAGE
    if args.interval <= 0:
        output.note("--interval must be greater than 0")
        return output.USAGE
    if args.warmup < 0:
        output.note("--warmup must be 0 or more")
        return output.USAGE
    if args.max_growth_mb < 0 or args.max_slope_mb_per_hour < 0:
        output.note("memory limits must be 0 or more")
        return output.USAGE
    if not (0.0 <= args.min_alpha <= 1.0):
        output.note("--min-alpha must be between 0 and 1")
        return output.USAGE

    profile = profiles.load(args.profile)
    app_info = profile["app"]
    process_label = " or ".join(app_core.process_names(app_info))
    pid = args.pid if args.pid is not None else app_core.running_pid_for(app_info)
    if pid is None:
        record = {"source": "summary", "ok": False, "alive": False, "memory_ok": False, "overlay_ok": False,
                  "reason": f"{process_label} is not running"}
        output.emit(record, f"soak FAIL: {process_label} is not running", args.format)
        return output.NOT_MET

    csv_record = soak_core.CsvRecord(Path(args.csv)) if args.csv else None

    def emit_sample(sample: soak_core.SoakSample) -> None:
        if csv_record:
            csv_record.add(sample)
        output.emit({"source": "sample", **sample.to_dict()}, _format_sample(sample), args.format)

    try:
        cm = steamvr.background_client() if args.overlay else contextlib.nullcontext(None)
        with cm as openvr:
            read_ov = (lambda keys, ma: soak_core.sample_overlays(openvr, keys, ma)) if args.overlay else None
            _, summary = soak_core.run_soak(
                pid=pid,
                duration_s=args.duration,
                interval_s=args.interval,
                warmup_s=args.warmup,
                max_growth_mb=args.max_growth_mb,
                max_slope_mb_per_hour=args.max_slope_mb_per_hour,
                overlay_keys=args.overlay,
                min_alpha=args.min_alpha,
                read_overlays=read_ov,
                on_sample=emit_sample,
            )
    finally:
        if csv_record:
            csv_record.close()

    output.emit({"source": "summary", **summary.to_dict()}, _format_summary(summary), args.format)
    return output.OK if summary.ok else output.NOT_MET
