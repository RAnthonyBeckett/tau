"""Tests for Windows doors and interfaces finder."""

from __future__ import annotations

import os
from typing import Any

from tau.connect import LookContext
from tau.connect.finders.os_windows import find_windows_doors
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
        registry: dict[tuple[str, str], dict[str, Any]] | None = None,
        run_output: str = "",
    ) -> None:
        self.files = {_norm(k): list(v) for k, v in (files or {}).items()}
        self.registry = {
            (hive.upper(), key.replace("/", "\\").lower()): val
            for (hive, key), val in (registry or {}).items()
        }
        self.run_output = run_output
        self.run_calls: list[tuple[list[str], float]] = []
        self.registry_queries: list[tuple[str, str]] = []
        self.listed_paths: list[str] = []

    def list_dir(self, path: Any, start: bool = False) -> list[str]:
        p_str = str(path)
        self.listed_paths.append(_norm(p_str))
        return list(self.files.get(_norm(p_str), []))

    def registry_read(self, hive: str, key: str) -> dict[str, Any]:
        normalized_key = key.replace("/", "\\").lower()
        self.registry_queries.append((hive.upper(), normalized_key))
        return self.registry.get(
            (hive.upper(), normalized_key),
            {"subkeys": [], "values": {}},
        )

    def run(self, cmd: list[str], timeout: float = 10.0) -> str:
        self.run_calls.append((list(cmd), timeout))
        return self.run_output


def test_linux_returns_empty() -> None:
    ctx = LookContext(machine="test-linux", system="Linux")
    assert find_windows_doors(ctx) == []


def test_section_a_link_types() -> None:
    registry_data = {
        ("HKCR", ""): {
            "subkeys": ["thing", "plain", "http", "mailto", "has.dot", "has space"],
            "values": {},
        },
        ("HKCR", "thing"): {
            "subkeys": [],
            "values": {"URL Protocol": "", "": "URL:Thing Protocol"},
        },
        ("HKCR", "plain"): {
            "subkeys": [],
            "values": {"": "Just Plain"},
        },
        ("HKCR", "http"): {
            "subkeys": [],
            "values": {"URL Protocol": "", "": "URL:Hypertext Transfer Protocol"},
        },
        ("HKCR", "mailto"): {
            "subkeys": [],
            "values": {"URL Protocol": "", "": "URL:MailTo Protocol"},
        },
    }
    fake = FakeMachine(registry=registry_data)
    ctx = LookContext(
        machine="test-win",
        registry_read=fake.registry_read,
        environ={"PSModulePath": "", "SystemRoot": ""},
        system="Windows",
    )
    results = find_windows_doors(ctx)
    link_conns = [c for c in results if c.kind == "Link type that starts a program"]
    names = [c.name for c in link_conns]

    assert "thing://" in names
    assert "mailto://" in names
    assert "plain://" not in names
    assert "http://" not in names

    thing_conn = next(c for c in link_conns if c.name == "thing://")
    assert thing_conn.detail["label"] == "Thing Protocol"
    assert thing_conn.detail["scheme"] == "thing"

    assert ("HKCR", "has.dot") not in fake.registry_queries
    assert ("HKCR", "has space") not in fake.registry_queries


def test_section_b_scripted_programs() -> None:
    registry_data = {
        ("HKCR", ""): {
            "subkeys": [
                "Tool.Application",
                "Tool.Application.16",
                "Other.Application",
                "Tool.Document",
            ],
            "values": {},
        },
        ("HKCR", "Tool.Application"): {
            "subkeys": [],
            "values": {"": "Tool Application Program"},
        },
        ("HKCR", "Tool.Application.16"): {
            "subkeys": ["CLSID"],
            "values": {},
        },
        ("HKCR", "Other.Application"): {
            "subkeys": [],
            "values": {"": "Other Application Program"},
        },
        ("HKCR", "Tool.Document"): {
            "subkeys": ["CLSID"],
            "values": {"": "Tool Document"},
        },
    }
    fake = FakeMachine(registry=registry_data)
    ctx = LookContext(
        machine="test-win",
        registry_read=fake.registry_read,
        environ={"PSModulePath": "", "SystemRoot": ""},
        system="Windows",
    )
    results = find_windows_doors(ctx)
    prog_conns = [c for c in results if c.kind == "Program that accepts scripted commands"]
    names = [c.name for c in prog_conns]

    assert names == ["Tool.Application"]
    conn = prog_conns[0]
    assert conn.detail["prog_id"] == "Tool.Application"
    assert conn.detail["label"] == "Tool Application Program"
    assert "Other.Application" not in names
    assert "Tool.Document" not in names


