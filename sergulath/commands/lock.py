"""sergulath lock: who is using SteamVR on this PC (the lease the changing commands share).

  lock status            Read-only. The lease's owner (a working folder), the command, the running
                         pids and what it holds (an `hmd on`, `view open`, `chaperone apply` or
                         `settings set` not undone yet). Exit 0 when free, 1 when held.
  lock release --force   Drop the lease. Only after checking the session that holds it is gone:
                         its change (the virtual headset, say) stays, and must be undone by hand.

Commands that start, quit or restart SteamVR or change its headset, drivers or settings take the
lease and are refused (exit 4) while another working folder holds it. Child commands of a suite,
and `hmd off` after `hmd on` from the same folder, share it.
"""

from __future__ import annotations

from .. import output
from ..core import lock

NAME = "lock"
HELP = "who is using SteamVR on this PC (start/stop/hmd/driver/settings share one lease)"


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    status = sub.add_parser("status", help="show the lease (read-only)")
    output.add_format(status)
    release = sub.add_parser("release", help="drop the lease (needs --force)")
    release.add_argument("--force", action="store_true", help="yes, the session that holds it is gone")
    output.add_format(release)


def _text(lease: dict | None) -> str:
    if not lease:
        return "free"
    holds = ", ".join(f"{h['label']} ({h['snapshot']})" for h in lease.get("holds", [])) or "none"
    return (f"held by {lease['owner']}: {lease.get('command')!r} since {lease.get('since')}; "
            f"running pids {lease.get('pids') or 'none'}; holds {holds}")


def run(args) -> int:
    if args.action == "status":
        lease = lock.live(lock.read())
        mine = bool(lease) and lease.get("owner") == lock.owner()
        output.emit({"held": bool(lease), "mine": mine, "lease": lease, "file": str(lock.lease_file())},
                    _text(lease) + (" (this folder)" if mine else ""), args.format)
        return output.NOT_MET if lease else output.OK
    if not args.force:
        output.note("`lock release` drops another session's lease: check it is gone, then pass --force")
        return output.USAGE
    dropped = lock.force_release()
    output.emit({"released": dropped}, "released " + _text(lock.live(dropped)) if dropped else "nothing to release",
                args.format)
    return output.OK
