"""Tests for storage and other machines finders."""

from __future__ import annotations

import json
from typing import Any

from tau.connect import LookContext
from tau.connect.finders.machines import find_other_machines
from tau.connect.finders.storage import find_storage
import tau.connect.registry
from tau.connect.registry import load_finders


class FakeMachine:
    """Fake machine environment providing injected methods for LookContext."""

    def __init__(
        self,
        registry: dict[tuple[str, str], dict[str, Any]] | None = None,
        files: dict[str, str] | None = None,
    ) -> None:
        self.registry = {
            (hive.upper(), key.replace("/", "\\").lower()): val
            for (hive, key), val in (registry or {}).items()
        }
        self.files = files or {}
        self.read_text_calls: list[str] = []

    def registry_read(self, hive: str, key: str) -> dict[str, Any]:
        normalized_key = key.replace("/", "\\").lower()
        return self.registry.get(
            (hive.upper(), normalized_key),
            {"subkeys": [], "values": {}},
        )

    def read_text(self, path: Any, limit: int = 200000) -> str:
        p_str = str(path)
        self.read_text_calls.append(p_str)
        return self.files.get(p_str, "")


def test_storage_drives() -> None:
    snapshot = {
        "drives": [
            {"mount": "C:", "fstype": "NTFS", "total_gb": 931.5, "free_gb": 120.0, "network": False},
            {"mount": "D:", "fstype": "NTFS", "total_gb": None, "network": False},
            {"mount": "Z:", "network": True, "remote": r"\\nas\media\films", "fstype": "SMB"},
            {"mount": "/mnt/filer", "network": True, "device": "filer:/export/data", "fstype": "NFS"},
            {"mount": "/mnt/box", "network": True, "device": "//box/pics", "fstype": "CIFS"},
            {"fstype": "ext4", "total_gb": 50.0, "network": False},
            {"mount": "", "total_gb": 10.0, "network": False},
        ]
    }
    no_shares = lambda hive, key: {"subkeys": [], "values": {}}  # keep the real machine out of this test
    ctx = LookContext(machine="test-box", snapshot=snapshot, system="Windows", registry_read=no_shares)
    results = find_storage(ctx)

    c_conn = next(c for c in results if c.name == "C:")
    assert c_conn.kind == "Drive"
    assert "932 GB in size" in c_conn.evidence
    assert c_conn.detail["total_gb"] == 931.5
    assert c_conn.detail["free_gb"] == 120.0
    assert c_conn.detail["fstype"] == "NTFS"

    d_conn = next(c for c in results if c.name == "D:")
    assert d_conn.kind == "Drive"
    assert "of unknown size" in d_conn.evidence

    z_conn = next(c for c in results if c.name == "Z:")
    assert z_conn.kind == "Shared folder from another machine"
    assert z_conn.door == "file sharing"
    assert z_conn.detail["remote_host"] == "nas"
    assert z_conn.detail["remote_share"] == "media"

    filer_conn = next(c for c in results if c.name == "/mnt/filer")
    assert filer_conn.kind == "Shared folder from another machine"
    assert filer_conn.detail["remote_host"] == "filer"
    assert filer_conn.detail["remote_share"] == "/export/data"

    box_conn = next(c for c in results if c.name == "/mnt/box")
    assert box_conn.kind == "Shared folder from another machine"
    assert box_conn.detail["remote_host"] == "box"
    assert box_conn.detail["remote_share"] == "pics"

    assert len(results) == 5


def test_storage_windows_shares() -> None:
    registry_data = {
        ("HKLM", "SYSTEM\\CurrentControlSet\\Services\\LanmanServer\\Shares"): {
            "values": {
                "Media": ["CSCFlags=0", "Path=D:\\Media", "Type=0"],
                "ADMIN$": ["Path=C:\\Windows"],
                "Odd": "Remark=x\nPath=E:\\Odd",
            }
        }
    }
    fake = FakeMachine(registry=registry_data)
    ctx = LookContext(machine="test-win", registry_read=fake.registry_read, system="Windows")
    results = find_storage(ctx)
    shares = [c for c in results if c.kind == "Folder this machine shares"]
    names = [c.name for c in shares]

    assert "Media" in names
    assert "Odd" in names
    assert "ADMIN$" not in names

    media = next(c for c in shares if c.name == "Media")
    assert media.detail["path"] == "D:\\Media"
    assert media.detail["how"] == "windows sharing"

    odd = next(c for c in shares if c.name == "Odd")
    assert odd.detail["path"] == "E:\\Odd"
    assert odd.detail["how"] == "windows sharing"


