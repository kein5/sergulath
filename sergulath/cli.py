"""Command line: `sergulath watch` and `sergulath overlay`. Both only read."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import overlay, profiles, watch
from .steamvr import SteamVRNotRunning, background_client


def _watch(args) -> int:
    with background_client() as openvr:
        watch.run(openvr, Path(args.out), args.minutes, args.interval)
    return 0


def _overlay(args) -> int:
    profile = profiles.load(args.profile)
    labels = profile.get("legacy_overlays" if args.legacy else "overlays", {})
    keys = args.key or list(labels)
    with background_client() as openvr:
        states = overlay.capture(openvr, keys, Path(args.out) if args.out else None)
    for state in states:
        print(overlay.describe(state, labels.get(state.key, "")))
    return 0 if any(s.found for s in states) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sergulath", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    w = sub.add_parser("watch", help="log headset and controller connection, pose, tracking and activity")
    w.add_argument("--out", required=True, help="CSV file to write")
    w.add_argument("--minutes", type=int, default=20, help="how long to watch (default 20)")
    w.add_argument("--interval", type=float, default=60.0, help="seconds between samples (default 60)")
    w.set_defaults(func=_watch)

    o = sub.add_parser("overlay", help="list an app's overlays and optionally save their pixels")
    o.add_argument("--profile", default="astarot", help="app profile (default astarot)")
    o.add_argument("--legacy", action="store_true", help="use the profile's older overlay keys")
    o.add_argument("--key", action="append", help="overlay key to read instead of the profile's (repeatable)")
    o.add_argument("--out", help="folder to save <key>.png into; without it, pixels are not read")
    o.set_defaults(func=_overlay)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SteamVRNotRunning as e:
        print(f"sergulath: {e}. Start SteamVR first; the tools do not start it.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
