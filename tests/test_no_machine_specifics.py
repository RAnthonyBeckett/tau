"""A source grep: nothing machine-specific outside the example files."""
import re
import socket
import getpass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = [ROOT / "src", ROOT / "scripts", ROOT / "reflexes.yaml"]
EXAMPLES = {"profile.example.yaml", "banned_words.example.txt"}

PATTERNS = {
    "windows drive path": re.compile(r"\b[A-Za-z]:\\\\[A-Za-z]"),
    "unix home or mount path": re.compile(r"(?<![\w.])/(home|Users|opt|mnt|media|srv)/\w"),
    "private LAN address": re.compile(r"\b(10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)\b"),
    "hard-coded local port": re.compile(r"\b(localhost|127\.0\.0\.1):\d{2,5}\b"),
    "model file name": re.compile(r"\b[\w.-]+\.(gguf|safetensors|ggml)\b", re.I),
}
# the only lines allowed to say 0.0.0.0 are the guards that refuse it
ALL_IFACES = re.compile(r"0\.0\.0\.0")


def _files():
    for base in SCAN:
        if base.is_file():
            yield base
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in (".py", ".js", ".html", ".yaml", ".yml", ".toml", ".txt", ".md") \
                    and "vendor" not in p.parts and p.name not in EXAMPLES:
                yield p


def test_no_machine_specific_values_in_source():
    me = {getpass.getuser().lower(), socket.gethostname().lower()} - {"", "root", "user", "localhost", "administrator", "admin", "runner"}
    hits = []
    for f in _files():
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for name, rx in PATTERNS.items():
                if rx.search(line):
                    hits.append(f"{f.relative_to(ROOT)}:{i}: {name}: {line.strip()[:100]}")
            if ALL_IFACES.search(line) and not re.search(r"(never|unless|not lan_opt_in|refuse|opt in)", line):
                hits.append(f"{f.relative_to(ROOT)}:{i}: listens on every interface: {line.strip()[:100]}")
            low = line.lower()
            for word in me:
                # Only inside a string literal or a path: this machine's name or user hard-coded there
                # is the risk. The same word in ordinary code ("runner", "vm") is not.
                if re.search(rf"[\"'/\\][^\"'\n]*\b{re.escape(word)}\b", low):
                    hits.append(f"{f.relative_to(ROOT)}:{i}: this machine's name/user '{word}'")
    assert not hits, "\n".join(hits)


def test_profile_example_is_all_placeholders_for_machine_values():
    text = (ROOT / "profile.example.yaml").read_text()
    for key in ("GPU_VRAM_GB", "LOCAL_LLM_URL", "WORKSPACE_ROOTS", "HUB_HOST", "OS_USER", "MACHINE_NAME",
                "DISPLAY_PORT", "HUB_PORT", "LOCAL_MODEL", "HEAVY_APPS"):
        assert "{{" + key + "}}" in text, key
