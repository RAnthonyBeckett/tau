"""Discovers ways Windows allows programs to be started or driven."""

from __future__ import annotations

import csv
import re
from typing import Any

from tau.connect.ctx import LookContext, WalkRefused
from tau.connect.record import Connection, make_id
from tau.connect.registry import finder

_A_EXCLUDED = {
    "clsid",
    "interface",
    "typelib",
    "appid",
    "installer",
    "directshow",
    "media type",
    "mime",
    "local settings",
    "systemfileassociations",
    "*",
}
_A_SKIP_SCHEMES = {"http", "https", "ftp", "file"}
_B_PATTERN = re.compile(r"^([A-Za-z]\w*\.Application)(?:\.\d+)?$")


def _get_hkcr_names(ctx: LookContext) -> list[str]:
    cached = getattr(ctx, "_hkcr_names_cache", None)
    if cached is not None:
        return cached
    root = ctx.registry_read("HKCR", "", max_subkeys=60000)
    raw_subkeys = root.get("subkeys", []) if isinstance(root, dict) else root["subkeys"]
    names = [s for s in raw_subkeys if isinstance(s, str)]
    setattr(ctx, "_hkcr_names_cache", names)
    return names


def _is_inside_or_equal(folder: str, sys_root: str) -> bool:
    if not sys_root:
        return False
    norm_root = sys_root.replace("/", "\\").rstrip("\\").lower() + "\\"
    norm_folder = folder.replace("/", "\\").rstrip("\\").lower() + "\\"
    return norm_folder.startswith(norm_root)


def _find_link_types(ctx: LookContext) -> list[Connection]:
    names = _get_hkcr_names(ctx)
    candidates: list[str] = []
    for name in names:
        if name.startswith("."):
            continue
        if "." in name or " " in name:
            continue
        if len(name) > 40:
            continue
        if name.lower() in _A_EXCLUDED:
            continue
        candidates.append(name)
        if len(candidates) == 4000:
            break

    connections: list[Connection] = []
    kind = "Link type that starts a program"
    door = "a link"
    allows = "TAU could start that program at a particular place by using such a link."

    for candidate in candidates:
        reg = ctx.registry_read("HKCR", candidate)
        values = reg.get("values", {}) if isinstance(reg, dict) else {}
        if "URL Protocol" not in values:
            continue
        if candidate.lower() in _A_SKIP_SCHEMES:
            continue

        raw_default = values.get("", "")
        label = ""
        if isinstance(raw_default, str) and raw_default.strip():
            lbl = raw_default.strip()
            if lbl.startswith("URL:"):
                lbl = lbl[4:].strip()
            label = lbl

        evidence = f"Windows starts a program when a link beginning with {candidate}:// is used."
        conn = Connection(
            id=make_id(ctx.machine, "", kind, candidate.lower()),
            machine=ctx.machine,
            kind=kind,
            name=f"{candidate}://",
            evidence=evidence,
            door=door,
            state="Found",
            allows=allows,
            detail={"scheme": candidate, "label": label},
        )
        connections.append(conn)

    return connections


def _find_scripted_programs(ctx: LookContext) -> list[Connection]:
    names = _get_hkcr_names(ctx)
    groups: dict[str, list[str]] = {}
    for name in names:
        m = _B_PATTERN.match(name)
        if m:
            base = m.group(1)
            if base not in groups:
                groups[base] = []
            groups[base].append(name)

    connections: list[Connection] = []
    kind = "Program that accepts scripted commands"
    door = "scripted commands"
    evidence = "It is registered with Windows as a program other programs may drive."
    allows = "TAU could ask it to do things directly, without using its window. TAU cannot do this yet."

    for group_name, members in groups.items():
        has_clsid = False
        first_default = ""
        for idx, member in enumerate(members):
            reg = ctx.registry_read("HKCR", member)
            if idx == 0 and isinstance(reg, dict):
                raw_val = reg.get("values", {}).get("", "")
                if isinstance(raw_val, str) and raw_val.strip():
                    first_default = raw_val.strip()
            subkeys = reg.get("subkeys", []) if isinstance(reg, dict) else []
            if any(isinstance(s, str) and s.upper() == "CLSID" for s in subkeys):
                has_clsid = True

        if not has_clsid:
            continue

        conn = Connection(
            id=make_id(ctx.machine, "", kind, group_name.lower()),
            machine=ctx.machine,
            kind=kind,
            name=group_name,
            evidence=evidence,
            door=door,
            state="Found",
            allows=allows,
            detail={"prog_id": group_name, "label": first_default},
        )
        connections.append(conn)

    return connections


