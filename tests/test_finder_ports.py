"""Tests for local ports and model shelf finders."""

from __future__ import annotations

import http.server
import json
import socket
import threading
from typing import Any, Callable, Generator

import pytest

from tau.connect.ctx import LookContext
from tau.connect.finders.models import find_models
from tau.connect.finders.ports import find_local_ports
from tau.connect.registry import FINDERS, load_finders


def make_server(
    routes: dict[str, tuple[int, str, str]],
) -> tuple[http.server.ThreadingHTTPServer, int]:
    """Start a tiny HTTP server serving the provided routes or 404."""

    class TinyHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            clean_path = self.path.split("?")[0]
            if clean_path in routes:
                status, ctype, text = routes[clean_path]
                encoded = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            else:
                body = b"Not Found"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), TinyHandler)
    port = int(server.server_address[1])
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port


@pytest.fixture
def start_server() -> Generator[Callable[[dict[str, tuple[int, str, str]]], int], None, None]:
    """Fixture that manages the lifetime of test HTTP servers."""
    servers: list[http.server.ThreadingHTTPServer] = []

    def _start(routes: dict[str, tuple[int, str, str]]) -> int:
        server, port = make_server(routes)
        servers.append(server)
        return port

    yield _start

    for s in servers:
        try:
            s.shutdown()
            s.server_close()
        except Exception:
            pass


def test_tau_describe_server(start_server: Callable[[dict[str, tuple[int, str, str]]], int]) -> None:
    routes = {
        "/tau/v1/describe": (
            200,
            "application/json",
            json.dumps({"name": "Thing", "actions": [{"id": "a", "label": "Do a"}]}),
        ),
    }
    port = start_server(routes)
    ctx = LookContext(machine="test-pc", snapshot={"ports": [{"port": port, "address": "127.0.0.1"}]})
    conns = find_local_ports(ctx)
    assert len(conns) == 1
    c = conns[0]
    assert c.kind == "Program that offers TAU's own connection"
    assert c.state == "Ready"
    assert c.name.startswith("Thing")
    assert c.detail["actions"] == 1


def test_v1_models_server(start_server: Callable[[dict[str, tuple[int, str, str]]], int]) -> None:
    routes = {
        "/v1/models": (
            200,
            "application/json",
            json.dumps({"data": [{"id": "m1"}, {"id": "m2"}]}),
        ),
    }
    port = start_server(routes)
    ctx = LookContext(machine="test-pc", snapshot={"ports": [{"port": port, "address": "127.0.0.1"}]})
    conns = find_local_ports(ctx)
    assert len(conns) == 1
    c = conns[0]
    assert c.kind == "Model server"
    assert c.detail["models"] == ["m1", "m2"]


def test_api_tags_server(start_server: Callable[[dict[str, tuple[int, str, str]]], int]) -> None:
    routes = {
        "/api/tags": (
            200,
            "application/json",
            json.dumps({"models": [{"name": "x"}]}),
        ),
    }
    port = start_server(routes)
    ctx = LookContext(machine="test-pc", snapshot={"ports": [{"port": port, "address": "127.0.0.1"}]})
    conns = find_local_ports(ctx)
    assert len(conns) == 1
    c = conns[0]
    assert c.kind == "Model server"
    assert c.detail["models"] == ["x"]


def test_openapi_server(start_server: Callable[[dict[str, tuple[int, str, str]]], int]) -> None:
    routes = {
        "/openapi.json": (
            200,
            "application/json",
            json.dumps({"openapi": "3.0.0", "info": {"title": "Notes"}, "paths": {"/a": {}, "/b": {}}}),
        ),
    }
    port = start_server(routes)
    ctx = LookContext(machine="test-pc", snapshot={"ports": [{"port": port, "address": "127.0.0.1"}]})
    conns = find_local_ports(ctx)
    assert len(conns) == 1
    c = conns[0]
    assert c.kind == "Web service that describes itself"
    assert c.name.startswith("Notes")
    assert c.detail["actions"] == 2


def test_web_page_server(start_server: Callable[[dict[str, tuple[int, str, str]]], int]) -> None:
    routes = {
        "/": (200, "text/html", "<html><title> My   Page </title></html>"),
    }
    port = start_server(routes)
    ctx = LookContext(machine="test-pc", snapshot={"ports": [{"port": port, "address": "127.0.0.1"}]})
    conns = find_local_ports(ctx)
    assert len(conns) == 1
    c = conns[0]
    assert c.kind == "Web page"
    assert c.name == f"My Page (port {port})"


