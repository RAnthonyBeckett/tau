"""Tests for connection switches and permission management."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from tau.connect import (
    Connection,
    LookStore,
    allowed,
    describe_switch,
    switch,
    switch_all_off,
    switched_on,
)
from tau.connect.switches import find


class FakeLog:
    """Record receipt calls for verification in tests."""

    def __init__(self, explode: bool = False) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.explode = explode

    def receipt(
        self,
        what: str,
        who: str,
        machine: Optional[str] = None,
        organ: str = "permissions",
    ) -> Dict[str, Any]:
        if self.explode:
            raise RuntimeError("SECRET")
        call = {
            "what": what,
            "who": who,
            "machine": machine,
            "organ": organ,
        }
        self.calls.append(call)
        return call


def build_test_store(root: Path) -> tuple[LookStore, Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Build a store with one machine holding four connections."""
    store = LookStore(root)
    conn_a = Connection(
        id="conn-a",
        machine="den-pc",
        kind="Tool",
        name="Tool A",
        evidence="Found Tool A",
        door="door-a",
        state="Found",
        setting="off",
        allows="Can read files and report findings.",
    ).to_dict()
    conn_b = Connection(
        id="conn-b",
        machine="den-pc",
        kind="Service",
        name="Service B",
        evidence="Found Service B",
        door="door-b",
        state="Ready",
        place="Work-Env",
        setting="off",
        allows="Can send messages to friends.",
    ).to_dict()
    conn_c = Connection(
        id="conn-c",
        machine="den-pc",
        kind="Integration",
        name="Integration C",
        evidence="Found Integration C",
        door="door-c",
        state="Needs something",
        needs="Type its key into TAU's settings.",
        setting="off",
    ).to_dict()
    conn_d = Connection(
        id="conn-d",
        machine="den-pc",
        kind="App",
        name="App D",
        evidence="Found App D",
        door="door-d",
        state="Found",
        setting="off",
    ).to_dict()

    look = {
        "machine": "den-pc",
        "at": "2026-11-15T12:00:00Z",
        "connections": [conn_a, conn_b, conn_c, conn_d],
        "finders": {},
    }
    store.save(look)
    return store, conn_a, conn_b, conn_c, conn_d


def test_describe_switch(tmp_path: Path) -> None:
    _, conn_a, conn_b, _, _ = build_test_store(tmp_path)

    desc_a = describe_switch(conn_a, "sense")
    assert "Tool A" in desc_a
    assert "Tool" in desc_a
    assert "den-pc" in desc_a
    assert "able to look but not change anything" in desc_a
    assert "Can read files and report findings." in desc_a
    assert desc_a.endswith("Is that right?")
    assert "inside" not in desc_a

    desc_b = describe_switch(conn_b, "suggest")
    assert "Service B" in desc_b
    assert "inside Work-Env" in desc_b
    assert "able to suggest changes and wait for your yes" in desc_b
    assert "Can send messages to friends." in desc_b
    assert desc_b.endswith("Is that right?")

    desc_off = describe_switch(conn_a, "off")
    assert "switched off" in desc_off
    assert "Can read files and report findings." not in desc_off
    assert desc_off.endswith("Is that right?")


def test_yes_switches_a_to_sense(tmp_path: Path) -> None:
    store, _, _, _, _ = build_test_store(tmp_path)
    log = FakeLog()

    res = switch(store, "den-pc", "conn-a", "sense", ask=lambda q: True, actionlog=log, who="you")
    assert res["ok"] is True
    assert "Done. Tool A is now able to look but not change anything." == res["say"]

    loaded = find(store, "den-pc", "conn-a")
    assert loaded is not None
    assert loaded["setting"] == "sense"
    assert loaded["state"] == "On"

    assert len(log.calls) == 1
    call = log.calls[0]
    assert call["organ"] == "connections"
    assert call["machine"] == "den-pc"
    assert call["who"] == "you"
    assert "Tool A" in call["what"]
    assert "sense" in call["what"]


