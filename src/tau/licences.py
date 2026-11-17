"""Licence gate: ``tau licence-check``.

* Resolves every installed dependency of TAU's core and each extra (following
  ``Requires-Dist`` with environment markers), plus the packages installed
  ``--no-deps`` from ``requirements-kokoro.txt``.
* Reads each package's licence metadata (``License-Expression``, then trove
  classifiers, then a short ``License`` field) and fails on GPL or AGPL.
  LGPL and MPL are allowed and listed.
* Checks every model the code downloads against its model-card licence and
  fails on non-commercial ones. Hugging Face is asked live when reachable;
  otherwise the recorded card licence below is used, and the report says so.
* Builds the README's dependency table from the same data.
"""

from __future__ import annotations

import importlib.metadata as md
import json
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from tau import paths

EXTRAS = ("core", "voice", "display", "windows", "gesture")
NO_DEPS_FILE = "requirements-kokoro.txt"

PERMISSIVE = ("MIT", "BSD", "APACHE", "ISC", "PSF", "PYTHON SOFTWARE FOUNDATION", "ZLIB", "UNLICENSE",
              "CC0", "OFL", "HPND", "MIT-CMU", "BOOST", "BSL-1.0", "0BSD", "UPL", "POSTGRESQL", "X11", "NCSA",
              "PUBLIC DOMAIN", "CNRI", "W3C")
WEAK_COPYLEFT = ("LGPL", "LESSER GENERAL PUBLIC", "LIBRARY GENERAL PUBLIC", "MPL", "MOZILLA PUBLIC")
STRONG_COPYLEFT = ("AGPL", "AFFERO", "GPL", "GENERAL PUBLIC LICENSE")
NON_COMMERCIAL = ("NC", "NON-COMMERCIAL", "NONCOMMERCIAL")

# Models the code downloads, and the licence on each model card (recorded
# 2026-11; confirmed live when Hugging Face is reachable).
MODELS: Dict[str, Dict[str, str]] = {
    "hexgrad/Kokoro-82M": {"licence": "apache-2.0", "used_by": "tau.kokoro_voice (voice)"},
    "PeterReid/graphemes_to_phonemes_en_us": {"licence": "apache-2.0", "used_by": "tau.kokoro_voice (unknown words)"},
    "PeterReid/graphemes_to_phonemes_en_gb": {"licence": "apache-2.0", "used_by": "tau.kokoro_voice (unknown words)"},
    "Systran/faster-whisper-base.en": {"licence": "mit", "used_by": "tau.ears (transcription, via faster-whisper)"},
    "explosion/en_core_web_sm": {"licence": "mit", "used_by": "misaki's tokenizer (spaCy model wheel)"},
    # Starter models setup offers through Ollama when no model server is found (tau.engines).
    "Qwen/Qwen3-8B": {"licence": "apache-2.0", "used_by": "tau.engines (optional starter model, qwen3:8b)"},
    "Qwen/Qwen3-4B": {"licence": "apache-2.0", "used_by": "tau.engines (optional starter model, qwen3:4b)"},
    "Qwen/Qwen3-1.7B": {"licence": "apache-2.0", "used_by": "tau.engines (optional starter model, qwen3:1.7b)"},
}
# How each model is referenced in source, so a new download can't slip past.
MODEL_PATTERNS = [
    (re.compile(r"hexgrad/Kokoro-82M"), "hexgrad/Kokoro-82M"),
    (re.compile(r"PeterReid/graphemes_to_phonemes_en_(us|gb)"), None),
    (re.compile(r"\bTranscriber\(|WhisperModel\("), "Systran/faster-whisper-base.en"),
    (re.compile(r"en_core_web_sm"), "explosion/en_core_web_sm"),
    (re.compile(r"qwen3:8b"), "Qwen/Qwen3-8B"),
    (re.compile(r"qwen3:4b"), "Qwen/Qwen3-4B"),
    (re.compile(r"qwen3:1\.7b"), "Qwen/Qwen3-1.7B"),
]
HF_REPO_RE = re.compile(r"[\"']([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)[\"']")


