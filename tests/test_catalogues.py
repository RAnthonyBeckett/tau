"""Tests for catalogue database inspection and search."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, Dict

import pytest

from tau.connect import catalogues


def make_test_db(db_path: Path) -> Path:
    """Create a real small SQLite database for testing."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE files (path TEXT, size INTEGER, blob BLOB)")
    cur.execute("CREATE TABLE notes (text TEXT)")

    long_path = "a" * 500
    rows = [
        ("docs/readme.txt", 100, None),
        ("photos/vacation.jpg", 2048, None),
        ("archive/project_100%_done.zip", 5000, None),
        (long_path, 10, None),
        ("data/blob.bin", 50, b"\x00\x01\x02\x03\x04\x05"),
    ]
    cur.executemany("INSERT INTO files VALUES (?, ?, ?)", rows)
    cur.execute("INSERT INTO notes VALUES ('first note')")
    cur.execute("INSERT INTO notes VALUES ('second note')")
    conn.commit()
    conn.close()
    return db_path


def test_describe_lists_tables_columns_rows(tmp_path: Path) -> None:
    db_path = make_test_db(tmp_path / "test.db")
    res = catalogues.describe(db_path)

    assert res["ok"] is True
    tables = res["tables"]
    assert len(tables) == 2

    assert tables[0]["name"] == "files"
    assert tables[0]["columns"] == ["path", "size", "blob"]
    assert tables[0]["rows"] == 5

    assert tables[1]["name"] == "notes"
    assert tables[1]["columns"] == ["text"]
    assert tables[1]["rows"] == 2


def test_search_finds_cuts_long_and_formats_blob(tmp_path: Path) -> None:
    db_path = make_test_db(tmp_path / "test.db")

    res = catalogues.search(db_path, "files", "path", "readme")
    assert res["ok"] is True
    assert len(res["rows"]) == 1
    assert res["rows"][0]["path"] == "docs/readme.txt"
    assert res["rows"][0]["size"] == 100
    assert res["more"] is False
    assert res["stopped_early"] is False

    res_long = catalogues.search(db_path, "files", "path", "aaaaa")
    assert res_long["ok"] is True
    assert len(res_long["rows"]) == 1
    assert len(res_long["rows"][0]["path"]) == 300
    assert res_long["rows"][0]["path"] == "a" * 300

    res_blob = catalogues.search(db_path, "files", "path", "blob.bin")
    assert res_blob["ok"] is True
    assert len(res_blob["rows"]) == 1
    assert res_blob["rows"][0]["blob"] == "(binary, 6 bytes)"


def test_search_wildcards_taken_literally(tmp_path: Path) -> None:
    db_path = make_test_db(tmp_path / "test.db")

    res_exact = catalogues.search(db_path, "files", "path", "100%_done")
    assert res_exact["ok"] is True
    assert len(res_exact["rows"]) == 1
    assert res_exact["rows"][0]["path"] == "archive/project_100%_done.zip"

    res_pct = catalogues.search(db_path, "files", "path", "%")
    assert res_pct["ok"] is True
    assert len(res_pct["rows"]) == 1
    assert res_pct["rows"][0]["path"] == "archive/project_100%_done.zip"


def test_search_invalid_inputs_refused(tmp_path: Path) -> None:
    db_path = make_test_db(tmp_path / "test.db")

    res_tbl = catalogues.search(db_path, "missing_table", "path", "foo")
    assert res_tbl["ok"] is False
    assert res_tbl["say"] == "That table or column isn't in the catalogue."

    res_col = catalogues.search(db_path, "files", "missing_col", "foo")
    assert res_col["ok"] is False
    assert res_col["say"] == "That table or column isn't in the catalogue."

    res_empty = catalogues.search(db_path, "files", "path", "")
    assert res_empty["ok"] is False
    assert res_empty["say"] == "Give me a few words to look for."

    res_too_long = catalogues.search(db_path, "files", "path", "x" * 201)
    assert res_too_long["ok"] is False
    assert res_too_long["say"] == "Give me a few words to look for."


