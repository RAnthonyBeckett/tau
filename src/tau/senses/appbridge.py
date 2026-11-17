"""App bridges: plugins for apps that expose a local API.

A bridge has ``status()`` (read) and ``command(text)`` (change). The profile
lists bridges under ``app_bridges``; ``kind`` picks the class. One example
ships: ``http_json``, which GETs a JSON status URL and can POST a command to
another URL. Write your own by subclassing ``AppBridge`` and adding it to
``BRIDGE_KINDS`` from an organ file in ``<TAU home>/organs/``.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, Mapping, Type

from tau.profile import as_list


class AppBridge:
    def __init__(self, name: str, config: Mapping[str, Any]):
        self.name = name
        self.config = dict(config)

    def status(self) -> Dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def command(self, text: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def summarize(self, data: Dict[str, Any]) -> str:
        keys = list(data)[:3]
        return f"{self.name}: " + ", ".join(f"{k} {data[k]}" for k in keys)


class HttpJsonBridge(AppBridge):
    """Example bridge for any app with a loopback JSON status endpoint."""

    def status(self) -> Dict[str, Any]:
        with urllib.request.urlopen(self.config["url"], timeout=3) as r:
            data = json.loads(r.read())
        return data if isinstance(data, dict) else {"value": data}

    def command(self, text: str) -> str:
        url = self.config.get("command_url")
        if not url:
            return f"{self.name} has no command URL."
        req = urllib.request.Request(url, data=json.dumps({"command": text}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return f"{self.name} answered {r.status}."


BRIDGE_KINDS: Dict[str, Type[AppBridge]] = {"http_json": HttpJsonBridge}


def bridges_from_profile(profile: Mapping[str, Any]) -> Dict[str, AppBridge]:
    out = {}
    for b in as_list(profile.get("app_bridges")):
        if isinstance(b, dict) and b.get("name") and b.get("kind") in BRIDGE_KINDS:
            out[b["name"]] = BRIDGE_KINDS[b["kind"]](b["name"], b)
    return out
