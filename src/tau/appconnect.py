"""App connections with a real result: the TAU app contract, version 1.

An app that wants TAU to drive it serves three loopback URLs under its base address:

* ``GET  <base>/tau/v1/describe`` -> ``{"name": "...", "actions": [{"id": "render", "label": "Render the timeline"}]}``
* ``GET  <base>/tau/v1/status``   -> any JSON object (shown as the app's status)
* ``POST <base>/tau/v1/do``       ``{"action": "render", "args": {...}}`` -> ``{"ok": true|false, "result": "..."}``

TAU offers only the actions the app describes, and reports the app's own ``ok`` and ``result``.
An HTTP 200 without ``"ok": true`` is reported as "the app didn't confirm it", never as done.
Addresses must be on this computer (127.0.0.1, localhost or ::1).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Mapping

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
TIMEOUT = 5.0


def _base(address: str) -> str:
    a = (address or "").strip().rstrip("/")
    if a and "://" not in a:
        a = "http://" + a
    u = urllib.parse.urlparse(a)
    if u.scheme not in ("http", "https") or (u.hostname or "") not in LOOPBACK:
        raise PermissionError("App connections must be on this computer (an address like 127.0.0.1:PORT).")
    return a


def _get(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        return json.loads(r.read() or b"null")


def describe(address: str) -> Dict[str, Any]:
    """Ask the app what TAU can do with it. Never guesses actions."""
    base = _base(address)
    try:
        d = _get(base + "/tau/v1/describe")
    except urllib.error.HTTPError as exc:
        return {"state": "Unavailable", "why": f"The app answered but doesn't offer TAU's connection (HTTP {exc.code}).",
                "next": "Check the app has its TAU connection switched on, or that the address is right."}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"state": "Unavailable", "why": f"Nothing answered at {base} ({getattr(exc, 'reason', exc)}).",
                "next": "Start the app, then check again."}
    acts = [a for a in (d.get("actions") or []) if isinstance(a, dict) and a.get("id") and a.get("label")] \
        if isinstance(d, dict) else []
    if not acts:
        return {"state": "Needs connection", "why": "The app answered but listed nothing TAU can do.",
                "next": "Check the app's TAU connection settings."}
    return {"state": "Ready", "name": str(d.get("name") or "App"), "address": base,
            "actions": [{"id": str(a["id"]), "label": str(a["label"])} for a in acts],
            "why": "The app lists what TAU may ask it to do and reports each result."}


def status(address: str) -> Dict[str, Any]:
    d = _get(_base(address) + "/tau/v1/status")
    return d if isinstance(d, dict) else {"value": d}


def do(conn: Mapping[str, Any], action: str, args: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Run one described action and return the app's own outcome."""
    ids = {a["id"]: a["label"] for a in conn.get("actions") or []}
    if action not in ids:
        raise PermissionError(f"{conn.get('name', 'That app')} doesn't offer that.")
    req = urllib.request.Request(_base(conn["address"]) + "/tau/v1/do",
                                 data=json.dumps({"action": action, "args": dict(args or {})}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT * 6) as r:
            body = json.loads(r.read() or b"null")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "say": f"{conn.get('name')} refused it (HTTP {exc.code})."}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ok": False, "say": f"{conn.get('name')} didn't answer ({getattr(exc, 'reason', exc)}). "
                                    "It may or may not have done it; check the app."}
    if not isinstance(body, dict) or body.get("ok") is not True:
        why = body.get("result") if isinstance(body, dict) else ""
        return {"ok": False, "say": f"{conn.get('name')} didn't confirm it" + (f": {why}" if why else ".")}
    return {"ok": True, "say": f"{conn.get('name')}: {body.get('result') or 'done, it says.'}", "result": body}
