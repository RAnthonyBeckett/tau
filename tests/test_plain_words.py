"""Item 7: every user-facing string follows docs/ACCESSIBILITY.md.

Scans what TAU says (setup, check, runtime, display, installers) for:
* banned words (the style guard's own list);
* length: one short message at a time (questions shorter still);
* raw exception text or tracebacks shown to the user;
* every installer failure naming what broke and one next step.
"""
import ast
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "tau"
BANNED = [w.strip().lower() for w in (ROOT / "banned_words.example.txt").read_text().splitlines()
          if w.strip() and not w.startswith("#")]
BANNED_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in BANNED) + r")\b", re.I)

SAY_CALLS = {"say", "flash", "_tell", "add_message", "Say", "print", "plain_failure"}
ASK_CALLS = {"ask", "yes", "secret"}
MAX_SAY, MAX_ASK = 320, 220


def _text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{x}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _text(node.left), _text(node.right)
        return (a or "{x}") + (b or "{x}") if (a or b) else None
    return None


def user_strings():
    out = []
    for f in sorted(SRC.rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if fn not in SAY_CALLS | ASK_CALLS or not node.args:
                continue
            t = _text(node.args[0])
            if t:
                out.append((f"{f.relative_to(ROOT)}:{node.lineno}", fn, t))
    return out


def test_found_the_strings():
    assert len(user_strings()) > 100


def test_no_banned_words_in_anything_tau_says():
    hits = [(where, m.group(0)) for where, _, t in user_strings() for m in [BANNED_RE.search(t)] if m]
    assert not hits, hits


def test_one_short_message_at_a_time():
    long = [(where, len(t)) for where, fn, t in user_strings()
            if len(t) > (MAX_ASK if fn in ASK_CALLS else MAX_SAY)]
    assert not long, long


def test_no_raw_exception_text_on_screen():
    bad = [where for where, fn, t in user_strings()
           if fn != "print" and re.search(r"\{x\}\)?$", t) and "exc" in t]
    for f in sorted(SRC.rglob("*.py")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"(say|flash|_tell)\(.*\{(exc|e)\}", line):
                bad.append(f"{f.relative_to(ROOT)}:{n}")
    assert not bad, bad


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks, self._skip = [], False

    def handle_starttag(self, tag, attrs):
        self._skip = tag in ("style", "script")
        for k, v in attrs:
            if k in ("title", "placeholder", "aria-label") and v:
                self.chunks.append(v)

    def handle_endtag(self, tag):
        self._skip = False

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.chunks.append(data.strip())


def test_display_page_text():
    p = _Text()
    p.feed((SRC / "display" / "static" / "index.html").read_text(encoding="utf-8"))
    js = (SRC / "display" / "static" / "tau.js").read_text(encoding="utf-8")
    texts = p.chunks + re.findall(r"'([A-Z][^'\n]{12,})'", js)
    assert any("Talk" in t for t in texts)
    for t in texts:
        assert not BANNED_RE.search(t), t
        assert len(t) <= MAX_SAY, t


def installer_lines():
    out = []
    for name in ("install.ps1", "install.sh"):
        for n, line in enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r"\b(Say|say|Fail|fail)\s+(['\"])(.*)", line.strip())
            if m and not line.strip().startswith("#"):
                out.append((f"{name}:{n}", m.group(1), line.strip()))
    return out


def test_installer_text():
    lines = installer_lines()
    assert len(lines) > 40
    for where, kind, line in lines:
        assert not BANNED_RE.search(line), where
        if kind.lower() == "fail":
            # what broke, then one next step: two quoted arguments
            assert len(re.findall(r"(?<!\\)[\"'](?:[^\"'\\]|\\.)+[\"']", line)) >= 2 or "$" in line, where


def test_accessibility_doc_exists_and_names_the_rules():
    doc = (ROOT / "docs" / "ACCESSIBILITY.md").read_text(encoding="utf-8")
    for rule in ("One question at a time", "3 seconds", "log", "No hidden controls", "next step"):
        assert rule.lower() in doc.lower(), rule


def test_docs_use_plain_words():
    for name in ("docs/ACCESSIBILITY.md", "README.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        text = text.split("## Licences")[0]          # the generated licence table quotes package names
        hits = sorted({m.group(0).lower() for m in BANNED_RE.finditer(text)})
        assert not hits, (name, hits)
