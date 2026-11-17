"""Append-only action log: one JSON object per line in ``log/actions.jsonl``."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_SECRETISH = re.compile(r"(key|token|secret|password|passwd|auth)", re.I)
_LOCK = threading.Lock()


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("<redacted>" if _SECRETISH.search(str(k)) else _redact(v))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact(v) for v in obj]
    if isinstance(obj, str) and re.search(r"\b(sk|pk|ghp|xox[bp])[-_][A-Za-z0-9_-]{8,}", obj):
        return "<redacted>"
    return obj


class ActionLog:
    """``receipt`` entries are the owner's rule ("access slowly and with receipts"): every change of
    a permission level, and every action at suggest or act level, says what, when, which machine
    and who approved. The display's Recent actions section reads them back from this file."""

    def __init__(self, folder: Path, on_write: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.path = Path(folder) / "actions.jsonl"
        self.on_write = on_write

    def write(self, organ: str, action: str, decision: str,
              args: Optional[Dict[str, Any]] = None, result: Any = None,
              machine: Optional[str] = None, via: str = "",
              receipt: Optional[str] = None, who: Optional[str] = None) -> Dict[str, Any]:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "organ": organ,
            "action": action,
            "decision": decision,
            "machine": machine,
            "via": via,
            "args": _redact(args or {}),
            "result": _redact(result) if result is not None else None,
        }
        if receipt:
            entry["receipt"] = {"what": receipt, "who": who or "", "machine": machine or "this machine"}
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        if receipt and self.on_write is not None:
            try:
                self.on_write(entry)
            except Exception:
                pass
        return entry

    def receipt(self, what: str, who: str, machine: Optional[str] = None, organ: str = "permissions",
                action: str = "grant", args: Optional[Dict[str, Any]] = None, via: str = "") -> Dict[str, Any]:
        return self.write(organ, action, "allowed", args, machine=machine, via=via, receipt=what, who=who)

    def receipts(self, n: int = 30) -> List[Dict[str, Any]]:
        """The last ``n`` receipts for the display: ``{what, when, machine, who}``."""
        out = []
        for e in self.tail(2000):
            r = e.get("receipt")
            if isinstance(r, dict):
                out.append({"what": r.get("what", ""), "who": r.get("who", ""), "machine": r.get("machine", ""),
                            "when": _when(e.get("ts", "")), "ts": e.get("ts", "")})
        return out[-n:]

    def tail(self, n: int = 20) -> list:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        out = []
        for x in lines:
            try:
                out.append(json.loads(x))
            except ValueError:
                continue
        return out


def _when(ts: str) -> str:
    """"2026-11-05T07:12:03+0000" -> "5 Nov, 07:12"."""
    try:
        t = time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        return f"{t.tm_mday} {time.strftime('%b', t)}, {time.strftime('%H:%M', t)}"
    except Exception:
        return ts