@dataclass
class Pkg:
    name: str
    version: str
    licence: str
    verdict: str            # permissive | weak-copyleft | copyleft | proprietary | unknown
    extras: Set[str] = field(default_factory=set)
    via: str = ""


@dataclass
class Report:
    packages: Dict[str, Pkg] = field(default_factory=dict)
    missing: Dict[str, Set[str]] = field(default_factory=dict)      # extra -> names not installed
    models: List[Dict[str, str]] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def licence_text(meta) -> str:
    expr = (meta.get("License-Expression") or "").strip()
    if expr:
        return expr
    cls = [c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    cls = [c for c in cls if c not in ("OSI Approved",)]
    if cls:
        return " / ".join(cls)
    lic = (meta.get("License") or "").strip()
    if lic and len(lic) <= 120 and "\n" not in lic:
        return lic
    if lic:
        first = lic.strip().splitlines()[0].strip()
        return first[:120] + (" (full text in metadata)" if len(lic) > 120 else "")
    return ""


def classify(text: str) -> str:
    t = text.upper()
    if "PROPRIETARY" in t:
        return "proprietary"
    if not t.strip() or t.strip() in ("UNKNOWN", "OTHER/PROPRIETARY LICENSE", "DUAL LICENSE"):
        return "unknown"
    # an "A OR B" expression is as good as its best option
    if " OR " in t:
        return min((classify(p) for p in re.split(r"\s+OR\s+", t)), key=_rank)
    if any(k in t for k in WEAK_COPYLEFT):
        rest = re.sub(r"LGPL[^ /;,)]*|LESSER GENERAL PUBLIC[^/;,)]*|LIBRARY GENERAL PUBLIC[^/;,)]*", "", t)
        if any(re.search(rf"\b{k}\b", rest) for k in ("GPL", "AGPL", "AFFERO")):
            return "copyleft"
        return "weak-copyleft"
    if any(re.search(rf"\b{re.escape(k)}\b", t) for k in STRONG_COPYLEFT):
        if "RUNTIME LIBRARY EXCEPTION" in t or "LINKING EXCEPTION" in t or "CLASSPATH EXCEPTION" in t:
            return "weak-copyleft"
        return "copyleft"
    if any(k in t for k in PERMISSIVE):
        return "permissive"
    return "unknown"


def _rank(v: str) -> int:
    return {"permissive": 0, "weak-copyleft": 1, "unknown": 2, "proprietary": 2, "copyleft": 3}[v]


# -- dependency resolution ---------------------------------------------------------------
def _requirements(dist, extras: Iterable[str]) -> List[Tuple[str, Set[str]]]:
    from packaging.requirements import Requirement

    out = []
    for raw in dist.requires or []:
        try:
            req = Requirement(raw)
        except Exception:
            continue
        envs = [{"extra": e} for e in (list(extras) or [""])]
        if req.marker is None or any(req.marker.evaluate(env) for env in envs):
            out.append((req.name, set(req.extras)))
    return out


def resolve(root: str, extra: Optional[str], get_dist: Callable[[str], object] = md.distribution
            ) -> Tuple[Dict[str, object], Set[str]]:
    """Installed distributions reachable from ``root[extra]``; and names not installed."""
    seen: Dict[str, object] = {}
    missing: Set[str] = set()
    stack: List[Tuple[str, Set[str]]] = [(root, {extra} if extra else set())]
    while stack:
        name, extras = stack.pop()
        key = _norm(name)
        try:
            dist = get_dist(name)
        except md.PackageNotFoundError:
            missing.add(key)
            continue
        first = key not in seen
        seen[key] = dist
        reqs = _requirements(dist, extras)
        if not first and not extras:
            continue
        for n, ex in reqs:
            if _norm(n) not in seen or ex:
                stack.append((n, ex))
    seen.pop(_norm(root), None)
    return seen, missing


def no_deps_packages() -> List[str]:
    f = paths.shipped_file(NO_DEPS_FILE)
    names = []
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                names.append(re.split(r"[=<>@ \[]", line, maxsplit=1)[0].strip())
    return names


# -- models ------------------------------------------------------------------------------
def model_refs(src: Optional[Path] = None) -> Dict[str, List[str]]:
    """Every model repo referenced in TAU's source, with where it's referenced."""
    src = Path(src) if src else Path(__file__).parent
    refs: Dict[str, List[str]] = {}
    for f in sorted(src.rglob("*.py")):
        if f.name == "licences.py":
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            for rx, repo in MODEL_PATTERNS:
                for m in rx.finditer(line):
                    r = repo or m.group(0)
                    refs.setdefault(r, []).append(f"{f.name}:{i}")
            if "hf_hub_download" in line or "from_pretrained" in line or "huggingface.co/" in line:
                for m in HF_REPO_RE.finditer(line):
                    if m.group(1) not in refs and "/" in m.group(1) and not m.group(1).startswith(("voices/", "http")):
                        refs.setdefault(m.group(1), []).append(f"{f.name}:{i}")
    req = paths.shipped_file(NO_DEPS_FILE)
    if req.exists() and src == Path(__file__).parent:
        for i, line in enumerate(req.read_text(encoding="utf-8").splitlines(), 1):
            if "en_core_web_sm" in line and not line.lstrip().startswith("#"):
                refs.setdefault("explosion/en_core_web_sm", []).append(f"{NO_DEPS_FILE}:{i}")
    return refs


def card_licence(repo: str, timeout: float = 4.0) -> Tuple[Optional[str], str]:
    """Licence from the model card, asked live; falls back to the recorded value."""
    if not repo.startswith("explosion/"):
        try:
            with urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}", timeout=timeout) as r:
                data = json.loads(r.read())
            lic = (data.get("cardData") or {}).get("license") or next(
                (t.split(":", 1)[1] for t in data.get("tags", []) if t.startswith("license:")), None)
            if lic:
                return str(lic), "live"
        except Exception:
            pass
    rec = MODELS.get(repo, {}).get("licence")
    return rec, "recorded" if rec else "unknown"


