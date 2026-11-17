"""Manage connection switches and permissions with receipts."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

from tau.connect.store import LookStore

LEVELS: tuple[str, ...] = ("off", "sense", "suggest", "act")
PLAIN: dict[str, str] = {
    "off": "switched off",
    "sense": "able to look but not change anything",
    "suggest": "able to suggest changes and wait for your yes",
    "act": (
        "able to act without asking and tell you in one sentence "
        "(destructive things still need a yes)"
    ),
}
_RANK: dict[str, int] = {"off": 0, "sense": 1, "suggest": 2, "act": 3}


def find(store: LookStore, machine: str, connection_id: str) -> Optional[Dict[str, Any]]:
    """Return the saved connection dict, or None."""
    look = store.load(machine)
    if not isinstance(look, dict):
        return None
    conns = look.get("connections")
    if not isinstance(conns, list):
        return None
    for c in conns:
        if isinstance(c, dict) and c.get("id") == connection_id:
            return c
    return None


def describe_switch(conn: Mapping[str, Any], level: str, name: str = "TAU") -> str:
    """Return a plain sentence asking for confirmation before changing a setting."""
    conn_name = conn.get("name", "")
    conn_kind = conn.get("kind", "")
    conn_machine = conn.get("machine", "")
    place = conn.get("place")
    place_part = f", inside {place}" if place else ""
    desc = f"{name}'s access to {conn_name} ({conn_kind}) on {conn_machine}{place_part} will be {PLAIN[level]}."
    if level != "off":
        allows = conn.get("allows")
        if allows:
            desc += f" {allows}"
    desc += " Is that right?"
    return desc


def switch(
    store: LookStore,
    machine: str,
    connection_id: str,
    level: str,
    ask: Callable[[str], bool],
    actionlog: Any = None,
    who: str = "you",
    name: str = "TAU",
) -> Dict[str, Any]:
    """Change a connection setting if confirmed, leaving a receipt."""
    if level not in LEVELS:
        return {"ok": False, "say": f"I don't know the level '{level}'. Use one of: off, sense, suggest, act."}

    conn = find(store, machine, connection_id)
    if conn is None:
        return {"ok": False, "say": "I can't find that connection any more. Look again, then try once more."}

    state = conn.get("state")
    if level != "off" and state == "Needs something":
        needs = conn.get("needs")
        if needs:
            say = f"It needs something first. {needs}"
        else:
            say = "It needs something first. Check it, then look again."
        return {"ok": False, "say": say}

    if level != "off" and state == "Not found this time":
        return {
            "ok": False,
            "say": "TAU didn't find it in the last look, so it can't be switched on. Look again first.",
        }

    if conn.get("setting") == level:
        return {"ok": True, "say": "It is already set that way."}

    question = describe_switch(conn, level, name=name)
    if ask(question) is not True:
        return {"ok": False, "say": "Okay, nothing changed."}

    if not store.set_setting(machine, connection_id, level):
        return {"ok": False, "say": "I couldn't save that. Nothing changed."}

    receipt_failed = False
    if actionlog is not None:
        what = f"{conn.get('name', '')} ({conn.get('kind', '')}) set to {level}"
        place = conn.get("place")
        if place:
            what += f", inside {place}"
        try:
            actionlog.receipt(what, who, machine=machine, organ="connections")
        except Exception:
            receipt_failed = True

    say = f"Done. {conn.get('name', '')} is now {PLAIN[level]}."
    if receipt_failed:
        say += " I could not write the receipt, so check the log."
    return {"ok": True, "say": say}


def allowed(store: LookStore, machine: str, connection_id: str, need: str) -> bool:
    """Check if the connection is active and meets the requested permission rank."""
    if need not in ("sense", "suggest", "act"):
        return False
    conn = find(store, machine, connection_id)
    if conn is None:
        return False
    if conn.get("state") != "On":
        return False
    setting = conn.get("setting", "off")
    setting_rank = _RANK.get(setting, 0)
    need_rank = _RANK.get(need, 999)
    return setting_rank >= need_rank


def switched_on(store: LookStore) -> List[Dict[str, Any]]:
    """Return every saved connection whose setting is not 'off' across all machines."""
    return [c for c in store.all_connections() if c.get("setting") != "off"]


def switch_all_off(store: LookStore, machine: Optional[str] = None) -> int:
    """Turn off every active connection, optionally restricted to one machine."""
    changed = 0
    for conn in switched_on(store):
        conn_machine = conn.get("machine", "")
        if machine is not None and conn_machine != machine and conn_machine.lower() != machine.lower():
            continue
        conn_id = conn.get("id")
        if conn_id and store.set_setting(conn_machine, conn_id, "off"):
            changed += 1
    return changed
