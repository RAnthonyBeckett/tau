"""Discovers installed desktop applications across operating systems."""

from __future__ import annotations

import os
from typing import Any

from tau.connect.ctx import LookContext
from tau.connect.record import Connection, make_id
from tau.connect.registry import finder

WIN_UNINSTALL_KEYS = [
    ("HKLM", "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall"),
    ("HKLM", "SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall"),
    ("HKCU", "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall"),
]


def _find_windows_apps(ctx: LookContext) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    # a. Start menu entries
    start_folders: list[str] = []
    prog_data = ctx.env("ProgramData").strip()
    if prog_data:
        start_folders.append(os.path.join(prog_data, "Microsoft", "Windows", "Start Menu", "Programs"))
    app_data = ctx.env("APPDATA").strip()
    if app_data:
        start_folders.append(os.path.join(app_data, "Microsoft", "Windows", "Start Menu", "Programs"))

    for folder in start_folders:
        names = ctx.list_dir(folder, start=True)
        for name in names:
            if name.lower().endswith(".lnk"):
                prog_name = name[:-4]
                prog_lower = prog_name.lower()
                if (
                    prog_lower.startswith("uninstall")
                    or "readme" in prog_lower
                    or "help" in prog_lower
                    or "website" in prog_lower
                ):
                    continue
                entries.append({
                    "name": prog_name,
                    "source": "start menu",
                    "evidence": "It has an entry in the Start menu.",
                    "extra": {},
                })
            elif "." not in name:
                subfolder = os.path.join(folder, name)
                subnames = ctx.list_dir(subfolder)
                for subname in subnames:
                    if subname.lower().endswith(".lnk"):
                        prog_name = subname[:-4]
                        prog_lower = prog_name.lower()
                        if (
                            prog_lower.startswith("uninstall")
                            or "readme" in prog_lower
                            or "help" in prog_lower
                            or "website" in prog_lower
                        ):
                            continue
                        entries.append({
                            "name": prog_name,
                            "source": "start menu",
                            "evidence": "It has an entry in the Start menu.",
                            "extra": {},
                        })

    # b. Programs startable by name
    for hive in ("HKLM", "HKCU"):
        reg_info = ctx.registry_read(hive, "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths")
        subkeys = reg_info.get("subkeys", [])
        if isinstance(subkeys, list):
            for subkey in subkeys:
                if isinstance(subkey, str) and subkey.lower().endswith(".exe"):
                    prog_name = subkey[:-4]
                    entries.append({
                        "name": prog_name,
                        "source": "app paths",
                        "evidence": "Windows lists it as a program that can be started by name.",
                        "extra": {},
                    })

    # c. The installed-programs list
    for hive, base_key in WIN_UNINSTALL_KEYS:
        reg_info = ctx.registry_read(hive, base_key)
        subkeys = reg_info.get("subkeys", [])
        if isinstance(subkeys, list):
            for subkey in subkeys[:600]:
                sub_path = f"{base_key}\\{subkey}"
                entry_data = ctx.registry_read(hive, sub_path)
                values = entry_data.get("values", {})
                if not isinstance(values, dict):
                    continue
                display_name = values.get("DisplayName")
                if not isinstance(display_name, str) or not display_name.strip():
                    continue
                display_name = display_name.strip()

                sys_comp = values.get("SystemComponent")
                if sys_comp == 1 or sys_comp == "1":
                    continue

                parent_key = values.get("ParentKeyName")
                if parent_key and str(parent_key).strip():
                    continue

                name_lower = display_name.lower()
                if (
                    name_lower.startswith("update for")
                    or name_lower.startswith("security update")
                    or name_lower.startswith("hotfix")
                ):
                    continue

                extra: dict[str, Any] = {}
                version = values.get("DisplayVersion")
                if isinstance(version, str):
                    extra["version"] = version
                publisher = values.get("Publisher")
                if isinstance(publisher, str):
                    extra["publisher"] = publisher

                entries.append({
                    "name": display_name,
                    "source": "installed list",
                    "evidence": "Windows lists it as an installed program.",
                    "extra": extra,
                })

    return entries


def _find_darwin_apps(ctx: LookContext) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    folders = ["/Applications", "/System/Applications"]
    home = ctx.env("HOME").strip()
    if home:
        folders.append(os.path.join(home, "Applications"))

    for folder in folders:
        names = ctx.list_dir(folder, start=True)
        for name in names:
            if name.endswith(".app"):
                prog_name = name[:-4]
                entries.append({
                    "name": prog_name,
                    "source": "applications folder",
                    "evidence": "It is in the Applications folder.",
                    "extra": {},
                })
    return entries


