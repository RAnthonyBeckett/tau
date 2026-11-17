"""Tests for assistant workers finder."""

from __future__ import annotations

import os
from typing import Any

import pytest

from tau.connect import LookContext, make_id
from tau.connect.finders.cli_tools import find_cli_tools
from tau.connect.finders.workers import find_assistant_workers
import tau.connect.recipes
import tau.connect.registry
from tau.connect.registry import load_finders
import tau.detect


def _norm(path: Any) -> str:
    cleaned = str(path).replace("\\", "/")
    return os.path.normcase(os.path.normpath(cleaned))


class FakeMachine:
    """Fake machine environment providing injected methods for LookContext."""

    def __init__(
        self,
        files: dict[str, list[str]] | None = None,
        texts: dict[str, str] | None = None,
        registry: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.files = {_norm(k): list(v) for k, v in (files or {}).items()}
        self.texts = {_norm(k): str(v) for k, v in (texts or {}).items()}
        self.registry = {
            (hive.upper(), key.replace("/", "\\").lower()): val
            for (hive, key), val in (registry or {}).items()
        }
        self.listed_paths: list[str] = []

    def list_dir(self, path: Any) -> list[str]:
        p_str = str(path)
        self.listed_paths.append(_norm(p_str))
        return list(self.files.get(_norm(p_str), []))

    def read_text(self, path: Any, limit: int = 200000) -> str:
        content = self.texts.get(_norm(str(path)), "")
        return content[:limit]

    def registry_read(self, hive: str, key: str) -> dict[str, Any]:
        normalized_key = key.replace("/", "\\").lower()
        return self.registry.get(
            (hive.upper(), normalized_key),
            {"subkeys": [], "values": {}},
        )


SAMPLE_RECIPE = [
    {"command": "alphabot", "label": "Alpha Bot"},
    {"command": "betabot"},
    {"command": "gammabot", "label": "Gamma"},
    "junk",
    {"label": "no command"},
]


def test_workers_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tau.connect.finders.workers.load", lambda name: SAMPLE_RECIPE)
    base = os.path.abspath(f"{os.sep}tau_test_workers_win")
    tools = os.path.join(base, "Tools")
    sys_root = os.path.join(base, "SystemRoot")
    os_folder = os.path.join(sys_root, "System32")

    fake_files = {
        tools: ["alphabot.cmd", "BetaBot.exe"],
        os_folder: ["gammabot.exe"],
    }
    fake = FakeMachine(files=fake_files)
    path_str = os.pathsep.join([tools, os_folder])
    ctx = LookContext(
        machine="test-win",
        list_dir=fake.list_dir,
        environ={
            "PATH": path_str,
            "PATHEXT": ".EXE;.CMD",
            "SystemRoot": sys_root,
        },
        system="Windows",
    )

    results = find_assistant_workers(ctx)
    names = [c.name for c in results]
    assert names == ["Alpha Bot", "betabot", "Gamma"]

    by_name = {c.name: c for c in results}
    alpha = by_name["Alpha Bot"]
    assert alpha.detail["command"] == "alphabot"
    assert alpha.detail["folder"] == tools
    assert alpha.detail["file"] == "alphabot.cmd"

    beta = by_name["betabot"]
    assert beta.detail["command"] == "betabot"
    assert beta.detail["folder"] == tools
    assert beta.detail["file"] == "BetaBot.exe"

    gamma = by_name["Gamma"]
    assert gamma.detail["command"] == "gammabot"
    assert gamma.detail["folder"] == os_folder
    assert gamma.detail["file"] == "gammabot.exe"


def test_workers_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tau.connect.finders.workers.load", lambda name: SAMPLE_RECIPE)
    fake_files = {
        "/usr/bin": ["alphabot"],
        "/extra": [],
    }
    fake = FakeMachine(files=fake_files)
    ctx = LookContext(
        machine="test-linux",
        list_dir=fake.list_dir,
        environ={"PATH": "/usr/bin:/extra"},
        system="Linux",
    )
    results = find_assistant_workers(ctx)
    assert len(results) == 1
    alpha = results[0]
    assert alpha.name == "Alpha Bot"
    assert alpha.detail["command"] == "alphabot"
    assert alpha.detail["folder"] == "/usr/bin"
    assert alpha.detail["file"] == "alphabot"


def test_workers_not_on_path_and_empty_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tau.connect.finders.workers.load", lambda name: SAMPLE_RECIPE)
    fake = FakeMachine(files={"/tools": ["other.exe"]})
    ctx_not_found = LookContext(
        machine="test-box",
        list_dir=fake.list_dir,
        environ={"PATH": "/tools", "PATHEXT": ".exe"},
        system="Windows",
    )
    assert find_assistant_workers(ctx_not_found) == []

    ctx_empty = LookContext(machine="test-box", environ={"PATH": ""}, system="Windows")
    assert find_assistant_workers(ctx_empty) == []


def test_workers_and_cli_tools_caching(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tau.connect.finders.workers.load", lambda name: SAMPLE_RECIPE)
    base = os.path.abspath(f"{os.sep}tau_test_cache")
    tools = os.path.join(base, "Tools")
    sys_root = os.path.join(base, "SystemRoot")
    os_folder = os.path.join(sys_root, "System32")

    fake_files = {
        tools: ["alphabot.cmd", "other.exe"],
        os_folder: ["gammabot.exe"],
    }
    fake = FakeMachine(files=fake_files)
    path_str = os.pathsep.join([tools, os_folder])
    ctx = LookContext(
        machine="test-cache",
        list_dir=fake.list_dir,
        environ={
            "PATH": path_str,
            "PATHEXT": ".EXE;.CMD",
            "SystemRoot": sys_root,
        },
        system="Windows",
    )

    find_cli_tools(ctx)
    find_assistant_workers(ctx)

    for folder in (tools, os_folder):
        count = fake.listed_paths.count(_norm(folder))
        assert count <= 2

    listed_before = len(fake.listed_paths)
    find_assistant_workers(ctx)
    assert len(fake.listed_paths) == listed_before


def test_workers_load_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raising_load(name: str) -> Any:
        raise RuntimeError("Load failure")

    monkeypatch.setattr("tau.connect.finders.workers.load", _raising_load)
    fake = FakeMachine(files={"/tools": ["alphabot.exe"]})
    ctx = LookContext(
        machine="test-box",
        list_dir=fake.list_dir,
        environ={"PATH": "/tools", "PATHEXT": ".exe"},
        system="Windows",
    )
    assert find_assistant_workers(ctx) == []


def test_workers_connection_fields_and_stable_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tau.connect.finders.workers.load", lambda name: SAMPLE_RECIPE)
    fake = FakeMachine(files={"/tools": ["alphabot.exe"]})
    ctx = LookContext(
        machine="test-box",
        list_dir=fake.list_dir,
        environ={"PATH": "/tools", "PATHEXT": ".exe"},
        system="Windows",
    )
    results1 = find_assistant_workers(ctx)
    assert len(results1) == 1
    conn = results1[0]
    assert conn.state == "Found"
    assert conn.door == "command line"
    assert conn.kind == "Assistant that can take on work"
    assert conn.evidence != ""
    assert conn.allows != ""
    expected_id = make_id("test-box", "", "Assistant that can take on work", "alphabot")
    assert conn.id == expected_id

    results2 = find_assistant_workers(ctx)
    assert results2[0].id == conn.id


def test_shipped_workers_recipe() -> None:
    data = tau.connect.recipes.load("workers")
    assert isinstance(data, list)
    assert len(data) == 6
    expected_commands = ["claude", "codex", "rig", "gemini", "aider", "opencode"]
    commands = [e.get("command") for e in data if isinstance(e, dict)]
    assert commands == expected_commands
    for entry in data:
        assert isinstance(entry, dict)
        label = entry.get("label")
        assert isinstance(label, str) and bool(label.strip())


def test_detect_agent_clis() -> None:
    expected = ("claude", "codex", "rig", "gemini", "aider", "opencode")
    assert tau.detect.AGENT_CLIS == expected


def test_registry_has_assistant_workers() -> None:
    load_finders()
    assert "assistant_workers" in tau.connect.registry.FINDERS
    spec = tau.connect.registry.FINDERS["assistant_workers"]
    assert spec.timeout == 20.0
    assert spec.max_rows == 100
