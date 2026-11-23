"""sergulath overlay: list an app's overlays, or press a spot on one with a virtual controller.

  overlay [--placement] [--save DIR] [--key KEY ...]
      List the profile's overlays: found, shown, alpha, width; --placement adds what each follows
      and where it sits; --save writes their pixels as PNG. Read-only.
  overlay press --overlay KEY (--at U V | --button NAME) [--hand right] [--dry-run]
      Point a virtual controller's laser at a spot and click its trigger. U and V run 0..1 from
      the left and the top of what the overlay shows; --button takes the spot from the profile
      ([buttons.KEY]). The laser follows the profile's [laser] (ASTAROT: tilted up 12 degrees).
      Needs the virtual controller driver (`sergulath driver register --restart`). Reports the
      spot SteamVR's own intersection test hit; --dry-run aims without pressing.
"""

from __future__ import annotations

from pathlib import Path

from .. import output, profiles
from ..core import overlay, press, steamvr

NAME = "overlay"
HELP = "list an app's overlays (found, shown, alpha, width, placement), or press a spot on one"


def add_arguments(parser) -> None:
    parser.add_argument("--profile", default="astarot", help="app profile (default astarot; see `sergulath profiles`)")
    parser.add_argument("--legacy", action="store_true", help="use the profile's older overlay keys")
    parser.add_argument("--key", action="append", help="overlay key to read instead of the profile's (repeatable)")
    parser.add_argument("--save", metavar="DIR", help="save each overlay's pixels as DIR/<key>.png")
    parser.add_argument("--placement", action="store_true",
                        help="also read where each overlay sits: what it follows (headset, which hand, the room), "
                             "its offset, its world position and its distance from the head")
    output.add_format(parser)
    sub = parser.add_subparsers(dest="action", metavar="ACTION")
    p = sub.add_parser("press", help="click a spot on an overlay with a virtual controller")
    p.add_argument("--profile", default="astarot", help="app profile (default astarot)")
    p.add_argument("--overlay", required=True, metavar="KEY", help="overlay key, such as ASTAROT3")
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--at", nargs=2, type=float, metavar=("U", "V"), help="0..1 from the left and the top")
    where.add_argument("--button", metavar="NAME", help="a spot named in the profile's [buttons.KEY]")
    p.add_argument("--hand", choices=("left", "right"), default="right", help="the controller that presses (default right)")
    p.add_argument("--distance", type=float, default=0.12, help="metres between controller and overlay (default 0.12)")
    p.add_argument("--hold", type=float, default=0.3, help="seconds the trigger stays down (default 0.3: ASTAROT missed 0.15 s, and 0.6 s is its long press)")
    p.add_argument("--hover", type=float, default=1.5,
                   help="seconds the laser rests on the spot before the trigger (default 1.5; the app needs to see it first)")
    p.add_argument("--dry-run", action="store_true", help="aim and report the hit, but do not press")
    output.add_format(p)


def _press(args) -> int:
    profile = profiles.load(args.profile)
    if args.button:
        spots = profile.get("buttons", {}).get(args.overlay, {})
        if args.button not in spots:
            output.note(f"no button {args.button!r} for {args.overlay} in profile {args.profile} "
                        f"(known: {', '.join(spots) or 'none'})")
            return output.USAGE
        u, v = spots[args.button]
    else:
        u, v = args.at
    pitch = float(profile.get("laser", {}).get("pitch_up_deg", 0.0))
    with steamvr.background_client() as openvr:
        result = press.press(openvr, args.overlay, u, v, hand=args.hand, distance_m=args.distance,
                             pitch_up_deg=pitch, hold_s=args.hold, hover_s=args.hover, dry_run=args.dry_run)
    record = {**result.to_dict(), "button": args.button}
    if result.error:
        text = f"{args.overlay} at ({u}, {v}): {result.error}"
    else:
        hit = (f"SteamVR's test hits uv {result.hit_uv} at {result.hit_distance_m} m" if result.hit
               else "SteamVR's test does not hit the overlay")
        text = (f"{args.overlay} at ({u}, {v}) -> {result.target_m}; {args.hand} controller at {result.controller_m}; "
                f"{hit}; {'pressed' if result.pressed else 'not pressed (dry run)'}")
    output.emit(record, text, args.format)
    if result.error:
        return output.FAILED
    return output.OK if result.hit else output.NOT_MET


def run(args) -> int:
    if getattr(args, "action", None) == "press":
        return _press(args)
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
