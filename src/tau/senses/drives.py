"""Drives. Network and slow drives are listed but never walked: TAU only ever
asks the OS for size and free space (one ``statvfs``/``GetDiskFreeSpaceEx``)."""

from __future__ import annotations

import platform
from typing import Any, Dict, List


def list_drives() -> List[Dict[str, Any]]:
    import importlib

    # the module, not the ``collect`` function that tau.discovery re-exports under the same name
    c = importlib.import_module("tau.discovery.collect")

    if platform.system() == "Windows":
        r = c.default_runner
        return c.parse_win_drives(r(c._ps(c.WIN_DRIVES)), r(c._ps(c.WIN_MEDIA)))
    return c.linux_drives()


def may_walk(path: str, never_walk: List[str]) -> bool:
    """False when ``path`` sits on a drive marked never-walk in the profile."""
    p = str(path).replace("\\", "/").lower()
    for m in never_walk or []:
        mm = str(m).replace("\\", "/").lower().rstrip("/")
        if mm and (p == mm or p.startswith(mm + "/") or (len(mm) == 2 and mm[1] == ":" and p.startswith(mm))):
            return False
    return True
