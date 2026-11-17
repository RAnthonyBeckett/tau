"""Add notes to a shared folder."""

from __future__ import annotations

import datetime
import pathlib
import re
from typing import Any, Dict, Optional

MAX_NOTE = 100000


def _slug(title: str) -> str:
    """Return a clean file-safe slug from a title or author string."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    shortened = cleaned[:60]
    return shortened if shortened else "note"


def add_note(
    folder: Any,
    title: str,
    body: str,
    who: str,
    actionlog: Any = None,
    now: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Add one new note file to a shared notes folder."""
    folder_path = pathlib.Path(folder)
    if not folder_path.is_dir():
        return {"ok": False, "say": "That notes folder isn't there.", "path": ""}

    if not isinstance(title, str) or not title.strip():
        return {"ok": False, "say": "A note needs a title, some text, and who wrote it.", "path": ""}
    if not isinstance(body, str) or not body.strip():
        return {"ok": False, "say": "A note needs a title, some text, and who wrote it.", "path": ""}
    if not isinstance(who, str) or not who.strip():
        return {"ok": False, "say": "A note needs a title, some text, and who wrote it.", "path": ""}

    if len(body) > MAX_NOTE:
        return {"ok": False, "say": "That note is too long to add in one piece.", "path": ""}

    clean_title = " ".join(title.strip()[:120].split())
    clean_who = " ".join(who.strip().split())

    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)

    stamp = now.strftime("%Y%m%d-%H%M%S")
    file_name = f"{stamp}_{_slug(who)}_{_slug(clean_title)}.md"
    target = folder_path / file_name

    iso_stamp = now.isoformat(timespec="seconds")
    body_text = body.strip()
    content = (
        f"---\n"
        f"title: {clean_title}\n"
        f"written_by: {clean_who}\n"
        f"written_at: {iso_stamp}\n"
        f"added_by: TAU\n"
        f"---\n\n"
        f"{body_text}\n"
    )

    try:
        with target.open(mode="x", encoding="utf-8") as f:
            f.write(content)
    except FileExistsError:
        return {
            "ok": False,
            "say": "A note with that name was just added. Nothing was changed.",
            "path": "",
        }
    except OSError:
        return {
            "ok": False,
            "say": "TAU couldn't add the note. Nothing was changed.",
            "path": "",
        }

    receipt_failed = False
    if actionlog is not None:
        try:
            actionlog.receipt(
                f"Added the note '{clean_title}' to the shared notes folder",
                who,
                organ="notes",
            )
        except Exception:
            receipt_failed = True

    say = "Added the note."
    if receipt_failed:
        say += " I could not write the receipt, so check the log."

    return {"ok": True, "say": say, "path": str(target)}
