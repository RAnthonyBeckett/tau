"""Tests for the connections screen view, switches, spoken summary, and HTTP routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.request

import pytest

from tau.connect import (
    LEVEL_CHOICES,
    LookStore,
    handle_connections,
    screen_view,
    spoken_summary,
    switches,
)
from tau.display.server import DisplayHub


def make_conn(
    id: str = "c1",
    machine: str = "box",
    place: str = "",
    kind: str = "Command-line tool",
    name: str = "tool",
    state: str = "Found",
    setting: str = "off",
    evidence: str = "e",
    allows: str = "a",
    needs: str = "",
    door: str = "d",
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a connection dictionary for testing view and screen logic."""
    return {
        "id": id,
        "machine": machine,
        "place": place,
        "kind": kind,
        "name": name,
        "state": state,
        "setting": setting,
        "evidence": evidence,
        "allows": allows,
        "needs": needs,
        "door": door,
        "detail": dict(detail) if detail is not None else {},
    }


class FakeLog:
    """Record receipt calls for verification in tests."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def receipt(
        self,
        what: str,
        who: str,
        machine: Optional[str] = None,
        organ: str = "connections",
    ) -> Dict[str, Any]:
        call = {
            "what": what,
            "who": who,
            "machine": machine,
            "organ": organ,
        }
        self.calls.append(call)
        return call


def test_screen_view_empty_store(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    view_data = screen_view(store)
    assert view_data["looked"] is False
    assert view_data["machines"] == []
    assert view_data["summary"] == ["TAU hasn't looked at any machine yet."]
    assert view_data["levels"] == LEVEL_CHOICES
    assert len(view_data["levels"]) == 4
    assert view_data["only_on"] is False
    assert view_data["text"] == ""


def test_screen_view_one_look_two_kinds(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    c1 = make_conn(id="c1", machine="box-a", kind="Model server", name="server-1", setting="off")
    c2 = make_conn(id="c2", machine="box-a", kind="Command-line tool", name="tool-1", setting="sense")
    store.save({
        "machine": "box-a",
        "at": "2026-11-15T12:00:00Z",
        "connections": [c1, c2],
    })

    view_data = screen_view(store)
    assert view_data["looked"] is True
    assert len(view_data["machines"]) == 1
    m = view_data["machines"][0]
    assert m["machine"] == "box-a"
    assert m["found"] == 2
    assert m["on"] == 1
    assert len(m["places"]) == 1
    kinds = [k["kind"] for k in m["places"][0]["kinds"]]
    assert "Model server" in kinds
    assert "Command-line tool" in kinds

    filtered_on = screen_view(store, only_on=True)
    assert filtered_on["only_on"] is True
    assert len(filtered_on["machines"]) == 1
    kinds_on = [k["kind"] for k in filtered_on["machines"][0]["places"][0]["kinds"]]
    assert kinds_on == ["Command-line tool"]

    filtered_text = screen_view(store, text="server")
    assert filtered_text["text"] == "server"
    assert len(filtered_text["machines"]) == 1
    kinds_text = [k["kind"] for k in filtered_text["machines"][0]["places"][0]["kinds"]]
    assert kinds_text == ["Model server"]


def test_handle_switch_confirm_and_non_confirm(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    c = make_conn(id="conn-1", machine="box-a", kind="Tool", name="My Tool", setting="off")
    store.save({"machine": "box-a", "connections": [c]})

    # Without confirm
    res = handle_connections(store, {"op": "switch", "machine": "box-a", "ids": ["conn-1"], "level": "sense"})
    assert res["ok"] is False
    assert "confirm" in res
    assert "My Tool" in res["confirm"]
    conn_after = switches.find(store, "box-a", "conn-1")
    assert conn_after is not None
    assert conn_after["setting"] == "off"

    # With confirm as string "yes", not boolean True
    res_str = handle_connections(
        store,
        {"op": "switch", "machine": "box-a", "ids": ["conn-1"], "level": "sense", "confirm": "yes"},
    )
    assert res_str["ok"] is False
    assert "confirm" in res_str
    conn_after2 = switches.find(store, "box-a", "conn-1")
    assert conn_after2 is not None
    assert conn_after2["setting"] == "off"

    # With confirm True
    res_true = handle_connections(
        store,
        {"op": "switch", "machine": "box-a", "ids": ["conn-1"], "level": "sense", "confirm": True},
    )
    assert res_true["ok"] is True
    assert "view" in res_true
    assert res_true["view"]["machines"][0]["on"] == 1
    conn_after3 = switches.find(store, "box-a", "conn-1")
    assert conn_after3 is not None
    assert conn_after3["setting"] == "sense"


def test_handle_switch_three_ids_no_confirm(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    c1 = make_conn(id="c1", machine="box", name="Prog 1", setting="off")
    c2 = make_conn(id="c2", machine="box", name="Prog 2", setting="off")
    c3 = make_conn(id="c3", machine="box", name="Prog 3", setting="off")
    store.save({"machine": "box", "connections": [c1, c2, c3]})

    res = handle_connections(
        store,
        {"op": "switch", "machine": "box", "ids": ["c1", "c2", "c3"], "level": "sense"},
    )
    assert res["ok"] is False
    assert "confirm" in res
    assert res["confirm"].startswith("This changes 3 entries")


def test_handle_switch_invalid_inputs(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    c = make_conn(id="c1", machine="box", name="Tool", setting="off")
    store.save({"machine": "box", "connections": [c]})

    # Unknown level
    r1 = handle_connections(
        store,
        {"op": "switch", "machine": "box", "ids": ["c1"], "level": "bad_level", "confirm": True},
    )
    assert r1["ok"] is False
    assert "I don't know the level 'bad_level'" in r1["say"]

    # Empty ids list
    r2 = handle_connections(
        store,
        {"op": "switch", "machine": "box", "ids": [], "level": "sense", "confirm": True},
    )
    assert r2["ok"] is False
    assert isinstance(r2.get("say"), str) and len(r2["say"]) > 0

    # Unknown id with confirm True
    r3 = handle_connections(
        store,
        {"op": "switch", "machine": "box", "ids": ["missing-id"], "level": "sense", "confirm": True},
    )
    assert r3["ok"] is False
    assert "I can't find that connection any more" in r3["say"]

    # Unknown id without confirm
    r4 = handle_connections(
        store,
        {"op": "switch", "machine": "box", "ids": ["missing-id"], "level": "sense"},
    )
    assert r4["ok"] is False
    assert "I can't find that connection any more" in r4["say"]

    conn = switches.find(store, "box", "c1")
    assert conn is not None and conn["setting"] == "off"


def test_handle_all_off(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    c1 = make_conn(id="c1", machine="box", name="Tool 1", setting="sense")
    c2 = make_conn(id="c2", machine="box", name="Tool 2", setting="act")
    store.save({"machine": "box", "connections": [c1, c2]})

    # Without confirm
    res_no_confirm = handle_connections(store, {"op": "all_off", "machine": "box"})
    assert res_no_confirm["ok"] is False
    assert "confirm" in res_no_confirm
    assert "Every connection on box will be switched off" in res_no_confirm["confirm"]

    # With confirm True
    res_confirmed = handle_connections(store, {"op": "all_off", "machine": "box", "confirm": True})
    assert res_confirmed["ok"] is True
    assert res_confirmed["say"] == "Done. 2 switched off."
    assert "view" in res_confirmed
    assert res_confirmed["view"]["machines"][0]["on"] == 0


def test_handle_look(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    calls = []

    def fake_looker(hub: Any, st: LookStore) -> None:
        calls.append((hub, st))
        c = make_conn(id="c1", machine="box", name="Tool", setting="off")
        st.save({"machine": "box", "connections": [c]})

    res = handle_connections(store, {"op": "look"}, hub="test-hub", looker=fake_looker)
    assert res["ok"] is True
    assert len(calls) == 1
    assert calls[0] == ("test-hub", store)
    assert "say" in res
    assert "view" in res

    def exploding_looker(hub: Any, st: LookStore) -> None:
        raise RuntimeError("look error")

    res_fail = handle_connections(store, {"op": "look"}, hub="test-hub", looker=exploding_looker)
    assert res_fail["ok"] is False
    assert res_fail["say"] == "The look didn't finish. Nothing was changed."
    assert "view" in res_fail


def test_handle_switch_writes_receipt(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    c = make_conn(id="c1", machine="box", name="Tool", setting="off")
    store.save({"machine": "box", "connections": [c]})

    fake_log = FakeLog()
    res = handle_connections(
        store,
        {"op": "switch", "machine": "box", "ids": ["c1"], "level": "sense", "confirm": True},
        actionlog=fake_log,
    )
    assert res["ok"] is True
    assert len(fake_log.calls) == 1
    assert fake_log.calls[0]["machine"] == "box"
    assert "sense" in fake_log.calls[0]["what"]


def test_spoken_summary(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    empty_res = spoken_summary(store)
    assert empty_res == "I haven't looked yet. Open Connections on the display and press Look now."

    c = make_conn(id="c1", machine="box-alpha", name="SecretToolNameThatMustNotAppear", setting="off")
    store.save({"machine": "box-alpha", "connections": [c]})
    filled_res = spoken_summary(store)
    assert "SecretToolNameThatMustNotAppear" not in filled_res
    assert "box-alpha: 1 ways to connect, 0 switched on." in filled_res
    assert filled_res.endswith("Open Connections on the display to see them and choose what to switch on.")


def test_http_connections_endpoints(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    c1 = make_conn(id="c1", machine="box", name="Tool 1", setting="sense")
    c2 = make_conn(id="c2", machine="box", name="Tool 2", setting="off")
    store.save({"machine": "box", "connections": [c1, c2]})

    def fake_looker(hub: Any, st: LookStore) -> None:
        pass

    hub = DisplayHub({}, on_connections=lambda d: handle_connections(store, d, looker=fake_looker))
    port = hub.serve(0)
    base = f"http://127.0.0.1:{port}"
    try:
        # GET /connections
        with urllib.request.urlopen(base + "/connections") as r:
            assert r.status == 200
            data = json.loads(r.read().decode())
        assert data["looked"] is True
        assert len(data["machines"]) == 1
        assert data["machines"][0]["found"] == 2

        # GET /connections?only_on=1
        with urllib.request.urlopen(base + "/connections?only_on=1") as r:
            assert r.status == 200
            data_on = json.loads(r.read().decode())
        assert data_on["only_on"] is True
        assert data_on["machines"][0]["found"] == 1
        rows = data_on["machines"][0]["places"][0]["kinds"][0]["rows"]
        assert len(rows) == 1
        assert rows[0]["id"] == "c1"

        # POST /connections without confirm
        body = json.dumps({"op": "switch", "machine": "box", "ids": ["c2"], "level": "sense"}).encode()
        req = urllib.request.Request(
            base + "/connections",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as r:
            assert r.status == 200
            post_reply = json.loads(r.read().decode())
        assert post_reply["ok"] is False
        assert "confirm" in post_reply
    finally:
        hub.stop()


def test_no_software_names_in_screen() -> None:
    target = Path(__file__).resolve().parent.parent / "src" / "tau" / "connect" / "screen.py"
    content = target.read_text(encoding="utf-8").lower()
    software_products = [
        "chrome",
        "firefox",
        "photoshop",
        "slack",
        "discord",
        "spotify",
        "excel",
        "powerpoint",
        "vlc",
        "zoom",
    ]
    for product in software_products:
        assert product not in content, f"Software product '{product}' found in screen.py"


def test_reflex_connections_patterns() -> None:
    from tau.reflex import ReflexRouter

    router = ReflexRouter()
    phrases = [
        "what can you connect to",
        "what can you connect to?",
        "what could you connect to",
        "show me the connections",
        "what connections are there",
    ]
    for phrase in phrases:
        m = router.match(phrase)
        assert m is not None, f"Phrase '{phrase}' failed to match any reflex"
        assert m.reflex.name == "connections"
        assert m.reflex.special == "connections"


def test_assistant_connections_reflex(tmp_path: Path) -> None:
    from tau.assistant import Assistant

    profile = {"tau": {"name": "TAU"}}
    asst = Assistant(profile)
    asst.look_store = LookStore(tmp_path)
    c = make_conn(id="c1", machine="box", name="Tool", setting="off")
    asst.look_store.save({"machine": "box", "connections": [c]})

    reply = asst.handle("what can you connect to?")
    assert reply.kind == "reflex"
    assert reply.reflex == "connections"
    assert "box: 1 ways to connect, 0 switched on." in reply.say
