"""Open Knowledge Format (OKF v0.2) reader, writer and validator.

Spec: https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf

* A bundle is a folder of markdown files with YAML frontmatter.
* ``index.md`` lists a folder (no frontmatter, except ``okf_version`` at the
  bundle root); ``log.md`` is dated history, newest first.
* Every other ``.md`` is a concept and needs a non-empty ``type``.
* Trust: ``generated {by, at}``, ``verified`` (list or one mapping), tiers
  unverified < machine-confirmed < human-reviewed; ``status`` draft | stable |
  deprecated (absent = stable); ``stale_after`` an absolute instant.

Reading is streaming (one file at a time); nothing loads a whole bundle body
into memory unless asked.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import yaml

from tau import __version__

OKF_VERSION = "0.2"
RESERVED = ("index.md", "log.md")
TAU_ACTOR = f"tau/{__version__}"
TIERS = ("unverified", "machine-confirmed", "human-reviewed")
_DATE_HEADING = re.compile(r"^##\s+(\S+)")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class OKFError(ValueError):
    pass


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t: Optional[dt.datetime] = None) -> str:
    return (t or now_utc()).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: Any) -> Optional[dt.datetime]:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else None
    if isinstance(value, dt.date):
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else None


# -- documents -------------------------------------------------------------------
def split_frontmatter(text: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return (frontmatter or None, body). Raises OKFError on broken YAML."""
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            raw = "".join(lines[1:i])
            try:
                meta = yaml.safe_load(raw) if raw.strip() else {}
            except yaml.YAMLError as exc:
                raise OKFError(f"frontmatter is not valid YAML: {exc}") from exc
            if meta is None:
                meta = {}
            if not isinstance(meta, dict):
                raise OKFError("frontmatter is not a mapping")
            return meta, "".join(lines[i + 1:])
    raise OKFError("frontmatter has no closing ---")


def render(meta: Dict[str, Any], body: str) -> str:
    fm = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=None)
    return f"---\n{fm}---\n\n{body.strip()}\n"