def test_search_sql_injection_attempt(tmp_path: Path) -> None:
    db_path = make_test_db(tmp_path / "test.db")

    res1 = catalogues.search(db_path, "files", "path", "x' OR 1=1 --")
    assert res1["ok"] is True
    assert len(res1["rows"]) == 0

    res2 = catalogues.search(db_path, "files", "path", '"; DROP TABLE files; --')
    assert res2["ok"] is True
    assert len(res2["rows"]) == 0

    desc = catalogues.describe(db_path)
    assert desc["ok"] is True
    assert desc["tables"][0]["rows"] == 5


def test_file_never_changed_and_read_only(tmp_path: Path) -> None:
    db_path = make_test_db(tmp_path / "test.db")
    before_bytes = db_path.read_bytes()

    catalogues.describe(db_path)
    catalogues.search(db_path, "files", "path", "readme")
    catalogues.search(db_path, "files", "path", "vacation")
    catalogues.search(db_path, "notes", "text", "note")

    after_bytes = db_path.read_bytes()
    assert before_bytes == after_bytes

    conn = catalogues._open_read_only(db_path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO notes VALUES ('x')")
    conn.close()


def test_missing_file_refused_and_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    res = catalogues.describe(missing)

    assert res["ok"] is False
    assert res["say"] == "That file isn't there any more."
    assert not missing.exists()


def test_non_database_file_refused(tmp_path: Path) -> None:
    text_file = tmp_path / "plain.txt"
    text_file.write_text("Not a database file.", encoding="utf-8")

    res = catalogues.describe(text_file)
    assert res["ok"] is False
    assert res["say"] == "TAU can't read that file as a catalogue."


def test_limit_and_more_flag(tmp_path: Path) -> None:
    db_path = make_test_db(tmp_path / "test.db")

    res = catalogues.search(db_path, "files", "path", ".", limit=2)
    assert res["ok"] is True
    assert len(res["rows"]) == 2
    assert res["more"] is True


def test_time_limit_stops_early(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    big_db = tmp_path / "big.db"
    conn = sqlite3.connect(big_db)
    conn.execute("CREATE TABLE big (col TEXT)")
    conn.executemany("INSERT INTO big VALUES (?)", [(f"val_{i}",) for i in range(20000)])
    conn.commit()
    conn.close()

    current_time = [100.0]

    def fake_monotonic() -> float:
        t = current_time[0]
        current_time[0] += 50.0
        return t

    monkeypatch.setattr(catalogues.time, "monotonic", fake_monotonic)

    res_search = catalogues.search(big_db, "big", "col", "zzz-no-such-text")  # no match, so the whole table is read
    assert res_search["ok"] is True
    assert res_search["stopped_early"] is True

    res_desc = catalogues.describe(big_db)
    assert res_desc["ok"] is True
    # counting rows is a quick operation for the database, so it may finish before the limit is checked
    assert res_desc["tables"][0]["rows"] in (None, 20000)


def test_as_connection_ready_and_needs_something(tmp_path: Path) -> None:
    db_path = make_test_db(tmp_path / "test.db")

    conn_ready = catalogues.as_connection("den-pc", db_path, label="Drive Inventory")
    assert conn_ready.state == "Ready"
    assert conn_ready.kind == "Catalogue file"
    assert conn_ready.name == "Drive Inventory"
    assert conn_ready.door == "read-only lookup"
    assert "2 tables" in conn_ready.evidence
    table_names = [t["name"] for t in conn_ready.detail.get("tables", [])]
    assert "files" in table_names
    assert "notes" in table_names

    missing_path = tmp_path / "missing.db"
    conn_missing = catalogues.as_connection("den-pc", missing_path)
    assert conn_missing.state == "Needs something"
    assert conn_missing.needs == "That file isn't there any more."
    assert conn_missing.evidence == "You pointed TAU at this file."
