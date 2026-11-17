"""Organ framework. Importing this package registers the built-in organs.

Your own organs: drop a ``.py`` file in ``<TAU home>/organs/`` that builds an
``Organ``, adds actions with ``@organ.action(...)`` and calls
``OrganRegistry.add``. ``load_user_organs()`` imports them at start-up.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import List

from tau import paths
from tau.organs import builtin  # noqa: F401  (registers built-ins)
from tau.organs.base import (ALLOWED, DENIED, ERROR, NEEDS_YES, OFF, Organ,
                             OrganRegistry, Organs, Outcome, decide, level_for)

__all__ = ["Organ", "OrganRegistry", "Organs", "Outcome", "decide", "level_for",
           "ALLOWED", "DENIED", "NEEDS_YES", "OFF", "ERROR", "load_user_organs"]


def load_user_organs(folder: Path | None = None) -> List[str]:
    folder = Path(folder) if folder else paths.tau_home() / "organs"
    loaded = []
    if not folder.is_dir():
        return loaded
    for f in sorted(folder.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"tau_user_organ_{f.stem}", f)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            loaded.append(f.stem)
    return loaded
