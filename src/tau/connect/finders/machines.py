"""Discovers other machines that could be paired with TAU."""

from __future__ import annotations

import re
from typing import Any

from tau.connect.ctx import LookContext
from tau.connect.record import Connection, make_id
from tau.connect.registry import finder

_RAW_ADDR_PATTERN = re.compile(r"^[0-9.:]+$")


def _is_valid_nickname(name: str, current_machine: str) -> bool:
    if not name:
        return False
    lower = name.lower()
    if lower == current_machine.lower():
        return False
    if lower in ("localhost", "github.com"):
        return False
    if _RAW_ADDR_PATTERN.match(name):
        return False
    return True


@finder("other_machines", timeout=15.0, max_rows=300)
def find_other_machines(ctx: LookContext) -> list[Connection]:
    """Finds machines that could be paired with TAU."""
    seen: dict[str, dict[str, Any]] = {}
    paired = ctx.snapshot.get("paired") or []

    def _is_paired(name: str) -> bool:
        low = name.lower()
        short = low.split(".", 1)[0]
        for p in paired:
            if isinstance(p, str) and p.strip():
                p_low = p.strip().lower()
                p_short = p_low.split(".", 1)[0]
                if low == p_low or short == p_short:
                    return True
        return False

    def add_candidate(raw_name: str, source_tag: str) -> None:
        trimmed = raw_name.strip()
        if not _is_valid_nickname(trimmed, ctx.machine):
            return
        if _is_paired(trimmed):
            return
        key = trimmed.lower()
        if key not in seen:
            seen[key] = {
                "name": trimmed,
                "seen_as": [],
            }
        if source_tag not in seen[key]["seen_as"]:
            seen[key]["seen_as"].append(source_tag)

    # A. Network neighbours
    neighbours = ctx.snapshot.get("neighbours") or []
    if isinstance(neighbours, list):
        for item in neighbours:
            if isinstance(item, dict):
                n_name = item.get("name")
                if isinstance(n_name, str) and n_name.strip():
                    add_candidate(n_name, "announced on the network")

    # B. Remote-login settings
    if ctx.system == "Windows":
        home = ctx.env("USERPROFILE")
        if not home:
            home = ctx.env("HOME")
        config_path = home + "\\.ssh\\config" if home else ""
    else:
        home = ctx.env("HOME")
        config_path = home + "/.ssh/config" if home else ""

    if home and config_path:
        config_text = ctx.read_text(config_path)
        if config_text:
            for line in config_text.splitlines():
                words = line.strip().split()
                if not words:
                    continue
                if words[0].lower() == "host":
                    for word in words[1:]:
                        if "*" in word or "?" in word or word.startswith("!"):
                            continue
                        add_candidate(word, "named in your remote-login settings")

    kind = "Machine that could be paired"
    door = "pairing"
    allows_text = (
        "TAU does not sign in to it. To include it, install TAU's helper on that machine and pair it."
    )
    connections: list[Connection] = []

    for entry in seen.values():
        name = entry["name"]
        tags = entry["seen_as"]
        if "announced on the network" in tags and "named in your remote-login settings" in tags:
            evidence = "It is announced on the network and named in your remote-login settings."
        elif "announced on the network" in tags:
            evidence = "It is announced on the network."
        else:
            evidence = "It is named in your remote-login settings."

        conn_id = make_id(ctx.machine, "", kind, name.lower())
        connections.append(
            Connection(
                id=conn_id,
                machine=ctx.machine,
                kind=kind,
                name=name,
                evidence=evidence,
                door=door,
                state="Found",
                allows=allows_text,
                detail={"seen_as": list(tags)},
            )
        )

    connections.sort(key=lambda c: c.name.lower())
    return connections
