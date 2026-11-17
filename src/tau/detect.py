"""Detect what this machine has, read-only. Nothing here changes anything.

Default engine addresses are read from OpenJarvis itself (its engine
registry knows each server's default host), so TAU carries no port numbers.
"""

from __future__ import annotations

import getpass
import os
import platform
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tau.brainclient import get_json, post_json

def _agent_cli_names() -> tuple:
    try:
        from tau.connect.recipes import load
        return tuple(str(e["command"]) for e in load("workers") if isinstance(e, dict) and e.get("command"))
    except Exception:
        return ()


AGENT_CLIS = _agent_cli_names()

# Environment variable names only; values are never read here beyond "is it set".
CLOUD_KEYS = {
    "ANTHROPIC_API_KEY": ("anthropic", "https://api.anthropic.com/v1"),
    "OPENAI_API_KEY": ("openai", "https://api.openai.com/v1"),
    "OPENROUTER_API_KEY": ("openrouter", "https://openrouter.ai/api/v1"),
    "GROQ_API_KEY": ("groq", "https://api.groq.com/openai/v1"),
    "GEMINI_API_KEY": ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "MISTRAL_API_KEY": ("mistral", "https://api.mistral.ai/v1"),
}


@dataclass
class FoundBrain:
    kind: str           # "local" | "cloud" | "agent_cli"
    engine: str         # ollama | llamacpp | lmstudio | vllm | openai_compat | provider name
    url: str = ""
    models: List[str] = field(default_factory=list)
    key_env: Optional[str] = None
    label: str = ""
    sizes: Dict[str, int] = field(default_factory=dict)   # model -> bytes, when the server says
    meta: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # model -> metadata (experts, file)
    loaded: Dict[str, bool] = field(default_factory=dict)  # model -> in memory now, when the server says


def default_engine_hosts() -> Dict[str, str]:
    """Default host for each local engine, taken from OpenJarvis."""
    hosts: Dict[str, str] = {}
    try:
        from openjarvis.engine.ollama import OllamaEngine

        hosts["ollama"] = OllamaEngine._DEFAULT_HOST
    except Exception:
        pass
    try:
        from openjarvis.engine import openai_compat_engines as oce

        for key, (_cls, host, _prefix) in oce._ENGINES.items():
            if key in ("llamacpp", "lmstudio", "vllm", "sglang", "lemonade"):
                hosts[key] = host
    except Exception:
        pass
    try:
        from tau.oj_bridge import openjarvis_engine_hosts

        hosts.update(openjarvis_engine_hosts())
    except Exception:
        pass
    return hosts


def probe_model_server(engine: str, url: str, timeout: float = 1.0) -> Optional[FoundBrain]:
    base = url.rstrip("/")
    try:
        if engine == "ollama":
            data = get_json(f"{base}/api/tags", timeout)
            rows = data.get("models", [])
            models = [m.get("name", "") for m in rows]
            sizes = {m.get("name", ""): int(m["size"]) for m in rows if m.get("size")}
            meta, loaded = {}, {}
        else:
            if base.endswith("/v1"):
                base = base[:-3]
            data = get_json(f"{base}/v1/models", timeout)
            rows = data.get("data", [])
            models = [m.get("id", "") for m in rows]
            # llama.cpp's server reports each model's file size under "meta"
            sizes = {m.get("id", ""): int((m.get("meta") or {})["size"]) for m in rows
                     if isinstance(m.get("meta"), dict) and (m.get("meta") or {}).get("size")}
            meta = {m.get("id", ""): _model_meta(m) for m in rows}
            loaded = {m.get("id", ""): _status_loaded(m) for m in rows if _status_loaded(m) is not None}
    except Exception:
        return None
    return FoundBrain("local", engine, base, [m for m in models if m], label=f"{ENGINE_NAMES.get(engine, engine)} at {base}",
                      sizes=sizes, meta=meta, loaded=loaded)


def _gguf_paths(obj: Any, depth: int = 0) -> List[str]:
    if depth > 3:
        return []
    if isinstance(obj, str):
        return [obj] if obj.lower().endswith(".gguf") else []
    if isinstance(obj, dict):
        return [p for v in obj.values() for p in _gguf_paths(v, depth + 1)]
    if isinstance(obj, list):
        return [p for v in obj for p in _gguf_paths(v, depth + 1)]
    return []


def _model_meta(row: Dict[str, Any]) -> Dict[str, Any]:
    """What a model server says about one model: llama.cpp's "meta", plus the GGUF file's own
    expert count when the server names a file that is on this machine."""
    out = dict(row.get("meta") or {}) if isinstance(row.get("meta"), dict) else {}
    for p in _gguf_paths(row):
        if os.path.isfile(p):
            from tau.engines import gguf_metadata

            out.update(gguf_metadata(p))
            out["file"] = p
            break
    return out


def _status_loaded(row: Dict[str, Any]) -> Optional[bool]:
    st = row.get("status")
    if isinstance(st, dict):
        st = st.get("value")
    if isinstance(st, str):
        return st.lower() == "loaded"
    return None


