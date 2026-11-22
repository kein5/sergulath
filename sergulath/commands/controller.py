"""sergulath controller: move and press the virtual controllers (needs `sergulath driver register`).

  controller connect --hand left|right      Add the controller to SteamVR (the first time) and
                                            report it connected, with a valid pose.
  controller disconnect --hand left|right   Report it not connected again (it lets go of buttons).
  controller pose --hand H --position X Y Z [--look-at X Y Z | --rotation W X Y Z]
                                            Place it, in the standing universe apps read (metres;
                                            y up, -z forward). It points along its -z.
  controller button --hand H --button trigger|grip|menu|system --state down|up|click [--hold S]
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
    pose.add_argument("--position", nargs=3, type=float, required=True, metavar=("X", "Y", "Z"))
    turn = pose.add_mutually_exclusive_group()
    turn.add_argument("--look-at", nargs=3, type=float, metavar=("X", "Y", "Z"), help="point its -z at this point")
    turn.add_argument("--rotation", nargs=4, type=float, metavar=("W", "X", "Y", "Z"), help="quaternion")
    output.add_format(pose)
    button = sub.add_parser("button", help="press or release a button")
    _hand(button)
    button.add_argument("--button", choices=("trigger", "grip", "menu", "system"), required=True)
    button.add_argument("--state", choices=("down", "up", "click"), default="click")
    button.add_argument("--hold", type=float, default=0.15, help="seconds a click holds the button (default 0.15)")
    output.add_format(button)
    state = sub.add_parser("state", help="what the driver holds")
    output.add_format(state)


def click(hand: str, button: str, hold: float) -> None:
    driver.send(f"button {hand} {button} 1")
    time.sleep(hold)
    driver.send(f"button {hand} {button} 0")


def run(args) -> int:
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
    position = list(args.position)
    if args.look_at:
        standing = aim.look_at(position, list(args.look_at))
    else:
        standing = aim.from_quaternion(position, list(args.rotation) if args.rotation else [1.0, 0.0, 0.0, 0.0])
    with steamvr.background_client() as openvr:
        driver.set_pose(openvr, args.hand, standing)
        time.sleep(0.2)
        seen = driver.read_pose(openvr, args.hand)
    record = {"hand": args.hand, "position": position,
              "seen": [round(seen[r][3], 4) for r in range(3)] if seen else None}
    output.emit(record, f"{args.hand} at {position}; apps see " +
                (str(record["seen"]) if seen else "no valid pose (is it connected?)"), args.format)
    return output.OK if seen else output.NOT_MET
