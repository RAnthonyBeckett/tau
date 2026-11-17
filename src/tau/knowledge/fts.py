"""Per-folder full-text index in SQLite FTS5 (opt-in per folder).

Files are read one at a time and split into chunks; the index lives on disk
and is queried there. Nothing loads a knowledge base into RAM whole. Folders
on drives marked never-walk are refused.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tau import paths

TEXT_EXT = {".md", ".txt", ".rst", ".org", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml",
            ".ini", ".cfg", ".csv", ".html", ".css", ".sh", ".ps1", ".bat", ".c", ".h", ".cpp", ".rs",
            ".go", ".java", ".cs", ".sql", ".tex", ".log"}
MAX_FILE = 2 * 1024 * 1024
CHUNK = 2000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", "dist", "build"}


class FTSIndex:
    def __init__(self, db: Optional[Path] = None):
        self.db = Path(db) if db else paths.state_dir() / "fts.sqlite"
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
                      "path UNINDEXED, folder UNINDEXED, chunk UNINDEXED, body, tokenize='porter unicode61')")
            c.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, folder TEXT, mtime REAL, size INT)")

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db))

    def index_folder(self, folder: Path, never_walk: Iterable[str] = ()) -> Dict[str, int]:
        from tau.senses.drives import may_walk

        folder = Path(folder).expanduser().resolve()
        if not may_walk(str(folder), list(never_walk)):
            raise PermissionError(f"{folder} is on a drive TAU never walks")
        added = skipped = 0
        seen = set()
        with self._conn() as c:
            for root, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
                for name in files:
                    p = Path(root) / name
                    if p.suffix.lower() not in TEXT_EXT:
                        continue
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    if st.st_size > MAX_FILE:
                        continue
                    key = str(p)
                    seen.add(key)
                    row = c.execute("SELECT mtime, size FROM files WHERE path=?", (key,)).fetchone()
                    if row and row[0] == st.st_mtime and row[1] == st.st_size:
                        skipped += 1
                        continue
                    c.execute("DELETE FROM chunks WHERE path=?", (key,))
                    for i, body in enumerate(_chunks(p)):
                        c.execute("INSERT INTO chunks(path, folder, chunk, body) VALUES (?,?,?,?)",
                                  (key, str(folder), i, body))
                    c.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)",
                              (key, str(folder), st.st_mtime, st.st_size))
                    added += 1
            gone = [r[0] for r in c.execute("SELECT path FROM files WHERE folder=?", (str(folder),))
                    if r[0] not in seen]
            for g in gone:
                c.execute("DELETE FROM chunks WHERE path=?", (g,))
                c.execute("DELETE FROM files WHERE path=?", (g,))
        return {"indexed": added, "unchanged": skipped, "removed": len(gone)}

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        q = " ".join(f'"{w}"' for w in query.replace('"', " ").split() if w)
        if not q:
            return []
        with self._conn() as c:
            rows = c.execute("SELECT path, chunk, snippet(chunks, 3, '[', ']', ' ... ', 12), bm25(chunks) "
                             "FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT ?",
                             (q.replace('" "', '" OR "'), limit)).fetchall()
        return [{"source": "fts", "path": r[0], "chunk": r[1], "snippet": r[2], "score": -r[3]} for r in rows]


def _chunks(p: Path) -> Iterable[str]:
    buf = ""
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            buf += line
            if len(buf) >= CHUNK:
                yield buf
                buf = ""
    if buf.strip():
        yield buf