def _find_linux_apps(ctx: LookContext) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    raw_data_dirs = ctx.env("XDG_DATA_DIRS", "/usr/local/share:/usr/share").strip()
    if not raw_data_dirs:
        raw_data_dirs = "/usr/local/share:/usr/share"

    candidate_roots = [d.strip() for d in raw_data_dirs.split(":") if d.strip()]

    data_home = ctx.env("XDG_DATA_HOME").strip()
    if not data_home:
        home = ctx.env("HOME").strip()
        if home:
            data_home = os.path.join(home, ".local", "share")
    if data_home:
        candidate_roots.append(data_home)

    app_folders: list[str] = []
    seen_folders: set[str] = set()
    for root in candidate_roots:
        folder = os.path.join(root, "applications")
        norm = os.path.normcase(os.path.normpath(folder))
        if norm not in seen_folders:
            seen_folders.add(norm)
            app_folders.append(folder)

    total_files = 0
    for folder in app_folders:
        if total_files >= 500:
            break
        names = ctx.list_dir(folder, start=True)
        for name in names:
            if total_files >= 500:
                break
            if not name.endswith(".desktop"):
                continue

            total_files += 1
            file_path = os.path.join(folder, name)
            text = ctx.read_text(file_path, limit=20000)

            in_desktop_entry = False
            keys: dict[str, str] = {}
            for line in text.splitlines():
                stripped = line.strip()
                if stripped == "[Desktop Entry]":
                    in_desktop_entry = True
                    continue
                if in_desktop_entry:
                    if stripped.startswith("["):
                        break
                    if not stripped or stripped.startswith("#"):
                        continue
                    if "=" in stripped:
                        key, val = stripped.split("=", 1)
                        key = key.strip()
                        val = val.strip()
                        if key in ("Name", "NoDisplay", "Hidden", "Type", "Exec", "Actions"):
                            if key not in keys:
                                keys[key] = val

            prog_name = keys.get("Name")
            if not prog_name:
                continue

            if keys.get("NoDisplay", "").strip().lower() == "true":
                continue
            if keys.get("Hidden", "").strip().lower() == "true":
                continue
            if "Type" in keys and keys["Type"].strip() != "Application":
                continue

            exec_line = keys.get("Exec", "").strip()
            first_word = exec_line.split()[0] if exec_line.split() else ""
            command = first_word.strip('"\'')

            actions_line = keys.get("Actions", "")
            actions = [a.strip() for a in actions_line.split(";") if a.strip()]

            extra: dict[str, Any] = {
                "command": command,
                "actions": actions,
            }
            entries.append({
                "name": prog_name,
                "source": "desktop entry",
                "evidence": "It has a desktop entry, so it appears in the applications menu.",
                "extra": extra,
            })

    return entries


@finder("installed_programs", timeout=30.0, max_rows=1500)
def find_installed_programs(ctx: LookContext) -> list[Connection]:
    """List installed desktop applications for the current operating system."""
    if ctx.system == "Windows":
        entries = _find_windows_apps(ctx)
    elif ctx.system == "Darwin":
        entries = _find_darwin_apps(ctx)
    else:
        entries = _find_linux_apps(ctx)

    merged: dict[str, dict[str, Any]] = {}
    for entry in entries:
        name = entry["name"]
        key = name.lower()
        source = entry["source"]
        extra = entry["extra"]

        if key not in merged:
            detail: dict[str, Any] = {"sources": [source]}
            detail.update(extra)
            merged[key] = {
                "name": name,
                "evidence": entry["evidence"],
                "sources": [source],
                "detail": detail,
            }
        else:
            if source not in merged[key]["sources"]:
                merged[key]["sources"].append(source)
                merged[key]["detail"]["sources"].append(source)
            for k, v in extra.items():
                if k not in merged[key]["detail"]:
                    merged[key]["detail"][k] = v

    connections: list[Connection] = []
    kind = "Installed program"
    door = "start it, or hand it a file"
    state = "Found"
    allows = "TAU could start it for you, or hand it a file to work on."

    for key, item in merged.items():
        conn = Connection(
            id=make_id(ctx.machine, "", kind, key),
            machine=ctx.machine,
            kind=kind,
            name=item["name"],
            evidence=item["evidence"],
            door=door,
            state=state,
            allows=allows,
            detail=item["detail"],
        )
        connections.append(conn)

    connections.sort(key=lambda c: c.name.lower())
    return connections
