"""Tests for adding notes to a shared folder."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from tau.connect.notes import MAX_NOTE, add_note


class FakeLog:
    """Record receipt calls for verification in tests."""

    def __init__(self, explode: bool = False) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.explode = explode

    def receipt(
        self,
        what: str,
        who: str,
        organ: str = "notes",
        machine: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self.explode:
            raise RuntimeError("RECEIPT_EXPLOSION")
        call = {
            "what": what,
            "who": who,
            "organ": organ,
            "machine": machine,
        }
        self.calls.append(call)
        return call


def test_add_note_creates_file_with_front_block(tmp_path: Path) -> None:
    res = add_note(tmp_path, "My First Note", "Body of the note.", "alice")

    assert res["ok"] is True
    assert res["say"] == "Added the note."
    path = Path(res["path"])
    assert path.is_file()
    assert path.name.endswith("_alice_my-first-note.md")

    content = path.read_text(encoding="utf-8")
    assert content.startswith("---")
    assert "title: My First Note\n" in content
    assert "written_by: alice\n" in content
    assert "written_at: " in content
    assert "added_by: TAU\n" in content
    assert "Body of the note." in content
    assert content.endswith("\n")


def test_never_overwrites(tmp_path: Path) -> None:
    unrelated = tmp_path / "existing.txt"
    unrelated.write_bytes(b"existing content")
    unrelated_bytes = unrelated.read_bytes()

    fixed_now = datetime.datetime(2026, 11, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
    res1 = add_note(tmp_path, "Fixed Note", "Initial body", "bob", now=fixed_now)
    assert res1["ok"] is True
    created_path = Path(res1["path"])
    created_bytes = created_path.read_bytes()

    res2 = add_note(tmp_path, "Fixed Note", "New body trying to overwrite", "bob", now=fixed_now)
    assert res2["ok"] is False
    assert res2["path"] == ""
    assert "Nothing was changed" in res2["say"]

    assert created_path.read_bytes() == created_bytes
    assert unrelated.read_bytes() == unrelated_bytes


def test_path_traversal_title_contained(tmp_path: Path) -> None:
    evil_title = r"../../outside\..\evil"
    res = add_note(tmp_path, evil_title, "Safe body content", "alice")

    assert res["ok"] is True
    created_path = Path(res["path"])
    assert created_path.is_file()
    assert created_path.parent == tmp_path

    assert not (tmp_path.parent / "outside").exists()
    assert not (tmp_path.parent / "evil").exists()


def test_line_breaks_cannot_inject_front_block_lines(tmp_path: Path) -> None:
    malicious_title = "a\nadded_by: someone-else"
    res = add_note(tmp_path, malicious_title, "Note content", "attacker\nadded_by: rogue")

    assert res["ok"] is True
    content = Path(res["path"]).read_text(encoding="utf-8")
    lines = content.splitlines()

    added_by_lines = [line for line in lines if line.startswith("added_by:")]
    assert len(added_by_lines) == 1
    assert added_by_lines[0] == "added_by: TAU"


def test_invalid_inputs_refused(tmp_path: Path) -> None:
    missing_folder = tmp_path / "not_existing_folder"
    res_dir = add_note(missing_folder, "Title", "Body", "alice")
    assert res_dir["ok"] is False
    assert res_dir["say"] == "That notes folder isn't there."

    res_title = add_note(tmp_path, "   ", "Body", "alice")
    assert res_title["ok"] is False
    assert res_title["say"] == "A note needs a title, some text, and who wrote it."

    res_body = add_note(tmp_path, "Title", "   ", "alice")
    assert res_body["ok"] is False
    assert res_body["say"] == "A note needs a title, some text, and who wrote it."

    res_who = add_note(tmp_path, "Title", "Body", "   ")
    assert res_who["ok"] is False
    assert res_who["say"] == "A note needs a title, some text, and who wrote it."

    long_body = "x" * (MAX_NOTE + 1)
    res_long = add_note(tmp_path, "Title", long_body, "alice")
    assert res_long["ok"] is False
    assert res_long["say"] == "That note is too long to add in one piece."

    assert list(tmp_path.glob("*.md")) == []


def test_receipt_logging_and_receipt_failure(tmp_path: Path) -> None:
    log = FakeLog()
    res = add_note(tmp_path, "Logging Note", "Body with receipt", "alice", actionlog=log)
    assert res["ok"] is True
    assert res["say"] == "Added the note."
    assert len(log.calls) == 1
    assert log.calls[0]["organ"] == "notes"
    assert log.calls[0]["who"] == "alice"
    assert "Logging Note" in log.calls[0]["what"]

    exploding_log = FakeLog(explode=True)
    res_fail = add_note(tmp_path, "Exploding Note", "Body with failing receipt", "bob", actionlog=exploding_log)
    assert res_fail["ok"] is True
    assert Path(res_fail["path"]).is_file()
    assert res_fail["say"] == "Added the note. I could not write the receipt, so check the log."
    assert "RECEIPT_EXPLOSION" not in res_fail["say"]


def test_source_rule() -> None:
    path = Path("src/tau/connect/notes.py")
    text = path.read_text(encoding="utf-8")

    forbidden_names = ["unlink", "remove", "rename", "replace(", "rmtree", "write_text", "truncate"]
    for forbidden in forbidden_names:
        assert forbidden not in text

    forbidden_modes = ['"w"', "'w'", '"a"', "'a'", '"r+"', "'r+'", '"w+"', "'w+'"]
    for mode in forbidden_modes:
        assert mode not in text
