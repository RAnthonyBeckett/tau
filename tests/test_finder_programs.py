"""Tests for command-line tools and desktop applications finders."""

from __future__ import annotations

import os
from typing import Any

from tau.connect import LookContext, make_id
from tau.connect.finders.cli_tools import find_cli_tools
from tau.connect.finders.desktop_apps import find_installed_programs
import tau.connect.registry
from tau.connect.registry import load_finders


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


def test_cli_tools_windows() -> None:
    base = os.path.abspath(f"{os.sep}tau_test_cli")
    sys_root = os.path.join(base, "SystemRoot")
    os_folder = os.path.join(sys_root, "System32")
    sibling_folder = os.path.join(base, "SystemRootSibling")
    tools1 = os.path.join(base, "Tools1")
    tools2 = os.path.join(base, "Tools2")

    fake_files = {
        os_folder: ["os_tool.exe", "calc.cmd"],
        sibling_folder: ["sibling.exe"],
        tools1: ["alpha.exe", "beta.CMD", "notes.txt", "shared.exe"],
        tools2: ["shared.cmd", "other.exe"],
    }

    fake = FakeMachine(files=fake_files)
    path_entries = [os_folder, tools1, tools1, tools2, sibling_folder]
    path_str = os.pathsep.join(path_entries)

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

    results = find_cli_tools(ctx)
    names = [c.name for c in results]

    assert "os_tool" not in names
    assert "calc" not in names
    assert "sibling" in names
    assert "alpha" in names
    assert "beta" in names
    assert "notes" not in names
    assert "notes.txt" not in names

    shared_conns = [c for c in results if c.name.lower() == "shared"]
    assert len(shared_conns) == 1
    shared = shared_conns[0]
    assert shared.detail["also_in"] == 1
    assert shared.detail["folder"] == tools1


def test_cli_tools_linux() -> None:
    fake_files = {
        "/usr/bin": ["sh", "ls"],
        "/usr/local/bin": ["custom_tool", ".hidden", "lib.so"],
        "/extra/tools": ["special_tool"],
    }
    fake = FakeMachine(files=fake_files)
    ctx = LookContext(
        machine="test-linux",
        list_dir=fake.list_dir,
        environ={"PATH": "/usr/bin:/usr/local/bin:/extra/tools"},
        system="Linux",
    )
    results = find_cli_tools(ctx)
    names = [c.name for c in results]

    assert "sh" not in names
    assert "ls" not in names
    assert ".hidden" not in names
    assert "lib.so" not in names
    assert "custom_tool" in names
    assert "special_tool" in names
    assert len(results) == 2


def test_cli_tools_empty_path() -> None:
    ctx = LookContext(machine="test-machine", environ={"PATH": ""}, system="Windows")
    assert find_cli_tools(ctx) == []


def test_cli_tools_connection_fields() -> None:
    fake = FakeMachine(files={"/tools": ["tool1.exe"]})
    ctx = LookContext(
        machine="test-box",
        list_dir=fake.list_dir,
        environ={"PATH": "/tools", "PATHEXT": ".exe"},
        system="Windows",
    )
    results = find_cli_tools(ctx)
    assert len(results) == 1
    conn = results[0]
    assert conn.state == "Found"
    assert conn.door == "command line"
    assert conn.kind == "Command-line tool"
    assert conn.evidence != ""
    assert conn.allows != ""
    assert conn.id == make_id("test-box", "", "Command-line tool", "tool1")


def test_cli_tools_list_dir_start_depth() -> None:
    base = os.path.abspath(f"{os.sep}tau_test_hierarchy")
    f1 = os.path.join(base, "dir1")
    f2 = os.path.join(f1, "dir2")
    f3 = os.path.join(f2, "dir3")

    fake = FakeMachine(files={
        f1: ["a.exe"],
        f2: ["b.exe"],
        f3: ["c.exe"],
    })
    path_str = os.pathsep.join([f1, f2, f3])
    ctx = LookContext(
        machine="test-box",
        list_dir=fake.list_dir,
        environ={"PATH": path_str, "PATHEXT": ".exe"},
        system="Windows",
    )
    results = find_cli_tools(ctx)
    assert len(results) == 3
    assert ctx.dirs_listed == 3