def test_quiet_socket_port() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = int(sock.getsockname()[1])
    try:
        ctx = LookContext(
            machine="test-pc",
            snapshot={
                "ports": [
                    {"proto": "tcp", "address": "127.0.0.1", "port": port, "process": "quiet.exe"},
                ]
            },
        )
        conns = find_local_ports(ctx)
        assert len(conns) == 1
        c = conns[0]
        assert c.kind == "Program listening on a port"
        assert c.name == f"quiet.exe (port {port})"
    finally:
        sock.close()


def test_filtering_and_deduplication(start_server: Callable[[dict[str, tuple[int, str, str]]], int]) -> None:
    port = start_server({"/": (200, "text/html", "<title>Page</title>")})
    snap = {
        "ports": [
            {"proto": "tcp", "address": "192.168.50.7", "port": port, "process": "lan.exe"},
            {"proto": "udp", "address": "127.0.0.1", "port": port, "process": "udp.exe"},
            {"proto": "tcp", "address": "127.0.0.1", "port": port, "process": "proc1.exe"},
            {"proto": "tcp", "address": "127.0.0.1", "port": port, "process": "proc2.exe"},
        ]
    }
    ctx = LookContext(machine="test-pc", snapshot=snap)
    conns = find_local_ports(ctx)
    assert len(conns) == 1
    assert conns[0].detail["port"] == port
    assert conns[0].detail["process"] == "proc1.exe"


def test_non_json_models_fallback(start_server: Callable[[dict[str, tuple[int, str, str]]], int]) -> None:
    routes = {
        "/": (200, "text/plain", "Hello"),
        "/v1/models": (200, "text/plain", "this is not json {invalid"),
    }
    port = start_server(routes)
    ctx = LookContext(machine="test-pc", snapshot={"ports": [{"port": port, "address": "127.0.0.1"}]})
    conns = find_local_ports(ctx)
    assert len(conns) == 1
    assert conns[0].kind == "Web page"


def test_connection_fields_stability(start_server: Callable[[dict[str, tuple[int, str, str]]], int]) -> None:
    port = start_server({"/": (200, "text/html", "<title>Stable</title>")})
    snap = {
        "ports": [
            {"proto": "tcp", "address": "127.0.0.1", "port": port, "process": "srv.exe"},
        ]
    }
    ctx1 = LookContext(machine="test-pc", snapshot=snap)
    ctx2 = LookContext(machine="test-pc", snapshot=snap)
    conns1 = find_local_ports(ctx1)
    conns2 = find_local_ports(ctx2)
    assert len(conns1) == 1
    assert len(conns2) == 1
    for c in conns1:
        assert isinstance(c.evidence, str) and c.evidence.strip()
        assert isinstance(c.allows, str) and c.allows.strip()
        assert c.machine == "test-pc"
    assert conns1[0].id == conns2[0].id


def test_model_shelf_finder() -> None:
    snap = {
        "model_servers": [
            {
                "engine": "e",
                "url": "http://host-a",
                "models": ["alpha-7b", "alpha-7b", "", "mix-8x7b"],
            },
            {
                "engine": "e2",
                "url": "http://host-b",
                "models": ["solo"],
            },
            {
                "models": ["nourl"],
            },
        ]
    }
    ctx = LookContext(machine="test-pc", snapshot=snap)
    conns = find_models(ctx)
    assert len(conns) == 3
    by_name = {c.name: c for c in conns}
    assert set(by_name.keys()) == {"alpha-7b", "mix-8x7b", "solo"}

    c_alpha = by_name["alpha-7b"]
    assert c_alpha.detail["server"] == "http://host-a"
    assert c_alpha.detail["engine"] == "e"
    assert c_alpha.detail["server_lists"] == 2
    assert c_alpha.detail["can_switch"] is True

    c_mix = by_name["mix-8x7b"]
    assert c_mix.detail["server"] == "http://host-a"
    assert c_mix.detail["server_lists"] == 2
    assert c_mix.detail["can_switch"] is True
    assert c_mix.detail["architecture"] == "MoE"

    c_solo = by_name["solo"]
    assert c_solo.detail["server"] == "http://host-b"
    assert c_solo.detail["engine"] == "e2"
    assert c_solo.detail["server_lists"] == 1
    assert c_solo.detail["can_switch"] is False


def test_load_finders_registration() -> None:
    load_finders()
    assert "local_ports" in FINDERS
    assert "model_shelf" in FINDERS