def _find_file_actions(ctx: LookContext) -> list[Connection]:
    names = _get_hkcr_names(ctx)
    ext_candidates = sorted([n for n in names if n.startswith(".")])[:1500]

    groups: dict[str, dict[str, Any]] = {}
    for ext in ext_candidates:
        reg = ctx.registry_read("HKCR", ext)
        raw_val = reg.get("values", {}).get("", "") if isinstance(reg, dict) else ""
        if not isinstance(raw_val, str) or not raw_val.strip():
            continue
        handler = raw_val.strip()
        key = handler.lower()
        if key not in groups:
            groups[key] = {"handler": handler, "extensions": []}
        groups[key]["extensions"].append(ext)

    connections: list[Connection] = []
    kind = "File type with extra actions"
    door = "file action"
    allows = "TAU could ask Windows to do one of those actions to a file you choose."

    for item in list(groups.values())[:400]:
        handler = item["handler"]
        extensions = item["extensions"]
        shell_reg = ctx.registry_read("HKCR", f"{handler}\\shell")
        raw_subkeys = shell_reg.get("subkeys", []) if isinstance(shell_reg, dict) else []
        verbs = [v for v in raw_subkeys if isinstance(v, str)]

        has_extra_verb = False
        for v in verbs:
            if v.strip().lower() != "open":
                has_extra_verb = True
                break
        if not has_extra_verb:
            continue

        handler_reg = ctx.registry_read("HKCR", handler)
        raw_label = handler_reg.get("values", {}).get("", "") if isinstance(handler_reg, dict) else ""
        if isinstance(raw_label, str) and raw_label.strip():
            conn_name = raw_label.strip()
        else:
            conn_name = handler

        sorted_verbs = sorted(verbs)
        sorted_extensions = sorted(extensions)[:40]
        verb_sample = ", ".join(sorted_verbs[:3])
        ext_sample = ", ".join(sorted_extensions[:4])
        evidence = f"Windows offers actions such as {verb_sample} for files ending in {ext_sample}."

        conn = Connection(
            id=make_id(ctx.machine, "", kind, handler.lower()),
            machine=ctx.machine,
            kind=kind,
            name=conn_name,
            evidence=evidence,
            door=door,
            state="Found",
            allows=allows,
            detail={
                "handler": handler,
                "verbs": sorted_verbs,
                "extensions": sorted_extensions,
            },
        )
        connections.append(conn)

    return connections


def _find_command_sets(ctx: LookContext) -> list[Connection]:
    raw_path = ctx.env("PSModulePath")
    parts = [p.strip() for p in raw_path.split(";") if p.strip()]
    unique_folders: list[str] = []
    seen_folders: set[str] = set()
    for part in parts:
        key = part.lower()
        if key not in seen_folders:
            seen_folders.add(key)
            unique_folders.append(part)

    sys_root = ctx.env("SystemRoot").strip()
    candidate_folders: list[str] = []
    for f in unique_folders:
        if not _is_inside_or_equal(f, sys_root):
            candidate_folders.append(f)
            if len(candidate_folders) == 30:
                break

    connections: list[Connection] = []
    kind = "Command set for the command shell"
    door = "command line"
    allows = (
        "Nothing yet. If you switch it on, TAU will read what commands it offers and can"
        " then suggest commands for you to approve."
    )
    seen_names: set[str] = set()

    for folder in candidate_folders:
        entries = ctx.list_dir(folder, start=True)
        for entry in entries:
            if "." in entry:
                continue
            key = entry.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            conn = Connection(
                id=make_id(ctx.machine, "", kind, key),
                machine=ctx.machine,
                kind=kind,
                name=entry,
                evidence=f"It is installed as an add-in command set, in {folder}.",
                door=door,
                state="Found",
                allows=allows,
                detail={"folder": folder},
            )
            connections.append(conn)

    return connections


def _find_scheduled_tasks(ctx: LookContext) -> list[Connection]:
    text = ctx.run(["schtasks", "/query", "/fo", "csv", "/nh"], timeout=20.0)
    if not text:
        return []

    lines = text.splitlines()
    reader = csv.reader(lines)
    connections: list[Connection] = []
    seen_paths: set[str] = set()
    kind = "Scheduled task"
    door = "task scheduler"
    allows = "TAU could tell you when it runs next, and with your yes, run it now or pause it."

    for row in reader:
        if len(row) < 3:
            continue
        path = row[0].strip()
        if not path.startswith("\\"):
            continue
        if path.lower().startswith("\\microsoft\\"):
            continue

        key = path.lower()
        if key in seen_paths:
            continue
        seen_paths.add(key)

        parts = path.rsplit("\\", 1)
        name = parts[-1]
        evidence = f"Windows runs it on a schedule. Its place in the task list is {path}."
        conn = Connection(
            id=make_id(ctx.machine, "", kind, key),
            machine=ctx.machine,
            kind=kind,
            name=name,
            evidence=evidence,
            door=door,
            state="Found",
            allows=allows,
            detail={"path": path, "next_run": row[1], "status": row[2]},
        )
        connections.append(conn)

    return connections


def _find_onscreen_controls(ctx: LookContext) -> list[Connection]:
    kind = "On-screen controls"
    return [
        Connection(
            id=make_id(ctx.machine, "", kind, "ui"),
            machine=ctx.machine,
            kind=kind,
            name="Reading and pressing on-screen controls",
            evidence="Windows lets a helper program read the controls in a window and press them.",
            door="accessibility interface",
            state="Found",
            allows="A last resort for programs that offer no other way in. TAU cannot do this yet.",
        )
    ]


@finder("windows_doors", timeout=60.0, max_rows=1500)
def find_windows_doors(ctx: LookContext) -> list[Connection]:
    """Finds doors and interfaces provided by Windows for starting and driving programs."""
    if ctx.system != "Windows":
        return []

    sections = [
        _find_link_types,
        _find_scripted_programs,
        _find_file_actions,
        _find_command_sets,
        _find_scheduled_tasks,
        _find_onscreen_controls,
    ]

    connections: list[Connection] = []
    for section in sections:
        try:
            connections.extend(section(ctx))
        except WalkRefused:
            raise
        except Exception:
            pass

    connections.sort(key=lambda c: (c.kind, c.name.lower()))
    return connections
