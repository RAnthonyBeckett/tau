"""The post-check every reply goes through: style guard + pacing rules.

Style guard: an optional banned-words list (case-insensitive whole words or
phrases). It's a post-check because logit bias works per token and can't ban
phrases. On a hit TAU rewrites the offending sentence once (with the brain if
one is given), and if the rewrite still hits, falls back to a plain local fix.

Pacing: length, list and number rules are enforced locally. Overflow is not
thrown away; it is returned as ``overflow`` for the pin card.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from tau.pacing import SUGGESTION_START, PacingRules

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_LIST_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)\.(\d+)(?![\w.])|(?<![\w.,])(\d{4,})(?![\w.,])")

PLAIN = {
    "utilize": "use", "leverage": "use", "optimize": "improve", "streamline": "simplify",
    "furthermore": "also", "moreover": "also", "additionally": "also", "subsequently": "then",
    "whilst": "while", "robust": "solid", "seamless": "smooth", "seamlessly": "smoothly",
    "comprehensive": "full", "navigate": "work through", "delve": "dig", "myriad": "many",
    "notably": "", "crucially": "", "undoubtedly": "", "meticulously": "carefully",
    "elucidate": "explain", "illuminate": "show", "embark": "start", "augment": "add to",
}


def load_banned(path: Optional[str | Path]) -> List[str]:
    if not path:
        return []
    p = Path(path).expanduser()
    if not p.exists():
        return []
    words = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(line)
    return [w.lower() for w in words]


def _word_re(words: List[str]) -> Optional[re.Pattern]:
    if not words:
        return None
    alts = sorted((re.escape(w) for w in words), key=len, reverse=True)
    return re.compile(r"(?<![\w-])(" + "|".join(alts) + r")(?![\w-])", re.I)


def banned_hits(text: str, banned: List[str]) -> List[str]:
    rx = _word_re(banned)
    return sorted({m.group(1).lower() for m in rx.finditer(text)}) if rx else []


def sentences(text: str) -> List[str]:
    return [s for s in _SENT_SPLIT.split(text.strip()) if s.strip()]


@dataclass
class Checked:
    text: str
    overflow: str = ""
    hits: List[str] = field(default_factory=list)
    rewrites: int = 0
    notes: List[str] = field(default_factory=list)


def _local_fix(sentence: str, banned: List[str]) -> str:
    rx = _word_re(banned)

    def sub(m):
        w = m.group(1)
        rep = PLAIN.get(w.lower(), "")
        if rep and w[0].isupper():
            rep = rep[0].upper() + rep[1:]
        return rep

    out = rx.sub(sub, sentence) if rx else sentence
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"^[,;:]\s*", "", out).strip()
    return out[:1].upper() + out[1:] if out else out


def style_guard(text: str, banned: List[str],
                rewrite: Optional[Callable[[str, List[str]], str]] = None) -> Checked:
    res = Checked(text)
    if not banned:
        return res
    out_paras = []
    for para in text.split("\n"):
        new_sents = []
        for s in sentences(para) if para.strip() else [para]:
            hits = banned_hits(s, banned)
            if hits:
                res.hits.extend(hits)
                fixed = None
                if rewrite is not None:
                    try:
                        fixed = rewrite(s, hits)
                        res.rewrites += 1
                    except Exception:
                        fixed = None
                if not fixed or banned_hits(fixed, banned):
                    fixed = _local_fix(fixed or s, banned)
                s = fixed
            new_sents.append(s)
        out_paras.append(" ".join(new_sents))
    res.text = "\n".join(out_paras)
    res.hits = sorted(set(res.hits))
    return res


def _round_number(m: re.Match) -> str:
    if m.group(3):
        n = int(m.group(3))
        if n >= 1_000_000:
            return f"about {round(n / 1_000_000)} million"
        if n >= 10_000:
            return f"about {round(n / 1000)} thousand"
        return f"about {round(n, -2)}"
    whole = m.group(1).replace(",", "")
    return f"about {round(float(whole + '.' + m.group(2)))}"


def pacing_guard(text: str, rules: PacingRules) -> Checked:
    res = Checked(text)
    if not rules.active:
        return res
    r = rules.effective()
    lines = text.splitlines()
    if not r.lists_allowed and any(_LIST_LINE.match(l) for l in lines):
        out_lines: List[str] = []
        items: List[str] = []

        def flush():
            if items:
                joined = "; ".join(items)
                out_lines.append(joined[:1].upper() + joined[1:] + ".")
                items.clear()

        for l in lines:
            if _LIST_LINE.match(l):
                items.append(_LIST_LINE.sub("", l).strip().rstrip("."))
            else:
                flush()
                out_lines.append(l)
        flush()
        text = "\n".join(out_lines).strip()
        res.notes.append("list turned into sentences")
    if r.sparse_numbers and _NUMBER.search(text):
        res.overflow += "Exact figures: " + ", ".join(m.group(0) for m in _NUMBER.finditer(text)) + "\n"
        text = _NUMBER.sub(_round_number, text)
        res.notes.append("numbers rounded; exact ones on the pin card")
    paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if r.hard_day:
        kept = [s for s in sentences(" ".join(paras)) if not SUGGESTION_START.match(s.strip())]
        paras = [" ".join(kept)] if kept else paras[:1]
    if r.max_paragraphs and len(paras) > r.max_paragraphs:
        res.overflow += "\n\n".join(paras[r.max_paragraphs:]) + "\n"
        paras = paras[: r.max_paragraphs]
        res.notes.append("trimmed to the paragraph limit; the rest is on the pin card")
    text = "\n\n".join(paras)
    if r.max_sentences:
        ss = sentences(text.replace("\n\n", " "))
        if len(ss) > r.max_sentences:
            res.overflow += " ".join(ss[r.max_sentences:]) + "\n"
            text = " ".join(ss[: r.max_sentences])
            res.notes.append("trimmed to the sentence limit")
    res.text = text.strip()
    return res


def check_reply(text: str, banned: List[str], rules: Optional[PacingRules] = None,
                rewrite: Optional[Callable[[str, List[str]], str]] = None) -> Checked:
    s = style_guard(text, banned, rewrite)
    if rules is None:
        return s
    p = pacing_guard(s.text, rules)
    p.hits, p.rewrites = s.hits, s.rewrites
    return p
