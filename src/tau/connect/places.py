"""Discovers and inspects Linux environments and containers inside this machine."""

from __future__ import annotations

import re
from typing import Any

from tau.connect import registry
from tau.connect.ctx import LookContext
from tau.connect.record import Connection, make_id
from tau.connect.registry import run_look

ENTER = "wsl.exe"
INSIDE_FINDERS = ("command_line_tools", "assistant_workers", "linux_doors", "local_ports")
_ENV_VAR_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _clean(text: str) -> str:
    """Return text with null characters and carriage returns removed."""
    return text.replace("\x00", "").replace("\r", "")


def list_places(ctx: LookContext) -> list[dict[str, Any]]:
    """Return list of places found on this machine."""
    places: list[dict[str, Any]] = []

    if ctx.system == "Windows":
        text = _clean(ctx.run([ENTER, "-l", "-v"], timeout=8))
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("*"):
                line = line[1:].strip()
            parts = line.split()
            if not parts:
                continue
            if parts[0].upper() == "NAME":
                continue
            if len(parts) < 2:
                continue
            name = parts[0]
            if name.lower().startswith("docker-desktop"):
                continue
            state_word = parts[1]
            running = state_word.lower() == "running"
            places.append({
                "name": name,
                "type": "linux",
                "running": running,
                "detail": {"state": state_word},
            })

    raw_containers = ctx.snapshot.get("containers") or []
    if isinstance(raw_containers, list):
        for row in raw_containers:
            if isinstance(row, dict):
                raw_name = row.get("name")
                if isinstance(raw_name, str) and raw_name.strip():
                    name = raw_name.strip()
                    running = str(row.get("state") or "").lower() == "running"
                    places.append({
                        "name": name,
                        "type": "container",
                        "running": running,
                        "detail": {
                            "image": row.get("image") or "",
                            "ports": row.get("ports") or "",
                        },
                    })

    places.sort(key=lambda p: (p["type"], p["name"].lower()))
    return places


def place_connections(ctx: LookContext, places: list[dict[str, Any]]) -> list[Connection]:
    """Build one connection per place describing the place itself."""
    conns: list[Connection] = []
    for place in places:
        p_type = place.get("type", "")
        p_name = place.get("name", "")
        p_running = bool(place.get("running"))
        p_detail = dict(place.get("detail") or {})

        if p_type == "linux":
            kind = "Linux environment inside this machine"
            door = "the host's own way in"
            state = "Ready" if p_running else "Needs something"
            needs = "" if p_running else "It is not running. Start it yourself, then look again."
            evidence = "Windows lists it as a Linux environment on this machine."
            allows = "TAU could find and use the tools inside it, the same way it does on this machine."
            detail = {"state": p_detail.get("state", "")}
        elif p_type == "container":
            kind = "Container on this machine"
            door = "container tools"
            state = "Found"
            needs = ""
            image = p_detail.get("image") or ""
            if image:
                evidence = f"It is a container on this machine, made from {image}."
            else:
                evidence = "It is a container on this machine."
            allows = "TAU can see it. Programs it serves on a local port are listed separately."
            detail = p_detail
        else:
            continue

        conn_id = make_id(ctx.machine, "", kind, p_name.lower())
        conns.append(
            Connection(
                id=conn_id,
                machine=ctx.machine,
                kind=kind,
                name=p_name,
                evidence=evidence,
                door=door,
                state=state,
                allows=allows,
                needs=needs,
                detail=detail,
            )
        )

    return conns


