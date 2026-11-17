"""Tests for Linux doors and interfaces finder."""

from __future__ import annotations

from typing import Any

import pytest

from tau.connect.ctx import LookContext, WalkRefused
from tau.connect.finders.os_linux import find_linux_doors
import tau.connect.registry
from tau.connect.registry import load_finders


class FakeLinuxMachine:
    """Fake machine environment providing injected methods for LookContext."""

    def __init__(
        self,
        files: dict[str, list[str]] | None = None,
        commands: dict[tuple[str, ...], str] | None = None,
    ) -> None:
        self.files = dict(files or {})
        self.commands = dict(commands or {})
        self.run_calls: list[tuple[list[str], float]] = []
        self.listed_paths: list[str] = []

    def list_dir(self, path: Any, start: bool = False) -> list[str]:
        p_str = str(path)
        self.listed_paths.append(p_str)
        return list(self.files.get(p_str, []))

    def run(self, cmd: list[str], timeout: float = 10.0) -> str:
        self.run_calls.append((list(cmd), timeout))
        return self.commands.get(tuple(cmd), "")


def test_windows_system_returns_empty() -> None:
    ctx = LookContext(machine="test-linux", system="Windows")
    assert find_linux_doors(ctx) == []


def test_section_a_message_bus_filtering_and_discovery() -> None:
    user_output = (
        ":1.42\n"
        "org.freedesktop.Notifications\n"
        "org.mpris.MediaPlayer2.tuneplayer.instance1234\n"
        "com.example.Notes\n"
    )
    system_output = (
        "org.freedesktop.NetworkManager\n"
        "com.example.Backup\n"
        "com.example.Notes\n"
    )
    commands = {
        ("busctl", "--user", "list", "--no-pager", "--no-legend"): user_output,
        ("busctl", "--system", "list", "--no-pager", "--no-legend"): system_output,
    }
    fake = FakeLinuxMachine(commands=commands)
    ctx = LookContext(
        machine="test-box",
        system="Linux",
        environ={"HOME": "/users-home/pat"},
        list_dir=fake.list_dir,
        run=fake.run,
    )
    results = find_linux_doors(ctx)

    media_conns = [c for c in results if c.kind == "Media player with standard controls"]
    assert len(media_conns) == 1
    m = media_conns[0]
    assert m.name == "tuneplayer"
    assert m.detail["bus"] == "user"
    assert m.detail["bus_name"] == "org.mpris.MediaPlayer2.tuneplayer.instance1234"
    assert m.door == "standard media controls"
    assert m.evidence == "It offers the standard play, pause and skip controls to other programs."
    assert m.allows == "TAU could play, pause, skip and read what is playing."

    bus_conns = [c for c in results if c.kind == "Program reachable on the message bus"]
    bus_names = [c.name for c in bus_conns]
    assert "com.example.Notes" in bus_names
    assert "com.example.Backup" in bus_names
    assert bus_names.count("com.example.Notes") == 1

    notes_conn = next(c for c in bus_conns if c.name == "com.example.Notes")
    assert notes_conn.detail["bus"] == "user"
    assert "your session" in notes_conn.evidence

    backup_conn = next(c for c in bus_conns if c.name == "com.example.Backup")
    assert backup_conn.detail["bus"] == "system"
    assert "the whole system" in backup_conn.evidence

    all_names = [c.name for c in results]
    assert not any(n.startswith(":") for n in all_names)
    assert not any("org.freedesktop" in n for n in all_names)


def test_section_a_empty_command_output() -> None:
    commands = {
        ("busctl", "--user", "list", "--no-pager", "--no-legend"): "",
        ("busctl", "--system", "list", "--no-pager", "--no-legend"): "",
    }
    fake = FakeLinuxMachine(commands=commands)
    ctx = LookContext(
        machine="test-box",
        system="Linux",
        environ={"HOME": "/users-home/pat"},
        list_dir=fake.list_dir,
        run=fake.run,
    )
    results = find_linux_doors(ctx)
    bus_kinds = {
        "Media player with standard controls",
        "Program reachable on the message bus",
    }
    assert [c for c in results if c.kind in bus_kinds] == []


def test_section_b_services_and_tasks_filtering() -> None:
    files = {
        "/etc/systemd/system": [
            "mytool.service",
            "nightly.timer",
            "multi-user.target.wants",
            "dbus-org.example.service",
            "override.d",
            "sshd.service",
        ],
        "/users-home/pat/.config/systemd/user": [
            "helper.service",
            "mytool.service",
        ],
    }
    fake = FakeLinuxMachine(files=files)
    ctx = LookContext(
        machine="test-box",
        system="Linux",
        environ={"HOME": "/users-home/pat"},
        list_dir=fake.list_dir,
        run=fake.run,
    )
    results = find_linux_doors(ctx)

    services = [c for c in results if c.kind == "Background service"]
    mytool_conns = [c for c in services if c.name == "mytool"]
    assert len(mytool_conns) == 2
    mytool_scopes = {c.detail["scope"] for c in mytool_conns}
    assert mytool_scopes == {"system", "user"}
    assert mytool_conns[0].id != mytool_conns[1].id

    sshd_conns = [c for c in services if c.name == "sshd"]
    assert len(sshd_conns) == 1
    assert sshd_conns[0].detail["scope"] == "system"

    helper_conns = [c for c in services if c.name == "helper"]
    assert len(helper_conns) == 1
    assert helper_conns[0].detail["scope"] == "user"

    tasks = [c for c in results if c.kind == "Scheduled task"]
    assert len(tasks) == 1
    assert tasks[0].name == "nightly"

    all_names = [c.name for c in results]
    assert "multi-user.target.wants" not in all_names
    assert "override.d" not in all_names
    assert not any("dbus-" in n for n in all_names)

    assert fake.listed_paths == [
        "/etc/systemd/system",
        "/users-home/pat/.config/systemd/user",
    ]