def test_yes_must_be_strictly_true(tmp_path: Path) -> None:
    store, _, _, _, _ = build_test_store(tmp_path)

    for invalid_approval in ["yes", 1, None]:
        log = FakeLog()
        res = switch(
            store,
            "den-pc",
            "conn-a",
            "sense",
            ask=lambda q: invalid_approval,  # type: ignore[arg-type]
            actionlog=log,
        )
        assert res["ok"] is False
        assert res["say"] == "Okay, nothing changed."
        assert len(log.calls) == 0

        current = find(store, "den-pc", "conn-a")
        assert current is not None
        assert current["setting"] == "off"
        assert current["state"] == "Found"


def test_no_changes_nothing(tmp_path: Path) -> None:
    store, _, _, _, _ = build_test_store(tmp_path)
    log = FakeLog()

    res = switch(store, "den-pc", "conn-a", "sense", ask=lambda q: False, actionlog=log)
    assert res["ok"] is False
    assert res["say"] == "Okay, nothing changed."
    assert len(log.calls) == 0

    current = find(store, "den-pc", "conn-a")
    assert current is not None
    assert current["setting"] == "off"


def test_unknown_level_and_unknown_id(tmp_path: Path) -> None:
    store, _, _, _, _ = build_test_store(tmp_path)
    ask_called = False

    def spy_ask(q: str) -> bool:
        nonlocal ask_called
        ask_called = True
        return True

    res_level = switch(store, "den-pc", "conn-a", "turbo", ask=spy_ask)
    assert res_level["ok"] is False
    assert "I don't know the level 'turbo'. Use one of: off, sense, suggest, act." == res_level["say"]
    assert not ask_called

    res_id = switch(store, "den-pc", "missing-conn", "sense", ask=spy_ask)
    assert res_id["ok"] is False
    assert "I can't find that connection any more. Look again, then try once more." == res_id["say"]
    assert not ask_called


def test_needs_something_cannot_be_switched_on(tmp_path: Path) -> None:
    store, _, _, _, _ = build_test_store(tmp_path)
    ask_called = False

    def spy_ask(q: str) -> bool:
        nonlocal ask_called
        ask_called = True
        return True

    res_sense = switch(store, "den-pc", "conn-c", "sense", ask=spy_ask)
    assert res_sense["ok"] is False
    assert not ask_called
    assert "Type its key into TAU's settings." in res_sense["say"]
    assert res_sense["say"] == "It needs something first. Type its key into TAU's settings."

    res_off = switch(store, "den-pc", "conn-c", "off", ask=spy_ask)
    assert res_off["ok"] is True
    assert not ask_called
    assert res_off["say"] == "It is already set that way."


def test_missing_connection_cannot_be_switched_on(tmp_path: Path) -> None:
    store, conn_a, conn_b, conn_c, _ = build_test_store(tmp_path)

    second_look = {
        "machine": "den-pc",
        "at": "2026-11-15T13:00:00Z",
        "connections": [conn_a, conn_b, conn_c],
        "finders": {},
    }
    store.save(second_look)

    conn_d = find(store, "den-pc", "conn-d")
    assert conn_d is not None
    assert conn_d["state"] == "Not found this time"

    ask_called = False

    def spy_ask(q: str) -> bool:
        nonlocal ask_called
        ask_called = True
        return True

    res = switch(store, "den-pc", "conn-d", "sense", ask=spy_ask)
    assert res["ok"] is False
    assert not ask_called
    assert res["say"] == "TAU didn't find it in the last look, so it can't be switched on. Look again first."


def test_switching_already_at_level(tmp_path: Path) -> None:
    store, _, _, _, _ = build_test_store(tmp_path)
    log = FakeLog()

    res1 = switch(store, "den-pc", "conn-a", "sense", ask=lambda q: True, actionlog=log)
    assert res1["ok"] is True
    assert len(log.calls) == 1

    ask_called = False

    def spy_ask(q: str) -> bool:
        nonlocal ask_called
        ask_called = True
        return True

    res2 = switch(store, "den-pc", "conn-a", "sense", ask=spy_ask, actionlog=log)
    assert res2["ok"] is True
    assert res2["say"] == "It is already set that way."
    assert not ask_called
    assert len(log.calls) == 1


