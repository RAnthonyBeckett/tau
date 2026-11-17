"""Tests for connection view grouping, folding, and summary display."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tau.connect.view import (
    KIND_ORDER,
    find_ids,
    fold,
    is_noise,
    program_key,
    summary_lines,
    tree,
)


def make_conn(
    id: str = "c1",
    machine: str = "box",
    place: str = "",
    kind: str = "Command-line tool",
    name: str = "tool",
    state: str = "Found",
    setting: str = "off",
    evidence: str = "e",
    allows: str = "a",
    needs: str = "",
    door: str = "d",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a connection dictionary for testing view logic."""
    return {
        "id": id,
        "machine": machine,
        "place": place,
        "kind": kind,
        "name": name,
        "state": state,
        "setting": setting,
        "evidence": evidence,
        "allows": allows,
        "needs": needs,
        "door": door,
        "detail": dict(detail) if detail is not None else {},
    }


def test_is_noise() -> None:
    assert is_noise(make_conn(kind="Command-line tool", name="libthing.so.1")) is True
    assert is_noise(make_conn(kind="Command-line tool", name="libthing.so")) is True
    assert is_noise(make_conn(kind="Command-line tool", name="x.dll")) is True
    assert is_noise(make_conn(kind="Command-line tool", name="tool")) is False

    assert is_noise(make_conn(kind="Installed program", name="Uninstall Thing")) is True
    assert is_noise(make_conn(kind="Installed program", name="Thing Documentation")) is True

    assert is_noise(make_conn(kind="Command-line tool", name="x.dll", setting="sense")) is False
    assert is_noise(make_conn(kind="Installed program", name="Uninstall Thing", setting="sense")) is False

    # Drive with zero free/total space is not treated as noise
    assert is_noise(make_conn(kind="Drive", name="D:", detail={"total_gb": 0, "free_gb": 0})) is False


def test_program_key() -> None:
    assert program_key("Thing 64bit - E-book management") == "thing"
    assert program_key("Thing 3.7.9") == "thing"
    assert program_key("Thing (x64 edition)") == "thing"
    assert program_key("7-Zip 26.01 (x64 edition)") == "7-zip"
    assert program_key("123") == "123"


def test_fold_command_line_tools() -> None:
    conns = [
        make_conn(id=f"a{i}", kind="Command-line tool", name=f"tool_a_{i}", detail={"folder": "A"})
        for i in range(5)
    ] + [
        make_conn(id=f"b{i}", kind="Command-line tool", name=f"tool_b_{i}", detail={"folder": "B"})
        for i in range(2)
    ]
    rows = fold(conns)
    bundle = next((r for r in rows if r["type"] == "bundle"), None)
    assert bundle is not None
    assert bundle["name"] == "5 commands in A"
    assert bundle["count"] == 5
    assert bundle["on"] == 0
    item_names = [it["name"] for it in bundle["items"]]
    assert item_names == sorted(item_names, key=lambda s: s.lower())

    singles = [r for r in rows if r["type"] == "one"]
    assert len(singles) == 2
    assert {s["name"] for s in singles} == {"tool_b_0", "tool_b_1"}


def test_fold_installed_programs() -> None:
    c1 = make_conn(id="p1", kind="Installed program", name="Thing")
    c2 = make_conn(id="p2", kind="Installed program", name="Thing 64bit")
    c3 = make_conn(id="p3", kind="Installed program", name="Thing 1.2", setting="suggest", state="On")

    rows = fold([c1, c2, c3])
    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "one"
    assert row["name"] == "Thing"
    assert row["detail"]["also_listed_as"] == ["Thing 1.2", "Thing 64bit"]
    assert row["detail"]["ids"] == ["p1", "p2", "p3"]
    assert row["setting"] == "suggest"
    assert row["state"] == "On"


def test_fold_file_types() -> None:
    conns = [
        make_conn(id=f"p{i}", kind="File type with extra actions", name=f"Ext {i} File (Player)")
        for i in range(4)
    ] + [
        make_conn(id=f"o{i}", kind="File type with extra actions", name=f"Other {i} File (Other)")
        for i in range(3)
    ]
    rows = fold(conns)
    bundles = [r for r in rows if r["type"] == "bundle"]
    singles = [r for r in rows if r["type"] == "one"]
    assert len(bundles) == 1
    assert bundles[0]["name"] == "4 file types handled by Player"
    assert bundles[0]["count"] == 4
    assert len(singles) == 3


