"""App profiles: per-app facts such as overlay keys."""

from __future__ import annotations

import tomllib
from importlib import resources


def load(name: str) -> dict:
    """Load a bundled profile by name, such as "astarot"."""
    text = resources.files(__package__).joinpath(f"{name}.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)


def overlay_keys(profile: dict, legacy: bool = False) -> list[str]:
    table = profile.get("legacy_overlays" if legacy else "overlays", {})
    return list(table)
