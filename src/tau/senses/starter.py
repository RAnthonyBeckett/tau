"""The recommended starter set of senses, filled from what discovery found.

A fresh install should show real data on the display straight away, so setup
offers: the graphics card, drives, the brain's models, notifications, and a
handful of services picked from what discovery saw listening on this machine.
Everything here is read-only (``sense``); nothing is started or stopped.

Services are picked from listening ports, not from the full service list
(a normal Windows machine has over 200 services, nearly all part of Windows).
Operating-system processes are skipped, each program appears once, and known
model servers come first.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional

# Processes that are part of the operating system or its plumbing, not the user's lab.
SYSTEM_PROCESSES = {
    "system", "idle", "svchost", "lsass", "wininit", "services", "spoolsv", "smss", "csrss", "winlogon",
    "searchindexer", "searchhost", "lsaiso", "dashost", "msmpeng", "nissrv", "mdnsresponder", "jhi_service",
    "systemd", "systemd-resolved", "systemd-networkd", "systemd-timesyncd", "init", "sshd", "cupsd",
    "avahi-daemon", "dnsmasq", "rpcbind", "chronyd", "exim4", "master", "smbd", "nmbd", "rpc.statd",
    "dbus-daemon", "networkmanager", "containerd", "dockerd", "kdeconnectd", "spotify", "steam",
    "onedrive", "teams", "msedge", "chrome", "firefox", "code", "explorer", "nvcontainer",
    "nvidia web helper", "rapportd", "launchd", "remoting_host", "tau", "python", "pythonw", "node",
}
MODEL_SERVERS = ("llama-server", "llama_server", "llamacpp", "ollama", "lm studio", "lms", "vllm",
                 "koboldcpp", "text-generation", "tabbyapi", "sglang", "lemonade", "localai")
MAX_SERVICES = 8


def _clean(name: str) -> str:
    return re.sub(r"\.exe$", "", (name or "").strip(), flags=re.I)


EPHEMERAL_LOW = 49152     # ports from here up are handed out for a moment and change every restart


def _parent_port(pid: Any, fixed: Dict[Any, Dict[str, Any]], procs: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The nearest parent process that listens on a fixed port (a llama-server router's child -> 8080)."""
    seen = set()
    cur = procs.get(str(pid), {}).get("ppid") if pid is not None else None
    while cur not in (None, 0, "0") and cur not in seen and len(seen) < 8:
        seen.add(cur)
        if cur in fixed or str(cur) in {str(k) for k in fixed}:
            return fixed.get(cur) or next(v for k, v in fixed.items() if str(k) == str(cur))
        cur = procs.get(str(cur), {}).get("ppid")
    return None


def starter_services(snapshot: Mapping[str, Any], machine: str = "", limit: Optional[int] = MAX_SERVICES,
                     skip_system: bool = True) -> List[Dict[str, Any]]:
    """Services worth watching, from discovery's listening ports.

    Only fixed ports are used. A process listening on a temporary port (49152 and up) is
    replaced by its nearest parent that listens on a fixed one, or left out.
    """
    ports = list(snapshot.get("ports") or [])
    procs = snapshot.get("procs") or {}
    fixed: Dict[Any, Dict[str, Any]] = {}
    for p in ports:
        try:
            port = int(p.get("port"))
        except (TypeError, ValueError):
            continue
        if p.get("pid") is not None and 0 < port < EPHEMERAL_LOW:
            if p["pid"] not in fixed or port < fixed[p["pid"]]["port"]:
                fixed[p["pid"]] = {"process": p.get("process"), "port": port, "pid": p["pid"]}
    picked: Dict[str, Dict[str, Any]] = {}
    for p in ports:
        try:
            port = int(p.get("port"))
        except (TypeError, ValueError):
            continue
        proc = p.get("process")
        if port >= EPHEMERAL_LOW or port <= 0:
            parent = _parent_port(p.get("pid"), fixed, procs)
            if parent is None:
                continue        # a temporary port with no fixed parent: it would flash "down" after a restart
            proc, port = parent["process"] or procs.get(str(parent["pid"]), {}).get("name"), parent["port"]
        name = _clean(str(proc or ""))
        if not name or name in picked or (skip_system and name.lower() in SYSTEM_PROCESSES):
            continue
        is_model = any(m in name.lower() for m in MODEL_SERVERS)
        if skip_system and port < 1024 and not is_model:
            continue        # low ports are nearly always system services
        picked[name] = {"name": name, "port": port, "importance": 2 if is_model else 3,
                        "found_by": "discovery"}
        if machine:
            picked[name]["machine"] = machine
    rows = sorted(picked.values(), key=lambda s: (s["importance"], s["name"].lower()))
    return rows[:limit] if limit else rows


# Each sense: (organ, plain question, what TAU will show)
SENSES = (
    ("gpu", "the graphics card", "how much of its memory is free, so I don't load a model that won't fit"),
    ("drives", "your drives", "free space on each one (I never look inside slow or network drives)"),
    ("models", "the brain's models", "which model is answering"),
    ("services", "services", "whether each one is up"),
    ("notifications", "notifications", "how many recent ones there are"),
)
