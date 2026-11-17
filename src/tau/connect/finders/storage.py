"""Discovers storage drives and folders this machine shares."""

from __future__ import annotations

import re
from typing import Any

from tau.connect.ctx import LookContext, WalkRefused
from tau.connect.record import Connection, make_id
from tau.connect.registry import finder


def _find_drives(ctx: LookContext) -> list[Connection]:
    raw_drives = ctx.snapshot.get("drives") or []
    if not isinstance(raw_drives, list):
        return []

    connections: list[Connection] = []
    allows_text = (
        "Nothing by itself. You choose folders on it for TAU to read. "
        "TAU never goes through it looking for files."
    )

    for row in raw_drives:
        if not isinstance(row, dict):
            continue
        mount = row.get("mount")
        if not isinstance(mount, str) or not mount.strip():
            continue

        mount_name = mount.strip()
        is_network = bool(row.get("network"))

        if is_network:
            kind = "Shared folder from another machine"
            door = "file sharing"
            evidence = f"It is a folder shared by another machine, attached here as {mount_name}."

            raw_remote = row.get("remote")
            if isinstance(raw_remote, str) and raw_remote.strip():
                remote_str = raw_remote.strip()
            else:
                raw_device = row.get("device")
                remote_str = raw_device.strip() if isinstance(raw_device, str) else ""

            remote_host = ""
            remote_share = ""
            if remote_str.startswith(("\\\\", "//")):
                rem = remote_str[2:].replace("\\", "/")
                parts = [p for p in rem.split("/") if p]
                if len(parts) >= 2:
                    remote_host = parts[0]
                    remote_share = parts[1]
                elif len(parts) == 1:
                    remote_host = parts[0]
                    remote_share = ""
            elif ":" in remote_str:
                host_part, _, path_part = remote_str.partition(":")
                if host_part and "/" not in host_part and "\\" not in host_part:
                    remote_host = host_part
                    remote_share = path_part

            detail = {
                "mount": mount_name,
                "remote_host": remote_host,
                "remote_share": remote_share,
                "fstype": row.get("fstype"),
            }
            conn_id = make_id(ctx.machine, "", kind, mount_name.lower())
            connections.append(
                Connection(
                    id=conn_id,
                    machine=ctx.machine,
                    kind=kind,
                    name=mount_name,
                    evidence=evidence,
                    door=door,
                    state="Found",
                    allows=allows_text,
                    detail=detail,
                )
            )
        else:
            kind = "Drive"
            door = "files"
            total_val = row.get("total_gb")
            if isinstance(total_val, (int, float)) and not isinstance(total_val, bool):
                evidence = f"It is a drive on this machine, {round(total_val)} GB in size."
            else:
                evidence = "It is a drive on this machine, of unknown size."

            detail = {
                "mount": mount_name,
                "fstype": row.get("fstype"),
                "total_gb": row.get("total_gb"),
                "free_gb": row.get("free_gb"),
                "slow": row.get("slow"),
                "never_walk": row.get("never_walk"),
            }
            conn_id = make_id(ctx.machine, "", kind, mount_name.lower())
            connections.append(
                Connection(
                    id=conn_id,
                    machine=ctx.machine,
                    kind=kind,
                    name=mount_name,
                    evidence=evidence,
                    door=door,
                    state="Found",
                    allows=allows_text,
                    detail=detail,
                )
            )

    return connections


