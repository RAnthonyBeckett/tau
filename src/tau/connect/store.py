"""Persistent storage and merge coordinator for machine looks."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, List, Optional

from tau.connect.record import Connection, merge


class LookStore:
    """Keeps and merges saved looks for local and paired machines."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _file(self, machine: str) -> Path:
        slug = re.sub(r"[^a-z0-9_-]+", "-", machine.lower()).strip("-")
        if not slug:
            slug = "machine"
        return self.root / f"{slug}.json"

    def _write(self, machine: str, data: Dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._file(machine)
        content = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            dir=self.root,
            prefix=f"{target.stem}_",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        )
        try:
            tmp.write(content)
            tmp.flush()
            tmp.close()
            os.replace(tmp.name, target)
        except Exception:
            if os.path.exists(tmp.name):
                try:
                    os.remove(tmp.name)
                except OSError:
                    pass
            raise

    def load(self, machine: str) -> Dict[str, Any]:
        """Return the saved look for that machine, or an empty dict."""
        p = self._file(machine)
        if not p.is_file():
            return {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
        except Exception:
            return {}

    def machines(self) -> List[str]:
        """Return machine names from every saved look, sorted case-insensitively."""
        names: list[str] = []
        if not self.root.is_dir():
            return names
        for p in self.root.glob("*.json"):
            if p.is_file():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and "machine" in data and isinstance(data["machine"], str):
                        names.append(data["machine"])
                except Exception:
                    continue
        names.sort(key=lambda s: s.lower())
        return names

    def save(self, look: Dict[str, Any]) -> Dict[str, Any]:
        """Merge the incoming look with what is already saved, write and return it."""
        machine = str(look.get("machine", ""))
        saved = self.load(machine)
        previous_connections = saved.get("connections") or []

        found: list[Connection] = []
        for c in look.get("connections") or []:
            if isinstance(c, dict):
                try:
                    found.append(Connection.from_dict(c))
                except Exception:
                    pass

        now = look.get("at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        merged = merge(previous_connections, found, now)

        written: dict[str, Any] = {
            "machine": look.get("machine", machine),
            "at": now,
            "connections": merged,
            "finders": dict(look.get("finders") or {}),
        }
        if "note" in look:
            written["note"] = look["note"]

        self._write(machine, written)
        return written

    def keep(self, machine: str, note: str) -> Dict[str, Any]:
        """Keep saved connections when a look cannot be refreshed, updating the note."""
        saved = self.load(machine)
        if saved:
            data = dict(saved)
        else:
            data = {
                "machine": machine,
                "at": "",
                "connections": [],
                "finders": {},
            }
        data["note"] = note
        self._write(machine, data)
        return data

    def set_setting(self, machine: str, connection_id: str, setting: str) -> bool:
        """Set a connection setting and update its state accordingly."""
        look = self.load(machine)
        if not look:
            return False
        conns = look.get("connections")
        if not isinstance(conns, list):
            return False

        target_conn = None
        for c in conns:
            if isinstance(c, dict) and c.get("id") == connection_id:
                target_conn = c
                break
        if target_conn is None:
            return False

        target_conn["setting"] = setting
        state = target_conn.get("state")
        if setting != "off" and state in ("Found", "Ready"):
            target_conn["state"] = "On"
        elif setting == "off" and state == "On":
            target_conn["state"] = "Ready"

        self._write(machine, look)
        return True

    def all_connections(self) -> List[Dict[str, Any]]:
        """Return every connection from every saved look, sorted by machine, kind, name."""
        results: list[dict[str, Any]] = []
        if not self.root.is_dir():
            return results
        for p in self.root.glob("*.json"):
            if p.is_file():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        conns = data.get("connections")
                        if isinstance(conns, list):
                            for c in conns:
                                if isinstance(c, dict):
                                    results.append(c)
                except Exception:
                    continue
        results.sort(
            key=lambda c: (
                str(c.get("machine", "")).lower(),
                str(c.get("kind", "")),
                str(c.get("name", "")).lower(),
            )
        )
        return results