def verified_list(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    v = meta.get("verified")
    if v is None:
        return []
    if isinstance(v, dict):  # a bare mapping is a one-element list (spec 5.2)
        return [v]
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def trust_tier(meta: Dict[str, Any]) -> str:
    vs = verified_list(meta)
    if not vs:
        return "unverified"
    if any(str(x.get("by", "")).startswith("human:") for x in vs):
        return "human-reviewed"
    return "machine-confirmed"


def is_stale(meta: Dict[str, Any], now: Optional[dt.datetime] = None) -> bool:
    t = parse_time(meta.get("stale_after"))
    return bool(t and (now or now_utc()) >= t)


def status_of(meta: Dict[str, Any]) -> str:
    return str(meta.get("status") or "stable")


@dataclass
class Concept:
    id: str
    path: Path
    meta: Dict[str, Any]
    body: str = ""

    @property
    def type(self) -> str:
        return str(self.meta.get("type", ""))

    @property
    def title(self) -> str:
        return str(self.meta.get("title") or Path(self.id).name.replace("_", " "))

    @property
    def description(self) -> str:
        return str(self.meta.get("description") or "")

    @property
    def tier(self) -> str:
        return trust_tier(self.meta)

    @property
    def stale(self) -> bool:
        return is_stale(self.meta)

    @property
    def status(self) -> str:
        return status_of(self.meta)

    def rank(self) -> Tuple[int, int, int]:
        """Higher is more trustworthy: tier, not deprecated, not stale."""
        return (TIERS.index(self.tier), int(self.status != "deprecated"), int(not self.stale))

    def warning(self) -> Optional[str]:
        notes = []
        if self.stale:
            notes.append(f"'{self.title}' is past its stale_after date")
        if self.status == "deprecated":
            notes.append(f"'{self.title}' is deprecated")
        if self.status == "draft":
            notes.append(f"'{self.title}' is a draft")
        return "; ".join(notes) or None


def read_concept(path: Path, root: Path) -> Concept:
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    if meta is None:
        raise OKFError(f"{path}: no frontmatter")
    cid = path.relative_to(root).with_suffix("").as_posix()
    return Concept(cid, path, meta, body)


def iter_concepts(root: Path, with_body: bool = True) -> Iterator[Concept]:
    """Walk a bundle one file at a time. Bad files are skipped (see validate)."""
    root = Path(root)
    for path in sorted(root.rglob("*.md")):
        if path.name in RESERVED or any(p.startswith(".") for p in path.relative_to(root).parts):
            continue
        try:
            c = read_concept(path, root)
        except (OKFError, UnicodeDecodeError):
            continue
        if not with_body:
            c.body = ""
        yield c


def ranked(concepts: List[Concept]) -> List[Concept]:
    """Prefer verified, current, non-stale concepts."""
    return sorted(concepts, key=lambda c: c.rank(), reverse=True)


# -- validation --------------------------------------------------------------------
@dataclass
class Report:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(root: Path) -> Report:
    """Check conformance (spec section 11). Errors break conformance; warnings don't."""
    root = Path(root)
    rep = Report()
    if not root.is_dir():
        rep.errors.append(f"{root} is not a folder")
        return rep
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if any(p.startswith(".") for p in path.relative_to(root).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            rep.errors.append(f"{rel}: not UTF-8")
            continue
        if path.name == "index.md":
            _check_index(text, rel, path.parent == root, rep)
            continue
        if path.name == "log.md":
            _check_log(text, rel, rep)
            continue
        try:
            meta, _ = split_frontmatter(text)
        except OKFError as exc:
            rep.errors.append(f"{rel}: {exc}")
            continue
        if meta is None:
            rep.errors.append(f"{rel}: no YAML frontmatter")
            continue
        if not str(meta.get("type") or "").strip():
            rep.errors.append(f"{rel}: frontmatter has no 'type'")
        _check_trust(meta, rel, rep)
    return rep


def _check_index(text: str, rel: str, is_root: bool, rep: Report) -> None:
    try:
        meta, _ = split_frontmatter(text)
    except OKFError as exc:
        rep.errors.append(f"{rel}: {exc}")
        return
    if meta is not None:
        extra = set(meta) - {"okf_version"}
        if not is_root or extra:
            rep.errors.append(f"{rel}: index.md may only carry okf_version, and only at the bundle root")


def _check_log(text: str, rel: str, rep: Report) -> None:
    dates = []
    for line in text.splitlines():
        m = _DATE_HEADING.match(line)
        if m:
            if not _ISO_DATE.match(m.group(1)):
                rep.errors.append(f"{rel}: log heading '{m.group(1)}' is not YYYY-MM-DD")
            else:
                dates.append(m.group(1))
    if dates != sorted(dates, reverse=True):
        rep.warnings.append(f"{rel}: log entries are not newest first")


def _check_trust(meta: Dict[str, Any], rel: str, rep: Report) -> None:
    gen = meta.get("generated")
    if gen is not None:
        if not isinstance(gen, dict) or not gen.get("by"):
            rep.warnings.append(f"{rel}: generated needs a 'by' actor")
        elif gen.get("at") is not None and parse_time(gen.get("at")) is None:
            rep.warnings.append(f"{rel}: generated.at is not an ISO datetime with offset")
    for v in verified_list(meta):
        if not v.get("by") or parse_time(v.get("at")) is None:
            rep.warnings.append(f"{rel}: each verified entry needs 'by' and an ISO 'at'")
    if "stale_after" in meta and parse_time(meta["stale_after"]) is None:
        rep.warnings.append(f"{rel}: stale_after is not an ISO datetime with offset")
    if "status" in meta and meta["status"] not in ("draft", "stable", "deprecated"):
        rep.warnings.append(f"{rel}: unknown status {meta['status']!r}")
    for s in meta.get("sources") or []:
        if not isinstance(s, dict) or not s.get("resource"):
            rep.warnings.append(f"{rel}: each sources entry needs a 'resource'")


# -- writing -------------------------------------------------------------------------
def write_concept(root: Path, cid: str, type_: str, body: str, *, title: str = "",
                  description: str = "", tags: Optional[List[str]] = None,
                  sources: Optional[List[Dict[str, Any]]] = None,
                  generated_by: str = TAU_ACTOR, verified: Optional[List[Dict[str, Any]]] = None,
                  stale_after: Optional[dt.datetime] = None, status: Optional[str] = None,
                  extra: Optional[Dict[str, Any]] = None, log_entry: Optional[str] = None,
                  update_index: bool = True) -> Path:
    """Write one concept as valid OKF and keep the folder's index.md current."""
    root = Path(root)
    if not type_:
        raise OKFError("type is required")
    path = root / f"{cid}.md"
    if path.name in RESERVED:
        raise OKFError(f"{path.name} is reserved")
    meta: Dict[str, Any] = {"type": type_}
    if title:
        meta["title"] = title
    if description:
        meta["description"] = description
    if tags:
        meta["tags"] = list(tags)
    if sources:
        meta["sources"] = sources
    meta["generated"] = {"by": generated_by, "at": iso()}
    if verified:
        meta["verified"] = verified
    if stale_after:
        meta["stale_after"] = iso(stale_after)
    if status:
        meta["status"] = status
    if extra:
        for k, v in extra.items():
            meta.setdefault(k, v)
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(meta, body), encoding="utf-8")
    if update_index:
        ensure_indexes(root, path.parent)
    word = "Update" if existed else "Creation"
    label = meta.get("title", cid)
    append_log(root, f"**{word}**: {log_entry or f'[{label}](/{cid}.md)'}")
    return path


def ensure_indexes(root: Path, folder: Path) -> None:
    """Regenerate index.md for ``folder`` and every parent up to the root."""
    root = Path(root).resolve()
    folder = Path(folder).resolve()
    while True:
        write_index(folder, is_root=(folder == root))
        if folder == root or root not in folder.parents:
            break
        folder = folder.parent


def write_index(folder: Path, is_root: bool = False, heading: Optional[str] = None) -> Path:
    folder = Path(folder)
    items: List[str] = []
    subs: List[str] = []
    for p in sorted(folder.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_dir() and any(p.rglob("*.md")):
            subs.append(f"* [{p.name}]({p.name}/) - {_folder_blurb(p)}")
        elif p.suffix == ".md" and p.name not in RESERVED:
            try:
                meta, _ = split_frontmatter(p.read_text(encoding="utf-8"))
            except OKFError:
                continue
            meta = meta or {}
            title = meta.get("title") or p.stem
            desc = meta.get("description") or meta.get("type", "")
            items.append(f"* [{title}]({p.name}) - {desc}")
    parts = []
    if is_root:
        parts.append(f"---\nokf_version: \"{OKF_VERSION}\"\n---\n")
    parts.append(f"# {heading or (folder.name.replace('_', ' ').title() or 'Contents')}\n")
    parts.extend(items or ["* (nothing here yet)"])
    if subs:
        parts.append("\n# Folders\n")
        parts.extend(subs)
    path = folder / "index.md"
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return path


def _folder_blurb(p: Path) -> str:
    n = sum(1 for x in p.rglob("*.md") if x.name not in RESERVED)
    return f"{n} note{'s' if n != 1 else ''}"


def append_log(root: Path, entry: str, when: Optional[dt.date] = None) -> Path:
    """Add a dated entry to the bundle-root log.md, newest date first."""
    path = Path(root) / "log.md"
    day = (when or dt.date.today()).isoformat()
    text = path.read_text(encoding="utf-8") if path.exists() else "# Update Log\n"
    lines = text.splitlines()
    heading = f"## {day}"
    if heading in lines:
        i = lines.index(heading)
        lines.insert(i + 1, f"* {entry}")
    else:
        # insert after the title, before the first date heading
        j = next((k for k, l in enumerate(lines) if l.startswith("## ")), len(lines))
        lines[j:j] = ["", heading, f"* {entry}"]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path
