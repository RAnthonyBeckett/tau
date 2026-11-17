"""Discovers models listed by known local model servers."""

from __future__ import annotations

from tau.connect.ctx import LookContext
from tau.connect.record import Connection, make_id
from tau.connect.registry import finder
from tau.engines import architecture


@finder("model_shelf", timeout=10.0, max_rows=500)
def find_models(ctx: LookContext) -> list[Connection]:
    """Return connection records for models provided by registered servers."""
    servers = ctx.snapshot.get("model_servers") or []
    if not isinstance(servers, list):
        return []

    connections: list[Connection] = []
    seen_pairs: set[tuple[str, str]] = set()

    for s in servers:
        if not isinstance(s, dict):
            continue
        url = s.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        server_url = url.strip()

        raw_models = s.get("models")
        if not isinstance(raw_models, list):
            continue

        distinct_models: list[str] = []
        seen_server_models: set[str] = set()
        for m in raw_models:
            if isinstance(m, str) and m.strip():
                clean = m.strip()
                if clean not in seen_server_models:
                    seen_server_models.add(clean)
                    distinct_models.append(clean)

        count = len(distinct_models)
        can_switch = count > 1
        engine = str(s.get("engine") or "")

        for model_name in distinct_models:
            pair = (server_url, model_name)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            arch = architecture(model_name)
            conn_id = make_id(ctx.machine, "", "Model", f"{server_url}|{model_name}")
            connections.append(
                Connection(
                    id=conn_id,
                    machine=ctx.machine,
                    kind="Model",
                    name=model_name,
                    evidence=f"The model server at {server_url} lists it.",
                    door="model server",
                    state="Ready",
                    allows="TAU could use this model for a kind of job you choose.",
                    detail={
                        "server": server_url,
                        "engine": engine,
                        "architecture": arch,
                        "server_lists": count,
                        "can_switch": can_switch,
                    },
                )
            )

    return connections