def test_section_b_empty_home_lists_only_system_folder() -> None:
    files = {
        "/etc/systemd/system": ["sample.service"],
    }
    fake = FakeLinuxMachine(files=files)
    ctx = LookContext(
        machine="test-box",
        system="Linux",
        environ={"HOME": ""},
        list_dir=fake.list_dir,
        run=fake.run,
    )
    find_linux_doors(ctx)
    assert fake.listed_paths == ["/etc/systemd/system"]


def test_section_c_onscreen_controls_single_connection() -> None:
    fake = FakeLinuxMachine()
    ctx = LookContext(
        machine="test-box",
        system="Linux",
        environ={"HOME": "/users-home/pat"},
        list_dir=fake.list_dir,
        run=fake.run,
    )
    results = find_linux_doors(ctx)
    controls = [c for c in results if c.kind == "On-screen controls"]
    assert len(controls) == 1
    conn = controls[0]
    assert conn.name == "Reading and pressing on-screen controls"
    assert conn.door == "accessibility interface"
    assert conn.state == "Found"


def test_section_failure_isolation() -> None:
    files = {
        "/etc/systemd/system": ["custom.service"],
    }
    fake = FakeLinuxMachine(files=files)

    def failing_run(cmd: list[str], timeout: float = 10.0) -> str:
        raise RuntimeError("Injected run failure")

    ctx = LookContext(
        machine="test-box",
        system="Linux",
        environ={"HOME": "/users-home/pat"},
        list_dir=fake.list_dir,
        run=failing_run,
    )
    results = find_linux_doors(ctx)
    kinds = {c.kind for c in results}

    assert "Background service" in kinds
    assert "On-screen controls" in kinds
    assert "Media player with standard controls" not in kinds
    assert "Program reachable on the message bus" not in kinds


def test_connection_invariants_and_stability() -> None:
    files = {
        "/etc/systemd/system": ["custom.service", "backup.timer"],
        "/users-home/pat/.config/systemd/user": ["userapp.service"],
    }
    commands = {
        ("busctl", "--user", "list", "--no-pager", "--no-legend"): (
            "org.mpris.MediaPlayer2.vlc\napp.chat"
        ),
        ("busctl", "--system", "list", "--no-pager", "--no-legend"): "sys.service",
    }
    fake = FakeLinuxMachine(files=files, commands=commands)
    ctx = LookContext(
        machine="test-box",
        system="Linux",
        environ={"HOME": "/users-home/pat"},
        list_dir=fake.list_dir,
        run=fake.run,
    )

    run1 = find_linux_doors(ctx)
    assert len(run1) > 0
    for conn in run1:
        assert conn.state == "Found"
        assert conn.evidence != ""
        assert conn.allows != ""

    run2 = find_linux_doors(ctx)
    assert [c.id for c in run1] == [c.id for c in run2]


def test_injected_run_called_exactly_twice() -> None:
    fake = FakeLinuxMachine()
    ctx = LookContext(
        machine="test-box",
        system="Linux",
        environ={"HOME": "/users-home/pat"},
        list_dir=fake.list_dir,
        run=fake.run,
    )
    find_linux_doors(ctx)
    assert len(fake.run_calls) == 2
    cmd1, _ = fake.run_calls[0]
    cmd2, _ = fake.run_calls[1]
    assert cmd1 == ["busctl", "--user", "list", "--no-pager", "--no-legend"]
    assert cmd2 == ["busctl", "--system", "list", "--no-pager", "--no-legend"]


def test_load_finders_registers_linux_doors() -> None:
    load_finders()
    assert "linux_doors" in tau.connect.registry.FINDERS
    spec = tau.connect.registry.FINDERS["linux_doors"]
    assert spec.timeout == 45.0
    assert spec.max_rows == 1000


def test_walk_refused_propagates() -> None:
    def refusing_list_dir(path: Any, start: bool = False) -> list[str]:
        raise WalkRefused("Folder limit exceeded")

    ctx = LookContext(
        machine="test-box",
        system="Linux",
        environ={"HOME": "/users-home/pat"},
        list_dir=refusing_list_dir,
    )
    with pytest.raises(WalkRefused):
        find_linux_doors(ctx)
