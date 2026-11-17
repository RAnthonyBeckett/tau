"""Tests for connection records and registry functionality."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from tau.connect import (
    OFF,
    Connection,
    FinderSpec,
    LookContext,
    counts,
    finder,
    load_finders,
    make_id,
    merge,
    run_look,
)
import tau.connect.registry as reg_module


def test_make_id_properties() -> None:
    first = make_id("host-a", "room", "Local port", "8080")
    second = make_id("host-a", "room", "Local port", "8080")
    assert first == second
    assert first.startswith("local-port-")

    third = make_id(" HOST-A ", " ROOM ", " local PORT ", " 8080 ")
    assert third == first

    assert make_id("host-b", "room", "Local port", "8080") != first
    assert make_id("host-a", "hall", "Local port", "8080") != first
    assert make_id("host-a", "room", "Remote port", "8080") != first
    assert make_id("host-a", "room", "Local port", "9090") != first


def test_connection_validation_and_dict_conversion() -> None:
    with pytest.raises(ValueError):
        Connection(
            id="c1",
            machine="box",
            kind="tool",
            name="echo",
            evidence="found in path",
            door="cli",
            state="BrokenState",
        )

    with pytest.raises(ValueError):
        Connection(
            id="c1",
            machine="box",
            kind="tool",
            name="echo",
            evidence="   ",
            door="cli",
        )

    conn = Connection(
        id="c1",
        machine="box",
        kind="tool",
        name="echo",
        evidence="found in path",
        door="cli",
        state="Ready",
        place="venv",
        allows="testing",
        needs="nothing",
        setting="active",
        last_seen="2026-11-14T00:00:00Z",
        detail={"version": "1.0"},
    )
    data = conn.to_dict()
    reconstructed = Connection.from_dict(data)
    assert reconstructed == conn

    data_with_extra = dict(data)
    data_with_extra["unknown_extra_field"] = "ignored_value"
    from_extra = Connection.from_dict(data_with_extra)
    assert from_extra == conn


def test_merge_rules() -> None:
    now = "2026-11-14T12:00:00Z"
    c_new = Connection(
        id="c-new",
        machine="host-1",
        kind="port",
        name="alpha",
        evidence="listening",
        door="http",
    )
    c_found = Connection(
        id="c-found",
        machine="host-1",
        kind="port",
        name="beta",
        evidence="listening",
        door="http",
        state="Found",
    )
    c_needs = Connection(
        id="c-needs",
        machine="host-1",
        kind="tool",
        name="gamma",
        evidence="binary present",
        door="cli",
        state="Needs something",
    )

    previous = [
        {
            "id": "c-found",
            "machine": "host-1",
            "kind": "port",
            "name": "beta",
            "evidence": "listening",
            "door": "http",
            "state": "Found",
            "setting": "allowed",
            "last_seen": "yesterday",
        },
        {
            "id": "c-needs",
            "machine": "host-1",
            "kind": "tool",
            "name": "gamma",
            "evidence": "binary present",
            "door": "cli",
            "state": "Needs something",
            "setting": "allowed",
            "last_seen": "yesterday",
        },
        {
            "id": "c-old",
            "machine": "host-0",
            "kind": "service",
            "name": "delta",
            "evidence": "service listed",
            "door": "pipe",
            "state": "Ready",
            "setting": "user_on",
            "last_seen": "yesterday",
        },
    ]

    merged = merge(previous, [c_new, c_found, c_needs], now)

    by_id = {row["id"]: row for row in merged}

    assert by_id["c-new"]["last_seen"] == now
    assert by_id["c-new"]["setting"] == OFF

    assert by_id["c-found"]["setting"] == "allowed"
    assert by_id["c-found"]["state"] == "On"
    assert by_id["c-found"]["last_seen"] == now

    assert by_id["c-needs"]["setting"] == "allowed"
    assert by_id["c-needs"]["state"] == "Needs something"

    assert by_id["c-old"]["state"] == "Not found this time"
    assert by_id["c-old"]["setting"] == "user_on"
    assert by_id["c-old"]["last_seen"] == "yesterday"

    keys = [
        (
            r.get("machine", ""),
            r.get("place", ""),
            r.get("kind", ""),
            r.get("name", "").lower(),
        )
        for r in merged
    ]
    assert keys == sorted(keys)


def test_finder_decorator(monkeypatch: pytest.MonkeyPatch) -> None:
    test_finders: dict[str, FinderSpec] = {}
    monkeypatch.setattr(reg_module, "FINDERS", test_finders)

    @finder("sample_finder", timeout=5.0, max_rows=100)
    def dummy_finder(ctx: LookContext) -> list[Connection]:
        return []

    assert "sample_finder" in test_finders
    assert test_finders["sample_finder"].timeout == 5.0
    assert test_finders["sample_finder"].max_rows == 100

    with pytest.raises(ValueError):
        @finder("sample_finder")
        def duplicate_finder(ctx: LookContext) -> list[Connection]:
            return []


def test_run_look_execution_and_error_handling() -> None:
    ctx = LookContext(machine="test-machine")

    def normal_finder(c: LookContext) -> list[Any]:
        return [
            Connection(
                id="item-1",
                machine="test-machine",
                kind="port",
                name="entry",
                evidence="proof",
                door="door-1",
            ),
            "not-a-connection-object",
        ]

    def slow_finder(c: LookContext) -> list[Connection]:
        time.sleep(2.0)
        return []

    def error_finder(c: LookContext) -> list[Connection]:
        raise ValueError("secret detail")

    def big_finder(c: LookContext) -> list[Connection]:
        rows: list[Connection] = []
        for i in range(10):
            rows.append(
                Connection(
                    id=f"big-{i}",
                    machine="test-machine",
                    kind="batch",
                    name=f"row-{i}",
                    evidence="generated",
                    door="none",
                )
            )
        return rows

    finders = {
        "normal": FinderSpec(name="normal", func=normal_finder),
        "slow": FinderSpec(name="slow", func=slow_finder, timeout=0.2),
        "error": FinderSpec(name="error", func=error_finder),
        "big": FinderSpec(name="big", func=big_finder, max_rows=3),
    }

    result = run_look(ctx, finders=finders, now="2026-11-14T12:00:00Z")

    assert result["machine"] == "test-machine"
    assert result["at"] == "2026-11-14T12:00:00Z"

    assert result["finders"]["normal"]["count"] == 1
    assert not result["finders"]["normal"]["stopped_early"]
    assert result["finders"]["normal"]["error"] == ""

    assert result["finders"]["slow"]["count"] == 0
    assert result["finders"]["slow"]["stopped_early"]
    assert result["finders"]["slow"]["error"] == "ran out of time"

    assert result["finders"]["error"]["count"] == 0
    assert not result["finders"]["error"]["stopped_early"]
    assert result["finders"]["error"]["error"] == "stopped with an error"
    dumped = json.dumps(result)
    assert "secret detail" not in dumped

    assert result["finders"]["big"]["count"] == 3
    assert result["finders"]["big"]["stopped_early"]
    assert result["finders"]["big"]["error"] == ""

    total_connections = result["connections"]
    assert len(total_connections) == 4


def test_counts() -> None:
    look = {
        "connections": [
            {"kind": "port", "state": "Found"},
            {"kind": "port", "state": "On"},
            {"kind": "tool", "state": "Ready"},
            {"kind": "tool", "state": "On"},
            {"kind": "tool", "state": "Not found this time"},
        ]
    }
    tally = counts(look)
    assert tally == {
        "port": {"found": 2, "on": 1},
        "tool": {"found": 2, "on": 1},
    }


def test_load_finders_idempotent() -> None:
    load_finders()
    load_finders()