def test_fold_link_types() -> None:
    conns = [
        make_conn(
            id=f"m{i}",
            kind="Link type that starts a program",
            name=f"ms-{c}://",
            detail={"scheme": f"ms-{c}"},
        )
        for i, c in enumerate(["a", "b", "c", "d", "e"])
    ] + [
        make_conn(id="t1", kind="Link type that starts a program", name="thing://"),
        make_conn(id="ox", kind="Link type that starts a program", name="other-x://"),
    ]
    rows = fold(conns)
    bundles = [r for r in rows if r["type"] == "bundle"]
    singles = [r for r in rows if r["type"] == "one"]
    assert len(bundles) == 1
    assert bundles[0]["name"] == "5 link types starting with ms-"
    assert len(singles) == 2
    assert {s["name"] for s in singles} == {"thing://", "other-x://"}


def test_fold_ordering() -> None:
    c_off = make_conn(id="c_off", kind="Other kind", name="Alpha Off", setting="off")
    c_on = make_conn(id="c_on", kind="Other kind", name="Beta On", setting="sense")

    rows_single = fold([c_off, c_on])
    assert [r["name"] for r in rows_single] == ["Beta On", "Alpha Off"]

    b1_items = [
        make_conn(
            id=f"b1_{i}",
            kind="Command-line tool",
            name=f"tool1_{i}",
            detail={"folder": "F1"},
            setting="off",
        )
        for i in range(4)
    ]
    b2_items = [
        make_conn(
            id=f"b2_{i}",
            kind="Command-line tool",
            name=f"tool2_{i}",
            detail={"folder": "F2"},
            setting="act" if i == 0 else "off",
        )
        for i in range(4)
    ]
    rows_bundle = fold(b1_items + b2_items)
    bundle_names = [r["name"] for r in rows_bundle if r["type"] == "bundle"]
    assert bundle_names == ["4 commands in F2", "4 commands in F1"]


def test_tree() -> None:
    conn_model = make_conn(
        id="c_model",
        machine="box-one",
        place="",
        kind="Model server",
        name="Local Server",
        setting="off",
    )
    conn_tool = make_conn(
        id="c_tool",
        machine="box-one",
        place="Work-Env",
        kind="Command-line tool",
        name="cli-tool",
        setting="sense",
    )
    conn_noise1 = make_conn(
        id="c_noise1",
        machine="box-one",
        place="",
        kind="Command-line tool",
        name="libtest.so.2",
    )

    conn_prog = make_conn(
        id="c_prog",
        machine="box-two",
        place="",
        kind="Installed program",
        name="My App",
        setting="off",
    )
    conn_zeta = make_conn(
        id="c_zeta",
        machine="box-two",
        place="",
        kind="Zeta thing",
        name="Zeta Component",
        setting="off",
    )
    conn_noise2 = make_conn(
        id="c_noise2",
        machine="box-two",
        place="",
        kind="Installed program",
        name="Uninstall My App",
    )

    looks = {
        "box-one": {
            "machine": "box-one",
            "at": "2026-11-15T12:00:00Z",
            "connections": [conn_model, conn_tool, conn_noise1],
            "finders": {"a": {"stopped_early": True}, "b": {"stopped_early": False}},
            "note": "Look finished with notes.",
        },
        "box-two": {
            "machine": "box-two",
            "at": "2026-11-15T12:05:00Z",
            "connections": [conn_prog, conn_zeta, conn_noise2],
            "finders": {},
        },
    }

    res = tree(looks)
    assert len(res) == 2

    m1 = res[0]
    assert m1["machine"] == "box-one"
    assert m1["found"] == 2
    assert m1["on"] == 1
    assert m1["stopped_early"] == ["a"]
    assert m1["note"] == "Look finished with notes."
    assert len(m1["places"]) == 2

    p1_root = m1["places"][0]
    assert p1_root["place"] == ""
    assert p1_root["title"] == "This machine"
    assert p1_root["found"] == 1
    assert p1_root["on"] == 0
    assert len(p1_root["kinds"]) == 1
    assert p1_root["kinds"][0]["kind"] == "Model server"
    assert p1_root["kinds"][0]["found"] == 1
    assert p1_root["kinds"][0]["on"] == 0

    p1_work = m1["places"][1]
    assert p1_work["place"] == "Work-Env"
    assert p1_work["title"] == "Work-Env"
    assert p1_work["found"] == 1
    assert p1_work["on"] == 1
    assert len(p1_work["kinds"]) == 1
    assert p1_work["kinds"][0]["kind"] == "Command-line tool"
    assert p1_work["kinds"][0]["found"] == 1
    assert p1_work["kinds"][0]["on"] == 1

    m2 = res[1]
    assert m2["machine"] == "box-two"
    assert m2["found"] == 2
    assert m2["on"] == 0
    assert m2["stopped_early"] == []
    assert m2["note"] == ""
    assert len(m2["places"]) == 1

    p2_root = m2["places"][0]
    assert p2_root["place"] == ""
    assert p2_root["title"] == "This machine"
    assert p2_root["found"] == 2
    assert p2_root["on"] == 0
    kind_names = [k["kind"] for k in p2_root["kinds"]]
    assert kind_names == ["Installed program", "Zeta thing"]


