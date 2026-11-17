"""Coordinator for running looks across the local machine and paired nodes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tau.connect.registry import run_look
from tau.connect.store import LookStore

OLD_HELPER_NOTE = "Update TAU's helper on this machine to see everything it can connect to."
NOT_CONNECTED_NOTE = "This machine isn't connected right now, so this is what TAU saw last time."


def look_here(paired: Optional[List[str]] = None) -> Dict[str, Any]:
    """Run a complete look on the local machine."""
    from tau import discovery
    from tau.discovery import neighbours
    from tau.connect.ctx import LookContext

    snapshot = discovery.collect()
    try:
        n_list = neighbours.list_neighbours()
    except Exception:
        n_list = []
    snapshot["neighbours"] = n_list
    snapshot["paired"] = list(paired or [])
    ctx = LookContext(machine=snapshot["machine"]["name"], snapshot=snapshot)
    look = run_look(ctx)

    try:
        from tau.connect import places

        extra = places.look_at_places(ctx)
    except Exception:
        extra = None

    if extra:
        extra_conns = []
        for c in extra.get("connections", []):
            cd = c.to_dict()
            cd["last_seen"] = look.get("at", "")
            extra_conns.append(cd)
        look["connections"].extend(extra_conns)
        look["connections"].sort(
            key=lambda entry: (
                str(entry.get("machine", "")),
                str(entry.get("place", "")),
                str(entry.get("kind", "")),
                str(entry.get("name", "")).lower(),
            )
        )
        look["finders"].update(extra.get("finders", {}))

    return look


def look_at_machine(
    hub: Any,
    machine: str,
    store: LookStore,
    timeout: float = 180.0,
) -> Dict[str, Any]:
    """Ask one paired machine for its look and save it to the store."""
    try:
        res = hub.remote_call(machine, "look", "run", {}, timeout=timeout)
    except ConnectionError:
        return store.keep(machine, NOT_CONNECTED_NOTE)
    except (RuntimeError, PermissionError) as exc:
        if "doesn't do" in str(exc):
            return store.keep(machine, OLD_HELPER_NOTE)
        return store.keep(
            machine,
            "TAU could not read this machine this time, so this is what it saw last time.",
        )
    except TimeoutError:
        return store.keep(
            machine,
            "This machine took too long to answer, so this is what TAU saw last time.",
        )
    except Exception:
        return store.keep(
            machine,
            "TAU could not read this machine this time, so this is what it saw last time.",
        )

    if isinstance(res, dict) and isinstance(res.get("connections"), list):
        res["machine"] = machine
        return store.save(res)

    return store.keep(
        machine,
        "TAU could not read this machine this time, so this is what it saw last time.",
    )


def look_everywhere(
    hub: Any,
    store: LookStore,
    local_machine: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Look at the local machine and every paired machine."""
    paired: list[str] = []
    if hub is not None and hasattr(hub, "paired_nodes") and callable(hub.paired_nodes):
        nodes = hub.paired_nodes()
        if isinstance(nodes, dict):
            paired = sorted(nodes.keys())

    local = look_here(paired=paired)
    if local_machine is not None:
        local["machine"] = local_machine
    saved_local = store.save(local)
    machine_key = str(saved_local.get("machine") or local.get("machine") or "")
    results: dict[str, dict[str, Any]] = {machine_key: saved_local}

    for name in paired:
        try:
            results[name] = look_at_machine(hub, name, store)
        except Exception:
            results[name] = store.keep(
                name,
                "TAU could not read this machine this time, so this is what it saw last time.",
            )

    return results