def model_details(found: "FoundBrain", timeout: float = 2.0, limit: int = 30) -> "FoundBrain":
    """Fill in experts (MoE or dense) and what's in memory, where the server can say."""
    if found.engine != "ollama":
        return found
    for name in found.models[:limit]:
        try:
            info = post_json(f"{found.url}/api/show", {"model": name}, timeout).get("model_info") or {}
        except Exception:
            continue
        found.meta[name] = {k: v for k, v in info.items() if k.endswith("expert_count")} or {"gguf_read": bool(info)}
    try:
        running = {m.get("name") for m in get_json(f"{found.url}/api/ps", timeout).get("models", [])}
        found.loaded = {n: n in running for n in found.models}
    except Exception:
        pass
    return found


ENGINE_NAMES = {"ollama": "Ollama", "llamacpp": "llama.cpp", "lmstudio": "LM Studio", "vllm": "vLLM",
                "sglang": "SGLang", "lemonade": "Lemonade", "openai_compat": "a model server"}


def find_brains(extra_urls: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None,
                timeout: float = 1.0) -> List[FoundBrain]:
    env = dict(os.environ if env is None else env)
    found: List[FoundBrain] = []
    seen = set()
    candidates = list(default_engine_hosts().items())
    for u in extra_urls or []:
        candidates += [("ollama", u), ("openai_compat", u)]
    for engine, url in candidates:
        key = url.rstrip("/")
        if key in seen:
            continue
        hit = probe_model_server(engine, url, timeout)
        if hit:
            seen.add(key)
            found.append(hit)
    for var, (provider, url) in CLOUD_KEYS.items():
        if env.get(var):
            found.append(FoundBrain("cloud", provider, url, key_env=var,
                                    label=f"{provider} (key found in {var})"))
    for cli in agent_clis():
        found.append(FoundBrain("agent_cli", cli["name"], label=f"{cli['name']} CLI at {cli['path']}"))
    return found


def agent_clis() -> List[Dict[str, str]]:
    out = []
    for name in AGENT_CLIS:
        p = shutil.which(name)
        if p:
            out.append({"name": name, "path": p})
    return out


def _run(cmd: List[str], timeout: float = 5) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def gpus() -> List[Dict[str, Any]]:
    from tau.senses.gpu import read_gpus

    return read_gpus()


def ram_gb() -> float:
    try:
        import psutil

        return round(psutil.virtual_memory().total / 2**30, 1)
    except Exception:
        return 0.0


def cpu_name() -> str:
    try:
        from openjarvis.core.config import _detect_cpu_brand

        return _detect_cpu_brand() or platform.processor()
    except Exception:
        return platform.processor() or platform.machine()


def audio_devices() -> Dict[str, List[Dict[str, str]]]:
    """One row per physical device, System default first (see :mod:`tau.audio`)."""
    from tau import audio

    return audio.list_devices()


def cameras() -> List[str]:
    if platform.system() == "Linux":
        return sorted(p for p in (os.listdir("/dev") if os.path.isdir("/dev") else [])
                      if p.startswith("video"))
    if platform.system() == "Windows":
        out = _run(["powershell", "-NoProfile", "-Command",
                    "Get-PnpDevice -Class Camera -Status OK | Select-Object -ExpandProperty FriendlyName"])
        return [x.strip() for x in out.splitlines() if x.strip()]
    return []


def monitors() -> List[Dict[str, Any]]:
    if platform.system() == "Windows":
        try:
            import ctypes
            from ctypes import wintypes

            mons: List[Dict[str, Any]] = []
            proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                      ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

            def cb(h, dc, rect, lp):
                r = rect.contents
                mons.append({"index": len(mons), "x": r.left, "y": r.top,
                             "w": r.right - r.left, "h": r.bottom - r.top})
                return 1

            ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(cb), 0)
            return mons
        except Exception:
            return []
    out = _run(["xrandr", "--listmonitors"])
    mons = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        for p in parts:
            if "x" in p and "+" in p and "/" in p:
                try:
                    wh, x, y = p.split("+")
                    w, h = wh.split("x")
                    mons.append({"index": len(mons), "x": int(x), "y": int(y),
                                 "w": int(w.split("/")[0]), "h": int(h.split("/")[0])})
                except ValueError:
                    pass
    return mons


def machine_basics() -> Dict[str, Any]:
    g = gpus()
    return {
        "MACHINE_NAME": socket.gethostname(),
        "OS_NAME": f"{platform.system()} {platform.release()}",
        "OS_USER": getpass.getuser(),
        "CPU_NAME": cpu_name(),
        "RAM_GB": ram_gb(),
        "GPU_NAME": g[0]["name"] if g else None,
        "GPU_VRAM_GB": round(g[0]["total_mb"] / 1024, 1) if g else None,
    }


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        try:
            s.bind((host, int(port)))
            return True
        except OSError:
            return False


def fixed_port(preferred: int, host: str = "127.0.0.1", tries: int = 20) -> int:
    """``preferred`` if it's free, else the next free fixed port above it. Never 0, never a
    temporary (49152+) port: TAU's own ports must stay the same across restarts."""
    from tau.settings import EPHEMERAL_LOW

    for p in range(int(preferred), min(int(preferred) + tries, EPHEMERAL_LOW)):
        if port_free(p, host):
            return p
    return int(preferred)


def lan_address() -> str:
    """This machine's LAN address (no traffic is sent; UDP connect only picks a route)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed; only selects an interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