def inside_context(ctx: LookContext, place: str) -> LookContext:
    """Build a LookContext for inspecting the inside of a running Linux environment."""

    def _exec(cmd: list[str], timeout: float = 8.0) -> str:
        return _clean(ctx.run([ENTER, "-d", place, "-e", *cmd], timeout=timeout))

    def inside_run(cmd: list[str], timeout: float = 10.0) -> str:
        return _clean(ctx.run([ENTER, "-d", place, "-e", *cmd], timeout=timeout))

    def inside_list_dir(path: Any, *args: Any, **kwargs: Any) -> list[str]:
        out = _exec(["ls", "-1A", "--", str(path)], timeout=8.0)
        return [line for line in out.splitlines() if line]

    def inside_read_text(path: Any, limit: int = 200000, *args: Any, **kwargs: Any) -> str:
        return _exec(["head", "-c", str(int(limit)), "--", str(path)], timeout=8.0)

    def inside_registry_read(hive: str, key: str, max_subkeys: int = 5000) -> dict[str, Any]:
        return {"subkeys": [], "values": {}}

    env_text = _exec(["bash", "-ic", "env"], timeout=8.0)
    parsed_env: dict[str, str] = {}
    for line in env_text.splitlines():
        if "=" in line:
            var_name, val = line.split("=", 1)
            if _ENV_VAR_PATTERN.match(var_name):
                parsed_env[var_name] = val
    if not parsed_env:
        parsed_env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}

    ss_text = _exec(["ss", "-ltnH"], timeout=8.0)
    ports: list[dict[str, Any]] = []
    for line in ss_text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        field4 = parts[3]
        if ":" not in field4:
            continue
        addr_part, port_part = field4.rsplit(":", 1)
        if not port_part.isdigit():
            continue
        port = int(port_part)
        address = addr_part.replace("[", "").replace("]", "")
        ports.append({
            "proto": "tcp",
            "address": address,
            "port": port,
            "pid": None,
            "process": None,
        })

    snapshot = {"ports": ports}

    return LookContext(
        machine=ctx.machine,
        run=inside_run,
        read_text=inside_read_text,
        list_dir=inside_list_dir,
        http_get=ctx.http_get,
        registry_read=inside_registry_read,
        snapshot=snapshot,
        environ=parsed_env,
        system="Linux",
        max_dirs=120,
    )


def look_inside(ctx: LookContext, place: str) -> dict[str, Any]:
    """Run finders inside one Linux environment."""
    registry.load_finders()
    specs = {name: registry.FINDERS[name] for name in INSIDE_FINDERS if name in registry.FINDERS}
    inside_ctx = inside_context(ctx, place)
    look = run_look(inside_ctx, finders=specs)

    connections: list[Connection] = []
    for c_dict in look.get("connections", []):
        if not isinstance(c_dict, dict):
            continue
        if c_dict.get("state") == "Not found this time":
            continue
        kind = str(c_dict.get("kind", ""))
        if kind == "On-screen controls":
            continue

        orig_id = str(c_dict.get("id", ""))
        new_id = make_id(ctx.machine, place, kind, orig_id)
        orig_ev = str(c_dict.get("evidence", ""))
        new_ev = f"{orig_ev} This is inside the Linux environment {place}."

        item_dict = dict(c_dict)
        item_dict["id"] = new_id
        item_dict["place"] = place
        item_dict["evidence"] = new_ev
        connections.append(Connection.from_dict(item_dict))

    raw_finders = look.get("finders") or {}
    prefixed_finders = {f"{place}: {k}": v for k, v in raw_finders.items()}

    return {"connections": connections, "finders": prefixed_finders}


def look_at_places(ctx: LookContext) -> dict[str, Any]:
    """Inspect places on the host machine and inside running Linux environments."""
    try:
        places = list_places(ctx)
    except Exception:
        return {"connections": [], "finders": {}}

    connections = place_connections(ctx, places)
    finders: dict[str, Any] = {}

    running_linux = [p for p in places if p.get("type") == "linux" and p.get("running") is True][:4]
    for place_info in running_linux:
        name = place_info.get("name", "")
        try:
            inside = look_inside(ctx, name)
            connections.extend(inside.get("connections", []))
            finders.update(inside.get("finders", {}))
        except Exception:
            finders[f"{name}: look"] = {
                "count": 0,
                "stopped_early": False,
                "seconds": 0.0,
                "error": "stopped with an error",
            }

    return {"connections": connections, "finders": finders}
