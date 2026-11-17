"""Tests for hub-side look storage, node operation and coordinator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from tau.connect import (
    NOT_CONNECTED_NOTE,
    OLD_HELPER_NOTE,
    Connection,
    LookContext,
    LookStore,
    look_at_machine,
    look_everywhere,
    look_here,
    make_id,
)
from tau.connect.finders.machines import find_other_machines
import tau.hub.node


class FakeHub:
    """Mock hub implementation recording calls and supplying configured node behaviors."""

    def __init__(
        self,
        nodes: Optional[Dict[str, Any]] = None,
        behaviours: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._nodes = dict(nodes or {})
        self._behaviours = dict(behaviours or {})
        self.calls: List[Dict[str, Any]] = []

    def paired_nodes(self) -> Dict[str, Any]:
        return dict(self._nodes)

    def remote_call(
        self,
        machine: str,
        organ: str,
        action: str,
        args: Dict[str, Any],
        timeout: float = 30,
    ) -> Any:
        self.calls.append(
            {
                "machine": machine,
                "organ": organ,
                "action": action,
                "args": dict(args),
                "timeout": timeout,
            }
        )
        if machine in self._behaviours:
            beh = self._behaviours[machine]
            if isinstance(beh, BaseException):
                raise beh
            if callable(beh):
                return beh(machine, organ, action, args, timeout)
            return beh
        raise ConnectionError(f"{machine} isn't connected right now")


def make_conn(
    machine: str,
    name: str,
    kind: str = "Tool",
    door: str = "door",
    state: str = "Found",
    setting: str = "off",
) -> Dict[str, Any]:
    """Build a valid connection dictionary via Connection."""
    conn_id = make_id(machine, "", kind, name)
    c = Connection(
        id=conn_id,
        machine=machine,
        kind=kind,
        name=name,
        evidence=f"Found {name}",
        door=door,
        state=state,
        setting=setting,
    )
    return c.to_dict()


def make_look(
    machine: str,
    connections: Optional[List[Dict[str, Any]]] = None,
    at: str = "2026-11-15T12:00:00Z",
    finders: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a look dictionary."""
    return {
        "machine": machine,
        "at": at,
        "connections": list(connections or []),
        "finders": dict(finders or {}),
    }


