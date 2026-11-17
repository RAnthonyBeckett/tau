"""Discovers assistant programs installed on the machine."""

from __future__ import annotations

from typing import Any

from tau.connect.ctx import LookContext
from tau.connect.finders.cli_tools import path_programs
from tau.connect.recipes import load
from tau.connect.record import Connection, make_id
from tau.connect.registry import finder


@finder("assistant_workers", timeout=20.0, max_rows=100)
def find_assistant_workers(ctx: LookContext) -> list[Connection]:
    """Find assistant programs that can take on work."""
    programs = path_programs(ctx, skip_system=False)
    try:
        recipe = load("workers")
    except Exception:
        return []

    if not isinstance(recipe, list):
        return []

    connections: list[Connection] = []
    kind = "Assistant that can take on work"
    door = "command line"
    state = "Found"
    allows = (
        "TAU could hand it a job in a folder you choose and bring back the result."
        " TAU cannot tell from here whether it is signed in."
    )

    for entry in recipe:
        if not isinstance(entry, dict):
            continue
        raw_cmd = entry.get("command")
        if not isinstance(raw_cmd, str) or not raw_cmd.strip():
            continue
        command = raw_cmd.strip()
        cmd_key = command.lower()
        if cmd_key not in programs:
            continue

        prog = programs[cmd_key]
        folder = prog["folder"]
        file_name = prog["file"]

        raw_label = entry.get("label")
        if isinstance(raw_label, str) and raw_label.strip():
            name = raw_label.strip()
        else:
            name = command

        evidence = f"Its command can be run by name on this machine. It sits in {folder}."
        conn = Connection(
            id=make_id(ctx.machine, "", kind, cmd_key),
            machine=ctx.machine,
            kind=kind,
            name=name,
            evidence=evidence,
            door=door,
            state=state,
            allows=allows,
            detail={
                "command": command,
                "folder": folder,
                "file": file_name,
            },
        )
        connections.append(conn)

    connections.sort(key=lambda c: c.name.lower())
    return connections
