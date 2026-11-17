"""Reflex router: match what was said against ``reflexes.yaml`` before any model
call. A reflex runs an organ action (or a TAU-internal special) in well under
100 ms and spends zero tokens."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from tau import paths

# words that point at an organ when the user grants by voice
ORGAN_WORDS = [
    ("services", ("service", "services", "start", "stop", "restart")),
    ("gpu", ("gpu", "vram", "graphics card", "video memory", "unload")),
    ("models", ("model", "models", "switch")),
    ("apps", ("prep", "prepare", "app", "apps")),
    ("files", ("file", "files", "write", "save")),
    ("handoff", ("hand off", "handoff", "agent", "agents", "coding")),
    ("clipboard", ("clipboard", "copy")),
    ("window", ("window", "screen", "what i'm doing", "focus")),
    ("processes", ("process", "processes")),
    ("notifications", ("notification", "notifications")),
    ("heartbeat", ("health", "heartbeat")),
    ("cmdfail", ("error", "errors", "failed command", "failures")),
    ("drives", ("drive", "drives", "disk", "disks")),
    ("knowledge", ("notes", "knowledge", "documents")),
]


def organ_from_words(words: str) -> Optional[str]:
    w = words.lower()
    for organ, keys in ORGAN_WORDS:
        if any(re.search(rf"\b{re.escape(k)}\b", w) for k in keys):
            return organ
    return None


@dataclass
class Reflex:
    name: str
    patterns: List[re.Pattern]
    organ: Optional[str] = None
    action: Optional[str] = None
    special: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Match:
    reflex: Reflex
    groups: Dict[str, str]

    def args(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in self.reflex.args.items():
            out[k] = v.format(**{g: (x or "").strip() for g, x in self.groups.items()}) \
                if isinstance(v, str) else v
        return out


def load_reflexes(extra: Optional[Path] = None) -> List[Reflex]:
    files = []
    user = Path(extra) if extra else paths.tau_home() / "reflexes.yaml"
    if user.exists():
        files.append(user)
    files.append(paths.shipped_file("reflexes.yaml"))
    out: List[Reflex] = []
    for f in files:
        for r in yaml.safe_load(f.read_text(encoding="utf-8")) or []:
            out.append(Reflex(r["name"], [re.compile(p, re.I) for p in r["patterns"]],
                              r.get("organ"), r.get("action"), r.get("special"), r.get("args") or {}))
    return out


def normalize(text: str, names: List[str]) -> str:
    t = text.strip().lower()
    t = re.sub(r"[‘’]", "'", t)
    for n in sorted({n.lower() for n in names if n}, key=len, reverse=True):
        t = re.sub(rf"^(hey |ok |okay )?{re.escape(n)}[,:!.]?\s*", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"[?!.]+$", lambda m: m.group(0)[:1] if m.group(0)[:1] == "?" else "", t).strip()


class ReflexRouter:
    def __init__(self, reflexes: Optional[List[Reflex]] = None, names: Optional[List[str]] = None):
        self.reflexes = reflexes if reflexes is not None else load_reflexes()
        self.names = names or ["tau"]

    def match(self, text: str) -> Optional[Match]:
        t = normalize(text, self.names)
        raw = text.strip()
        for n in sorted({n for n in self.names if n}, key=len, reverse=True):
            raw = re.sub(rf"^(hey |ok |okay )?{re.escape(n)}[,:!.]?\s*", "", raw, flags=re.I)
        for r in self.reflexes:
            for p in r.patterns:
                m = p.search(t)
                if m:
                    groups = {k: v for k, v in m.groupdict().items() if v is not None}
                    groups["_raw"] = raw
                    return Match(r, groups)
        return None


@dataclass
class ReflexResult:
    handled: bool
    say: str = ""
    name: str = ""
    status: str = ""
    elapsed_ms: float = 0.0
    data: Any = None


def run_reflex(m: Match, organs, specials: Dict[str, Callable[[Match], ReflexResult]]) -> ReflexResult:
    t0 = time.perf_counter()
    r = m.reflex
    if r.special:
        fn = specials.get(r.special)
        res = fn(m) if fn else ReflexResult(True, f"I can't do '{r.special}' here.", r.name, "error")
    else:
        args = m.args()
        machine = m.groups.get("machine")
        out = organs.call(r.organ, r.action, machine=machine, via="reflex", **args)
        res = ReflexResult(True, out.message, r.name, out.status, data=out.data)
        if out.pending_id:
            res.data = {"pending_id": out.pending_id}
    res.elapsed_ms = (time.perf_counter() - t0) * 1000
    return res
