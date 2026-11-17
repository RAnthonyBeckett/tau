"""Discovers tool servers set up for assistant programs and registered with the operating system."""

from __future__ import annotations

import json
import re
from typing import Any

from tau.connect.ctx import LookContext, WalkRefused
from tau.connect.recipes import load
from tau.connect.record import Connection, make_id
from tau.connect.registry import finder


def _extract_server_fields(
    raw_name: str,
    spec: dict[str, Any],
) -> tuple[str, str, str, str]:
    name = raw_name.strip()[:80]
    cmd_val = spec.get("command")
    url_val = spec.get("url")
    if not (isinstance(url_val, str) and url_val.strip()):
        url_val = spec.get("serverUrl")
    if not (isinstance(url_val, str) and url_val.strip()):
        url_val = spec.get("httpUrl")

    if isinstance(cmd_val, str) and cmd_val.strip():
        transport = "command"
        clean_cmd = cmd_val.strip()
        parts = [p for p in re.split(r"[/\\]+", clean_cmd) if p]
        program = parts[-1][:60] if parts else clean_cmd[:60]
        host = ""
    elif isinstance(url_val, str) and url_val.strip():
        transport = "address"
        program = ""
        raw_url = url_val.strip()
        m_auth = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*://[^/?#]+)", raw_url)
        m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*://[^/?#@]+)", raw_url)
        if not m or "@" in m.group(1) or (m_auth and "@" in m_auth.group(1)):
            host = ""
        else:
            host = m.group(1)
    else:
        transport = "unknown"
        program = ""
        host = ""

    return (name, transport, program, host)


def _record_server(
    raw_sname: str,
    spec: dict[str, Any],
    prog_label: str,
    target_map: dict[tuple[str, str, str], dict[str, Any]],
) -> None:
    name, transport, program, host = _extract_server_fields(raw_sname, spec)
    if not name:
        return
    key = (name.lower(), program.lower(), host.lower())
    if key not in target_map:
        target_map[key] = {
            "name": name,
            "transport": transport,
            "program": program,
            "host": host,
            "labels": [],
        }
    if prog_label not in target_map[key]["labels"]:
        target_map[key]["labels"].append(prog_label)


