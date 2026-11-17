"""Discovers command-line tools available in PATH."""

from __future__ import annotations

import os
from typing import Any

from tau.connect.ctx import LookContext
from tau.connect.record import Connection, make_id
from tau.connect.registry import finder

OS_FOLDERS = {
    "/bin",
    "/sbin",
    "/usr/bin",
    "/usr/sbin",
    "/usr/games",
    "/usr/local/games",
}

NON_PROGRAM_EXTS = (
    ".so",
    ".a",
    ".conf",
    ".txt",
    ".md",
    ".json",
)


def path_programs(ctx: LookContext, skip_system: bool = True) -> dict[str, dict[str, Any]]:
    """Return programs found in PATH folders mapped by lower-cased name."""
    cache = getattr(ctx, "_path_programs_cache", None)
    if cache is None:
        cache = {}
        setattr(ctx, "_path_programs_cache", cache)
    if skip_system in cache:
        return cache[skip_system]

    raw_path = ctx.env("PATH")
    if not raw_path.strip():
        cache[skip_system] = {}
        return cache[skip_system]

    folders: list[str] = []
    seen_folders: set[str] = set()
    # Go by the system being looked at, not the one this code happens to run on.
    windows = ctx.system == "Windows"
    for piece in raw_path.split(";" if windows else ":"):
        clean = piece.strip().strip('"\'').strip()
        if not clean:
            continue
        norm = clean.lower().replace("/", "\\") if windows else clean
        if norm in seen_folders:
            continue
        seen_folders.add(norm)
        folders.append(clean)

    filtered_folders: list[str] = []
    if skip_system:
        if ctx.system == "Windows":
            sys_root = ctx.env("SystemRoot").strip()
            if sys_root:
                norm_root = sys_root.lower().replace("/", "\\").rstrip("\\")
                for folder in folders:
                    norm_f = folder.lower().replace("/", "\\").rstrip("\\")
                    if norm_f == norm_root or norm_f.startswith(norm_root + "\\"):
                        continue
                    filtered_folders.append(folder)
            else:
                filtered_folders = folders
        else:
            for folder in folders:
                if (folder.rstrip("/") or "/") in OS_FOLDERS:
                    continue
                filtered_folders.append(folder)
    else:
        filtered_folders = folders

    candidate_folders = filtered_folders[:150]

    pathext_raw = ctx.env("PATHEXT")
    if pathext_raw.strip():
        win_exts = [e.strip().lower() for e in pathext_raw.split(";") if e.strip()]
    else:
        win_exts = [".exe", ".cmd", ".bat", ".com"]

    tools_by_key: dict[str, dict[str, Any]] = {}

    for folder in candidate_folders:
        names = ctx.list_dir(folder, start=True)
        folder_tools: dict[str, tuple[str, str]] = {}

        for filename in names:
            if ctx.system == "Windows":
                base, ext = os.path.splitext(filename)
                if ext.lower() in win_exts:
                    tool_name = base
                    tool_key = tool_name.lower()
                    if tool_key not in folder_tools:
                        folder_tools[tool_key] = (tool_name, filename)
            else:
                if filename.startswith("."):
                    continue
                if filename.lower().endswith(NON_PROGRAM_EXTS):
                    continue
                tool_name = filename
                tool_key = tool_name.lower()
                if tool_key not in folder_tools:
                    folder_tools[tool_key] = (tool_name, filename)

        for tool_key, (tool_name, filename) in folder_tools.items():
            if tool_key not in tools_by_key:
                tools_by_key[tool_key] = {
                    "name": tool_name,
                    "folder": folder,
                    "file": filename,
                    "also_in": 0,
                }
            else:
                tools_by_key[tool_key]["also_in"] += 1

    cache[skip_system] = tools_by_key
    return cache[skip_system]


@finder("command_line_tools", timeout=20.0, max_rows=1500)
def find_cli_tools(ctx: LookContext) -> list[Connection]:
    """List programs that can be run by name from a command window."""
    tools_by_key = path_programs(ctx, skip_system=True)

    connections: list[Connection] = []
    kind = "Command-line tool"
    door = "command line"
    state = "Found"
    allows = (
        "Nothing yet. If you switch it on, TAU will read its help text and can then suggest commands for you to"
        " approve."
    )

    for tool_key, info in tools_by_key.items():
        tool_name = info["name"]
        first_folder = info["folder"]
        evidence = f"It can be run by name from a command window. It sits in {first_folder}."
        conn = Connection(
            id=make_id(ctx.machine, "", kind, tool_key),
            machine=ctx.machine,
            kind=kind,
            name=tool_name,
            evidence=evidence,
            door=door,
            state=state,
            allows=allows,
            detail={
                "folder": first_folder,
                "file": info["file"],
                "also_in": info["also_in"],
            },
        )
        connections.append(conn)

    connections.sort(key=lambda c: c.name.lower())
    return connections
