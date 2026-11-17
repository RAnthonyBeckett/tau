"""TAU's own working memory: an OKF folder of plain markdown.

Files (created on first run, kept current by TAU):

* ``journal.md``     dated log of done / next / shelved, newest first.
* ``user.md``        who the user is and how to speak to them.
* ``rules.md``       standing rules, each with the date and the user's words.
* ``lab/``           the lab map (one note per machine, service, drive).
* ``workspaces.md``  projects and folders and what each is for.
* ``components.md``  installed tools, model servers and agent CLIs.
* ``private/``       sensitive notes (pacing reasons). Never sent to a model.
* ``index.md`` per folder and ``log.md`` at the root, as OKF requires.

The files on disk are the truth. TAU never keeps a shadow copy: every update
re-reads the file and inserts into it, so the user's own edits always win.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from tau import okf
from tau.profile import get

FILES = {
    "journal": ("Journal", "Journal", "What was done, what is next and what was shelved, newest first.",
                "# Entries\n"),
    "user": ("Person", "User", "Who the user is and how they want to be spoken to.",
             "# About\n\n# How to speak to them\n\n# Corrections\n"),
    "rules": ("Rules", "Standing rules", "Rules the user has given, with the date and their words.",
              "# Rules\n"),
    "workspaces": ("Reference", "Workspaces", "Projects and folders the user works in, and what each is for.",
                   "# Workspaces\n"),
    "components": ("Reference", "Components",
                   "Installed tools, model servers and agent CLIs: path, launch, port, reads/writes, used by.",
                   "# Components\n"),
}

_USER_HINTS = re.compile(
    r"\b(call me|my name|speak to me|talk to me|address me|i prefer|i like|i am|i'm|my pronouns|"
    r"say it|tell me)\b", re.I)


class Brain:
    def __init__(self, root: Path, user_actor: Optional[str] = None):
        self.root = Path(root)
        self.user_actor = user_actor or "human:user"

    # -- creation ---------------------------------------------------------------
    def ensure(self, profile: Optional[Mapping[str, Any]] = None) -> List[Path]:
        """Create any missing brain files. Existing files are never touched."""
        made: List[Path] = []
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "lab").mkdir(exist_ok=True)
        (self.root / "private").mkdir(exist_ok=True)
        for key, (type_, title, desc, body) in FILES.items():
            p = self.root / f"{key}.md"
            if p.exists():
                continue
            if key == "user" and profile:
                body = _user_body(profile)
            made.append(okf.write_concept(self.root, key, type_, body, title=title,
                                          description=desc, update_index=False,
                                          log_entry=f"Created [{title}](/{key}.md)"))
        priv = self.root / "private" / "README.md"
        if not priv.exists():
            okf.write_concept(self.root, "private/README", "Reference",
                              "Notes in this folder stay on this machine. TAU never puts them in a model prompt.\n",
                              title="Private notes", description="Sensitive notes kept local.",
                              update_index=False, log_entry="Created private folder")
        lab_readme = self.root / "lab" / "README.md"
        if not lab_readme.exists():
            okf.write_concept(self.root, "lab/README", "Reference",
                              "Run `tau discover` to fill this folder with one note per machine and service.\n",
                              title="Lab map", description="Machines, services and drives in the lab.",
                              update_index=False, log_entry="Created lab folder")
        self.reindex()
        return made

    def reindex(self) -> None:
        for folder in [self.root] + [p for p in self.root.rglob("*") if p.is_dir()]:
            if any(part.startswith(".") for part in folder.relative_to(self.root).parts):
                continue
            okf.write_index(folder, is_root=(folder == self.root),
                            heading="TAU brain" if folder == self.root else None)

    # -- reading ------------------------------------------------------------------
    def read(self, key: str) -> str:
        p = self.root / f"{key}.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def top_journal_entry(self) -> str:
        _, body = okf.split_frontmatter(self.read("journal"))
        m = re.search(r"^## .+?(?=^## |\Z)", body, re.S | re.M)
        return m.group(0).strip() if m else ""

    def rules(self) -> List[str]:
        _, body = okf.split_frontmatter(self.read("rules"))
        return [l[2:].strip() for l in body.splitlines() if l.startswith("- ")]

    # -- writing ------------------------------------------------------------------
    def _insert(self, key: str, heading: str, line: str, newest_first: bool = True) -> Path:
        p = self.root / f"{key}.md"
        if not p.exists():
            self.ensure()
        meta, body = okf.split_frontmatter(p.read_text(encoding="utf-8"))
        meta = meta or {"type": FILES[key][0]}
        lines = body.splitlines()
        if heading not in lines:
            lines += ["", heading]
        i = lines.index(heading) + 1
        if newest_first:
            while i < len(lines) and not lines[i].strip():
                i += 1
            lines.insert(i, line)
        else:
            j = i
            while j < len(lines) and not lines[j].startswith("#"):
                j += 1
            while j > i and not lines[j - 1].strip():
                j -= 1
            lines.insert(j, line)
        gen = meta.get("generated") if isinstance(meta.get("generated"), dict) else {}
        meta["generated"] = {**gen, "by": okf.TAU_ACTOR, "at": okf.iso()}
        p.write_text(okf.render(meta, "\n".join(lines)), encoding="utf-8")
        return p

    def add_rule(self, words: str, rule: Optional[str] = None, when: Optional[dt.date] = None) -> Path:
        day = (when or dt.date.today()).isoformat()
        text = rule or words
        line = f"- {day}: {text.strip()} (your words: \"{words.strip()}\")"
        p = self._insert("rules", "# Rules", line, newest_first=False)
        okf.append_log(self.root, f"**Update**: new rule in [Standing rules](/rules.md): {text.strip()[:80]}")
        return p

    def add_user_note(self, words: str, when: Optional[dt.date] = None) -> Path:
        day = (when or dt.date.today()).isoformat()
        p = self._insert("user", "# Corrections", f"- {day}: \"{words.strip()}\"", newest_first=False)
        okf.append_log(self.root, "**Update**: correction added to [User](/user.md)")
        return p

    def record_correction(self, words: str) -> Path:
        """A correction goes straight into user.md (about the person) or rules.md."""
        if _USER_HINTS.search(words):
            return self.add_user_note(words)
        return self.add_rule(words)

    def journal(self, done: str = "", next_: str = "", shelved: str = "",
                when: Optional[dt.datetime] = None) -> Path:
        stamp = (when or dt.datetime.now()).strftime("%Y-%m-%d %H:%M")
        block = [f"## {stamp}"]
        if done:
            block.append(f"- Done: {done}")
        if next_:
            block.append(f"- Next: {next_}")
        if shelved:
            block.append(f"- Shelved: {shelved}")
        p = self.root / "journal.md"
        if not p.exists():
            self.ensure()
        meta, body = okf.split_frontmatter(p.read_text(encoding="utf-8"))
        lines = body.splitlines()
        i = lines.index("# Entries") + 1 if "# Entries" in lines else len(lines)
        lines[i:i] = [""] + block
        meta = meta or {"type": "Journal"}
        meta["generated"] = {"by": okf.TAU_ACTOR, "at": okf.iso()}
        p.write_text(okf.render(meta, "\n".join(lines)), encoding="utf-8")
        return p

    def set_component(self, name: str, path: str = "", launch: str = "", port: Any = None,
                      reads: str = "", writes: str = "", used_by: str = "") -> Path:
        line = (f"- **{name}**: path `{path or '?'}`; launch `{launch or '?'}`; port {port or '-'}; "
                f"reads {reads or '-'}; writes {writes or '-'}; used by {used_by or '-'}")
        current = self.read("components")
        if f"**{name}**" in current:
            return self.root / "components.md"  # user may have edited it; theirs wins
        return self._insert("components", "# Components", line, newest_first=False)

    def has_component(self, name: str) -> bool:
        return f"**{name.lower()}**" in self.read("components").lower()

    def add_workspace(self, path: str, purpose: str) -> Path:
        return self._insert("workspaces", "# Workspaces", f"- `{path}`: {purpose}", newest_first=False)


def _user_body(profile: Mapping[str, Any]) -> str:
    name = get(profile, "user.name", "")
    form = get(profile, "user.speak_to_me_as", "")
    about = f"- Name: {name}\n" if name else ""
    how = f"- Speak to them as: {form}\n" if form else ""
    return f"# About\n\n{about}\n# How to speak to them\n\n{how}\n# Corrections\n"