def test_switching_back_to_off(tmp_path: Path) -> None:
    store, _, _, _, _ = build_test_store(tmp_path)
    switch(store, "den-pc", "conn-a", "sense", ask=lambda q: True)
    conn_a_on = find(store, "den-pc", "conn-a")
    assert conn_a_on is not None
    assert conn_a_on["state"] == "On"

    log = FakeLog()
    asked: List[str] = []

    def spy_ask(q: str) -> bool:
        asked.append(q)
        return True

    res = switch(store, "den-pc", "conn-a", "off", ask=spy_ask, actionlog=log)
    assert res["ok"] is True
    assert len(asked) == 1
    assert "switched off" in asked[0]

    conn_a_off = find(store, "den-pc", "conn-a")
    assert conn_a_off is not None
    assert conn_a_off["setting"] == "off"
    assert conn_a_off["state"] == "Ready"

    assert len(log.calls) == 1
    assert "off" in log.calls[0]["what"]


def test_receipt_failure_retains_change(tmp_path: Path) -> None:
    store, _, _, _, _ = build_test_store(tmp_path)
    failing_log = FakeLog(explode=True)

    res = switch(store, "den-pc", "conn-a", "suggest", ask=lambda q: True, actionlog=failing_log)
    assert res["ok"] is True
    conn_a = find(store, "den-pc", "conn-a")
    assert conn_a is not None
    assert conn_a["setting"] == "suggest"
    assert conn_a["state"] == "On"
    assert "receipt" in res["say"]
    assert "SECRET" not in res["say"]


def test_allowed(tmp_path: Path) -> None:
    store, conn_a, conn_b, conn_c, _ = build_test_store(tmp_path)

    assert allowed(store, "den-pc", "conn-a", "sense") is False

    switch(store, "den-pc", "conn-a", "suggest", ask=lambda q: True)
    assert allowed(store, "den-pc", "conn-a", "sense") is True
    assert allowed(store, "den-pc", "conn-a", "suggest") is True
    assert allowed(store, "den-pc", "conn-a", "act") is False
    assert allowed(store, "den-pc", "conn-a", "everything") is False
    assert allowed(store, "den-pc", "unknown-id", "sense") is False

    switch(store, "den-pc", "conn-d", "sense", ask=lambda q: True)
    second_look = {
        "machine": "den-pc",
        "at": "2026-11-15T14:00:00Z",
        "connections": [conn_a, conn_b, conn_c],
        "finders": {},
    }
    store.save(second_look)
    conn_d_missing = find(store, "den-pc", "conn-d")
    assert conn_d_missing is not None
    assert conn_d_missing["setting"] == "sense"
    assert conn_d_missing["state"] == "Not found this time"
    assert allowed(store, "den-pc", "conn-d", "sense") is False


def test_switched_on_and_switch_all_off(tmp_path: Path) -> None:
    store, _, _, _, _ = build_test_store(tmp_path)

    assert switched_on(store) == []

    switch(store, "den-pc", "conn-a", "sense", ask=lambda q: True)
    switch(store, "den-pc", "conn-b", "act", ask=lambda q: True)

    active = switched_on(store)
    assert len(active) == 2
    assert {c["id"] for c in active} == {"conn-a", "conn-b"}

    conn_other = Connection(
        id="conn-other-1",
        machine="remote-box",
        kind="Tool",
        name="Remote Tool",
        evidence="Found on remote box",
        door="door-remote",
        state="Found",
        setting="off",
    ).to_dict()
    store.save({
        "machine": "remote-box",
        "at": "2026-11-15T12:00:00Z",
        "connections": [conn_other],
        "finders": {},
    })
    switch(store, "remote-box", "conn-other-1", "suggest", ask=lambda q: True)

    assert len(switched_on(store)) == 3

    changed_den = switch_all_off(store, machine="den-pc")
    assert changed_den == 2

    conn_a = find(store, "den-pc", "conn-a")
    conn_b = find(store, "den-pc", "conn-b")
    conn_remote = find(store, "remote-box", "conn-other-1")
    assert conn_a is not None and conn_a["setting"] == "off"
    assert conn_b is not None and conn_b["setting"] == "off"
    assert conn_remote is not None and conn_remote["setting"] == "suggest"

    changed_all = switch_all_off(store)
    assert changed_all == 1
    assert switched_on(store) == []