def test_storage_linux_shares() -> None:
    exports_content = "# Comment line\n/export/data 10.0.0.0/24(rw)\n\n"
    smb_content = (
        "[global]\n"
        "  workgroup = WORKGROUP\n"
        "[homes]\n"
        "  comment = Home Directories\n"
        "[films]\n"
        "  path = /data/films\n"
        "[hidden$]\n"
        "  path = /x\n"
        "[nopath]\n"
        "  comment = No path defined\n"
    )
    fake = FakeMachine(
        files={
            "/etc/exports": exports_content,
            "/etc/samba/smb.conf": smb_content,
        }
    )
    ctx = LookContext(machine="test-linux", read_text=fake.read_text, system="Linux")
    results = find_storage(ctx)
    shares = [c for c in results if c.kind == "Folder this machine shares"]

    names = [c.name for c in shares]
    assert "/export/data" in names
    assert "films" in names
    assert "global" not in names
    assert "homes" not in names
    assert "hidden$" not in names
    assert "nopath" not in names

    export_conn = next(c for c in shares if c.name == "/export/data")
    assert export_conn.detail["path"] == "/export/data"
    assert export_conn.detail["how"] == "exports file"
    assert export_conn.evidence == "This machine shares the folder /export/data with other machines."

    films_conn = next(c for c in shares if c.name == "films")
    assert films_conn.detail["path"] == "/data/films"
    assert films_conn.detail["how"] == "sharing settings file"
    assert "under the name films" in films_conn.evidence


def test_storage_darwin_gives_no_shares() -> None:
    fake = FakeMachine(
        registry={
            ("HKLM", "SYSTEM\\CurrentControlSet\\Services\\LanmanServer\\Shares"): {
                "values": {"Shared": ["Path=D:\\Shared"]}
            }
        },
        files={
            "/etc/exports": "/export 10.0.0.1(rw)\n",
            "/etc/samba/smb.conf": "[share]\npath = /share\n",
        },
    )
    snapshot = {"drives": [{"mount": "/", "total_gb": 500.0, "network": False}]}
    ctx = LookContext(
        machine="test-mac",
        snapshot=snapshot,
        registry_read=fake.registry_read,
        read_text=fake.read_text,
        system="Darwin",
    )
    results = find_storage(ctx)
    assert len(results) == 1
    assert results[0].kind == "Drive"
    assert not any(c.kind == "Folder this machine shares" for c in results)


def test_storage_registry_failure_still_yields_drives() -> None:
    def failing_registry_read(hive: str, key: str) -> dict[str, Any]:
        raise RuntimeError("Registry read failed")

    snapshot = {"drives": [{"mount": "C:", "total_gb": 256.0, "network": False}]}
    ctx = LookContext(
        machine="test-win",
        snapshot=snapshot,
        registry_read=failing_registry_read,
        system="Windows",
    )
    results = find_storage(ctx)
    assert len(results) == 1
    assert results[0].kind == "Drive"
    assert results[0].name == "C:"


def test_storage_connection_invariants_and_stability() -> None:
    snapshot = {
        "drives": [
            {"mount": "C:", "total_gb": 500.0, "network": False},
            {"mount": "Z:", "network": True, "remote": r"\\server\share"},
        ]
    }
    registry_data = {
        ("HKLM", "SYSTEM\\CurrentControlSet\\Services\\LanmanServer\\Shares"): {
            "values": {"Data": ["Path=C:\\Data"]}
        }
    }
    fake = FakeMachine(registry=registry_data)
    ctx = LookContext(
        machine="test-win",
        snapshot=snapshot,
        registry_read=fake.registry_read,
        system="Windows",
    )

    run1 = find_storage(ctx)
    run2 = find_storage(ctx)

    assert len(run1) == 3
    for conn in run1:
        assert conn.state == "Found"
        assert conn.evidence != ""
        assert conn.allows != ""
        assert conn.id != ""

    assert [c.id for c in run1] == [c.id for c in run2]


