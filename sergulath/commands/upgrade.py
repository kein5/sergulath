"""sergulath upgrade: check the app's upgrade path in an isolated folder.

  upgrade channel [--channel beta|stable] [--from-version 0.6.0]
      Read-only. Query the app's update channel manifest and report whether a version newer
      than --from-version is published. Exit 0 when newer, 1 when not yet published.

  upgrade check --work-dir DIR [--from PATH | --from-version 0.6.0] [--to PATH | --channel beta]
      Stage the older version in DIR/install, seed settings and synthetic model files, update
      via the beta channel (or a local ZIP, folder or setup.exe), and check that settings and
      models survive and the updated app launches cleanly. Never touches the user's installed
      app or settings (ground rule 4). When an NSIS installer runs, its HKCU registry keys are
      snapshotted and restored in `finally` (ground rule 2).

Environment variable fallbacks (useful when running from a suite file):
  SERGULATH_UPGRADE_FROM   Local package path for --from
  SERGULATH_UPGRADE_TO     Local package path for --to (substitutes while beta is unpublished)

Exit codes: 0 passed / newer available, 1 check failed or beta not yet published (see JSON
`status`), 2 usage error, 3 SteamVR is not running (when --check-overlay is used), 4 failed.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import output, profiles
from ..core import upgrade as core

NAME = "upgrade"
HELP = "check update channels and upgrade paths (0.6.0 -> beta or local package) in an isolated folder"


def add_arguments(parser) -> None:
    parser.add_argument("--profile", default="astarot", help="app profile (default astarot)")
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")

    ch = sub.add_parser("channel", help="read-only: check whether the channel has a version newer than --from-version")
    ch.add_argument("--channel", choices=("beta", "stable"), default="beta", help="update channel (default beta)")
    ch.add_argument("--from-version", help="base version to compare against (default from profile, e.g. 0.6.0)")
    output.add_format(ch)

    ck = sub.add_parser("check", help="run the isolated upgrade check from --from-version to beta or --to PATH")
    ck.add_argument("--work-dir", required=True, metavar="DIR", help="isolated sandbox directory for the upgrade check")
    ck.add_argument("--from", dest="from_path", metavar="PATH",
                    help="local package (ZIP, folder or setup.exe) for the old version (else SERGULATH_UPGRADE_FROM or GitHub release)")
    ck.add_argument("--from-version", help="old version to start from (default from profile, e.g. 0.6.0)")
    ck.add_argument("--to", dest="to_path", metavar="PATH",
                    help="local package (ZIP, folder or setup.exe) to update to (else SERGULATH_UPGRADE_TO or --channel)")
    ck.add_argument("--channel", choices=("beta", "stable"), default="beta",
                    help="update channel used when --to is not set (default beta)")
    ck.add_argument("--method", choices=("auto", "installer", "zip"), default="auto",
                    help="how to apply the update: auto (default), installer, or zip")
    ck.add_argument("--cache-dir", metavar="DIR", help="folder for caching downloaded release packages")
    ck.add_argument("--expect-version", metavar="VER", help="require the updated executable to report this version")
    ck.add_argument("--allow-same-version", action="store_true",
                    help="do not fail when the updated executable has the same version string as the old one")
    ck.add_argument("--boot-wait", type=float, default=10.0,
                    help="seconds to keep the launched app running to confirm startup (default 10)")
    ck.add_argument("--boot-timeout", type=float, default=30.0,
                    help="seconds to wait for the main window on startup (default 30)")
    ck.add_argument("--no-window-check", action="store_true", help="skip checking for the main window on launch")
    ck.add_argument("--check-overlay", metavar="KEY",
                    help="also verify this OpenVR overlay appears after the updated version launches")
    ck.add_argument("--skip-old-boot", action="store_true",
                    help="skip starting the old version before applying the update")
    output.add_format(ck)


def _run_channel(args, profile: dict) -> int:
    up_info = profile.get("upgrade", {})
    from_ver = args.from_version or up_info.get("base_version", "0.6.0")
    manifest_url = up_info.get(f"{args.channel}_manifest", "")
    if not manifest_url:
        output.note(f"profile {args.profile} defines no {args.channel}_manifest")
        return output.USAGE
    status = core.check_channel(manifest_url, from_ver, channel=args.channel)
    text = (
        f"{args.channel} channel: {status.channel_version} "
        + (f"(newer than {from_ver})" if status.newer else f"(not newer than {from_ver})")
    )
    output.emit({"source": "channel", **status.to_dict()}, text, args.format)
    return output.OK if status.newer else output.NOT_MET


def _run_check(args, profile: dict) -> int:
    if args.boot_wait < 0 or args.boot_timeout <= 0:
        output.note("--boot-wait must be >= 0 and --boot-timeout must be > 0")
        return output.USAGE
    up_info = profile.get("upgrade", {})
    from_ver = args.from_version or up_info.get("base_version", "0.6.0")
    from_env = os.environ.get("SERGULATH_UPGRADE_FROM", "").strip()
    to_env = os.environ.get("SERGULATH_UPGRADE_TO", "").strip()
    from_path = Path(args.from_path or from_env) if (args.from_path or from_env) else None
    to_path = Path(args.to_path or to_env) if (args.to_path or to_env) else None
    cache_dir = Path(args.cache_dir) if args.cache_dir else None

    def on_stage(stage: str, info: dict) -> None:
        if args.format == "json":
            output.emit({"source": "stage", "stage": stage, **info}, "", "json")

    result = core.run_upgrade_check(
        profile=profile,
        work_dir=Path(args.work_dir),
        from_path=from_path,
        from_version=from_ver,
        to_path=to_path,
        channel=args.channel,
        method=args.method,
        cache_dir=cache_dir,
        expect_version=args.expect_version,
        allow_same_version=args.allow_same_version,
        boot_wait_s=args.boot_wait,
        boot_timeout_s=args.boot_timeout,
        check_window=not args.no_window_check,
        overlay_key=args.check_overlay,
        skip_old_boot=args.skip_old_boot,
        on_stage=on_stage,
    )
    mark = result.status.upper()
    text = (
        f"upgrade {mark}: {result.from_version or '?'} -> {result.to_version or '?'} "
        f"({result.delivery or result.channel}) settings={result.settings_preserved} "
        f"models={result.models_preserved} launch={result.launched_ok}"
    )
    if result.reason:
        text += f" ({result.reason})"
    output.emit({"source": "upgrade", **result.to_dict()}, text, args.format)
    return output.OK if result.ok else output.NOT_MET


def run(args) -> int:
    profile = profiles.load(args.profile)
    return _run_channel(args, profile) if args.action == "channel" else _run_check(args, profile)
