"""Tests for agent tool servers finder."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

import pytest

from tau.connect.ctx import LookContext
import tau.connect.finders.agent_connectors as agent_connectors_module
from tau.connect.finders.agent_connectors import find_agent_tool_servers
import tau.connect.recipes
from tau.connect.record import Connection
import tau.connect.registry
from tau.connect.registry import load_finders

TEST_RECIPES = [
    {
        "label": "Prog A",
        "format": "json",
        "keys": ["mcpServers"],
        "paths": {
            "Windows": ["{APPDATA}/ProgA/config.json"],
            "Linux": ["{HOME}/.config/ProgA/config.json"],
            "Darwin": ["{HOME}/Library/Application Support/ProgA/config.json"],
        },
    },
    {
        "label": "Prog B",
        "format": "json",
        "keys": ["mcpServers"],
        "paths": {
            "Windows": ["{APPDATA}/ProgB/config.json"],
            "Linux": ["{HOME}/.config/ProgB/config.json"],
            "Darwin": ["{HOME}/Library/Application Support/ProgB/config.json"],
        },
    },
    {
        "label": "Prog C",
        "format": "toml",
        "keys": ["mcp_servers"],
        "paths": {
            "Windows": ["{HOME}/.progc/config.toml"],
            "Linux": ["{HOME}/.progc/config.toml"],
            "Darwin": ["{HOME}/.progc/config.toml"],
        },
    },
]


def _norm(path: Any) -> str:
    cleaned = str(path).replace("\\", "/")
    return os.path.normcase(os.path.normpath(cleaned))


def _make_fake_read_text(
    files: dict[str, str],
    recorded_reads: list[str] | None = None,
) -> Callable[..., str]:
    normalized_files = {_norm(k): v for k, v in files.items()}

    def read_text(path: Any, limit: int = 2000000) -> str:
        p_str = str(path)
        if recorded_reads is not None:
            recorded_reads.append(p_str)
        content = normalized_files.get(_norm(p_str), "")
        return content[:limit]

    return read_text


def _make_fake_run(
    outputs: dict[tuple[str, ...], str],
    recorded_runs: list[tuple[list[str], float]] | None = None,
) -> Callable[..., str]:
    def run(cmd: list[str], timeout: float = 10.0) -> str:
        if recorded_runs is not None:
            recorded_runs.append((list(cmd), timeout))
        return outputs.get(tuple(cmd), "")

    return run


def test_secrets_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_connectors_module,
        "load",
        lambda name: TEST_RECIPES if name == "agent_settings" else [],
    )
    prog_a_path = "C:/AppData/ProgA/config.json"
    prog_a_content = json.dumps({
        "mcpServers": {
            "notes": {
                "command": r"C:\tools\bin\notes-server.exe",
                "args": ["--token", "SECRET-ARG-123"],
                "env": {"API_KEY": "SECRET-ENV-456"},
            },
            "cloud": {
                "url": "https://user:SECRET-PW-789@tools.example.org/path?key=SECRET-Q-000",
                "headers": {"Authorization": "Bearer SECRET-H-111"},
            },
        }
    })
    fake_read = _make_fake_read_text({prog_a_path: prog_a_content})
    ctx = LookContext(
        machine="test-win",
        read_text=fake_read,
        environ={"APPDATA": "C:/AppData", "USERPROFILE": "C:/Users/test"},
        system="Windows",
    )
    results = find_agent_tool_servers(ctx)
    assert len(results) == 2

    forbidden = ["SECRET", "--token", "API_KEY", "Authorization", "/path", "key="]
    for conn in results:
        dump = json.dumps(conn.to_dict())
        for token in forbidden:
            assert token not in dump

    notes_conn = next(c for c in results if c.name == "notes")
    assert notes_conn.detail["program"] == "notes-server.exe"
    assert notes_conn.detail["transport"] == "command"

    cloud_conn = next(c for c in results if c.name == "cloud")
    assert cloud_conn.detail["transport"] == "address"
    assert cloud_conn.detail["host"] == ""


def test_address_host_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_connectors_module,
        "load",
        lambda name: TEST_RECIPES if name == "agent_settings" else [],
    )
    prog_a_path = "C:/AppData/ProgA/config.json"
    prog_a_content = json.dumps({
        "mcpServers": {
            "myserver": {
                "url": "https://tools.example.org/mcp?x=1",
            }
        }
    })
    fake_read = _make_fake_read_text({prog_a_path: prog_a_content})
    ctx = LookContext(
        machine="test-win",
        read_text=fake_read,
        environ={"APPDATA": "C:/AppData", "USERPROFILE": "C:/Users/test"},
        system="Windows",
    )
    results = find_agent_tool_servers(ctx)
    assert len(results) == 1
    conn = results[0]
    assert conn.name == "myserver"
    assert conn.detail["host"] == "https://tools.example.org"
    assert conn.detail["transport"] == "address"


def test_deduplication_across_programs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_connectors_module,
        "load",
        lambda name: TEST_RECIPES if name == "agent_settings" else [],
    )
    prog_a_path = "C:/AppData/ProgA/config.json"
    prog_b_path = "C:/AppData/ProgB/config.json"
    content = json.dumps({
        "mcpServers": {
            "notes": {
                "command": r"C:\tools\bin\notes-server.exe",
            }
        }
    })
    fake_read = _make_fake_read_text({prog_a_path: content, prog_b_path: content})
    ctx = LookContext(
        machine="test-win",
        read_text=fake_read,
        environ={"APPDATA": "C:/AppData", "USERPROFILE": "C:/Users/test"},
        system="Windows",
    )
    results = find_agent_tool_servers(ctx)
    assert len(results) == 1
    conn = results[0]
    assert conn.name == "notes"
    assert conn.detail["listed_in"] == ["Prog A", "Prog B"]
    assert "1 other program" in conn.evidence


def test_toml_parsing_and_subtables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_connectors_module,
        "load",
        lambda name: TEST_RECIPES if name == "agent_settings" else [],
    )
    toml_text = (
        "[mcp_servers.files]\n"
        'command = "filetool"\n'
        'args = ["--key", "SECRET-T-222"]\n\n'
        "[mcp_servers.files.env]\n"
        'TOKEN = "SECRET-T-333"\n\n'
        "[mcp_servers.web]\n"
        'url = "https://web.example.org/x"\n'
    )
    prog_c_path = "C:/Users/test/.progc/config.toml"
    fake_read = _make_fake_read_text({prog_c_path: toml_text})
    ctx = LookContext(
        machine="test-win",
        read_text=fake_read,
        environ={"APPDATA": "C:/AppData", "USERPROFILE": "C:/Users/test"},
        system="Windows",
    )
    results = find_agent_tool_servers(ctx)
    names = [c.name for c in results]
    assert "files" in names
    assert "web" in names
    assert "files.env" not in names

    files_conn = next(c for c in results if c.name == "files")
    assert files_conn.detail["program"] == "filetool"
    assert files_conn.detail["transport"] == "command"

    web_conn = next(c for c in results if c.name == "web")
    assert web_conn.detail["host"] == "https://web.example.org"
    assert web_conn.detail["transport"] == "address"

    for conn in results:
        dump = json.dumps(conn.to_dict())
        assert "SECRET" not in dump


def test_invalid_json_list_and_missing_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_connectors_module,
        "load",
        lambda name: TEST_RECIPES if name == "agent_settings" else [],
    )
    prog_a_path = "C:/AppData/ProgA/config.json"

    # Invalid JSON
    fake_read1 = _make_fake_read_text({prog_a_path: "{not valid json"})
    ctx1 = LookContext(
        machine="test-win",
        read_text=fake_read1,
        environ={"APPDATA": "C:/AppData", "USERPROFILE": "C:/Users/test"},
        system="Windows",
    )
    assert find_agent_tool_servers(ctx1) == []

    # JSON is a list
    fake_read2 = _make_fake_read_text({prog_a_path: "[1, 2, 3]"})
    ctx2 = LookContext(
        machine="test-win",
        read_text=fake_read2,
        environ={"APPDATA": "C:/AppData", "USERPROFILE": "C:/Users/test"},
        system="Windows",
    )
    assert find_agent_tool_servers(ctx2) == []

    # Missing file
    fake_read3 = _make_fake_read_text({})
    ctx3 = LookContext(
        machine="test-win",
        read_text=fake_read3,
        environ={"APPDATA": "C:/AppData", "USERPROFILE": "C:/Users/test"},
        system="Windows",
    )
    assert find_agent_tool_servers(ctx3) == []


def test_empty_placeholder_template_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_connectors_module,
        "load",
        lambda name: TEST_RECIPES if name == "agent_settings" else [],
    )
    recorded_reads: list[str] = []
    fake_read = _make_fake_read_text({}, recorded_reads=recorded_reads)
    ctx = LookContext(
        machine="test-win",
        read_text=fake_read,
        environ={"APPDATA": "", "USERPROFILE": "C:/Users/test"},
        system="Windows",
    )
    find_agent_tool_servers(ctx)
    for p in recorded_reads:
        assert "ProgA" not in p
        assert "ProgB" not in p


def test_windows_registry_odr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_connectors_module, "load", lambda name: [])

    # Windows with valid JSON output
    json_out = '[{"name": "Files"}, {"id": "settings"}, "plain"]'
    recorded_runs: list[tuple[list[str], float]] = []
    fake_run = _make_fake_run({("odr", "mcp", "list"): json_out}, recorded_runs=recorded_runs)
    ctx_win = LookContext(
        machine="test-win",
        run=fake_run,
        environ={"USERPROFILE": "C:/Users/test"},
        system="Windows",
    )
    results = find_agent_tool_servers(ctx_win)
    win_conns = [c for c in results if c.kind == "Tool server registered with Windows"]
    names = [c.name for c in win_conns]
    assert sorted(names) == ["Files", "plain", "settings"]
    assert len(recorded_runs) == 1
    assert recorded_runs[0][0] == ["odr", "mcp", "list"]

    # Windows with non-JSON output
    fake_run_invalid = _make_fake_run({("odr", "mcp", "list"): "not json at all"})
    ctx_win_invalid = LookContext(
        machine="test-win",
        run=fake_run_invalid,
        environ={"USERPROFILE": "C:/Users/test"},
        system="Windows",
    )
    results_invalid = find_agent_tool_servers(ctx_win_invalid)
    assert [c for c in results_invalid if c.kind == "Tool server registered with Windows"] == []

    # Linux: run callable is never called
    recorded_linux_runs: list[tuple[list[str], float]] = []
    fake_run_linux = _make_fake_run(
        {("odr", "mcp", "list"): json_out},
        recorded_runs=recorded_linux_runs,
    )
    ctx_linux = LookContext(
        machine="test-linux",
        run=fake_run_linux,
        environ={"HOME": "/home/test"},
        system="Linux",
    )
    find_agent_tool_servers(ctx_linux)
    assert len(recorded_linux_runs) == 0


def test_connection_invariants_and_stable_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_connectors_module,
        "load",
        lambda name: TEST_RECIPES if name == "agent_settings" else [],
    )
    prog_a_content = json.dumps({"mcpServers": {"server1": {"command": "tool1"}}})
    odr_out = '[{"name": "RegisteredTool"}]'
    fake_read = _make_fake_read_text({"C:/AppData/ProgA/config.json": prog_a_content})
    fake_run = _make_fake_run({("odr", "mcp", "list"): odr_out})

    ctx = LookContext(
        machine="test-win",
        read_text=fake_read,
        run=fake_run,
        environ={"APPDATA": "C:/AppData", "USERPROFILE": "C:/Users/test"},
        system="Windows",
    )
    run1 = find_agent_tool_servers(ctx)
    run2 = find_agent_tool_servers(ctx)

    assert len(run1) == 2
    for conn in run1:
        assert conn.state == "Found"
        assert conn.evidence != ""
        assert conn.allows != ""

    assert [c.id for c in run1] == [c.id for c in run2]


def test_shipped_recipe_and_loader() -> None:
    entries = tau.connect.recipes.load("agent_settings")
    assert isinstance(entries, list)
    assert len(entries) >= 8

    for entry in entries:
        assert isinstance(entry, dict)
        label = entry.get("label")
        assert isinstance(label, str) and label.strip()

        fmt = entry.get("format")
        assert fmt in ("json", "toml")

        keys = entry.get("keys")
        assert isinstance(keys, list) and len(keys) > 0

        paths = entry.get("paths")
        assert isinstance(paths, dict)
        for _, template_list in paths.items():
            assert isinstance(template_list, list)
            for tmpl in template_list:
                assert "\\" not in tmpl
                assert not re.match(r"^[A-Za-z]:", tmpl)

    with pytest.raises(ValueError):
        tau.connect.recipes.load("Bad Name")

    with pytest.raises(FileNotFoundError):
        tau.connect.recipes.load("no_such_file")


def test_load_finders_registers_agent_tool_servers() -> None:
    load_finders()
    assert "agent_tool_servers" in tau.connect.registry.FINDERS
    spec = tau.connect.registry.FINDERS["agent_tool_servers"]
    assert spec.timeout == 20.0
    assert spec.max_rows == 500