def test_section_c_file_actions() -> None:
    registry_data = {
        ("HKCR", ""): {
            "subkeys": [".aaa", ".aab", ".bbb", ".ccc"],
            "values": {},
        },
        ("HKCR", ".aaa"): {"subkeys": [], "values": {"": "aaafile"}},
        ("HKCR", ".aab"): {"subkeys": [], "values": {"": "aaafile"}},
        ("HKCR", ".bbb"): {"subkeys": [], "values": {"": "bbbfile"}},
        ("HKCR", ".ccc"): {"subkeys": [], "values": {}},
        ("HKCR", "aaafile"): {"subkeys": [], "values": {"": "AAA File Description"}},
        ("HKCR", "aaafile\\shell"): {"subkeys": ["open", "print", "edit"], "values": {}},
        ("HKCR", "bbbfile"): {"subkeys": [], "values": {"": "BBB File Description"}},
        ("HKCR", "bbbfile\\shell"): {"subkeys": ["open"], "values": {}},
    }
    fake = FakeMachine(registry=registry_data)
    ctx = LookContext(
        machine="test-win",
        registry_read=fake.registry_read,
        environ={"PSModulePath": "", "SystemRoot": ""},
        system="Windows",
    )
    results = find_windows_doors(ctx)
    file_conns = [c for c in results if c.kind == "File type with extra actions"]
    assert len(file_conns) == 1
    conn = file_conns[0]
    assert conn.name == "AAA File Description"
    assert conn.detail["verbs"] == ["edit", "open", "print"]
    assert conn.detail["extensions"] == [".aaa", ".aab"]
    assert conn.detail["handler"] == "aaafile"


def test_section_d_command_sets() -> None:
    base = os.path.abspath(f"{os.sep}tau_test_ps_modules")
    sys_root = os.path.join(base, "SystemRoot")
    sys_folder = os.path.join(sys_root, "System32", "Modules")
    user_folder1 = os.path.join(base, "UserModules1")
    user_folder2 = os.path.join(base, "UserModules2")

    fake_files = {
        sys_folder: ["SysSet"],
        user_folder1: ["SetA", "SetB", "readme.txt"],
        user_folder2: ["SetA"],
    }
    fake = FakeMachine(files=fake_files)
    ps_path = f"{sys_folder};{user_folder1};{user_folder2}"
    ctx = LookContext(
        machine="test-win",
        list_dir=fake.list_dir,
        environ={"PSModulePath": ps_path, "SystemRoot": sys_root},
        system="Windows",
    )
    results = find_windows_doors(ctx)
    cmd_conns = [c for c in results if c.kind == "Command set for the command shell"]
    names = [c.name for c in cmd_conns]

    assert _norm(sys_folder) not in fake.listed_paths
    assert names == ["SetA", "SetB"]
    set_a = next(c for c in cmd_conns if c.name == "SetA")
    assert set_a.detail["folder"] == user_folder1


def test_section_e_scheduled_tasks() -> None:
    csv_output = (
        r'"\Microsoft\Windows\Defrag\ScheduledDefrag","2026-11-16 01:00:00","Ready"' + "\n"
        r'"\MyTasks\Nightly copy","2026-11-16 02:00:00","Running"' + "\n"
        r'"Malformed line with only one field"' + "\n"
    )
    fake = FakeMachine(run_output=csv_output)
    ctx = LookContext(
        machine="test-win",
        run=fake.run,
        environ={"PSModulePath": "", "SystemRoot": ""},
        system="Windows",
    )
    results = find_windows_doors(ctx)
    task_conns = [c for c in results if c.kind == "Scheduled task"]
    assert len(task_conns) == 1
    task = task_conns[0]
    assert task.name == "Nightly copy"
    assert task.detail["path"] == r"\MyTasks\Nightly copy"
    assert task.detail["next_run"] == "2026-11-16 02:00:00"
    assert task.detail["status"] == "Running"

    assert len(fake.run_calls) == 1
    cmd, timeout = fake.run_calls[0]
    assert cmd == ["schtasks", "/query", "/fo", "csv", "/nh"]
    assert timeout == 20.0

    fake_empty = FakeMachine(run_output="")
    ctx_empty = LookContext(
        machine="test-win",
        run=fake_empty.run,
        environ={"PSModulePath": "", "SystemRoot": ""},
        system="Windows",
    )
    results_empty = find_windows_doors(ctx_empty)
    assert [c for c in results_empty if c.kind == "Scheduled task"] == []


