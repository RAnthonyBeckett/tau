"""Recipe data loader for TAU."""

from __future__ import annotations

import pathlib
import re
from typing import Any

import yaml

_NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")
_CACHE: dict[str, Any] = {}


def load(name: str) -> Any:
    """Load a YAML recipe file by name from this package."""
    if not isinstance(name, str) or not _NAME_PATTERN.match(name):
        raise ValueError(f"Invalid recipe name: {name!r}")
    if name in _CACHE:
        return _CACHE[name]
    target = pathlib.Path(__file__).parent / f"{name}.yaml"
    text = target.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    _CACHE[name] = data
    return data