def test_other_machines_neighbours_and_settings() -> None:
    snapshot = {
        "neighbours": [
            {"name": "printer-den", "address": "192.168.7.9"},
            {"address": "192.168.7.10"},
            {"name": "workbox"},
        ]
    }
    settings_text = (
        "Host workbox wb\n"
        "  HostName 192.168.7.20\n"
        "  User pat\n"
        "  IdentityFile ~/.ssh/SECRET_KEY_FILE\n"
        "Host *\n"
        "  ForwardAgent no\n"
        "host Media-PC !skipme *.lan\n"
        "Host github.com\n"
        "Host 10.1.2.3\n"
    )
    home_path = r"C:\Users\testuser"
    config_file = home_path + r"\.ssh\config"
    fake = FakeMachine(files={config_file: settings_text})
    ctx = LookContext(
        machine="test-client",
        snapshot=snapshot,
        read_text=fake.read_text,
        environ={"USERPROFILE": home_path},
        system="Windows",
    )

    results = find_other_machines(ctx)
    names = [c.name for c in results]

    assert "printer-den" in names
    assert "workbox" in names
    assert "wb" in names
    assert "Media-PC" in names

    assert "*" not in names
    assert "!skipme" not in names
    assert "*.lan" not in names
    assert "github.com" not in names
    assert "10.1.2.3" not in names

    printer = next(c for c in results if c.name == "printer-den")
    assert printer.detail["seen_as"] == ["announced on the network"]
    assert printer.evidence == "It is announced on the network."

    wb = next(c for c in results if c.name == "wb")
    assert wb.detail["seen_as"] == ["named in your remote-login settings"]
    assert wb.evidence == "It is named in your remote-login settings."

    workbox = next(c for c in results if c.name == "workbox")
    assert "announced on the network" in workbox.detail["seen_as"]
    assert "named in your remote-login settings" in workbox.detail["seen_as"]
    assert workbox.evidence == "It is announced on the network and named in your remote-login settings."

    media_pc = next(c for c in results if c.name == "Media-PC")
    assert media_pc.detail["seen_as"] == ["named in your remote-login settings"]

    # Secrets test
    for conn in results:
        dumped = json.dumps(conn.to_dict())
        for secret in ("192.168", "pat", "SECRET_KEY_FILE", "IdentityFile", "HostName", "ForwardAgent"):
            assert secret not in dumped


def test_other_machines_skips_same_machine_case_insensitive() -> None:
    snapshot = {"neighbours": [{"name": "MY-HOST"}]}
    settings_text = "Host my-host otherbox\n"
    fake = FakeMachine(files={"/home/user/.ssh/config": settings_text})
    ctx = LookContext(
        machine="my-host",
        snapshot=snapshot,
        read_text=fake.read_text,
        environ={"HOME": "/home/user"},
        system="Linux",
    )
    results = find_other_machines(ctx)
    names = [c.name for c in results]
    assert "MY-HOST" not in names
    assert "my-host" not in names
    assert names == ["otherbox"]


def test_other_machines_empty_home_never_reads_text() -> None:
    read_text_called = False

    def tracking_read_text(path: Any, limit: int = 200000) -> str:
        nonlocal read_text_called
        read_text_called = True
        return "Host leaked-box\n"

    snapshot = {"neighbours": [{"name": "net-box"}]}
    ctx = LookContext(
        machine="test-machine",
        snapshot=snapshot,
        read_text=tracking_read_text,
        environ={"HOME": "", "USERPROFILE": ""},
        system="Windows",
    )
    results = find_other_machines(ctx)

    assert not read_text_called
    assert len(results) == 1
    assert results[0].name == "net-box"


def test_load_finders_registers_storage_and_other_machines() -> None:
    load_finders()
    assert "storage" in tau.connect.registry.FINDERS
    assert "other_machines" in tau.connect.registry.FINDERS

    storage_spec = tau.connect.registry.FINDERS["storage"]
    assert storage_spec.timeout == 20.0
    assert storage_spec.max_rows == 400

    machines_spec = tau.connect.registry.FINDERS["other_machines"]
    assert machines_spec.timeout == 15.0
    assert machines_spec.max_rows == 300