def test_tree_filtering() -> None:
    conn_on = make_conn(
        id="c1",
        machine="box-one",
        place="Work-Env",
        kind="Command-line tool",
        name="tool",
        setting="sense",
    )
    conn_off = make_conn(
        id="c2",
        machine="box-one",
        place="",
        kind="Model server",
        name="server",
        setting="off",
    )
    conn_two = make_conn(
        id="c3",
        machine="box-two",
        place="",
        kind="Installed program",
        name="app",
        setting="off",
    )

    looks = {
        "box-one": {
            "machine": "box-one",
            "at": "2026-11-15T12:00:00Z",
            "connections": [conn_on, conn_off],
        },
        "box-two": {
            "machine": "box-two",
            "at": "2026-11-15T12:00:00Z",
            "connections": [conn_two],
        },
    }

    res_only_on = tree(looks, only_on=True)
    assert len(res_only_on) == 1
    assert res_only_on[0]["machine"] == "box-one"
    assert len(res_only_on[0]["places"]) == 1
    assert res_only_on[0]["places"][0]["place"] == "Work-Env"

    c_match_name = make_conn(
        id="m_name",
        machine="box-one",
        name="Workshop Builder",
        evidence="local",
        kind="Tool",
    )
    c_match_kind = make_conn(
        id="m_kind",
        machine="box-one",
        name="Helper",
        evidence="local",
        kind="Worker Assistant",
    )
    c_match_evidence = make_conn(
        id="m_ev",
        machine="box-two",
        name="Service",
        evidence="Found in network workgroup",
        kind="Service",
    )
    c_nomatch = make_conn(
        id="nomatch",
        machine="box-two",
        name="Other",
        evidence="plain",
        kind="App",
    )

    text_looks = {
        "box-one": {
            "machine": "box-one",
            "connections": [c_match_name, c_match_kind],
        },
        "box-two": {
            "machine": "box-two",
            "connections": [c_match_evidence, c_nomatch],
        },
    }

    res_text = tree(text_looks, text="work")
    assert len(res_text) == 2
    assert res_text[0]["found"] == 2
    assert res_text[1]["found"] == 1


def test_summary_lines() -> None:
    assert summary_lines({}) == ["TAU hasn't looked at any machine yet."]

    looks = {
        "box-b": {
            "machine": "box-b",
            "connections": [
                make_conn(id="b1", machine="box-b", setting="sense"),
                make_conn(id="b2", machine="box-b", setting="off"),
                make_conn(id="b3", machine="box-b", kind="Command-line tool", name="noise.dll"),
            ],
            "finders": {"f1": {"stopped_early": True}},
            "note": "Machine B note text.",
        },
        "box-a": {
            "machine": "box-a",
            "connections": [
                make_conn(id="a1", machine="box-a", setting="off"),
            ],
            "finders": {"f1": {"stopped_early": True}, "f2": {"stopped_early": True}},
        },
    }

    lines = summary_lines(looks)
    expected = [
        "box-a: 1 ways to connect, 0 switched on.",
        "box-a: 2 parts of the look stopped early.",
        "box-b: 2 ways to connect, 1 switched on.",
        "Machine B note text.",
        "box-b: 1 part of the look stopped early.",
    ]
    assert lines == expected


def test_find_ids() -> None:
    c1 = make_conn(id="p1", kind="Installed program", name="Thing")
    c2 = make_conn(id="p2", kind="Installed program", name="Thing 64bit")
    c3 = make_conn(id="p3", kind="Installed program", name="Thing 1.2")
    merged_rows = fold([c1, c2, c3])
    assert len(merged_rows) == 1
    assert find_ids(merged_rows[0]) == ["p1", "p2", "p3"]

    tools = [
        make_conn(id=f"t{i}", kind="Command-line tool", name=f"tool_{i}", detail={"folder": "F"})
        for i in range(4)
    ]
    bundle_rows = fold(tools)
    assert len(bundle_rows) == 1
    assert find_ids(bundle_rows[0]) == ["t0", "t1", "t2", "t3"]


def test_no_software_names() -> None:
    target = Path(__file__).resolve().parent.parent / "src" / "tau" / "connect" / "view.py"
    content = target.read_text(encoding="utf-8").lower()

    software_products = [
        "chrome",
        "firefox",
        "photoshop",
        "slack",
        "discord",
        "spotify",
        "excel",
        "powerpoint",
        "vlc",
        "zoom",
    ]
    for product in software_products:
        assert product not in content, f"Software product '{product}' found in view.py"
