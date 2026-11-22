"""sergulath overlay: list an app's overlays and optionally save their pixels (read-only)."""

from __future__ import annotations

from pathlib import Path

from .. import output, profiles
from ..core import overlay, steamvr

NAME = "overlay"
HELP = "list an app's overlays (found, shown, alpha, width) and optionally save their pixels"


def add_arguments(parser) -> None:
    parser.add_argument("--profile", default="astarot", help="app profile (default astarot; see `sergulath profiles`)")
    parser.add_argument("--legacy", action="store_true", help="use the profile's older overlay keys")
    parser.add_argument("--key", action="append", help="overlay key to read instead of the profile's (repeatable)")
    parser.add_argument("--save", metavar="DIR", help="save each overlay's pixels as DIR/<key>.png")
    parser.add_argument("--placement", action="store_true",
                        help="also read where each overlay sits: what it follows (headset, which hand, the room), "
                             "its offset, its world position and its distance from the head")
    output.add_format(parser)


def run(args) -> int:
    profile = profiles.load(args.profile)
    labels = profile.get("legacy_overlays" if args.legacy else "overlays", {})
    keys = args.key or list(labels)
    with steamvr.background_client() as openvr:
        states = overlay.capture(openvr, keys, Path(args.save) if args.save else None,
                                 read_placement=args.placement)
    for state in states:
        label = labels.get(state.key, "")
        output.emit({**state.to_dict(), "label": label}, overlay.describe(state, label), args.format)
    return output.OK if any(s.found for s in states) else output.NOT_MET