def _find_windows_shares(ctx: LookContext) -> list[Connection]:
    if ctx.system != "Windows":
        return []

    data = ctx.registry_read("HKLM", "SYSTEM\\CurrentControlSet\\Services\\LanmanServer\\Shares")
    values = data.get("values", {}) if isinstance(data, dict) else {}
    if not isinstance(values, dict):
        return []

    connections: list[Connection] = []
    kind = "Folder this machine shares"
    door = "file sharing"
    allows_text = (
        "It tells TAU which machines can reach the same files. "
        "TAU does not read the folder unless you add it."
    )

    for share_key, raw_val in values.items():
        if not isinstance(share_key, str):
            continue
        share_name = share_key.strip()
        if not share_name or share_name.endswith("$"):
            continue

        if isinstance(raw_val, list):
            parts = [str(p) for p in raw_val]
        elif isinstance(raw_val, str):
            parts = re.split(r"[\r\n\x00]+", raw_val)
        else:
            continue

        path = ""
        for part in parts:
            trimmed = part.strip()
            if trimmed.lower().startswith("path="):
                path = trimmed[5:].strip()
                break

        if not path:
            continue

        if share_name == path:
            evidence = f"This machine shares the folder {path} with other machines."
        else:
            evidence = f"This machine shares the folder {path} with other machines under the name {share_name}."

        detail = {
            "share": share_name,
            "path": path,
            "how": "windows sharing",
        }
        conn_id = make_id(ctx.machine, "", kind, share_name.lower())
        connections.append(
            Connection(
                id=conn_id,
                machine=ctx.machine,
                kind=kind,
                name=share_name,
                evidence=evidence,
                door=door,
                state="Found",
                allows=allows_text,
                detail=detail,
            )
        )

    return connections


def _find_linux_shares(ctx: LookContext) -> list[Connection]:
    if ctx.system == "Windows" or ctx.system == "Darwin":
        return []

    connections: list[Connection] = []
    kind = "Folder this machine shares"
    door = "file sharing"
    allows_text = (
        "It tells TAU which machines can reach the same files. "
        "TAU does not read the folder unless you add it."
    )

    # 1. Exports file
    exports_text = ctx.read_text("/etc/exports")
    if exports_text:
        for line in exports_text.splitlines():
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#"):
                continue
            fields = trimmed.split()
            if not fields:
                continue
            first_field = fields[0]
            if not first_field.startswith("/"):
                continue

            folder = first_field
            share_name = folder
            path = folder
            evidence = f"This machine shares the folder {path} with other machines."
            detail = {
                "share": share_name,
                "path": path,
                "how": "exports file",
            }
            conn_id = make_id(ctx.machine, "", kind, share_name.lower())
            connections.append(
                Connection(
                    id=conn_id,
                    machine=ctx.machine,
                    kind=kind,
                    name=share_name,
                    evidence=evidence,
                    door=door,
                    state="Found",
                    allows=allows_text,
                    detail=detail,
                )
            )

    # 2. Sharing settings file
    conf_text = ctx.read_text("/etc/samba/smb.conf")
    if conf_text:
        sections_data: list[tuple[str, str]] = []
        current_sec: str | None = None
        current_path = ""

        def flush_section() -> None:
            nonlocal current_sec, current_path
            if current_sec is not None:
                sec_lower = current_sec.lower()
                if (
                    sec_lower not in ("global", "printers", "print$", "homes")
                    and not sec_lower.endswith("$")
                    and current_path
                ):
                    sections_data.append((current_sec, current_path))
            current_sec = None
            current_path = ""

        for line in conf_text.splitlines():
            trimmed = line.strip()
            if not trimmed or trimmed.startswith(("#", ";")):
                continue
            sec_match = re.match(r"^\[([^\]]+)\]$", trimmed)
            if sec_match:
                flush_section()
                current_sec = sec_match.group(1).strip()
                continue
            if current_sec is not None:
                path_match = re.match(r"^\s*path\s*=\s*(.+)$", line, re.IGNORECASE)
                if path_match:
                    current_path = path_match.group(1).strip()

        flush_section()

        for share_name, path in sections_data:
            if share_name == path:
                evidence = f"This machine shares the folder {path} with other machines."
            else:
                evidence = (
                    f"This machine shares the folder {path} with other machines "
                    f"under the name {share_name}."
                )

            detail = {
                "share": share_name,
                "path": path,
                "how": "sharing settings file",
            }
            conn_id = make_id(ctx.machine, "", kind, share_name.lower())
            connections.append(
                Connection(
                    id=conn_id,
                    machine=ctx.machine,
                    kind=kind,
                    name=share_name,
                    evidence=evidence,
                    door=door,
                    state="Found",
                    allows=allows_text,
                    detail=detail,
                )
            )

    return connections


@finder("storage", timeout=20.0, max_rows=400)
def find_storage(ctx: LookContext) -> list[Connection]:
    """Finds storage drives and folders this machine shares."""
    sections = [
        _find_drives,
        _find_windows_shares,
        _find_linux_shares,
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
