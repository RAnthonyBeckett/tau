"""Connection screen view and switch handling for TAU."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

from tau.connect import lab, switches, view
from tau.connect.store import LookStore
from tau.connect.view import summary_lines, tree

LEVEL_CHOICES: list[dict[str, str]] = [
    {"value": "off", "label": "Off"},
    {"value": "sense", "label": "Look only"},
    {"value": "suggest", "label": "Ask first"},
    {"value": "act", "label": "Act"},
]


def screen_view(store: LookStore, only_on: bool = False, text: str = "") -> dict[str, Any]:
    looks: dict[str, dict[str, Any]] = {}
    for name in store.machines():
        loaded = store.load(name)
        if isinstance(loaded, dict) and loaded:
            looks[name] = loaded
    return {
        "machines": tree(looks, only_on=only_on, text=text),
        "summary": summary_lines(looks),
        "levels": LEVEL_CHOICES,
        "looked": bool(looks),
        "only_on": only_on,
        "text": text,
    }


def spoken_summary(store: LookStore) -> str:
    looks: dict[str, dict[str, Any]] = {}
    for name in store.machines():
        loaded = store.load(name)
        if isinstance(loaded, dict) and loaded:
            looks[name] = loaded
    if not looks:
        return "I haven't looked yet. Open Connections on the display and press Look now."
    lines = summary_lines(looks)
    return " ".join(lines) + " Open Connections on the display to see them and choose what to switch on."


def handle(
    store: LookStore,
    data: Mapping[str, Any],
    hub: Any = None,
    actionlog: Any = None,
    name: str = "TAU",
    looker: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    if looker is None:
        looker = lab.look_everywhere

    def current_view() -> dict[str, Any]:
        return screen_view(store, only_on=bool(data.get("only_on")), text=str(data.get("text") or ""))

    op = data.get("op")
    if op == "view":
        return {"ok": True, "view": current_view()}

    if op == "look":
        try:
            looker(hub, store)
        except Exception:
            return {
                "ok": False,
                "say": "The look didn't finish. Nothing was changed.",
                "view": current_view(),
            }
        looks: dict[str, dict[str, Any]] = {}
        for m_name in store.machines():
            loaded = store.load(m_name)
            if isinstance(loaded, dict) and loaded:
                looks[m_name] = loaded
        return {
            "ok": True,
            "say": " ".join(summary_lines(looks)),
            "view": current_view(),
        }

    if op == "switch":
        machine = data.get("machine")
        if not isinstance(machine, str) or not machine:
            return {"ok": False, "say": "Please give a machine name."}

        ids = data.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
            return {"ok": False, "say": "Please give at least one connection to change."}

        level = data.get("level")
        if level not in switches.LEVELS:
            return {
                "ok": False,
                "say": f"I don't know the level '{level}'. Use one of: off, sense, suggest, act.",
            }

        if data.get("confirm") is not True:
            first_conn = switches.find(store, machine, ids[0])
            if first_conn is None:
                return {
                    "ok": False,
                    "say": "I can't find that connection any more. Look again, then try once more.",
                }
            question = switches.describe_switch(first_conn, level, name=name)
            if len(ids) > 1:
                question = f"This changes {len(ids)} entries for the same program. {question}"
            return {"ok": False, "confirm": question}

        last_say = ""
        for conn_id in ids:
            res = switches.switch(
                store,
                machine,
                conn_id,
                level,
                ask=lambda q: True,
                actionlog=actionlog,
                name=name,
            )
            if not res.get("ok"):
                return {
                    "ok": False,
                    "say": res.get("say", ""),
                    "view": current_view(),
                }
            last_say = res.get("say", "")
        return {
            "ok": True,
            "say": last_say,
            "view": current_view(),
        }

    if op == "all_off":
        machine_val = data.get("machine")
        machine_name = str(machine_val) if machine_val else None

        if data.get("confirm") is not True:
            if machine_name:
                question = f"Every connection on {machine_name} will be switched off. Is that right?"
            else:
                question = "Every connection on every machine will be switched off. Is that right?"
            return {"ok": False, "confirm": question}

        n = switches.switch_all_off(store, machine=machine_name)
        return {
            "ok": True,
            "say": f"Done. {n} switched off.",
            "view": current_view(),
        }

    return {"ok": False, "say": "I don't know that request."}
