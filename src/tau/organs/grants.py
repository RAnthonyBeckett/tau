"""Grant and revoke organ levels, always repeating the change back first."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import yaml

from tau import paths
from tau.organs.base import OrganRegistry, set_level_in_text
from tau.profile import LEVELS

PLAIN = {
    "off": "switched off",
    "sense": "able to look but not change anything",
    "suggest": "able to suggest changes and wait for your yes",
    "act": "able to act without asking and tell you in one sentence (destructive things still need a yes)",
}


def describe_change(organ: str, level: str, machine: Optional[str] = None,
                    name: str = "TAU") -> str:
    try:
        desc = OrganRegistry.get(organ).description
    except KeyError:
        desc = organ
    where = f" on {machine}" if machine else ""
    return f"{name}'s access to {organ} ({desc}){where} will be {PLAIN[level]}. Is that right?"


def apply_level(organ: str, level: str, machine: Optional[str] = None,
                profile_file: Optional[Path] = None) -> Path:
    if level not in LEVELS:
        raise ValueError(f"level must be one of {', '.join(LEVELS)}")
    profile_file = Path(profile_file) if profile_file else paths.profile_path()
    text = profile_file.read_text(encoding="utf-8")
    if machine is None:
        new = set_level_in_text(text, organ, level)
        if new is not None:
            profile_file.write_text(new, encoding="utf-8")
            return profile_file
    data = yaml.safe_load(text) or {}
    organs = data.setdefault("organs", {})
    cur = organs.get(organ, "off")
    if machine:
        if not isinstance(cur, dict):
            cur = {"default": cur, "machines": {}}
        cur.setdefault("machines", {})[machine] = level
        organs[organ] = cur
    else:
        if isinstance(cur, dict):
            cur["default"] = level
        else:
            organs[organ] = level
    profile_file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return profile_file


def grant(organ: str, level: str, ask: Callable[[str], bool],
          machine: Optional[str] = None, profile_file: Optional[Path] = None,
          name: str = "TAU") -> str:
    """Repeat the change, ask for a yes, then write it. Returns what to say."""
    if level not in LEVELS:
        return f"I don't know the level '{level}'. Use one of: {', '.join(LEVELS)}."
    if not ask(describe_change(organ, level, machine, name)):
        return "Okay, nothing changed."
    apply_level(organ, level, machine, profile_file)
    return f"Done. {organ} is now at {level}" + (f" on {machine}." if machine else ".")


def revoke(organ: str, ask: Callable[[str], bool], machine: Optional[str] = None,
           profile_file: Optional[Path] = None, name: str = "TAU") -> str:
    return grant(organ, "off", ask, machine, profile_file, name)