def non_commercial(lic: Optional[str]) -> bool:
    if not lic:
        return False
    t = lic.upper().replace("_", "-")
    return bool(re.search(r"(^|[-. ])NC([-. ]|$)", t)) or "NON-COMMERCIAL" in t or "NONCOMMERCIAL" in t


# -- the check ----------------------------------------------------------------------------
def check(get_dist: Callable[[str], object] = md.distribution, live_models: bool = True,
          src: Optional[Path] = None) -> Report:
    rep = Report()
    for extra in EXTRAS:
        dists, missing = resolve("tau", None if extra == "core" else extra, get_dist)
        rep.missing[extra] = missing
        for key, d in dists.items():
            _add(rep, key, d, extra, "tau" + ("" if extra == "core" else f"[{extra}]"))
    for name in no_deps_packages():
        try:
            d = get_dist(name)
        except md.PackageNotFoundError:
            rep.missing.setdefault("voice (no-deps)", set()).add(_norm(name))
            continue
        _add(rep, _norm(name), d, "voice", NO_DEPS_FILE + " (--no-deps)")
    for key, p in sorted(rep.packages.items()):
        if p.verdict == "copyleft":
            rep.problems.append(f"{p.name} {p.version} is {p.licence} (pulled in by {', '.join(sorted(p.extras))})")
    for repo, where in sorted(model_refs(src).items()):
        lic, how = card_licence(repo) if live_models else (MODELS.get(repo, {}).get("licence"), "recorded")
        rep.models.append({"repo": repo, "licence": lic or "unknown", "source": how, "where": ", ".join(where[:3])})
        if lic is None:
            rep.problems.append(f"model {repo} has no known licence; add it to tau.licences.MODELS after checking its card")
        elif non_commercial(lic):
            rep.problems.append(f"model {repo} is licensed {lic} (non-commercial)")
    return rep