def test_installed_programs_windows() -> None:
    base = os.path.abspath(f"{os.sep}tau_test_installed")
    prog_data = os.path.join(base, "ProgramData")
    start_menu = os.path.join(prog_data, "Microsoft", "Windows", "Start Menu", "Programs")
    suite_folder = os.path.join(start_menu, "Suite")
    deeper_folder = os.path.join(suite_folder, "DeeperFolder")

    fake_files = {
        start_menu: ["Alpha.lnk", "Suite"],
        suite_folder: ["Beta.lnk", "Uninstall Beta.lnk", "DeeperFolder"],
        deeper_folder: ["Hidden.lnk"],
    }

    registry_data = {
        ("HKLM", "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths"): {
            "subkeys": ["alpha.exe", "gamma.exe"],
            "values": {},
        },
        ("HKLM", "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall"): {
            "subkeys": ["AppDelta", "AppComp", "AppSecUpdate", "AppNoName"],
            "values": {},
        },
        ("HKLM", "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\AppDelta"): {
            "subkeys": [],
            "values": {"DisplayName": "Delta", "DisplayVersion": "1.2", "Publisher": "P"},
        },
        ("HKLM", "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\AppComp"): {
            "subkeys": [],
            "values": {"DisplayName": "Comp", "SystemComponent": 1},
        },
        ("HKLM", "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\AppSecUpdate"): {
            "subkeys": [],
            "values": {"DisplayName": "Security Update for X"},
        },
        ("HKLM", "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\AppNoName"): {
            "subkeys": [],
            "values": {"DisplayVersion": "9.9"},
        },
    }

    fake = FakeMachine(files=fake_files, registry=registry_data)
    ctx = LookContext(
        machine="test-win",
        list_dir=fake.list_dir,
        registry_read=fake.registry_read,
        environ={"ProgramData": prog_data, "APPDATA": ""},
        system="Windows",
    )

    results = find_installed_programs(ctx)
    results_by_name = {c.name.lower(): c for c in results}

    assert _norm(deeper_folder) not in fake.listed_paths

    assert "alpha" in results_by_name
    alpha = results_by_name["alpha"]
    assert alpha.name == "Alpha"
    assert alpha.detail["sources"] == ["start menu", "app paths"]

    assert "beta" in results_by_name
    beta = results_by_name["beta"]
    assert beta.name == "Beta"
    assert beta.detail["sources"] == ["start menu"]

    assert "gamma" in results_by_name
    gamma = results_by_name["gamma"]
    assert gamma.name == "gamma"
    assert gamma.detail["sources"] == ["app paths"]

    assert "delta" in results_by_name
    delta = results_by_name["delta"]
    assert delta.name == "Delta"
    assert delta.detail["sources"] == ["installed list"]
    assert delta.detail.get("version") == "1.2"
    assert delta.detail.get("publisher") == "P"

    assert "uninstall beta" not in results_by_name
    assert "comp" not in results_by_name
    assert "security update for x" not in results_by_name
    assert "appnoname" not in results_by_name
    assert len(results) == 4


def test_installed_programs_darwin() -> None:
    fake = FakeMachine(files={
        "/Applications": ["One.app", "notes.txt"],
        "/System/Applications": [],
    })
    ctx = LookContext(
        machine="test-mac",
        list_dir=fake.list_dir,
        environ={"HOME": ""},
        system="Darwin",
    )
    results = find_installed_programs(ctx)
    assert len(results) == 1
    assert results[0].name == "One"
    assert results[0].detail["sources"] == ["applications folder"]
    assert results[0].evidence == "It is in the Applications folder."


def test_installed_programs_linux() -> None:
    desktop_viewer = (
        "[Desktop Entry]\n"
        "Name=Viewer\n"
        "Name[fr]=Visionneur\n"
        'Exec="/usr/bin/viewer" %f\n'
        "Actions=new-window;print;\n"
        "Type=Application\n"
        "\n"
        "[Desktop Action print]\n"
        "Name=Print\n"
        "Exec=/usr/bin/viewer --print\n"
    )
    desktop_nodisplay = (
        "[Desktop Entry]\n"
        "Name=HiddenApp\n"
        "NoDisplay=true\n"
        "Type=Application\n"
    )
    desktop_link = (
        "[Desktop Entry]\n"
        "Name=LinkApp\n"
        "Type=Link\n"
    )

    app_dir = "/usr/share/applications"
    fake_files = {
        app_dir: ["viewer.desktop", "hidden.desktop", "link.desktop"],
        "/usr/local/share/applications": [],
    }
    fake_texts = {
        os.path.join(app_dir, "viewer.desktop"): desktop_viewer,
        os.path.join(app_dir, "hidden.desktop"): desktop_nodisplay,
        os.path.join(app_dir, "link.desktop"): desktop_link,
    }

    fake = FakeMachine(files=fake_files, texts=fake_texts)
    ctx = LookContext(
        machine="test-linux",
        list_dir=fake.list_dir,
        read_text=fake.read_text,
        environ={"XDG_DATA_DIRS": "/usr/share", "XDG_DATA_HOME": "", "HOME": ""},
        system="Linux",
    )

    results = find_installed_programs(ctx)
    assert len(results) == 1
    viewer = results[0]
    assert viewer.name == "Viewer"
    assert viewer.detail["command"] == "/usr/bin/viewer"
    assert viewer.detail["actions"] == ["new-window", "print"]
    assert viewer.detail["sources"] == ["desktop entry"]
    assert viewer.evidence == "It has a desktop entry, so it appears in the applications menu."


def test_load_finders_registers_both() -> None:
    load_finders()
    assert "command_line_tools" in tau.connect.registry.FINDERS
    assert "installed_programs" in tau.connect.registry.FINDERS
