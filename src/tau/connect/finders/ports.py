"""Discovers services listening on local machine ports."""

from __future__ import annotations

import concurrent.futures
import json
import re
from typing import Any, Optional

from tau.connect.ctx import LookContext
from tau.connect.record import Connection, make_id
from tau.connect.registry import finder

# Addresses another program's listener may report that can be reached on loopback.
# This is only compared against; TAU itself never binds to every interface here.
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "::", "*", "", None} | {"0.0.0.0"}  # read-only match, never a bind


def _loopback_ports(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Filter snapshot ports to reachable loopback TCP ports."""
    raw_ports = snapshot.get("ports")
    if not isinstance(raw_ports, list):
        return []

    seen_ports: set[int] = set()
    kept: list[dict[str, Any]] = []

    for row in raw_ports:
        if not isinstance(row, dict):
            continue
        proto = row.get("proto")
        if proto is not None and str(proto).lower() != "tcp":
            continue
        addr = row.get("address")
        if addr not in LOOPBACK_HOSTS:
            continue
        port = row.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or port < 1 or port > 65535:
            continue
        if port in seen_ports:
            continue
        seen_ports.add(port)
        kept.append(row)

    kept.sort(key=lambda r: int(r["port"]))
    return kept[:200]


def _json_object(text: str) -> Optional[dict[str, Any]]:
    """Parse text into a dictionary if valid JSON, otherwise None."""
    if not text or not text.strip():
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _title(html: str) -> Optional[str]:
    """Extract and collapse title text from HTML body."""
    if not html:
        return None
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    collapsed = " ".join(match.group(1).split()).strip()
    return collapsed[:80] if collapsed else None


def _classify(ctx: LookContext, row: dict[str, Any]) -> Optional[Connection]:
    """Inspect one port through HTTP probes and return a Connection record."""
    port = int(row["port"])
    proc = str(row.get("process") or "").strip()
    pid = row.get("pid")

    # a. GET "/"
    status_root, _, body_root = ctx.http_get(port, "/", timeout=0.7)
    if status_root == 0:
        name_base = proc if proc else "Unknown program"
        kind = "Program listening on a port"
        return Connection(
            id=make_id(ctx.machine, "", kind, str(port)),
            machine=ctx.machine,
            kind=kind,
            name=f"{name_base} (port {port})",
            evidence=f"It is listening on local port {port}, and did not answer as a web page.",
            door="port only",
            state="Found",
            allows="Nothing yet. TAU can see it is running, and cannot talk to it.",
            detail={"port": port, "process": proc, "pid": pid},
        )

    # b. GET "/tau/v1/describe"
    status_desc, _, body_desc = ctx.http_get(port, "/tau/v1/describe", timeout=0.7)
    if status_desc == 200:
        obj_desc = _json_object(body_desc)
        if obj_desc is not None:
            actions = obj_desc.get("actions")
            if isinstance(actions, list) and len(actions) > 0:
                json_name = obj_desc.get("name")
                if isinstance(json_name, str) and json_name.strip():
                    name_base = json_name.strip()
                elif proc:
                    name_base = proc
                else:
                    name_base = "App"
                kind = "Program that offers TAU's own connection"
                return Connection(
                    id=make_id(ctx.machine, "", kind, str(port)),
                    machine=ctx.machine,
                    kind=kind,
                    name=f"{name_base} (port {port})",
                    evidence=f"It answers on local port {port} and lists what TAU may ask it to do.",
                    door="TAU app connection",
                    state="Ready",
                    allows="TAU could ask it to do the things it lists, and report each result.",
                    detail={"port": port, "actions": len(actions), "process": proc, "pid": pid},
                )

    # c. GET "/v1/models"
    status_v1, _, body_v1 = ctx.http_get(port, "/v1/models", timeout=0.7)
    if status_v1 == 200:
        obj_v1 = _json_object(body_v1)
        if obj_v1 is not None:
            model_items = None
            if isinstance(obj_v1.get("data"), list):
                model_items = obj_v1["data"]
            elif isinstance(obj_v1.get("models"), list):
                model_items = obj_v1["models"]

            if model_items is not None:
                model_names: list[str] = []
                for item in model_items:
                    if isinstance(item, dict):
                        val = item.get("id") or item.get("name")
                        if isinstance(val, str) and val.strip():
                            model_names.append(val.strip())
                    elif isinstance(item, str) and item.strip():
                        model_names.append(item.strip())
                    if len(model_names) >= 200:
                        break
                name_base = proc if proc else "Model server"
                kind = "Model server"
                return Connection(
                    id=make_id(ctx.machine, "", kind, str(port)),
                    machine=ctx.machine,
                    kind=kind,
                    name=f"{name_base} (port {port})",
                    evidence=f"It answers on local port {port} with a list of {len(model_names)} models.",
                    door="model list",
                    state="Ready",
                    allows="TAU could send it questions, using the models it lists.",
                    detail={"port": port, "models": model_names, "process": proc, "pid": pid},
                )

    # d. GET "/api/tags"
    status_tags, _, body_tags = ctx.http_get(port, "/api/tags", timeout=0.7)
    if status_tags == 200:
        obj_tags = _json_object(body_tags)
        if obj_tags is not None and isinstance(obj_tags.get("models"), list):
            model_items = obj_tags["models"]
            model_names = []
            for item in model_items:
                if isinstance(item, dict):
                    val = item.get("name") or item.get("model")
                    if isinstance(val, str) and val.strip():
                        model_names.append(val.strip())
                elif isinstance(item, str) and item.strip():
                    model_names.append(item.strip())
                if len(model_names) >= 200:
                    break
            name_base = proc if proc else "Model server"
            kind = "Model server"
            return Connection(
                id=make_id(ctx.machine, "", kind, str(port)),
                machine=ctx.machine,
                kind=kind,
                name=f"{name_base} (port {port})",
                evidence=f"It answers on local port {port} with a list of {len(model_names)} models.",
                door="model list",
                state="Ready",
                allows="TAU could send it questions, using the models it lists.",
                detail={"port": port, "models": model_names, "process": proc, "pid": pid},
            )

    # e. GET "/openapi.json", then "/swagger.json"
    for desc_path in ("/openapi.json", "/swagger.json"):
        st, _, bd = ctx.http_get(port, desc_path, timeout=0.7)
        if st == 200:
            obj_doc = _json_object(bd)
            if obj_doc is not None and ("openapi" in obj_doc or "swagger" in obj_doc):
                info = obj_doc.get("info")
                title_val: Optional[str] = None
                if isinstance(info, dict) and "title" in info:
                    t = info["title"]
                    if isinstance(t, str):
                        title_val = t
                paths = obj_doc.get("paths")
                actions_count = len(paths) if isinstance(paths, dict) else 0
                if title_val is not None and title_val.strip():
                    name_base = title_val.strip()
                elif proc:
                    name_base = proc
                else:
                    name_base = "Web service"
                detail_dict: dict[str, Any] = {
                    "port": port,
                    "actions": actions_count,
                    "description_path": desc_path,
                    "process": proc,
                    "pid": pid,
                }
                if isinstance(info, dict) and "title" in info:
                    detail_dict["title"] = info["title"]
                kind = "Web service that describes itself"
                return Connection(
                    id=make_id(ctx.machine, "", kind, str(port)),
                    machine=ctx.machine,
                    kind=kind,
                    name=f"{name_base} (port {port})",
                    evidence=f"It answers on local port {port} and publishes a description of what it can do.",
                    door="its own description file",
                    state="Ready",
                    allows="TAU could read what it offers, and with your yes, ask it to do those things.",
                    detail=detail_dict,
                )

    # f. Otherwise (the "/" request answered with any status above 0)
    title_text = _title(body_root)
    if title_text is not None and title_text.strip():
        name_base = title_text.strip()
    elif proc:
        name_base = proc
    else:
        name_base = "Web page"
    kind = "Web page"
    return Connection(
        id=make_id(ctx.machine, "", kind, str(port)),
        machine=ctx.machine,
        kind=kind,
        name=f"{name_base} (port {port})",
        evidence=f"It answers on local port {port} as a web page.",
        door="web page",
        state="Found",
        allows="TAU could show you the page. It does not say what else it can do.",
        detail={"port": port, "status": status_root, "process": proc, "pid": pid},
    )


def _classify_safe(ctx: LookContext, row: dict[str, Any]) -> Optional[Connection]:
    try:
        return _classify(ctx, row)
    except Exception:
        return None


@finder("local_ports", timeout=45.0, max_rows=300)
def find_local_ports(ctx: LookContext) -> list[Connection]:
    """Inspect local listening TCP ports and discover reachable services."""
    rows = _loopback_ports(ctx.snapshot)
    if not rows:
        return []

    workers = min(16, len(rows))
    connections: list[Connection] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_classify_safe, ctx, r) for r in rows]
        for fut in futures:
            try:
                res = fut.result()
                if res is not None:
                    connections.append(res)
            except Exception:
                pass

    connections.sort(key=lambda c: int(c.detail.get("port", 0)))
    return connections