def _add(rep: Report, key: str, dist, extra: str, via: str) -> None:
    meta = dist.metadata
    if key in rep.packages:
        rep.packages[key].extras.add(extra)
        return
    lic = licence_text(meta)
    rep.packages[key] = Pkg(meta["Name"], dist.version, lic or "not stated", classify(lic), {extra}, via)


# -- output -------------------------------------------------------------------------------
def text_report(rep: Report) -> str:
    lines = []
    weak = [p for p in rep.packages.values() if p.verdict == "weak-copyleft"]
    unknown = [p for p in rep.packages.values() if p.verdict == "unknown"]
    prop = [p for p in rep.packages.values() if p.verdict == "proprietary"]
    lines.append(f"{len(rep.packages)} installed packages checked across {', '.join(EXTRAS)}.")
    for extra, miss in rep.missing.items():
        if miss:
            lines.append(f"Not installed here, so not checked ({extra}): {', '.join(sorted(miss))}")
    if weak:
        lines.append("Allowed, weak copyleft (used unmodified): " +
                     ", ".join(f"{p.name} ({p.licence})" for p in sorted(weak, key=lambda p: p.name.lower())))
    if unknown:
        lines.append("Licence not stated in metadata (check by hand): " +
                     ", ".join(f"{p.name} {p.version}" for p in sorted(unknown, key=lambda p: p.name.lower())))
    if prop:
        lines.append("Proprietary but redistributable, pulled in by torch's GPU build (not GPL; install the CPU-only "
                     "torch build to avoid them): " + ", ".join(sorted(p.name for p in prop)))
    for m in rep.models:
        lines.append(f"Model {m['repo']}: {m['licence']} ({m['source']})")
    if rep.problems:
        lines.append("FAILED:")
        lines += [f"  {p}" for p in rep.problems]
    else:
        lines.append("Passed: no GPL or AGPL packages and no non-commercial models.")
    return "\n".join(lines)


TABLE_START = "<!-- licence-table:start (generated by `tau licence-check --write-readme`) -->"
TABLE_END = "<!-- licence-table:end -->"


def markdown_table(rep: Report) -> str:
    rows = ["| Package | Version | Licence | Needed by |", "|---|---|---|---|"]
    for p in sorted(rep.packages.values(), key=lambda p: (min(EXTRAS.index(e) for e in p.extras), p.name.lower())):
        extras = "core" if "core" in p.extras else ", ".join(sorted(p.extras, key=EXTRAS.index))
        lic = p.licence.replace("|", "/")
        mark = {"weak-copyleft": " (weak copyleft, used unmodified)",
                "proprietary": " (NVIDIA, only with torch's GPU build)"}.get(p.verdict, "")
        rows.append(f"| {p.name} | {p.version} | {lic}{mark} | {extras} |")
    for extra, miss in rep.missing.items():
        for name in sorted(miss):
            if name not in rep.packages:
                rows.append(f"| {name} | - | not installed where this table was made; checked on CI | {extra} |")
    rows += ["", "| Model (downloaded on first use) | Licence on its card | Used by |", "|---|---|---|"]
    for m in rep.models:
        rows.append(f"| {m['repo']} | {m['licence']} | {MODELS.get(m['repo'], {}).get('used_by', m['where'])} |")
    return "\n".join(rows)


def write_readme(rep: Report, readme: Path) -> bool:
    text = readme.read_text(encoding="utf-8")
    block = f"{TABLE_START}\n{markdown_table(rep)}\n{TABLE_END}"
    if TABLE_START in text and TABLE_END in text:
        a = text.index(TABLE_START)
        b = text.index(TABLE_END) + len(TABLE_END)
        new = text[:a] + block + text[b:]
    else:
        new = text.rstrip() + "\n\n" + block + "\n"
    if new != text:
        readme.write_text(new, encoding="utf-8")
        return True
    return False
