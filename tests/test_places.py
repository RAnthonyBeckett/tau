"""Tests for places discovery and inspection inside Linux environments and containers."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple
import pytest

from tau.connect import (
    Connection,
    LookContext,
    inside_context,
    list_places,
    look_at_places,
    look_here,
    make_id,
)
import tau.connect.lab
import tau.connect.places
from tau.connect.places import ENTER, look_inside


class CommandRecorder:
    """Mock command executor backed by a command-to-output dictionary."""

    def __init__(self, responses: Dict[Tuple[str, ...], str]) -> None:
        self.responses = responses
        self.recorded: List[List[str]] = []

    def __call__(self, cmd: List[str], timeout: float = 10.0) -> str:
        self.recorded.append(list(cmd))
        return self.responses.get(tuple(cmd), "")


def _two_byte_wsl(text: str) -> str:
    """Simulate Windows host two-byte UTF-16 character output with CRLF line ends."""
    normalized = text.replace("\r\n", "\n").split("\n")
    crlf_text = "\r\n".join(normalized)
    return "".join(ch + "\x00" for ch in crlf_text)


def test_list_places() -> None:
    wsl_output = (
        "  NAME            STATE           VERSION\n"
        "* Ubuntu-Work     Running         2\n"
        "  Old-One         Stopped         2\n"
        "  docker-desktop  Running         2\n"
    )
    responses = {
        (ENTER, "-l", "-v"): _two_byte_wsl(wsl_output),
    }
    recorder = CommandRecorder(responses)

    containers_data = [
        {"name": "db", "image": "img:1", "state": "running", "ports": "5432"},
        {"image": "x"},
    ]
    ctx_win = LookContext(
        machine="host-pc",
        run=recorder,
        snapshot={"containers": containers_data},
        system="Windows",
    )

    places = list_places(ctx_win)
    assert len(places) == 3

    # Sorted by (type, name lower-cased): "container" before "linux"
    p_db = places[0]
    assert p_db["name"] == "db"
    assert p_db["type"] == "container"
    assert p_db["running"] is True
    assert p_db["detail"] == {"image": "img:1", "ports": "5432"}

    p_old = places[1]
    assert p_old["name"] == "Old-One"
    assert p_old["type"] == "linux"
    assert p_old["running"] is False
    assert p_old["detail"] == {"state": "Stopped"}

    p_ub = places[2]
    assert p_ub["name"] == "Ubuntu-Work"
    assert p_ub["type"] == "linux"
    assert p_ub["running"] is True
    assert p_ub["detail"] == {"state": "Running"}

    # docker-desktop was ignored
    names = [p["name"].lower() for p in places]
    assert not any("docker-desktop" in n for n in names)

    # With system="Linux", the host command is never run
    recorder_linux = CommandRecorder(responses)
    ctx_linux = LookContext(
        machine="host-pc",
        run=recorder_linux,
        snapshot={"containers": containers_data},
        system="Linux",
    )
    places_linux = list_places(ctx_linux)
    assert len(recorder_linux.recorded) == 0
    assert len(places_linux) == 1
    assert places_linux[0]["name"] == "db"


def test_place_connections() -> None:
    ctx = LookContext(machine="host-pc", system="Windows")
    places = [
        {
            "name": "Ubuntu-Work",
            "type": "linux",
            "running": True,
            "detail": {"state": "Running"},
        },
        {
            "name": "Old-One",
            "type": "linux",
            "running": False,
            "detail": {"state": "Stopped"},
        },
        {
            "name": "db",
            "type": "container",
            "running": True,
            "detail": {"image": "img:1", "ports": "5432"},
        },
        {
            "name": "anon",
            "type": "container",
            "running": False,
            "detail": {"image": "", "ports": ""},
        },
    ]

    conns = tau.connect.places.place_connections(ctx, places)
    by_name = {c.name: c for c in conns}

    c_ub = by_name["Ubuntu-Work"]
    assert c_ub.kind == "Linux environment inside this machine"
    assert c_ub.state == "Ready"
    assert c_ub.needs == ""
    assert c_ub.evidence == "Windows lists it as a Linux environment on this machine."
    assert "tools inside it" in c_ub.allows
    assert c_ub.id == make_id("host-pc", "", c_ub.kind, "ubuntu-work")

    c_old = by_name["Old-One"]
    assert c_old.state == "Needs something"
    assert c_old.needs == "It is not running. Start it yourself, then look again."

    c_db = by_name["db"]
    assert c_db.kind == "Container on this machine"
    assert c_db.door == "container tools"
    assert c_db.state == "Found"
    assert c_db.evidence == "It is a container on this machine, made from img:1."
    assert "Programs it serves on a local port" in c_db.allows

    c_anon = by_name["anon"]
    assert c_anon.evidence == "It is a container on this machine."


def test_inside_context() -> None:
    env_output = (
        "noise line from shell init\n"
        "FOO=bar\n"
        "PATH=/custom/bin\n"
        "INVALID LINE WITHOUT EQUALS\n"
        "123BAD=value\n"
    )
    ss_output = (
        "LISTEN 0 128 127.0.0.1:8099 0.0.0.0:*\n"
        "LISTEN 0 128 *:22 *:*\n"
        "LISTEN 0 128 [::]:80 [::]:*\n"
        "LISTEN 0 128 10.0.0.1:invalid 0.0.0.0:*\n"
    )
    responses = {
        (ENTER, "-d", "Ubuntu-Work", "-e", "bash", "-ic", "env"): env_output,
        (ENTER, "-d", "Ubuntu-Work", "-e", "ss", "-ltnH"): ss_output,
        (ENTER, "-d", "Ubuntu-Work", "-e", "ls", "-1A", "--", "/x/bin"): "prog1\nprog2\n",
        (ENTER, "-d", "Ubuntu-Work", "-e", "head", "-c", "200000", "--", "/proc/version"): "Linux ver 1\n",
    }
    recorder = CommandRecorder(responses)
    ctx = LookContext(machine="host-pc", run=recorder, system="Windows")

    ictx = inside_context(ctx, "Ubuntu-Work")
    assert ictx.system == "Linux"
    assert ictx.machine == "host-pc"
    assert ictx.environ.get("FOO") == "bar"
    assert ictx.environ.get("PATH") == "/custom/bin"
    assert "123BAD" not in ictx.environ

    # list_dir sends exact command and returns lines
    files = ictx.list_dir("/x/bin")
    assert files == ["prog1", "prog2"]
    assert [ENTER, "-d", "Ubuntu-Work", "-e", "ls", "-1A", "--", "/x/bin"] in recorder.recorded

    # read_text sends head with limit
    text = ictx.read_text("/proc/version", limit=200000)
    assert text == "Linux ver 1\n"
    assert [ENTER, "-d", "Ubuntu-Work", "-e", "head", "-c", "200000", "--", "/proc/version"] in recorder.recorded

    # registry_read inside linux is empty
    reg = ictx.registry_read("HKLM", "Software")
    assert reg == {"subkeys": [], "values": {}}

    # ports parsed correctly
    ports = ictx.snapshot.get("ports", [])
    assert len(ports) == 3
    assert ports[0] == {"proto": "tcp", "address": "127.0.0.1", "port": 8099, "pid": None, "process": None}
    assert ports[1] == {"proto": "tcp", "address": "*", "port": 22, "pid": None, "process": None}
    assert ports[2] == {"proto": "tcp", "address": "::", "port": 80, "pid": None, "process": None}

    # Fallback PATH when env outputs no matching variables
    recorder_empty = CommandRecorder({
        (ENTER, "-d", "Ubuntu-Work", "-e", "bash", "-ic", "env"): "not-an-env-var\n",
        (ENTER, "-d", "Ubuntu-Work", "-e", "ss", "-ltnH"): "",
    })
    ctx_empty = LookContext(machine="host-pc", run=recorder_empty, system="Windows")
    ictx_empty = inside_context(ctx_empty, "Ubuntu-Work")
    assert ictx_empty.environ == {"PATH": "/usr/local/bin:/usr/bin:/bin"}


def test_look_inside_end_to_end_real_finders() -> None:
    responses = {
        (ENTER, "-d", "Ubuntu-Work", "-e", "bash", "-ic", "env"): "PATH=/tools/bin\n",
        (ENTER, "-d", "Ubuntu-Work", "-e", "ss", "-ltnH"): "",
        (ENTER, "-d", "Ubuntu-Work", "-e", "ls", "-1A", "--", "/tools/bin"): "mytool\nrig\n",
    }
    recorder = CommandRecorder(responses)
    ctx = LookContext(machine="host-pc", run=recorder, system="Windows")

    result = look_inside(ctx, "Ubuntu-Work")
    conns = result["connections"]

    # Tool connection
    tool_conns = [c for c in conns if c.kind == "Command-line tool" and c.name == "mytool"]
    assert len(tool_conns) == 1
    tool = tool_conns[0]
    assert tool.place == "Ubuntu-Work"
    assert tool.id != make_id("host-pc", "", "Command-line tool", "mytool")
    assert tool.evidence.endswith("inside the Linux environment Ubuntu-Work.")

    # Assistant worker connection for command rig
    worker_conns = [
        c for c in conns
        if c.kind == "Assistant that can take on work" and c.detail.get("command") == "rig"
    ]
    assert len(worker_conns) == 1
    worker = worker_conns[0]
    assert worker.place == "Ubuntu-Work"
    assert worker.id != make_id("host-pc", "", "Assistant that can take on work", "rig")
    assert worker.evidence.endswith("inside the Linux environment Ubuntu-Work.")

    # On-screen controls dropped
    assert not any(c.kind == "On-screen controls" for c in conns)

    # Finders report keys start with place prefix
    finders = result["finders"]
    assert len(finders) > 0
    for key in finders.keys():
        assert key.startswith("Ubuntu-Work: ")


def test_stopped_linux_environment_never_entered() -> None:
    wsl_output = "  Old-One   Stopped   2\n"
    responses = {
        (ENTER, "-l", "-v"): wsl_output,
    }
    recorder = CommandRecorder(responses)
    ctx = LookContext(machine="host-pc", run=recorder, system="Windows")

    result = look_at_places(ctx)
    conns = result["connections"]
    assert len(conns) == 1
    assert conns[0].name == "Old-One"
    assert conns[0].state == "Needs something"

    # No command containing "-d" was ever run
    assert not any("-d" in cmd for cmd in recorder.recorded)


def test_look_at_places_when_look_inside_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    wsl_output = "* Ubuntu-Work   Running   2\n"
    responses = {
        (ENTER, "-l", "-v"): wsl_output,
    }
    recorder = CommandRecorder(responses)
    ctx = LookContext(machine="host-pc", run=recorder, system="Windows")

    def broken_look_inside(c: LookContext, place: str) -> Dict[str, Any]:
        raise RuntimeError("SECRET")

    monkeypatch.setattr(tau.connect.places, "look_inside", broken_look_inside)

    result = look_at_places(ctx)
    conns = result["connections"]
    assert len(conns) == 1
    assert conns[0].name == "Ubuntu-Work"

    report = result["finders"]
    assert "Ubuntu-Work: look" in report
    assert report["Ubuntu-Work: look"] == {
        "count": 0,
        "stopped_early": False,
        "seconds": 0.0,
        "error": "stopped with an error",
    }
    assert "SECRET" not in str(result)


def test_look_at_places_when_list_places_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = LookContext(machine="host-pc", system="Windows")

    def broken_list(c: LookContext) -> List[Dict[str, Any]]:
        raise OSError("failed to list")

    monkeypatch.setattr(tau.connect.places, "list_places", broken_list)

    result = look_at_places(ctx)
    assert result == {"connections": [], "finders": {}}


def test_lab_look_here_adds_place_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    from tau import discovery
    from tau.discovery import neighbours

    monkeypatch.setattr(
        discovery,
        "collect",
        lambda: {"machine": {"name": "unit-pc"}, "ports": [], "drives": []},
    )
    monkeypatch.setattr(neighbours, "list_neighbours", lambda: [])
    monkeypatch.setattr(
        tau.connect.lab,
        "run_look",
        lambda ctx: {"machine": "unit-pc", "at": "T", "connections": [], "finders": {}},
    )

    sample_conn = Connection(
        id="sample-id",
        machine="unit-pc",
        kind="Linux environment inside this machine",
        name="Ubuntu-Work",
        evidence="Some evidence",
        door="the host's own way in",
        state="Ready",
        place="",
    )

    def mock_look_at_places(ctx: LookContext) -> Dict[str, Any]:
        return {
            "connections": [sample_conn],
            "finders": {"p: x": {"count": 1, "stopped_early": False, "seconds": 0.1, "error": ""}},
        }

    monkeypatch.setattr(tau.connect.places, "look_at_places", mock_look_at_places)

    look = look_here()
    assert len(look["connections"]) == 1
    c_dict = look["connections"][0]
    assert c_dict["id"] == "sample-id"
    assert c_dict["last_seen"] == "T"
    assert "p: x" in look["finders"]
    assert look["finders"]["p: x"]["count"] == 1


def test_lab_look_here_survives_places_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    from tau import discovery
    from tau.discovery import neighbours

    monkeypatch.setattr(
        discovery,
        "collect",
        lambda: {"machine": {"name": "unit-pc"}, "ports": [], "drives": []},
    )
    monkeypatch.setattr(neighbours, "list_neighbours", lambda: [])
    monkeypatch.setattr(
        tau.connect.lab,
        "run_look",
        lambda ctx: {"machine": "unit-pc", "at": "T", "connections": [], "finders": {}},
    )

    def exploding_places(ctx: LookContext) -> Dict[str, Any]:
        raise RuntimeError("Places exploded")

    monkeypatch.setattr(tau.connect.places, "look_at_places", exploding_places)

    look = look_here()
    assert look["machine"] == "unit-pc"
    assert look["connections"] == []
    assert look["finders"] == {}