def _find_program_servers(ctx: LookContext) -> list[Connection]:
    home = ctx.env("USERPROFILE") if ctx.system == "Windows" and ctx.env("USERPROFILE") else ctx.env("HOME")
    appdata = ctx.env("APPDATA")
    localappdata = ctx.env("LOCALAPPDATA")
    replacements = {
        "{HOME}": home,
        "{APPDATA}": appdata,
        "{LOCALAPPDATA}": localappdata,
    }

    recipes = load("agent_settings")
    if not isinstance(recipes, list):
        return []

    merged: dict[tuple[str, str, str], dict[str, Any]] = {}

    for entry in recipes:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label", "")
        if not isinstance(label, str) or not label.strip():
            continue
        paths_map = entry.get("paths", {})
        if not isinstance(paths_map, dict):
            continue
        templates = paths_map.get(ctx.system, [])
        if not isinstance(templates, list):
            continue

        keys = entry.get("keys", [])
        if not isinstance(keys, list):
            keys = []

        fmt = entry.get("format", "")

        for template in templates:
            if not isinstance(template, str) or not template:
                continue
            skip = False
            resolved = template
            for placeholder, val in replacements.items():
                if placeholder in resolved:
                    if not val:
                        skip = True
                        break
                    resolved = resolved.replace(placeholder, val)
            if skip:
                continue

            if ctx.system == "Windows":
                resolved = resolved.replace("/", "\\")

            text = ctx.read_text(resolved, limit=2000000)
            if not text:
                continue

            if fmt == "json":
                try:
                    data = json.loads(text)
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                for k in keys:
                    servers_dict = data.get(k)
                    if isinstance(servers_dict, dict):
                        for raw_sname, raw_spec in servers_dict.items():
                            if not isinstance(raw_sname, str) or not raw_sname.strip():
                                continue
                            spec = raw_spec if isinstance(raw_spec, dict) else {}
                            _record_server(raw_sname, spec, label, merged)

            elif fmt == "toml":
                current_server: str | None = None
                current_spec: dict[str, str] = {}

                for line in text.splitlines():
                    line_str = line.strip()
                    if not line_str:
                        continue
                    if line_str.startswith("["):
                        if current_server is not None:
                            _record_server(current_server, current_spec, label, merged)
                            current_server = None
                            current_spec = {}
                        for k in keys:
                            m = re.match(rf"^\[{re.escape(k)}\.(.+)\]$", line_str)
                            if m:
                                raw_name = m.group(1).strip()
                                if (raw_name.startswith('"') and raw_name.endswith('"')) or (
                                    raw_name.startswith("'") and raw_name.endswith("'")
                                ):
                                    raw_name = raw_name[1:-1].strip()
                                if "." in raw_name:
                                    break
                                current_server = raw_name
                                current_spec = {}
                                break
                    else:
                        if current_server is not None:
                            m_cmd = re.match(r'^command\s*=\s*["\'](.*?)["\']', line_str)
                            if m_cmd:
                                current_spec["command"] = m_cmd.group(1)
                            else:
                                m_url = re.match(r'^(?:url|serverUrl|httpUrl)\s*=\s*["\'](.*?)["\']', line_str)
                                if m_url:
                                    current_spec["url"] = m_url.group(1)

                if current_server is not None:
                    _record_server(current_server, current_spec, label, merged)

    kind = "Tool server set up for an assistant"
    door = "tool server"
    state = "Found"
    allows = "TAU could use the same tools, after you switch it on and TAU has asked the server what it offers."

    connections: list[Connection] = []
    for key, item in merged.items():
        name = item["name"]
        labels = item["labels"]
        other_count = len(labels) - 1
        if other_count <= 0:
            evidence = f"{labels[0]} has it set up as a tool server."
        elif other_count == 1:
            evidence = f"{labels[0]} and 1 other program have it set up as a tool server."
        else:
            evidence = f"{labels[0]} and {other_count} other programs have it set up as a tool server."

        conn = Connection(
            id=make_id(ctx.machine, "", kind, "|".join(key)),
            machine=ctx.machine,
            kind=kind,
            name=name,
            evidence=evidence,
            door=door,
            state=state,
            allows=allows,
            detail={
                "transport": item["transport"],
                "program": item["program"],
                "host": item["host"],
                "listed_in": list(labels),
            },
        )
        connections.append(conn)

    return connections


def _find_system_servers(ctx: LookContext) -> list[Connection]:
    if ctx.system != "Windows":
        return []

    text = ctx.run(["odr", "mcp", "list"], timeout=10)
    if not text or not text.strip():
        return []

    try:
        data = json.loads(text)
    except Exception:
        return []

    items: list[Any] = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for k in ("servers", "mcpServers"):
            val = data.get(k)
            if isinstance(val, list):
                items = val
                break
    else:
        return []

    kind = "Tool server registered with Windows"
    door = "tool server"
    state = "Found"
    evidence = "Windows lists it in its own registry of tool servers for assistants."
    allows = "TAU could use its tools, after you switch it on. Windows may ask you to approve it as well."
    detail = {"source": "windows registry of tool servers"}

    seen_names: set[str] = set()
    connections: list[Connection] = []

    for item in items:
        server_name = ""
        if isinstance(item, str) and item.strip():
            server_name = item.strip()
        elif isinstance(item, dict):
            for key in ("name", "id", "displayName"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    server_name = val.strip()
                    break
        if not server_name:
            continue

        key = server_name.lower()
        if key in seen_names:
            continue
        seen_names.add(key)

        conn = Connection(
            id=make_id(ctx.machine, "", kind, key),
            machine=ctx.machine,
            kind=kind,
            name=server_name,
            evidence=evidence,
            door=door,
            state=state,
            allows=allows,
            detail=dict(detail),
        )
        connections.append(conn)

    return connections


@finder("agent_tool_servers", timeout=20.0, max_rows=500)
def find_agent_tool_servers(ctx: LookContext) -> list[Connection]:
    """Find tool servers configured for assistants or registered with the operating system."""
    connections: list[Connection] = []
    for section in (_find_program_servers, _find_system_servers):
        try:
            connections.extend(section(ctx))
        except WalkRefused:
            raise
        except Exception:
            pass

    connections.sort(key=lambda c: (c.kind, c.name.lower()))
    return connections