def test_look_store_save_load_and_slug(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    machine_name = "Big Box #2!"
    conn = make_conn(machine_name, "Tool A")
    look = make_look(machine_name, [conn])

    saved = store.save(look)
    assert saved["machine"] == machine_name
    assert len(saved["connections"]) == 1

    loaded = store.load(machine_name)
    assert loaded == saved

    # File slug check: special characters removed and replaced with safe hyphens
    slug_file = tmp_path / "big-box-2.json"
    assert slug_file.is_file()

    # machines() listing case-insensitively sorted
    store.save(make_look("Alpha", [make_conn("Alpha", "Tool B")]))
    assert store.machines() == ["Alpha", "Big Box #2!"]


def test_look_store_save_merges_and_setting_survives(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    machine = "unit-box"
    conn1 = make_conn(machine, "Tool 1")
    conn2 = make_conn(machine, "Tool 2")

    store.save(make_look(machine, [conn1, conn2]))

    # Set setting on Tool 1
    assert store.set_setting(machine, conn1["id"], "on")
    loaded_after_set = store.load(machine)
    c1 = next(c for c in loaded_after_set["connections"] if c["id"] == conn1["id"])
    assert c1["setting"] == "on"
    assert c1["state"] == "On"

    # Second save: Tool 2 is missing from incoming discoveries
    store.save(make_look(machine, [conn1]))

    loaded_merged = store.load(machine)
    conns = {c["id"]: c for c in loaded_merged["connections"]}
    assert len(conns) == 2

    # Tool 1 retained its active setting and state
    assert conns[conn1["id"]]["setting"] == "on"
    assert conns[conn1["id"]]["state"] == "On"

    # Tool 2 marked as missing
    assert conns[conn2["id"]]["state"] == "Not found this time"


def test_look_store_set_setting(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    machine = "target-box"
    conn = make_conn(machine, "Service A", state="Found", setting="off")
    store.save(make_look(machine, [conn]))

    # Found -> On when setting is not off
    assert store.set_setting(machine, conn["id"], "custom-val")
    loaded = store.load(machine)
    assert loaded["connections"][0]["setting"] == "custom-val"
    assert loaded["connections"][0]["state"] == "On"

    # On -> Ready when setting turned back to off
    assert store.set_setting(machine, conn["id"], "off")
    loaded = store.load(machine)
    assert loaded["connections"][0]["setting"] == "off"
    assert loaded["connections"][0]["state"] == "Ready"

    # Ready -> On when setting is enabled again
    assert store.set_setting(machine, conn["id"], "on")
    loaded = store.load(machine)
    assert loaded["connections"][0]["setting"] == "on"
    assert loaded["connections"][0]["state"] == "On"

    # Unknown machine and unknown connection id return False
    assert not store.set_setting("missing-box", conn["id"], "on")
    assert not store.set_setting(machine, "nonexistent-id", "on")


def test_look_store_keep(tmp_path: Path) -> None:
    store = LookStore(tmp_path)

    # Machine never seen before: initializes with empty connections and given note
    created = store.keep("unseen-box", "Machine not reachable")
    assert created["machine"] == "unseen-box"
    assert created["at"] == ""
    assert created["connections"] == []
    assert created["finders"] == {}
    assert created["note"] == "Machine not reachable"

    loaded = store.load("unseen-box")
    assert loaded == created

    # Machine previously seen: connections remain untouched, note updated
    machine = "known-box"
    conn = make_conn(machine, "Service B")
    store.save(make_look(machine, [conn]))

    kept = store.keep(machine, "Could not refresh")
    assert kept["note"] == "Could not refresh"
    assert len(kept["connections"]) == 1
    assert kept["connections"][0]["id"] == conn["id"]


def test_look_store_all_connections(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    store.save(make_look("BoxB", [make_conn("BoxB", "Zulu", kind="B-Kind")]))
    store.save(make_look("boxA", [
        make_conn("boxA", "Bravo", kind="Tool"),
        make_conn("boxA", "alpha", kind="Tool"),
    ]))

    all_conns = store.all_connections()
    assert len(all_conns) == 3
    # Sorted by (machine lower-cased, kind, name lower-cased)
    assert all_conns[0]["machine"] == "boxA" and all_conns[0]["name"] == "alpha"
    assert all_conns[1]["machine"] == "boxA" and all_conns[1]["name"] == "Bravo"
    assert all_conns[2]["machine"] == "BoxB" and all_conns[2]["name"] == "Zulu"


def test_look_at_machine_new_helper(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    hub_name = "worker-1"
    look_from_node = make_look("reported-wrong-name", [make_conn("reported-wrong-name", "Agent")])
    hub = FakeHub(nodes={hub_name: {}}, behaviours={hub_name: look_from_node})

    res = look_at_machine(hub, hub_name, store)
    assert res["machine"] == hub_name
    assert hub.calls == [
        {"machine": hub_name, "organ": "look", "action": "run", "args": {}, "timeout": 180.0}
    ]

    stored = store.load(hub_name)
    assert stored["machine"] == hub_name
    assert len(stored["connections"]) == 1


def test_look_at_machine_old_helper(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    machine = "legacy-box"
    conn = make_conn(machine, "Existing Item")
    store.save(make_look(machine, [conn]))

    hub = FakeHub(
        nodes={machine: {}},
        behaviours={machine: RuntimeError("this node doesn't do look.run")},
    )

    res = look_at_machine(hub, machine, store)
    assert res["note"] == OLD_HELPER_NOTE
    assert len(res["connections"]) == 1
    assert res["connections"][0]["id"] == conn["id"]


def test_look_at_machine_error_cases_and_secrets(tmp_path: Path) -> None:
    store = LookStore(tmp_path)
    machine = "test-node"
    conn = make_conn(machine, "Saved Item")
    store.save(make_look(machine, [conn]))

    # ConnectionError -> NOT_CONNECTED_NOTE
    hub_conn_err = FakeHub(nodes={machine: {}}, behaviours={machine: ConnectionError("socket lost")})
    res = look_at_machine(hub_conn_err, machine, store)
    assert res["note"] == NOT_CONNECTED_NOTE
    assert len(res["connections"]) == 1

    # TimeoutError -> timeout note
    hub_timeout = FakeHub(nodes={machine: {}}, behaviours={machine: TimeoutError("slow")})
    res = look_at_machine(hub_timeout, machine, store)
    assert res["note"] == "This machine took too long to answer, so this is what TAU saw last time."
    assert len(res["connections"]) == 1

    # Garbage reply -> general error note
    hub_garbage = FakeHub(nodes={machine: {}}, behaviours={machine: "nope"})
    res = look_at_machine(hub_garbage, machine, store)
    assert res["note"] == "TAU could not read this machine this time, so this is what it saw last time."
    assert len(res["connections"]) == 1

    # Unexpected ValueError with sensitive message -> general error note, secret not written
    secret_message = "SECRET DETAIL 12345"
    hub_val_err = FakeHub(nodes={machine: {}}, behaviours={machine: ValueError(secret_message)})
    res = look_at_machine(hub_val_err, machine, store)
    assert res["note"] == "TAU could not read this machine this time, so this is what it saw last time."
    assert len(res["connections"]) == 1

    file_text = (tmp_path / f"{machine}.json").read_text(encoding="utf-8")
    assert secret_message not in file_text


def test_look_everywhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = LookStore(tmp_path)
    recorded_paired: List[Optional[List[str]]] = []

    def fake_look_here(paired: Optional[List[str]] = None) -> Dict[str, Any]:
        recorded_paired.append(paired)
        return {
            "machine": "local-workstation",
            "at": "2026-11-15T12:00:00Z",
            "connections": [make_conn("local-workstation", "Local Tool")],
            "finders": {},
        }

    monkeypatch.setattr("tau.connect.lab.look_here", fake_look_here)

    hub = FakeHub(
        nodes={"node-ok": {}, "node-down": {}},
        behaviours={
            "node-ok": make_look("node-ok", [make_conn("node-ok", "Node Tool")]),
            "node-down": ConnectionError("unreachable"),
        },
    )

    results = look_everywhere(hub, store)

    # Three machines in result dictionary
    assert len(results) == 3
    assert set(results.keys()) == {"local-workstation", "node-ok", "node-down"}

    # Paired node names were passed to look_here in sorted order
    assert recorded_paired == [["node-down", "node-ok"]]

    # Store contains all three machines
    assert sorted(store.machines()) == ["local-workstation", "node-down", "node-ok"]


def test_look_here_imports_and_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from tau import discovery
    from tau.discovery import neighbours
    import tau.connect.lab

    monkeypatch.setattr(
        discovery,
        "collect",
        lambda: {"machine": {"name": "unit-pc"}, "ports": [], "drives": []},
    )

    def fail_neighbours() -> Any:
        raise OSError("Neighbour discovery failed")

    monkeypatch.setattr(neighbours, "list_neighbours", fail_neighbours)

    captured_ctx: List[LookContext] = []

    def mock_run_look(ctx: LookContext) -> Dict[str, Any]:
        captured_ctx.append(ctx)
        return {
            "machine": ctx.machine,
            "at": "2026-11-15T12:00:00Z",
            "connections": [],
            "finders": {},
        }

    monkeypatch.setattr(tau.connect.lab, "run_look", mock_run_look)

    look = look_here(paired=["a"])
    assert look["machine"] == "unit-pc"
    assert len(captured_ctx) == 1
    ctx = captured_ctx[0]
    assert ctx.snapshot["neighbours"] == []
    assert ctx.snapshot["paired"] == ["a"]


def test_node_helper_registered_operations() -> None:
    assert "look.run" in tau.hub.node.NODE_OPS
    assert "discover.run" in tau.hub.node.NODE_OPS

    op = tau.hub.node.NODE_OPS["look.run"]
    assert callable(op)


def test_machines_finder_paired_filtering() -> None:
    # box.lan matches paired BOX (case-insensitive and prefix before dot)
    ctx1 = LookContext(
        environ={},
        machine="host-pc",
        snapshot={
            "neighbours": [{"name": "box.lan"}, {"name": "spare"}],
            "paired": ["BOX"],
        },
    )
    results1 = find_other_machines(ctx1)
    assert [c.name for c in results1] == ["spare"]

    # Reverse: neighbour box matches paired BOX.LAN
    ctx2 = LookContext(
        environ={},
        machine="host-pc",
        snapshot={
            "neighbours": [{"name": "box"}, {"name": "spare"}],
            "paired": ["BOX.LAN"],
        },
    )
    results2 = find_other_machines(ctx2)
    assert [c.name for c in results2] == ["spare"]
