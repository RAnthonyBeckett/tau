"""Inspect and search SQLite catalogue files strictly read-only."""

from __future__ import annotations

import pathlib
import sqlite3
import time
from typing import Any, Dict, List, Optional

from tau.connect.record import Connection, make_id

MAX_ROWS = 50
MAX_CELL = 300


def _quote_ident(ident: str) -> str:
    """Return a SQL identifier wrapped in double quotes with double quotes doubled."""
    return '"' + ident.replace('"', '""') + '"'


def _open_read_only(path: Any) -> sqlite3.Connection:
    """Open an existing SQLite database strictly read-only."""
    p = pathlib.Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    address = p.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(address, uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _with_limit(conn: sqlite3.Connection, seconds: float) -> None:
    """Install a progress handler that aborts statement execution when deadline passes."""
    deadline = time.monotonic() + seconds

    def handler() -> int:
        return 1 if time.monotonic() > deadline else 0

    conn.set_progress_handler(handler, 1000)


def describe(path: Any, count_seconds: float = 2.0) -> Dict[str, Any]:
    """Return table names, columns, and row counts for a catalogue file."""
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _open_read_only(path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        table_names = [str(row[0]) for row in cur.fetchall() if not str(row[0]).startswith("sqlite_")][:200]
        tables: List[Dict[str, Any]] = []
        for name in table_names:
            quoted = _quote_ident(name)
            cur.execute(f"PRAGMA table_info({quoted})")
            columns = [str(r[1]) for r in cur.fetchall()][:200]

            rows: Optional[int] = None
            try:
                _with_limit(conn, count_seconds)
                cur.execute(f"SELECT COUNT(*) FROM {quoted}")
                count_row = cur.fetchone()
                if count_row is not None and count_row[0] is not None:
                    rows = int(count_row[0])
            except Exception:
                rows = None
            finally:
                try:
                    conn.set_progress_handler(None, 0)
                except Exception:
                    pass
            tables.append({"name": name, "columns": columns, "rows": rows})
        return {"ok": True, "tables": tables}
    except FileNotFoundError:
        return {"ok": False, "say": "That file isn't there any more."}
    except sqlite3.DatabaseError:
        return {"ok": False, "say": "TAU can't read that file as a catalogue."}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def search(
    path: Any,
    table: str,
    column: str,
    text: str,
    limit: int = 20,
    seconds: float = 5.0,
) -> Dict[str, Any]:
    """Look up rows in a table where a column contains the given search text."""
    desc = describe(path)
    if not desc.get("ok"):
        return {"ok": False, "say": "That table or column isn't in the catalogue."}

    target_table: Optional[Dict[str, Any]] = None
    for t in desc.get("tables", []):
        if t.get("name") == table:
            target_table = t
            break

    if target_table is None or column not in target_table.get("columns", []):
        return {"ok": False, "say": "That table or column isn't in the catalogue."}

    if not isinstance(text, str) or len(text) == 0 or len(text) > 200:
        return {"ok": False, "say": "Give me a few words to look for."}

    actual_limit = max(1, min(int(limit), MAX_ROWS))
    escaped_text = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped_text}%"
    sql = f"SELECT * FROM {_quote_ident(table)} WHERE {_quote_ident(column)} LIKE ? ESCAPE '\\' LIMIT ?"

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _open_read_only(path)
        _with_limit(conn, seconds)
        cur = conn.cursor()
        rows_collected: List[Any] = []
        stopped_early = False
        try:
            cur.execute(sql, (pattern, actual_limit))
            col_names = [col[0] for col in cur.description] if cur.description else target_table["columns"]
            for row in cur:
                rows_collected.append(row)
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                stopped_early = True
                col_names = [col[0] for col in cur.description] if cur.description else target_table["columns"]
            else:
                return {"ok": False, "say": "TAU couldn't search that catalogue."}

        formatted_rows: List[Dict[str, Any]] = []
        for row in rows_collected:
            row_dict: Dict[str, Any] = {}
            for col, val in zip(col_names, row):
                if isinstance(val, str):
                    row_dict[col] = val[:MAX_CELL]
                elif isinstance(val, (bytes, bytearray, memoryview)):
                    row_dict[col] = f"(binary, {len(val)} bytes)"
                else:
                    row_dict[col] = val
            formatted_rows.append(row_dict)

        return {
            "ok": True,
            "rows": formatted_rows,
            "more": len(rows_collected) == actual_limit,
            "stopped_early": stopped_early,
        }
    except (sqlite3.DatabaseError, FileNotFoundError):
        return {"ok": False, "say": "TAU couldn't search that catalogue."}
    finally:
        if conn is not None:
            try:
                conn.set_progress_handler(None, 0)
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass


def as_connection(machine: str, path: Any, label: str = "") -> Connection:
    """Describe the catalogue file as a Connection record."""
    desc = describe(path)
    file_path = pathlib.Path(path)
    name = label if label else (file_path.name or str(path))
    allows = "TAU could look things up in it to learn what is where. It is opened read-only and never changed."
    conn_id = make_id(machine, "", "Catalogue file", str(file_path).lower())

    if desc.get("ok"):
        tables = desc.get("tables", [])
        n = len(tables)
        unit = "table" if n == 1 else "tables"
        evidence = f"You pointed TAU at this file. It holds {n} {unit}."
        return Connection(
            id=conn_id,
            machine=machine,
            kind="Catalogue file",
            name=name,
            evidence=evidence,
            door="read-only lookup",
            state="Ready",
            allows=allows,
            detail={"path": str(path), "tables": tables[:30]},
        )
    else:
        needs = desc.get("say", "")
        evidence = "You pointed TAU at this file."
        return Connection(
            id=conn_id,
            machine=machine,
            kind="Catalogue file",
            name=name,
            evidence=evidence,
            door="read-only lookup",
            state="Needs something",
            needs=needs,
            allows=allows,
            detail={"path": str(path), "tables": []},
        )
