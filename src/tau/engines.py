"""Model engines: find what's already here, and only if nothing is, offer Ollama.

TAU never makes a model engine mandatory. It looks for a running or installed
llama.cpp server, LM Studio, Ollama, vLLM, or any OpenAI-compatible URL the
user types (``tau.detect.find_brains`` does the probing). Only when none is
found does setup *offer* Ollama plus one small model, with the size and an
estimate of the time said first.

Installing Ollama on Windows never waits on the setup program's process tree:
OllamaSetup.exe launches Ollama's tray app (and relaunches it if quit), so
``Start-Process -Wait`` or ``Popen.wait()`` on it can hang for ever. TAU starts
the setup program, then polls for ``ollama`` on disk and for the server
answering, with a time limit, and says what it is doing every few seconds.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

OLLAMA_WINDOWS_SETUP = "https://ollama.com/download/OllamaSetup.exe"
OLLAMA_LINUX_SCRIPT = "https://ollama.com/install.sh"
OLLAMA_SETUP_MB = 1100      # OllamaSetup.exe, about 1.1 GB (it bundles GPU libraries)

Say = Callable[[str], None]


# -- starter models ---------------------------------------------------------------------------
@dataclass
class Starter:
    tag: str
    size_gb: float
    why: str


# Qwen3 is Apache-2.0. Sizes are Ollama's default (Q4) downloads.
STARTERS = (
    (10.0, Starter("qwen3:8b", 5.2, "fits your graphics card's memory with room to spare")),
    (5.0, Starter("qwen3:4b", 2.5, "fits your graphics card's memory")),
    (0.0, Starter("qwen3:1.7b", 1.4, "small enough to run without a big graphics card")),
)


def pick_starter(vram_gb: Optional[float], ram_gb: Optional[float]) -> Starter:
    """Pick one small, permissively licensed model sized to this machine."""
    vram = float(vram_gb or 0)
    ram = float(ram_gb or 0)
    if vram <= 0:
        # CPU only: keep it small so replies stay quick.
        return Starter("qwen3:4b", 2.5, "runs on the processor at a usable speed with your memory") \
            if ram >= 24 else STARTERS[-1][1]
    for need, s in STARTERS:
        if vram >= need:
            return s
    return STARTERS[-1][1]


def minutes_for(mb: float, mbit_per_s: float = 40.0) -> int:
    """A rough download time at an ordinary home connection (40 Mbit/s), rounded up."""
    return max(1, int(round(mb * 8 / mbit_per_s / 60 + 0.49)))


# -- fit-to-machine sorting -----------------------------------------------------------------
_PARAMS = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])")


def estimate_gb(name: str, size_bytes: Optional[int] = None) -> Optional[float]:
    """Model size in GB: the server's own figure if it gave one, else from the name ("7b" at Q4)."""
    if size_bytes:
        return round(size_bytes / 1e9, 1)
    m = _PARAMS.search(name.replace("_", "-"))
    if not m:
        return None
    params_b = float(m.group(1))
    bits = 4.5
    low = name.lower()
    if "q8" in low or "8bit" in low:
        bits = 8.5
    elif "f16" in low or "fp16" in low or "bf16" in low:
        bits = 16
    return round(params_b * bits / 8 + 0.3, 1)


@dataclass
class Ranked:
    name: str
    size_gb: Optional[float]
    fits: Optional[bool]
    arch: Optional[str] = None      # "MoE" | "dense" | None (not stated)


def rank_models(models: Sequence[str], vram_gb: Optional[float], ram_gb: Optional[float],
                sizes: Optional[Dict[str, int]] = None,
                meta: Optional[Dict[str, Dict[str, Any]]] = None) -> Tuple[List[Ranked], Optional[str]]:
    """Sort models by how well they fit this machine.

    Returns the ranked list and a few words on why the first is suggested, or
    ``None`` when there's no real reason to suggest one (sizes unknown).
    The rule: the biggest model that fits in the graphics card's memory (or,
    with no card, in half the RAM) is suggested; models that fit come first,
    biggest first, then the rest smallest first.
    """
    sizes = sizes or {}
    budget = (float(vram_gb) * 0.9) if vram_gb else (float(ram_gb) * 0.5 if ram_gb else None)
    rows = []
    for m in models:
        gb = estimate_gb(m, sizes.get(m))
        fits = None if (gb is None or budget is None) else gb <= budget
        rows.append(Ranked(m, gb, fits, architecture(m, (meta or {}).get(m))))
    known = [r for r in rows if r.fits is not None]
    if not known:
        return rows, None
    fit = sorted([r for r in rows if r.fits], key=lambda r: -(r.size_gb or 0))
    too_big = sorted([r for r in rows if r.fits is False], key=lambda r: r.size_gb or 0)
    unknown = [r for r in rows if r.fits is None]
    ranked = fit + too_big + unknown
    if not fit:
        return ranked, None
    top = ranked[0]
    where = f"your {vram_gb:g} GB graphics card" if vram_gb else f"your {ram_gb:g} GB of memory"
    return ranked, f"about {top.size_gb:g} GB, the biggest that fits {where}"


