"""Tests for connection inspection safety rules and boundaries."""

from __future__ import annotations

import http.server
from pathlib import Path
import re
import socket
import threading
from typing import Any

import pytest

from tau.connect import FinderSpec, LookContext, WalkRefused, run_look


def test_list_dir_depth_rule(tmp_path: Path) -> None:
    folder_a = tmp_path / "a"
    folder_b = folder_a / "b"
    folder_c = folder_b / "c"
    folder_d = tmp_path / "d"

    folder_a.mkdir()
    folder_b.mkdir()
    folder_c.mkdir()
    folder_d.mkdir()

    ctx = LookContext(machine="test-machine")
    ctx.list_dir(folder_a)
    ctx.list_dir(folder_b)

    with pytest.raises(WalkRefused):
        ctx.list_dir(folder_c)

    assert ctx.list_dir(folder_d) == []


def test_list_dir_count_rule(tmp_path: Path) -> None:
    ctx = LookContext(machine="test-machine", max_dirs=3)
    folders = [tmp_path / f"dir_{i}" for i in range(4)]
    for folder in folders:
        folder.mkdir()

    ctx.list_dir(folders[0])
    ctx.list_dir(folders[1])
    ctx.list_dir(folders[2])

    with pytest.raises(WalkRefused):
        ctx.list_dir(folders[3])


def test_injected_list_dir_obeys_rules(tmp_path: Path) -> None:
    recorded_calls: list[Any] = []

    def custom_lister(path: Any) -> list[str]:
        recorded_calls.append(path)
        return ["x"]

    ctx = LookContext(machine="test-machine", list_dir=custom_lister, max_dirs=3)

    folder_a = tmp_path / "a"
    folder_b = folder_a / "b"
    folder_c = folder_b / "c"
    folder_a.mkdir()
    folder_b.mkdir()
    folder_c.mkdir()

    res_a = ctx.list_dir(folder_a)
    assert res_a == ["x"]
    res_b = ctx.list_dir(folder_b)
    assert res_b == ["x"]

    with pytest.raises(WalkRefused):
        ctx.list_dir(folder_c)

    other_folder = tmp_path / "other"
    other_folder.mkdir()
    ctx.list_dir(other_folder)

    excess_folder = tmp_path / "excess"
    excess_folder.mkdir()
    with pytest.raises(WalkRefused):
        ctx.list_dir(excess_folder)


def test_finder_walking_is_stopped(tmp_path: Path) -> None:
    folder_a = tmp_path / "a"
    folder_b = folder_a / "b"
    folder_c = folder_b / "c"
    folder_a.mkdir()
    folder_b.mkdir()
    folder_c.mkdir()

    def recursive_finder(ctx: LookContext) -> list[Any]:
        ctx.list_dir(folder_a)
        ctx.list_dir(folder_b)
        ctx.list_dir(folder_c)
        return []

    spec = FinderSpec(name="walker", func=recursive_finder)
    ctx = LookContext(machine="test-machine")
    result = run_look(ctx, finders={"walker": spec})

    report = result["finders"]["walker"]
    assert report["count"] == 0
    assert report["stopped_early"] is True
    assert report["error"] == "tried to read too many folders"


def test_http_get_validation_and_request() -> None:
    ctx = LookContext(machine="test-machine")

    with pytest.raises(ValueError):
        ctx.http_get(0)

    with pytest.raises(ValueError):
        ctx.http_get(70000)

    with pytest.raises(ValueError):
        ctx.http_get(8080, "invalid_path_without_slash")

    class SimpleHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SimpleHandler)
    assigned_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        status, headers, body = ctx.http_get(assigned_port, "/info")
        assert status == 200
        assert "application/json" in headers.get("content-type", "")
        assert '{"status": "ok"}' in body
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2.0)

    temp_sock = socket.socket()
    temp_sock.bind(("127.0.0.1", 0))
    closed_port = temp_sock.getsockname()[1]
    temp_sock.close()

    res = ctx.http_get(closed_port, "/")
    assert res == (0, {}, "")


