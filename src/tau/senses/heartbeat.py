"""Service heartbeats: a quick GET on each service's ``health_url``, or, for a
service found by discovery with only a ``port``, whether that port on this
machine accepts a connection."""

from __future__ import annotations

import urllib.request
from typing import Any, Dict, Mapping, Optional

from tau.profile import iter_services


def check_url(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 400
    except Exception:
        return False


def check_port(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def check_all(profile: Mapping[str, Any]) -> Dict[str, Optional[bool]]:
    out: Dict[str, Optional[bool]] = {}
    for s in iter_services(profile):
        if s.get("health_url"):
            out[s["name"]] = check_url(s["health_url"])
        elif s.get("port") and int(s["port"]) >= 49152:
            out[s["name"]] = None       # a temporary port: "unknown", never a false "down"
        elif s.get("port"):
            out[s["name"]] = check_port(s["port"])
        else:
            out[s["name"]] = None
    return out
