"""sergulath audio: what comes out of an audio device (Windows, WASAPI).

  audio devices                          Playback devices (read through their loopback) and
                                         recording devices.
  audio check --device NAME [--seconds 5] [--threshold -50] [--min-active 300]
              [--expect sound|silence] [--save FILE.wav] [--allow-input] [-- COMMAND ...]
                                         Listen for SECONDS and report the level (RMS and peak,
                                         dBFS) and how long it was above THRESHOLD in 20 ms frames.
                                         "Sound" is at least MIN-ACTIVE ms above it. Exit 0 when
                                         what was heard is what --expect says (default sound),
                                         1 when not. A COMMAND after -- runs once listening
                                         has begun (to make the sound being checked).

A playback device (such as a virtual cable's input, "CABLE Input") is read through its loopback,
which needs no microphone permission. A recording device ("CABLE Output", a microphone) is
microphone use: it is refused unless --allow-input says the user agreed (ground rule 5). For a
virtual cable both ends carry the same signal: check its input. Needs `pip install sergulath[audio]`.
"""

from __future__ import annotations

from pathlib import Path

from .. import output
from ..core import audio

NAME = "audio"
HELP = "what comes out of an audio device: level and whether there was sound"


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    listing = sub.add_parser("devices", help="list the devices")
    output.add_format(listing)
    check = sub.add_parser("check", help="listen to a device and measure it")
    check.add_argument("--device", required=True, help="device name, or a part of it")
    check.add_argument("--seconds", type=float, default=5.0, help="how long to listen (default 5)")
    check.add_argument("--threshold", type=float, default=-50.0, help="dBFS a 20 ms frame must exceed (default -50)")
    check.add_argument("--min-active", type=int, default=300, help="ms above the threshold that count as sound (default 300)")
    check.add_argument("--expect", choices=("sound", "silence"), default="sound", help="what passes (default sound)")
    check.add_argument("--save", metavar="FILE", help="also write what was heard as a WAV file")
    check.add_argument("--allow-input", action="store_true",
                       help="allow a recording device (microphone use; only with the user's consent)")
    check.add_argument("during", nargs="*", metavar="-- COMMAND",
                       help="a command to run once listening has begun, such as one that makes the app speak")
    output.add_format(check)


def run(args) -> int:
    if args.action == "devices":
        for d in audio.devices():
            output.emit(d.to_dict(), f"{d.kind:<6} {d.rate} Hz {d.channels} ch  {d.name}", args.format)
        return output.OK
    try:
        device = audio.find(args.device, args.allow_input)
    except PermissionError as e:
        output.note(str(e))
        return output.USAGE
    except (LookupError, ValueError) as e:
        output.note(str(e))
        return output.USAGE
    during = None
    if args.during:
        import subprocess
        import sys

        command = [sys.executable, "-m", "sergulath", *args.during[1:]] if args.during[0] == "sergulath" else args.during
        during = lambda: subprocess.run(command, capture_output=True)  # noqa: E731
    level = audio.check(device, args.seconds, args.threshold, args.min_active, Path(args.save) if args.save else None,
                        during)
    heard = "sound" if level.sound else "silence"
    output.emit({**level.to_dict(), "heard": heard, "expected": args.expect},
                f"{level.device} ({level.kind}): {heard}; RMS {level.rms_dbfs} dBFS, peak {level.peak_dbfs} dBFS, "
                f"{level.active_ms} ms above {level.threshold_dbfs} dBFS (longest {level.longest_active_ms} ms) in "
                f"{level.seconds:g} s", args.format)
    return output.OK if heard == args.expect else output.NOT_MET
