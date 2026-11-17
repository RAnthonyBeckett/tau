"""Model server tracking and starting: remember how the local server runs and start it."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from tau import detect, engines
from tau.profile import get

_LOGGER = logging.getLogger("tau.modelserver")

_SECRET_WORDS = ("key", "token", "secret", "password", "passwd", "auth", "bearer")


def local_url(profile: Mapping[str, Any]) -> str:
    """The local model server address from the profile, or empty string."""
    return str(get(profile, "brain.local.url") or "")


def is_local(url: str) -> bool:
    """Whether the address points to this computer."""
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url if "://" in url else f"http://{url}")
        host = (parsed.hostname or "").lower()
        return host in ("localhost", "127.0.0.1", "::1")
    except Exception:
        return False


def reachable(profile: Mapping[str, Any], timeout: float = 1.5,
              probe: Optional[Callable] = None) -> bool:
    """Whether the local model server is answering."""
    url = local_url(profile)
    if not url:
        return False
    engine = get(profile, "brain.local.engine") or "openai_compat"
    probe_fn = probe or detect.probe_model_server
    try:
        try:
            f = probe_fn("ollama" if engine == "ollama" else "openai_compat", url, timeout=timeout)
        except TypeError:
            f = probe_fn("ollama" if engine == "ollama" else "openai_compat", url)
        return f is not None
    except Exception:
        return False


def port_owner(port: int, connections: Optional[Callable] = None,
               process: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    """Find the process listening on that TCP port."""
    try:
        import psutil

        conn_fn = connections or psutil.net_connections
        proc_cls = process or psutil.Process

        try:
            conns = conn_fn(kind="tcp")
        except TypeError:
            conns = conn_fn()

        target_pid = None
        for c in conns:
            laddr = getattr(c, "laddr", None)
            if laddr is None:
                continue
            p = getattr(laddr, "port", None)
            if p is None and isinstance(laddr, (tuple, list)) and len(laddr) >= 2:
                p = laddr[1]
            if p != port:
                continue
            status = getattr(c, "status", None)
            if status is not None and str(status).upper() not in ("LISTEN", "LISTENING"):
                continue
            pid = getattr(c, "pid", None)
            if pid:
                target_pid = pid
                break

        if target_pid is None:
            return None

        proc = proc_cls(target_pid)
        try:
            cmd = proc.cmdline()
        except Exception:
            return None
        if not cmd:
            return None

        try:
            cwd = proc.cwd()
        except Exception:
            cwd = None

        return {"command": list(cmd), "cwd": cwd}
    except Exception:
        return None


def holds_secret(command: Sequence[str]) -> bool:
    """True when any argument contains a credential word."""
    if not command:
        return False
    for arg in command:
        low = str(arg).lower()
        if any(w in low for w in _SECRET_WORDS):
            return True
    return False


def remember(profile: Dict[str, Any], path: Optional[Any] = None,
             owner: Optional[Callable[[int], Optional[Dict[str, Any]]]] = None,
             probe: Optional[Callable] = None,
             now: Optional[Any] = None) -> bool:
    """Remember how the local model server was started."""
    try:
        url = local_url(profile)
        if not is_local(url):
            return False
        if not reachable(profile, probe=probe):
            return False

        parsed = urllib.parse.urlparse(url if "://" in url else f"http://{url}")
        port = parsed.port
        if port is None:
            return False

        owner_fn = owner or port_owner
        info = owner_fn(port)
        if not info or not isinstance(info, dict):
            return False

        command = info.get("command")
        cwd = info.get("cwd")
        if not command:
            return False
        if holds_secret(command):
            return False

        existing = get(profile, "brain.local.start")
        if (isinstance(existing, dict)
                and existing.get("command") == list(command)
                and existing.get("cwd") == cwd):
            return False

        if now is not None:
            val = now() if callable(now) else now
            seen = val.isoformat() if hasattr(val, "isoformat") else str(val)
        else:
            seen = datetime.now().isoformat()

        entry = {"command": list(command), "cwd": cwd, "seen": seen}
        profile.setdefault("brain", {}).setdefault("local", {})["start"] = entry

        if path is not None:
            from tau import profile as prof_mod

            prof_path = Path(path)
            fresh = prof_mod.load_profile(prof_path, check=False)
            fresh.setdefault("brain", {}).setdefault("local", {})["start"] = entry
            prof_mod.save_profile(fresh, prof_path)

        return True
    except Exception as exc:
        _LOGGER.warning("could not remember model server: %s", exc)
        return False


@dataclass
class Way:
    label: str
    command: List[str]
    cwd: Optional[str] = None
    source: str = "remembered"


def find_way(profile: Mapping[str, Any],
             which: Callable[[str], Optional[str]] = shutil.which,
             ollama: Optional[Callable[[], Optional[str]]] = None) -> Optional[Way]:
    """Find how to start the model server."""
    start_data = get(profile, "brain.local.start")
    if isinstance(start_data, dict):
        cmd = start_data.get("command")
        cwd = start_data.get("cwd")
        if isinstance(cmd, list) and cmd:
            first = cmd[0]
            if os.path.isfile(first) or which(first):
                return Way("the way it was started last time", list(cmd), cwd=cwd, source="remembered")

    engine = get(profile, "brain.local.engine")
    if engine == "ollama":
        ollama_fn = ollama if ollama is not None else engines.ollama_path
        opath = ollama_fn()
        if opath:
            return Way("Ollama", [opath, "serve"], cwd=None, source="ollama")

    if engine == "lmstudio":
        lpath = which("lms")
        if lpath:
            return Way("LM Studio", [lpath, "server", "start"], cwd=None, source="lmstudio")

    return None


def seen_but_unknown(profile: Mapping[str, Any],
                      which: Callable[[str], Optional[str]] = shutil.which) -> Optional[str]:
    """When find_way finds nothing, check if a known server program is on PATH."""
    if find_way(profile, which=which) is not None:
        return None
    if which("llama-server"):
        return "llama.cpp's server"
    if which("ollama"):
        return "Ollama"
    if which("lms"):
        return "LM Studio"
    return None


def start(way: Way, profile: Mapping[str, Any], wait_s: float = 180.0,
          poll: float = 2.0, popen: Callable = subprocess.Popen,
          is_up: Optional[Callable[[], bool]] = None,
          sleep: Callable[[float], None] = time.sleep,
          log_file: Optional[Any] = None) -> bool:
    """Start the model server using the given Way."""
    check_up = is_up if is_up is not None else (lambda: reachable(profile))
    try:
        if check_up():
            return True

        detached_kw = engines._detached()
        out_target = subprocess.DEVNULL
        out_f = None
        if log_file:
            log_p = Path(log_file)
            log_p.parent.mkdir(parents=True, exist_ok=True)
            out_f = open(log_p, "a", encoding="utf-8")
            out_target = out_f

        try:
            proc = popen(
                way.command,
                cwd=way.cwd,
                stdin=subprocess.DEVNULL,
                stdout=out_target,
                stderr=out_target,
                **detached_kw
            )
        finally:
            if out_f is not None:
                try:
                    out_f.close()
                except Exception:
                    pass

        waited = 0.0
        while waited < wait_s:
            sleep(poll)
            waited += poll
            if check_up():
                return True
            if hasattr(proc, "poll"):
                code = proc.poll()
                if code is not None and code != 0:
                    if not check_up():
                        return False
            elif getattr(proc, "returncode", None) is not None:
                code = proc.returncode
                if code != 0 and not check_up():
                    return False

        return False
    except Exception as exc:
        _LOGGER.warning("could not start model server: %s", exc)
        return False