# -- MoE or dense ---------------------------------------------------------------------------
# A mixture-of-experts model says so in its name ("8x7B", "30B-A3B", "MoE", or a family that only
# comes as MoE) or in its GGUF metadata ("<arch>.expert_count" above zero). Anything else is
# "not stated": a name alone can't prove a model is dense.
_MOE_NAME = re.compile(r"(\d+x\d+(\.\d+)?b)|([-_.]a\d+(\.\d+)?b\b)|(\bmoe\b)|(mixtral)|(deepseek-?v[23])|(gpt-oss)"
                       r"|(qwen\d?(\.\d)?-?moe)|(granite-?\d?(\.\d)?-?moe)|(olmoe)|(phi-?3\.5-?moe)|(jamba)", re.I)


def architecture(name: str, meta: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """``"MoE"``, ``"dense"`` or ``None`` (not stated), from the name or GGUF/server metadata."""
    meta = meta or {}
    for k, v in meta.items():
        if str(k).endswith("expert_count"):
            try:
                return "MoE" if int(v) > 0 else "dense"
            except (TypeError, ValueError):
                pass
    if _MOE_NAME.search((name or "").replace(" ", "-")):
        return "MoE"
    if meta.get("gguf_read"):           # the whole GGUF header was read and had no experts
        return "dense"
    return None


def gguf_metadata(path: Any, keys_like: Sequence[str] = ("expert_count", "general.architecture"),
                  limit: int = 4096) -> Dict[str, Any]:
    """Read a few metadata values from a GGUF file's header (never the weights)."""
    import struct

    out: Dict[str, Any] = {}
    sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
    fmt = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return out
            version = struct.unpack("<I", f.read(4))[0]
            if version < 2:
                return out
            _tensors, n_kv = struct.unpack("<QQ", f.read(16))

            def text() -> str:
                n = struct.unpack("<Q", f.read(8))[0]
                return f.read(n).decode("utf-8", "replace")

            def value(t: int) -> Any:
                if t == 8:
                    return text()
                if t == 9:
                    inner, n = struct.unpack("<IQ", f.read(12))
                    if inner == 8:
                        for _ in range(n):
                            text()
                    else:
                        f.seek(sizes.get(inner, 1) * n, 1)
                    return None
                return struct.unpack(fmt[t], f.read(sizes[t]))[0]

            for _ in range(min(n_kv, limit)):
                key = text()
                t = struct.unpack("<I", f.read(4))[0]
                v = value(t)
                if any(k in key for k in keys_like):
                    out[key] = v
            out["gguf_read"] = True
    except (OSError, struct.error, KeyError, ValueError):
        return out
    return out


# -- Ollama ---------------------------------------------------------------------------------
def ollama_url() -> str:
    """Ollama's address: $OLLAMA_HOST, else OpenJarvis's default (TAU carries no port numbers)."""
    env = os.environ.get("OLLAMA_HOST")
    if env:
        return env if "://" in env else f"http://{env}"
    from tau.detect import default_engine_hosts

    return default_engine_hosts().get("ollama", "")


def ollama_path() -> Optional[str]:
    p = shutil.which("ollama")
    if p:
        return p
    if platform.system() == "Windows":
        cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if cand.exists():
            return str(cand)
    return None


def ollama_running(url: Optional[str] = None, timeout: float = 1.5) -> bool:
    url = url or ollama_url()
    if not url:
        return False
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/version", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _download(url: str, dest: Path, say: Say, label: str, every: float = 3.0,
              opener: Callable = urllib.request.urlopen) -> None:
    with opener(url, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done, start, last = 0, time.monotonic(), 0.0
        with open(dest, "wb") as f:
            while True:
                buf = r.read(1 << 20)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                now = time.monotonic()
                if now - last >= every:
                    last = now
                    say(progress_line(label, done, total, now - start))
    say(f"    {label}: downloaded.")


def progress_line(label: str, done: int, total: int, elapsed: float) -> str:
    mb = done / 1e6
    if total:
        rate = done / max(elapsed, 0.1)
        left = (total - done) / max(rate, 1)
        return (f"    {label}: {mb:,.0f} of {total / 1e6:,.0f} MB, "
                f"about {_human_secs(left)} left")
    return f"    {label}: {mb:,.0f} MB so far"


def _human_secs(s: float) -> str:
    if s < 60:
        return f"{max(5, int(round(s / 5.0)) * 5)} seconds"
    m = int(round(s / 60))
    return f"{m} minute{'s' if m != 1 else ''}"


def install_ollama(say: Say, timeout: float = 900, poll: float = 2.0,
                   popen: Callable = subprocess.Popen, download: Callable = _download,
                   running: Callable[[], bool] = ollama_running,
                   find: Callable[[], Optional[str]] = ollama_path,
                   sleep: Callable[[float], None] = time.sleep,
                   system: Optional[str] = None) -> bool:
    """Install Ollama without hanging. Returns True once its server answers."""
    system = system or platform.system()
    if running():
        say("    Ollama is already running.")
        return True
    if find() is None:
        if system == "Windows":
            dest = Path(tempfile.gettempdir()) / "OllamaSetup.exe"
            say(f"    Downloading Ollama's setup program (about {OLLAMA_SETUP_MB / 1000:.1f} GB, "
                f"roughly {minutes_for(OLLAMA_SETUP_MB)} minutes).")
            download(OLLAMA_WINDOWS_SETUP, dest, say, "Ollama setup")
            say("    Running Ollama's setup quietly. Its window may flash up; that's normal.")
            # No wait on the process tree: the setup starts Ollama's tray app and keeps it alive.
            popen([str(dest), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"],
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "Linux":
            say("    Running Ollama's own Linux installer. It may ask for your password (sudo).")
            # The Linux installer leaves no tray app behind, so waiting on it is safe.
            proc = popen(["sh", "-c", f"curl -fsSL {OLLAMA_LINUX_SCRIPT} | sh"])
            if hasattr(proc, "wait"):
                proc.wait()
        else:
            say("    On this system, install Ollama from ollama.com, then choose 'check again'.")
            return False
    waited, started_server, last_note = 0.0, False, 0.0
    while waited < timeout:
        if running():
            say("    Ollama is installed and running.")
            return True
        exe = find()
        if exe and not started_server and waited >= 20:
            # Installed but its server isn't up (no tray app on Linux, or it was closed): start it.
            popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  **_detached())
            started_server = True
        if waited - last_note >= 15:
            last_note = waited
            what = "starting its server" if exe else "installing"
            say(f"    Still {what} ({int(waited)} seconds so far; this usually takes under 2 minutes).")
        sleep(poll)
        waited += poll
    say("    Ollama didn't start in time. Open Ollama from the Start menu, then choose 'check again'.")
    return False


def _detached() -> Dict:
    if platform.system() == "Windows":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def pull_model(tag: str, say: Say, url: Optional[str] = None, every: float = 3.0,
               opener: Callable = urllib.request.urlopen) -> bool:
    """Download a model through Ollama's own API, saying progress in plain words."""
    url = url or ollama_url()
    req = urllib.request.Request(url.rstrip("/") + "/api/pull", method="POST",
                                 data=json.dumps({"model": tag, "stream": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    start, last, ok = time.monotonic(), 0.0, False
    try:
        with opener(req, timeout=3600) as r:
            for raw in _lines(r):
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if ev.get("error"):
                    say(f"    Ollama couldn't download {tag}: {str(ev['error'])[:100]}")
                    return False
                if ev.get("status") == "success":
                    ok = True
                now = time.monotonic()
                if ev.get("total") and now - last >= every:
                    last = now
                    say(progress_line(tag, int(ev.get("completed") or 0), int(ev["total"]), now - start))
    except Exception as exc:
        say(f"    The download of {tag} stopped ({type(exc).__name__}). Choose it again to resume.")
        return False
    if ok:
        say(f"    {tag} is ready.")
    return ok


def _lines(resp) -> Iterable[str]:
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else str(raw).strip()
        if line:
            yield line