def test_run_command_rules() -> None:
    ctx = LookContext(machine="test-machine")
    with pytest.raises(TypeError):
        ctx.run("echo hello")  # type: ignore[arg-type]

    output = ctx.run(["this_command_does_not_exist_xyz123"])
    assert output == ""


def test_read_text_missing_file(tmp_path: Path) -> None:
    ctx = LookContext(machine="test-machine")
    missing = tmp_path / "does_not_exist.txt"
    assert ctx.read_text(missing) == ""


def test_registry_read_missing_key() -> None:
    ctx = LookContext(machine="test-machine")
    result = ctx.registry_read("HKLM", r"Software\NonExistentKeyXYZ")
    assert result == {"subkeys": [], "values": {}}


def test_finder_source_forbidden_substrings() -> None:
    finders_dir = Path(__file__).resolve().parents[1] / "src" / "tau" / "connect" / "finders"
    forbidden_tokens = [
        "os.walk",
        "rglob",
        ".glob(",
        "scandir",
        "os.listdir",
        "open(",
        "subprocess",
        "urllib",
        "winreg",
        "socket",
        "requests",
    ]
    if not finders_dir.exists():
        return

    for py_file in finders_dir.glob("*.py"):
        if py_file.name == "__init__.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in text, f"Forbidden token '{token}' in {py_file.name}"


def test_finder_source_forbidden_products() -> None:
    finders_dir = Path(__file__).resolve().parents[1] / "src" / "tau" / "connect" / "finders"
    forbidden_products = [
        "plex",
        "spotify",
        "blender",
        "steam",
        "vlc",
        "discord",
        "chrome",
        "firefox",
        "docker",
        "podman",
        "ollama",
        "winget",
        "scoop",
        "chocolatey",
        "homebrew",
        "brew",
        "snap",
        "flatpak",
        "apt",
        "dpkg",
        "pipx",
        "npm",
        "claude",
        "codex",
        "gemini",
        "openrig",
    ]
    if not finders_dir.exists():
        return

    for py_file in finders_dir.glob("*.py"):
        if py_file.name == "__init__.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        for product in forbidden_products:
            pattern = rf"\b{re.escape(product)}\b"
            assert not re.search(pattern, text, re.IGNORECASE), f"Forbidden product '{product}' in {py_file.name}"


def test_list_dir_start_depth_rule(tmp_path: Path) -> None:
    folder_gp = tmp_path / "gp"
    folder_p = folder_gp / "parent"
    folder_c = folder_p / "child"
    folder_gc = folder_c / "grandchild"
    folder_ggc = folder_gc / "greatgrandchild"

    folder_gp.mkdir()
    folder_p.mkdir()
    folder_c.mkdir()
    folder_gc.mkdir()
    folder_ggc.mkdir()

    ctx = LookContext(machine="test-machine")
    ctx.list_dir(folder_gp)
    ctx.list_dir(folder_p)

    # child_of_child with start=True is allowed after parent and grandparent
    assert ctx.list_dir(folder_c, start=True) == ["grandchild"]

    # One level below new starting folder is allowed
    assert ctx.list_dir(folder_gc) == ["greatgrandchild"]

    # Two levels below new starting folder is refused
    with pytest.raises(WalkRefused):
        ctx.list_dir(folder_ggc)


def test_env_case_sensitivity() -> None:
    custom_environ = {
        "Path": "/some/bin:/custom/bin",
        "TOOL_NAME": "helper",
    }

    win_ctx = LookContext(machine="test-machine", environ=custom_environ, system="Windows")
    assert win_ctx.env("path") == "/some/bin:/custom/bin"
    assert win_ctx.env("PATH") == "/some/bin:/custom/bin"
    assert win_ctx.env("tool_name") == "helper"
    assert win_ctx.env("TOOL_NAME") == "helper"
    assert win_ctx.env("MISSING", "default_val") == "default_val"

    linux_ctx = LookContext(machine="test-machine", environ=custom_environ, system="Linux")
    assert linux_ctx.env("Path") == "/some/bin:/custom/bin"
    assert linux_ctx.env("path") == ""
    assert linux_ctx.env("TOOL_NAME") == "helper"
    assert linux_ctx.env("tool_name") == ""
    assert linux_ctx.env("MISSING", "default_val") == "default_val"
