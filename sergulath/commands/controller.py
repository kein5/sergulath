"""sergulath controller: move and press the virtual controllers (needs `sergulath driver register`).

  controller connect --hand left|right      Add the controller to SteamVR (the first time) and
                                            report it connected, with a valid pose.
  controller disconnect --hand left|right   Report it not connected again (it lets go of buttons).
  controller pose --hand H --position X Y Z [--look-at X Y Z | --rotation W X Y Z]
                                            Place it, in the standing universe apps read (metres;
                                            y up, -z forward). It points along its -z.
  controller button --hand H --button trigger|grip|menu|system --state down|up|click [--hold S]
  controller dashboard status|close          SteamVR's dashboard. While it is open (SteamVR opens it
                                            when no game runs) apps get no buttons; close presses the
                                            virtual system button, which toggles it.
  controller state                          What the driver holds for both controllers.

Positions are checked by reading the controller back through OpenVR, as an app would.
"""

from __future__ import annotations

import time

from .. import output
from ..core import aim, driver, steamvr

NAME = "controller"
HELP = "move and press SERGULATH's virtual controllers"


def _hand(p) -> None:
    p.add_argument("--hand", choices=("left", "right"), required=True)


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    for name in ("connect", "disconnect"):
        p = sub.add_parser(name, help=f"{name} a controller")
        _hand(p)
        output.add_format(p)
    pose = sub.add_parser("pose", help="place a controller (standing universe)")
    _hand(pose)
    where = pose.add_mutually_exclusive_group(required=True)
    where.add_argument("--position", nargs=3, type=float, metavar=("X", "Y", "Z"))
    where.add_argument("--show", metavar="KEY",
                       help="hold the controller so the overlay that follows it (such as a wrist launcher) sits "
                            "in front of the headset, facing it")
    pose.add_argument("--distance", type=float, default=0.45, help="with --show: metres from the head (default 0.45)")
    pose.add_argument("--below", type=float, default=0.10, help="with --show: metres below eye level (default 0.10)")
    turn = pose.add_mutually_exclusive_group()
    turn.add_argument("--look-at", nargs=3, type=float, metavar=("X", "Y", "Z"), help="point its -z at this point")
    turn.add_argument("--rotation", nargs=4, type=float, metavar=("W", "X", "Y", "Z"), help="quaternion")
    output.add_format(pose)
    button = sub.add_parser("button", help="press or release a button")
    _hand(button)
    button.add_argument("--button", choices=("trigger", "grip", "menu", "system"), required=True)
    button.add_argument("--state", choices=("down", "up", "click"), default="click")
    button.add_argument("--hold", type=float, default=0.3, help="seconds a click holds the button (default 0.3)")
    output.add_format(button)
    dash = sub.add_parser("dashboard", help="SteamVR's dashboard: while it is open, apps get no buttons")
    dash.add_argument("dashboard_action", choices=("status", "close"), help="status (read-only) or close it")
    dash.add_argument("--hand", choices=("left", "right"), default="right", help="the controller whose system button closes it")
    output.add_format(dash)
    state = sub.add_parser("state", help="what the driver holds")
    output.add_format(state)


def _holding_to_show(openvr, key: str, distance: float, below: float):
    """The pose for the device an overlay follows, so the overlay sits in front of the head."""
    from ..core import placement

    system = openvr.VRSystem()
    poses = system.getDeviceToAbsoluteTrackingPose(openvr.TrackingUniverseStanding, 0, openvr.k_unMaxTrackedDeviceCount)
    head = poses[openvr.k_unTrackedDeviceIndex_Hmd]
    if not head.bPoseIsValid:
        output.note("the headset has no pose: nothing to show the overlay to (a virtual headset: `sergulath hmd on`)")
        return None
    overlay = openvr.IVROverlay()
    handle = overlay.findOverlay(key)
    kind = overlay.getOverlayTransformType(handle)
    if placement.TRANSFORM.get(int(getattr(kind, "value", kind))) != "tracked_device_relative":
        output.note(f"{key} does not follow a device (connect the hand it follows first)")
        return None
    _, relative = overlay.getOverlayTransformTrackedDeviceRelative(handle)
    head_m = placement.to_rows(head.mDeviceToAbsoluteTracking)
    forward = [-c for c in aim.axis(head_m, 2)]
    eye = aim.axis(head_m, 3)
    spot = [eye[i] + forward[i] * distance for i in range(3)]
    spot[1] -= below
    return aim.holder_for(placement.to_rows(relative), aim.facing(spot, eye))


def click(hand: str, button: str, hold: float) -> None:
    driver.send(f"button {hand} {button} 1")
    time.sleep(hold)
    driver.send(f"button {hand} {button} 0")


def dashboard_open(openvr) -> bool:
    return bool(openvr.IVROverlay().isDashboardVisible())


def _dashboard(args) -> int:
    with steamvr.background_client() as openvr:
        before = dashboard_open(openvr)
        after = before
        if args.dashboard_action == "close" and before:
            # SteamVR keeps the system button for itself: a click toggles the dashboard.
            driver.send(f"connect {args.hand}")
            click(args.hand, "system", 0.2)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and (after := dashboard_open(openvr)):
                time.sleep(0.2)
        # Apps get buttons again a moment after it closes.
        deadline = time.monotonic() + 3
        while not (available := bool(openvr.VRSystem().isInputAvailable())) and time.monotonic() < deadline:
            time.sleep(0.2)
    record = {"open_before": before, "open": after, "apps_get_input": available}
    output.emit(record, f"dashboard {'open' if after else 'closed'}" + (" (was open)" if before and not after else "") +
                f"; apps {'get' if available else 'do not get'} buttons", args.format)
    return output.OK if not after else output.NOT_MET


def run(args) -> int:
    if args.action == "dashboard":
        return _dashboard(args)
    if args.action == "state":
        record = driver.state()
        output.emit(record, "\n".join(f"{h}: {record[h]}" for h in ("left", "right")), args.format)
        return output.OK
    if args.action in ("connect", "disconnect"):
        driver.send(f"{args.action} {args.hand}")
        record = {"hand": args.hand, "connected": args.action == "connect"}
        output.emit(record, f"{args.hand} {args.action}ed", args.format)
        return output.OK
    if args.action == "button":
        if args.state == "click":
            click(args.hand, args.button, args.hold)
        else:
            driver.send(f"button {args.hand} {args.button} {1 if args.state == 'down' else 0}")
        output.emit({"hand": args.hand, "button": args.button, "state": args.state},
                    f"{args.hand} {args.button} {args.state}", args.format)
        return output.OK
    # pose
    with steamvr.background_client() as openvr:
        if args.show:
            standing = _holding_to_show(openvr, args.show, args.distance, args.below)
            if standing is None:
                return output.FAILED
            position = [round(standing[r][3], 4) for r in range(3)]
        else:
            position = list(args.position)
            if args.look_at:
                standing = aim.look_at(position, list(args.look_at))
            else:
                standing = aim.from_quaternion(position, list(args.rotation) if args.rotation else [1.0, 0.0, 0.0, 0.0])
        driver.set_pose(openvr, args.hand, standing)
        time.sleep(0.2)
        seen = driver.read_pose(openvr, args.hand)
    record = {"hand": args.hand, "position": position,
              "seen": [round(seen[r][3], 4) for r in range(3)] if seen else None}
    output.emit(record, f"{args.hand} at {position}; apps see " +
                (str(record["seen"]) if seen else "no valid pose (is it connected?)"), args.format)
    return output.OK if seen else output.NOT_MET
