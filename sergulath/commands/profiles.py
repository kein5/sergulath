"""sergulath profiles: list the bundled app profiles and their overlay keys (no SteamVR needed)."""

from __future__ import annotations

from .. import output, profiles

NAME = "profiles"
HELP = "list the bundled app profiles and their overlay keys"


def add_arguments(parser) -> None:
    output.add_format(parser)


def run(args) -> int:
    for name in profiles.names():
        profile = profiles.load(name)
        keys = profiles.overlay_keys(profile)
        legacy = profiles.overlay_keys(profile, legacy=True)
        record = {"profile": name, "app": profile.get("name", name), "overlays": keys, "legacy_overlays": legacy}
        text = f"{name}: {profile.get('name', name)}, overlays {', '.join(keys)}"
        if legacy:
            text += f" (older: {', '.join(legacy)})"
        output.emit(record, text, args.format)
    return output.OK
