"""Tests for the MCP tool server driver and start spec discovery."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

import pytest

from tau.connect import Connection, LookStore, places
from tau.connect.ctx import LookContext
from tau.connect.drivers import toolserver
from tau.connect.drivers.toolserver import (
    _args_summary,
    _tool_rows,
    call_tool,
    find_start_spec,
    list_tools,
)
import tau.connect.recipes


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
        organ: str = "connections",
    ) -> Dict[str, Any]:
        if self.explode:
            raise RuntimeError("RECEIPT_ERROR")
        call = {
            "what": what,
            "who": who,
            "machine": machine,
            "organ": organ,
        }
        self.calls.append(call)
        return call


class FakeClient:
    """Mock openjarvis MCPClient for driver testing."""

    def __init__(
        self,
        tools: Optional[List[Any]] = None,
        call_result: Optional[Dict[str, Any]] = None,
        raise_call: Optional[Exception] = None,
    ) -> None:
        self.tools = tools or []
        self.call_result = call_result or {"content": [{"type": "text", "text": "result-text"}], "isError": False}
        self.raise_call = raise_call
        self.calls: List[tuple[str, Dict[str, Any]]] = []
        self.closed = False

    def list_tools(self) -> List[Any]:
        return self.tools

    def call_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append((name, args))
        if self.raise_call is not None:
            raise self.raise_call
        return self.call_result

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    """Mock client opener recording given start specifications."""

    def __init__(
        self,
        client: Optional[FakeClient] = None,
        raise_exc: Optional[Exception] = None,
    ) -> None:
        self.client = client or FakeClient()
        self.raise_exc = raise_exc
        self.opened_specs: List[Dict[str, Any]] = []

    def __call__(self, spec: Dict[str, Any], timeout: float = 60.0) -> FakeClient:
        self.opened_specs.append(dict(spec))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.client


def build_test_store(root: Path) -> tuple[LookStore, Dict[str, Any]]:
    """Build a store holding one saved tool server connection."""
    store = LookStore(root)
    conn = Connection(
        id="conn-notes",
        machine="den-pc",
        kind="Tool server set up for an assistant",
        name="notes",
        evidence="Found in Prog A",
        door="tool server",
        state="Ready",
        setting="off",
        detail={
            "transport": "command",
            "program": "notes-server",
            "host": "",
            "listed_in": ["Prog A"],
        },
    ).to_dict()
    look = {
        "machine": "den-pc",
        "at": "2026-11-15T12:00:00Z",
        "connections": [conn],
        "finders": {},
    }
    store.save(look)
    return store, conn


def test_find_start_spec_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_recipe = [
        {
            "label": "Prog A",
            "format": "json",
            "keys": ["mcpServers"],
            "paths": {
                "Windows": ["{APPDATA}/ProgA/config.json"],
                "Linux": ["{HOME}/.config/proga.json"],
                "Darwin": ["{HOME}/Library/ProgA/config.json"],
            },
        }
    ]
    monkeypatch.setattr(toolserver, "load", lambda name: fake_recipe if name == "agent_settings" else [])

    settings_json = json.dumps({
        "mcpServers": {
            "notes": {
                "command": "notes-server",
                "args": ["--token", "SECRET-ARG-1"],
                "env": {"API_KEY": "SECRET-ENV-2", "N": 5},
            }
        }
    })

    ctx = LookContext(
        machine="den-pc",
        read_text=lambda path, **kw: settings_json,
        environ={"APPDATA": "appdata_path", "HOME": "home_path"},
        system="Windows",
    )
    conn = {
        "kind": "Tool server set up for an assistant",
        "name": "notes",
        "detail": {"listed_in": ["Prog A"]},
    }

    spec = find_start_spec(ctx, conn)
    assert spec == {
        "transport": "command",
        "command": ["notes-server", "--token", "SECRET-ARG-1"],
        "env": {"API_KEY": "SECRET-ENV-2"},
    }


def test_find_start_spec_address(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_recipe = [
        {
            "label": "Prog A",
            "format": "json",
            "keys": ["mcpServers"],
            "paths": {"Windows": ["{APPDATA}/ProgA/config.json"]},
        }
    ]
    monkeypatch.setattr(toolserver, "load", lambda name: fake_recipe if name == "agent_settings" else [])

    with_bearer = json.dumps({
        "mcpServers": {
            "remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer SECRET-TOK-3"},
            }
        }
    })

    ctx_auth = LookContext(
        machine="den-pc",
        read_text=lambda path, **kw: with_bearer,
        environ={"APPDATA": "appdata_path"},
        system="Windows",
    )
    conn = {
        "kind": "Tool server set up for an assistant",
        "name": "remote",
        "detail": {"listed_in": ["Prog A"]},
    }

    spec_auth = find_start_spec(ctx_auth, conn)
    assert spec_auth == {
        "transport": "address",
        "url": "https://example.com/mcp",
        "token": "SECRET-TOK-3",
    }

    without_bearer = json.dumps({
        "mcpServers": {
            "remote": {
                "url": "https://example.com/mcp",
            }
        }
    })
    ctx_no_auth = LookContext(
        machine="den-pc",
        read_text=lambda path, **kw: without_bearer,
        environ={"APPDATA": "appdata_path"},
        system="Windows",
    )
    spec_no_auth = find_start_spec(ctx_no_auth, conn)
    assert spec_no_auth == {
        "transport": "address",
        "url": "https://example.com/mcp",
        "token": None,
    }


def test_find_start_spec_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_recipe = [
        {
            "label": "Codex",
            "format": "toml",
            "keys": ["mcp_servers"],
            "paths": {"Windows": ["{APPDATA}/config.toml"]},
        }
    ]
    monkeypatch.setattr(toolserver, "load", lambda name: fake_recipe if name == "agent_settings" else [])

    toml_content = (
        "[mcp_servers.files]\n"
        'command = "filetool"\n'
        'args = ["--key", "SECRET-T-4"]\n\n'
        "[mcp_servers.files.env]\n"
        'TOKEN = "SECRET-T-5"\n'
    )
    ctx = LookContext(
        machine="den-pc",
        read_text=lambda path, **kw: toml_content,
        environ={"APPDATA": "appdata_path"},
        system="Windows",
    )
    conn = {
        "kind": "Tool server set up for an assistant",
        "name": "files",
        "detail": {"listed_in": ["Codex"]},
    }

    spec = find_start_spec(ctx, conn)
    assert spec == {
        "transport": "command",
        "command": ["filetool", "--key", "SECRET-T-4"],
        "env": {"TOKEN": "SECRET-T-5"},
    }


def test_find_start_spec_listed_in_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_recipes = [
        {
            "label": "Prog A",
            "format": "json",
            "keys": ["mcpServers"],
            "paths": {"Windows": ["{APPDATA}/ProgA/config.json"]},
        },
        {
            "label": "Prog B",
            "format": "json",
            "keys": ["mcpServers"],
            "paths": {"Windows": ["{APPDATA}/ProgB/config.json"]},
        },
    ]
    monkeypatch.setattr(toolserver, "load", lambda name: fake_recipes if name == "agent_settings" else [])

    requested_paths: List[str] = []

    def fake_read(path: Any, **kw: Any) -> str:
        requested_paths.append(str(path))
        return json.dumps({"mcpServers": {"notes": {"command": "notes-server"}}})

    ctx = LookContext(
        machine="den-pc",
        read_text=fake_read,
        environ={"APPDATA": "appdata_path"},
        system="Windows",
    )
    conn = {
        "kind": "Tool server set up for an assistant",
        "name": "notes",
        "detail": {"listed_in": ["Prog A"]},
    }

    spec = find_start_spec(ctx, conn)
    assert spec is not None
    assert any("ProgA" in p for p in requested_paths)
    assert not any("ProgB" in p for p in requested_paths)

    # Unknown server name
    conn_unknown = {
        "kind": "Tool server set up for an assistant",
        "name": "unknown_server",
        "detail": {"listed_in": ["Prog A"]},
    }
    assert find_start_spec(ctx, conn_unknown) is None

    # Unknown connection kind
    conn_bad_kind = {
        "kind": "Unknown kind",
        "name": "notes",
        "detail": {"listed_in": ["Prog A"]},
    }
    assert find_start_spec(ctx, conn_bad_kind) is None

    # Exception inside read_text
    def exploding_read(path: Any, **kw: Any) -> str:
        raise OSError("Read failed")

    ctx_err = LookContext(
        machine="den-pc",
        read_text=exploding_read,
        environ={"APPDATA": "appdata_path"},
        system="Windows",
    )
    assert find_start_spec(ctx_err, conn) is None


def test_find_start_spec_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_workers = [{"command": "helperbot", "label": "Helper", "tool_server": ["serve", "--stdio"]}]
    monkeypatch.setattr(toolserver, "load", lambda name: fake_workers if name == "workers" else [])

    ctx = LookContext(machine="den-pc", system="Windows")

    # Worker without place
    conn_no_place = {
        "kind": "Assistant that can take on work",
        "name": "Helper",
        "place": "",
        "detail": {"command": "helperbot"},
    }
    spec_no_place = find_start_spec(ctx, conn_no_place)
    assert spec_no_place == {
        "transport": "command",
        "command": ["helperbot", "serve", "--stdio"],
        "env": {},
    }

    # Worker with place
    conn_place = {
        "kind": "Assistant that can take on work",
        "name": "Helper",
        "place": "Work-Env",
        "detail": {"command": "helperbot"},
    }
    spec_place = find_start_spec(ctx, conn_place)
    assert spec_place == {
        "transport": "command",
        "command": [places.ENTER, "-d", "Work-Env", "-e", "bash", "-ic", "helperbot serve --stdio"],
        "env": {},
    }

    # Worker without tool_server field
    fake_no_tool = [{"command": "helperbot", "label": "Helper"}]
    monkeypatch.setattr(toolserver, "load", lambda name: fake_no_tool if name == "workers" else [])
    assert find_start_spec(ctx, conn_no_place) is None


def test_shipped_workers_data() -> None:
    workers = tau.connect.recipes.load("workers")
    rig_entries = [w for w in workers if isinstance(w, dict) and w.get("command") == "rig"]
    assert len(rig_entries) == 1
    assert rig_entries[0].get("tool_server") == ["mcp", "serve"]

    for w in workers:
        if isinstance(w, dict) and w.get("command") != "rig":
            assert "tool_server" not in w or w.get("tool_server") is None


def test_list_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, conn = build_test_store(tmp_path)
    ctx = LookContext(
        machine="den-pc",
        read_text=lambda p, **kw: json.dumps({"mcpServers": {"notes": {"command": "notes-server"}}}),
        environ={"APPDATA": "appdata_path"},
        system="Windows",
    )
    fake_recipe = [
        {
            "label": "Prog A",
            "format": "json",
            "keys": ["mcpServers"],
            "paths": {"Windows": ["{APPDATA}/config.json"]},
        }
    ]
    monkeypatch.setattr(toolserver, "load", lambda name: fake_recipe)

    # 1. Switched off -> refused, opener never called
    fake_client = FakeClient()
    fake_opener = FakeOpener(client=fake_client)
    res_off = list_tools(store, "den-pc", conn["id"], ctx, opener=fake_opener)
    assert res_off["ok"] is False
    assert res_off["say"] == "That connection is switched off. Switch it on first."
    assert len(fake_opener.opened_specs) == 0

    # 2. At "sense" -> ok, rows sorted by name, description cut to 300, params sorted, closed
    store.set_setting("den-pc", conn["id"], "sense")

    class ToolItem:
        def __init__(self, name: str, desc: str, props: Dict[str, Any]) -> None:
            self.name = name
            self.description = desc
            self.parameters = {"properties": props}

    tool_b = ToolItem("beta_tool", "B" * 400, {"z": {}, "a": {}})
    tool_a = ToolItem("alpha_tool", "short", {"item": {}})
    fake_client.tools = [tool_b, tool_a]

    res_sense = list_tools(store, "den-pc", conn["id"], ctx, opener=fake_opener)
    assert res_sense["ok"] is True
    tools = res_sense["tools"]
    assert len(tools) == 2
    assert tools[0]["name"] == "alpha_tool"
    assert tools[1]["name"] == "beta_tool"
    assert len(tools[1]["description"]) == 300
    assert tools[1]["params"] == ["a", "z"]
    assert tools[1]["read_only"] is False
    assert fake_client.closed is True

    # 3. Opener raises RuntimeError("SECRET-X")
    exploding_opener = FakeOpener(raise_exc=RuntimeError("SECRET-X"))
    res_err = list_tools(store, "den-pc", conn["id"], ctx, opener=exploding_opener)
    assert res_err["ok"] is False
    assert res_err["say"] == "It didn't answer. It may need to be started, or it may need a sign-in."
    assert "SECRET-X" not in res_err["say"]


def test_call_tool_at_sense_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, conn = build_test_store(tmp_path)
    ctx = LookContext(machine="den-pc")
    fake_opener = FakeOpener()

    store.set_setting("den-pc", conn["id"], "sense")
    res = call_tool(
        store,
        "den-pc",
        conn["id"],
        "read_note",
        {},
        ctx,
        ask=lambda q: True,
        opener=fake_opener,
    )
    assert res["ok"] is False
    assert res["say"] == "TAU may look at that connection but not use it. Raise its level first."
    assert len(fake_opener.opened_specs) == 0


def test_call_tool_at_suggest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, conn = build_test_store(tmp_path)
    fake_recipe = [
        {
            "label": "Prog A",
            "format": "json",
            "keys": ["mcpServers"],
            "paths": {"Windows": ["{APPDATA}/config.json"]},
        }
    ]
    monkeypatch.setattr(toolserver, "load", lambda name: fake_recipe)
    ctx = LookContext(
        machine="den-pc",
        read_text=lambda p, **kw: json.dumps({"mcpServers": {"notes": {"command": "notes-server"}}}),
        environ={"APPDATA": "appdata_path"},
        system="Windows",
    )
    store.set_setting("den-pc", conn["id"], "suggest")

    # ask returning False
    log = FakeLog()
    opener = FakeOpener()
    res_no = call_tool(
        store,
        "den-pc",
        conn["id"],
        "write_note",
        {"id": 1},
        ctx,
        ask=lambda q: False,
        actionlog=log,
        opener=opener,
    )
    assert res_no["ok"] is False
    assert res_no["say"] == "Okay, I didn't use it."
    assert len(opener.opened_specs) == 0
    assert len(log.calls) == 0

    # ask returning truthy string "yes"
    res_str_yes = call_tool(
        store,
        "den-pc",
        conn["id"],
        "write_note",
        {"id": 1},
        ctx,
        ask=lambda q: "yes",  # type: ignore[arg-type]
        actionlog=log,
        opener=opener,
    )
    assert res_str_yes["ok"] is False
    assert res_str_yes["say"] == "Okay, I didn't use it."
    assert len(opener.opened_specs) == 0
    assert len(log.calls) == 0

    # ask returning True
    client = FakeClient(call_result={"content": [{"type": "text", "text": "saved"}], "isError": False})
    opener_ok = FakeOpener(client=client)
    res_ok = call_tool(
        store,
        "den-pc",
        conn["id"],
        "write_note",
        {"id": 1},
        ctx,
        ask=lambda q: True,
        actionlog=log,
        who="you",
        opener=opener_ok,
    )
    assert res_ok["ok"] is True
    assert res_ok["text"] == "saved"
    assert res_ok["say"] == "Done. notes ran write_note."
    assert client.calls == [("write_note", {"id": 1})]
    assert len(log.calls) == 1
    assert log.calls[0]["who"] == "you"
    assert log.calls[0]["what"] == "Used write_note on notes"


def test_call_tool_at_act(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, conn = build_test_store(tmp_path)
    fake_recipe = [
        {
            "label": "Prog A",
            "format": "json",
            "keys": ["mcpServers"],
            "paths": {"Windows": ["{APPDATA}/config.json"]},
        }
    ]
    monkeypatch.setattr(toolserver, "load", lambda name: fake_recipe)
    ctx = LookContext(
        machine="den-pc",
        read_text=lambda p, **kw: json.dumps({"mcpServers": {"notes": {"command": "notes-server"}}}),
        environ={"APPDATA": "appdata_path"},
        system="Windows",
    )
    store.set_setting("den-pc", conn["id"], "act")

    asked = False

    def spy_ask(q: str) -> bool:
        nonlocal asked
        asked = True
        return True

    log = FakeLog()
    client = FakeClient()
    opener = FakeOpener(client=client)

    res = call_tool(
        store,
        "den-pc",
        conn["id"],
        "auto_tool",
        {},
        ctx,
        ask=spy_ask,
        actionlog=log,
        opener=opener,
    )
    assert not asked
    assert res["ok"] is True
    assert len(log.calls) == 1
    assert log.calls[0]["who"] == "TAU, at the level you set"
    assert log.calls[0]["what"] == "Used auto_tool on notes"


def test_call_tool_hidden_secret_arg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, conn = build_test_store(tmp_path)
    fake_recipe = [
        {
            "label": "Prog A",
            "format": "json",
            "keys": ["mcpServers"],
            "paths": {"Windows": ["{APPDATA}/config.json"]},
        }
    ]
    monkeypatch.setattr(toolserver, "load", lambda name: fake_recipe)
    ctx = LookContext(
        machine="den-pc",
        read_text=lambda p, **kw: json.dumps({"mcpServers": {"notes": {"command": "notes-server"}}}),
        environ={"APPDATA": "appdata_path"},
        system="Windows",
    )
    store.set_setting("den-pc", conn["id"], "suggest")

    questions: List[str] = []

    def spy_ask(q: str) -> bool:
        questions.append(q)
        return True

    log = FakeLog()
    opener = FakeOpener()

    call_tool(
        store,
        "den-pc",
        conn["id"],
        "secure_tool",
        {"api_key": "SECRET-A-6"},
        ctx,
        ask=spy_ask,
        actionlog=log,
        opener=opener,
    )

    assert len(questions) == 1
    assert "api_key: (hidden)" in questions[0]
    assert "SECRET-A-6" not in questions[0]
    for c in log.calls:
        assert "SECRET-A-6" not in c["what"]


def test_call_tool_is_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, conn = build_test_store(tmp_path)
    fake_recipe = [
        {
            "label": "Prog A",
            "format": "json",
            "keys": ["mcpServers"],
            "paths": {"Windows": ["{APPDATA}/config.json"]},
        }
    ]
    monkeypatch.setattr(toolserver, "load", lambda name: fake_recipe)
    ctx = LookContext(
        machine="den-pc",
        read_text=lambda p, **kw: json.dumps({"mcpServers": {"notes": {"command": "notes-server"}}}),
        environ={"APPDATA": "appdata_path"},
        system="Windows",
    )
    store.set_setting("den-pc", conn["id"], "act")

    client = FakeClient(call_result={"content": [{"type": "text", "text": "bad input"}], "isError": True})
    log = FakeLog()
    opener = FakeOpener(client=client)

    res = call_tool(
        store,
        "den-pc",
        conn["id"],
        "fail_tool",
        {},
        ctx,
        ask=lambda q: True,
        actionlog=log,
        opener=opener,
    )
    assert res["ok"] is False
    assert res["say"] == "The tool reported a problem."
    assert res["text"] == "bad input"
    assert len(log.calls) == 1
    assert "Used fail_tool on notes; it reported a problem" == log.calls[0]["what"]


def test_call_tool_client_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, conn = build_test_store(tmp_path)
    fake_recipe = [
        {
            "label": "Prog A",
            "format": "json",
            "keys": ["mcpServers"],
            "paths": {"Windows": ["{APPDATA}/config.json"]},
        }
    ]
    monkeypatch.setattr(toolserver, "load", lambda name: fake_recipe)
    ctx = LookContext(
        machine="den-pc",
        read_text=lambda p, **kw: json.dumps({"mcpServers": {"notes": {"command": "notes-server"}}}),
        environ={"APPDATA": "appdata_path"},
        system="Windows",
    )
    store.set_setting("den-pc", conn["id"], "act")

    client = FakeClient(raise_call=RuntimeError("Process terminated unexpectedly"))
    log = FakeLog()
    opener = FakeOpener(client=client)

    res = call_tool(
        store,
        "den-pc",
        conn["id"],
        "crash_tool",
        {},
        ctx,
        ask=lambda q: True,
        actionlog=log,
        opener=opener,
    )
    assert res["ok"] is False
    assert res["say"] == "It didn't answer, so I can't say whether it was done. Check it yourself."
    assert client.closed is True
    assert len(log.calls) == 1
    assert "Tried crash_tool on notes; it did not answer" == log.calls[0]["what"]


def test_secrets_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir = tmp_path / "store"
    store, conn = build_test_store(store_dir)
    store.set_setting("den-pc", conn["id"], "act")

    fake_recipe = [
        {
            "label": "Prog A",
            "format": "json",
            "keys": ["mcpServers"],
            "paths": {"Windows": ["{APPDATA}/ProgA/config.json"]},
        }
    ]
    monkeypatch.setattr(toolserver, "load", lambda name: fake_recipe if name == "agent_settings" else [])

    settings_json = json.dumps({
        "mcpServers": {
            "notes": {
                "command": "notes-server",
                "args": ["--token", "SECRET-ARG-1"],
                "env": {"API_KEY": "SECRET-ENV-2", "N": 5},
            }
        }
    })

    ctx = LookContext(
        machine="den-pc",
        read_text=lambda p, **kw: settings_json,
        environ={"APPDATA": "appdata_path"},
        system="Windows",
    )

    fake_client = FakeClient(
        tools=[{"name": "fetch_notes", "description": "Fetch notes", "inputSchema": {}}],
        call_result={"content": [{"type": "text", "text": "normal note text"}], "isError": False},
    )
    fake_opener = FakeOpener(client=fake_client)
    fake_log = FakeLog()

    res_list = list_tools(store, "den-pc", conn["id"], ctx, opener=fake_opener)
    res_call = call_tool(
        store,
        "den-pc",
        conn["id"],
        "fetch_notes",
        {},
        ctx,
        ask=lambda q: True,
        actionlog=fake_log,
        opener=fake_opener,
    )

    # 1. Assert secrets were passed through to the client opener in local memory
    assert len(fake_opener.opened_specs) == 2
    for spec in fake_opener.opened_specs:
        assert "SECRET-ARG-1" in spec["command"]
        assert spec["env"].get("API_KEY") == "SECRET-ENV-2"

    # 2. Assert neither secret appears in returned dicts
    json_list = json.dumps(res_list)
    json_call = json.dumps(res_call)
    assert "SECRET-ARG-1" not in json_list
    assert "SECRET-ENV-2" not in json_list
    assert "SECRET-ARG-1" not in json_call
    assert "SECRET-ENV-2" not in json_call

    # 3. Assert neither secret appears in receipt log calls
    for call in fake_log.calls:
        assert "SECRET-ARG-1" not in call["what"]
        assert "SECRET-ENV-2" not in call["what"]

    # 4. Assert neither secret appears in any file under tmp_path
    for p in tmp_path.rglob("*"):
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            assert "SECRET-ARG-1" not in text
            assert "SECRET-ENV-2" not in text


def test_tool_rows_annotations_and_params() -> None:
    class MockTool:
        def __init__(self, name: str, desc: str, props: Dict[str, Any], annotations: Optional[Dict[str, Any]]) -> None:
            self.name = name
            self.description = desc
            self.parameters = {"properties": props}
            if annotations is not None:
                self.annotations = annotations

    tool_ro = MockTool("read_tool", "reads", {"path": {}}, {"readOnlyHint": True})
    tool_rw = MockTool("write_tool", "writes", {"path": {}, "data": {}}, {"readOnlyHint": False})
    tool_no_ann = MockTool("other_tool", "other", {}, None)

    rows = _tool_rows([tool_rw, tool_ro, tool_no_ann])
    assert len(rows) == 3
    assert rows[0]["name"] == "other_tool"
    assert rows[0]["read_only"] is False

    assert rows[1]["name"] == "read_tool"
    assert rows[1]["read_only"] is True
    assert rows[1]["params"] == ["path"]

    assert rows[2]["name"] == "write_tool"
    assert rows[2]["read_only"] is False
    assert rows[2]["params"] == ["data", "path"]


def test_args_summary() -> None:
    assert _args_summary(None) == "nothing"
    assert _args_summary({}) == "nothing"

    args = {
        "user": "alice",
        "api_key": "secret123",
        "authToken": "bearer456",
        "normal": "value",
    }
    summary = _args_summary(args)
    assert "user: alice" in summary
    assert "api_key: (hidden)" in summary
    assert "authToken: (hidden)" in summary
    assert "normal: value" in summary

    # Value truncated to 80 chars
    long_val = "x" * 120
    summary_long = _args_summary({"data": long_val})
    assert len(summary_long.split(": ")[1]) == 80

    # At most 12 pairs
    many_args = {f"k{i}": f"v{i}" for i in range(20)}
    summary_many = _args_summary(many_args)
    assert len(summary_many.split("; ")) == 12


def test_call_tool_validation(tmp_path: Path) -> None:
    store, conn = build_test_store(tmp_path)
    store.set_setting("den-pc", conn["id"], "act")
    ctx = LookContext(machine="den-pc")

    # Connection not found
    res_no_conn = call_tool(store, "den-pc", "missing-id", "t", {}, ctx, ask=lambda q: True)
    assert res_no_conn["ok"] is False
    assert "I can't find that connection any more" in res_no_conn["say"]

    # Invalid tool string
    res_bad_tool = call_tool(store, "den-pc", conn["id"], "", {}, ctx, ask=lambda q: True)
    assert res_bad_tool["ok"] is False
    assert res_bad_tool["say"] == "I need the name of one of its tools."

    # Invalid args type
    res_bad_args = call_tool(
        store,
        "den-pc",
        conn["id"],
        "tool",
        "not-a-mapping",  # type: ignore[arg-type]
        ctx,
        ask=lambda q: True,
    )
    assert res_bad_args["ok"] is False
    assert res_bad_args["say"] == "I need the name of one of its tools."


def test_end_to_end_real_child_process(tmp_path: Path) -> None:
    pytest.importorskip("openjarvis")

    script_code = (
        "import json\n"
        "import os\n"
        "import sys\n"
        "\n"
        "for line in sys.stdin:\n"
        "    line = line.strip()\n"
        "    if not line:\n"
        "        continue\n"
        "    try:\n"
        "        msg = json.loads(line)\n"
        "    except Exception:\n"
        "        continue\n"
        '    req_id = msg.get("id")\n'
        "    if req_id is None:\n"
        "        continue\n"
        '    method = msg.get("method")\n'
        '    if method == "initialize":\n'
        "        res = {\n"
        '            "protocolVersion": "2024-11-05",\n'
        '            "capabilities": {},\n'
        '            "serverInfo": {"name": "t", "version": "1"},\n'
        "        }\n"
        '    elif method == "tools/list":\n'
        "        res = {\n"
        '            "tools": [\n'
        "                {\n"
        '                    "name": "show_env",\n'
        '                    "description": "d",\n'
        '                    "inputSchema": {"type": "object", "properties": {"x": {}}},\n'
        "                }\n"
        "            ]\n"
        "        }\n"
        '    elif method == "tools/call":\n'
        '        val = os.environ.get("TAU_TEST_VALUE", "missing")\n'
        "        res = {\n"
        '            "content": [{"type": "text", "text": "value=" + val}],\n'
        '            "isError": False,\n'
        "        }\n"
        "    else:\n"
        "        res = {}\n"
        '    resp = {"jsonrpc": "2.0", "id": req_id, "result": res}\n'
        '    sys.stdout.write(json.dumps(resp) + "\\n")\n'
        "    sys.stdout.flush()\n"
    )

    script_path = tmp_path / "server_script.py"
    script_path.write_text(script_code, encoding="utf-8")

    spec = {
        "transport": "command",
        "command": [sys.executable, str(script_path)],
        "env": {"TAU_TEST_VALUE": "passed-through"},
    }

    client = toolserver.open_client(spec, timeout=20.0)
    try:
        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "show_env"

        res = client.call_tool("show_env", {})
        content = res.get("content", [])
        assert len(content) == 1
        assert content[0].get("text") == "value=passed-through"
    finally:
        client.close()
