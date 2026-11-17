"""Connection record definitions and merge logic."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import Any

STATES = ("Found", "Needs something", "Ready", "On", "Not found this time")
OFF = "off"


def make_id(machine: str, place: str, kind: str, key: str) -> str:
    """Return a stable connection identifier from its components."""
    slug = re.sub(r"[^a-z0-9]+", "-", kind.lower()).strip("-")
    parts = [
        machine.strip().lower(),
        place.strip().lower(),
        kind.strip().lower(),
        key.strip().lower(),
    ]
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"


@dataclass
class Connection:
    """A discovered connection to a tool or service."""

    id: str
    machine: str
    kind: str
    name: str
    evidence: str
    door: str
    state: str = "Found"
    place: str = ""
    allows: str = ""
    needs: str = ""
    setting: str = OFF
    last_seen: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate state and required fields."""
        if self.state not in STATES:
            raise ValueError(f"State must be one of {STATES}, got: {self.state}")
        for val, field_name in (
            (self.id, "id"),
            (self.machine, "machine"),
            (self.kind, "kind"),
            (self.name, "name"),
            (self.evidence, "evidence"),
        ):
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"Field '{field_name}' must not be empty")

    def to_dict(self) -> dict[str, Any]:
        """Return all fields as a dictionary."""
        return {
            "id": self.id,
            "machine": self.machine,
            "kind": self.kind,
            "name": self.name,
            "evidence": self.evidence,
            "door": self.door,
            "state": self.state,
            "place": self.place,
            "allows": self.allows,
            "needs": self.needs,
            "setting": self.setting,
            "last_seen": self.last_seen,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Connection:
        """Build a Connection ignoring unknown keys."""
        known = {
            "id",
            "machine",
            "kind",
            "name",
            "evidence",
            "door",
            "state",
            "place",
            "allows",
            "needs",
            "setting",
            "last_seen",
            "detail",
        }
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


def merge(
    previous: list[dict[str, Any]],
    found: list[Connection],
    now: str,
) -> list[dict[str, Any]]:
    """Combine previous connection records with newly found ones."""
    prev_by_id = {item["id"]: dict(item) for item in previous if "id" in item}
    found_ids: set[str] = set()
    result: list[dict[str, Any]] = []

    for item in found:
        row = item.to_dict()
        row["last_seen"] = now
        if item.id in prev_by_id:
            row["setting"] = prev_by_id[item.id].get("setting", OFF)
        if row["setting"] != OFF and row["state"] in ("Found", "Ready"):
            row["state"] = "On"
        result.append(row)
        found_ids.add(item.id)

    for prev in previous:
        prev_id = prev.get("id")
        if prev_id and prev_id not in found_ids:
            kept = dict(prev)
            kept["state"] = "Not found this time"
            result.append(kept)
            found_ids.add(prev_id)

    def sort_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(entry.get("machine", "")),
            str(entry.get("place", "")),
            str(entry.get("kind", "")),
            str(entry.get("name", "")).lower(),
        )

    result.sort(key=sort_key)
    return result