def test_section_f_onscreen_controls() -> None:
    ctx = LookContext(
        machine="test-win",
        environ={"PSModulePath": "", "SystemRoot": ""},
        system="Windows",
    )
    results = find_windows_doors(ctx)
    ctrl_conns = [c for c in results if c.kind == "On-screen controls"]
    assert len(ctrl_conns) == 1
    conn = ctrl_conns[0]
    assert conn.name == "Reading and pressing on-screen controls"
    assert conn.door == "accessibility interface"


def test_section_failure_isolation() -> None:
    base = os.path.abspath(f"{os.sep}tau_test_failure_isolation")
    user_folder = os.path.join(base, "Modules")
    fake_files = {user_folder: ["CustomSet"]}
    csv_output = r'"\CustomTasks\Backup","2026-11-16 03:00:00","Ready"' + "\n"
    fake = FakeMachine(files=fake_files, run_output=csv_output)

    def failing_registry_read(hive: str, key: str) -> dict[str, Any]:
        raise RuntimeError("Registry unavailable")

    ctx = LookContext(
        machine="test-win",
        list_dir=fake.list_dir,
        registry_read=failing_registry_read,
        run=fake.run,
        environ={"PSModulePath": user_folder, "SystemRoot": ""},
        system="Windows",
    )
    results = find_windows_doors(ctx)
    kinds = {c.kind for c in results}

    assert "Command set for the command shell" in kinds
    assert "Scheduled task" in kinds
    assert "On-screen controls" in kinds
    assert "Link type that starts a program" not in kinds
    assert "Program that accepts scripted commands" not in kinds
    assert "File type with extra actions" not in kinds


def test_connection_invariants_and_stability() -> None:
    base = os.path.abspath(f"{os.sep}tau_test_invariants")
    user_folder = os.path.join(base, "Modules")
    fake_files = {user_folder: ["MyModule"]}
    csv_output = r'"\Tasks\Sync","2026-11-16 04:00:00","Ready"' + "\n"
    registry_data = {
        ("HKCR", ""): {
            "subkeys": ["sample", "Sample.Application", ".xyz"],
            "values": {},
        },
        ("HKCR", "sample"): {
            "subkeys": [],
            "values": {"URL Protocol": "", "": "URL:Sample Protocol"},
        },
        ("HKCR", "Sample.Application"): {
            "subkeys": ["CLSID"],
            "values": {"": "Sample Application"},
        },
        ("HKCR", ".xyz"): {"subkeys": [], "values": {"": "xyzfile"}},
        ("HKCR", "xyzfile"): {"subkeys": [], "values": {"": "XYZ File"}},
        ("HKCR", "xyzfile\\shell"): {"subkeys": ["open", "custom_action"], "values": {}},
    }
    fake = FakeMachine(files=fake_files, registry=registry_data, run_output=csv_output)
    ctx = LookContext(
        machine="test-win",
        list_dir=fake.list_dir,
        registry_read=fake.registry_read,
        run=fake.run,
        environ={"PSModulePath": user_folder, "SystemRoot": ""},
        system="Windows",
    )

    run1 = find_windows_doors(ctx)
    assert len(run1) == 6

    for conn in run1:
        assert conn.state == "Found"
        assert conn.evidence != ""
        assert conn.allows != ""

    run2 = find_windows_doors(ctx)
    assert [c.id for c in run1] == [c.id for c in run2]


def test_load_finders_registers_windows_doors() -> None:
    load_finders()
    assert "windows_doors" in tau.connect.registry.FINDERS
    spec = tau.connect.registry.FINDERS["windows_doors"]
    assert spec.timeout == 60.0
    assert spec.max_rows == 1500


def test_injected_run_called_exactly_once() -> None:
    fake = FakeMachine(run_output="")
    ctx = LookContext(
        machine="test-win",
        run=fake.run,
        environ={"PSModulePath": "", "SystemRoot": ""},
        system="Windows",
    )
    find_windows_doors(ctx)
    assert len(fake.run_calls) == 1
    cmd, timeout = fake.run_calls[0]
    assert cmd == ["schtasks", "/query", "/fo", "csv", "/nh"]
    assert timeout == 20.0


def test_lookcontext_registry_read_max_subkeys() -> None:
    fake_subkeys = [f"key_{i}" for i in range(100)]

    def custom_read(hive: str, key: str) -> dict[str, Any]:
        return {"subkeys": list(fake_subkeys), "values": {}}

    ctx = LookContext(machine="test-win", registry_read=custom_read, system="Windows")
    res = ctx.registry_read("HKCR", "", max_subkeys=10)
    assert len(res["subkeys"]) == 10
    assert res["subkeys"] == fake_subkeys[:10]

    res_all = ctx.registry_read("HKCR", "", max_subkeys=200)
    assert len(res_all["subkeys"]) == 100

