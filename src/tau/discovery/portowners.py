"""Which program owns each listening port on a Linux node.

The node helper runs as the user, so ``ss`` can't name programs that belong to other users
(Plex's 32400, for example). The owner: "knowing what's on a given machine's ports, esp if
it's active, gives me usable decision points."

``tau node --setup-port-owners`` prints one sudoers line that allows exactly
``ss -tlnpH`` (read-only: listening TCP ports and their programs) without a password, and,
if the user says yes, installs it with the user's own sudo. sudo asks for the password in
the terminal itself: TAU never asks for it, sees it or stores it. Discovery then runs
``sudo -n ss -tlnpH`` (``-n`` never prompts) and fills in the programs ``ss`` couldn't name.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from tau import paths

SUDOERS_FILE = "/etc/sudoers.d/tau-port-owners"
ARGS = "-tlnpH"
_PROC = re.compile(r'\("([^"]+)",pid=(\d+)')


def _on_windows() -> bool:
    return os.name == "nt"


def ss_path() -> str:
    return shutil.which("ss") or "/usr/bin/ss"


def sudoers_line(user: Optional[str] = None, ss: Optional[str] = None) -> str:
    return f"{user or getpass.getuser()} ALL=(root) NOPASSWD: {ss or ss_path()} {ARGS}"


def marker() -> Path:
    return paths.state_dir() / "port_owners.json"


def enabled() -> bool:
    return marker().exists()


def parse(text: str) -> List[Dict[str, Any]]:
    """``ss -tlnpH`` lines (no Netid column): State Recv-Q Send-Q Local Peer [users:(...)]."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] != "LISTEN":
            continue
        addr, _, port = parts[3].rpartition(":")
        try:
            port_n = int(port)
        except ValueError:
            continue
        m = _PROC.search(line)
        out.append({"proto": "tcp", "address": addr.strip("[]"), "port": port_n,
                    "process": m.group(1) if m else None, "pid": int(m.group(2)) if m else None})
    return out


def read(run: Callable[[List[str]], str]) -> List[Dict[str, Any]]:
    """The privileged read, only once it has been set up. Never prompts."""
    if not enabled():
        return []
    return parse(run(["sudo", "-n", ss_path(), ARGS]))


def fill_owners(ports: List[Dict[str, Any]], owned: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fill in the process for ports the user-level read couldn't name; add TCP ports it missed."""
    by = {(p["address"], p["port"]): p for p in owned if p.get("process")}
    seen = set()
    for p in ports:
        key = (p.get("address"), p.get("port"))
        seen.add(key)
        if not p.get("process") and key in by and p.get("proto", "tcp") == "tcp":
            p["process"], p["pid"] = by[key]["process"], by[key]["pid"]
    for key, p in by.items():
        if key not in seen:
            ports.append(dict(p))
    return ports


def setup(say: Callable[[str], None] = print, ask: Callable[[str], bool] = lambda q: False,
          run: Callable[..., Any] = subprocess.run) -> int:
    """Print the sudoers line; with a yes, install it with the user's sudo and check it works."""
    if _on_windows():
        say("This is for Linux nodes. Windows already names the program on each port.")
        return 0
    line = sudoers_line()
    say("To let me name the program on every port, sudo needs this one line. It allows only")
    say(f"`{ss_path()} {ARGS}` (a read-only list of listening ports), without a password:")
    say("")
    say(f"    {line}")
    say("")
    say(f"It goes in {SUDOERS_FILE}. I never ask for, see or keep your password; sudo asks for it itself.")
    if not ask("Install it now with your sudo?"):
        say(f"Okay. To do it yourself: sudo visudo -f {SUDOERS_FILE}, paste the line, save. "
            "Then run this again to switch it on.")
        if _works(run):
            _mark()
            say("It's already allowed, so I've switched it on.")
        return 0
    with tempfile.NamedTemporaryFile("w", suffix=".sudoers", delete=False) as fh:
        fh.write(line + "\n")
        tmp = fh.name
    try:
        if run(["sudo", "visudo", "-cf", tmp]).returncode != 0:
            say("sudo's checker didn't accept the line, so nothing was installed.")
            return 1
        if run(["sudo", "install", "-m", "0440", "-o", "root", "-g", "root", tmp, SUDOERS_FILE]).returncode != 0:
            say("Installing it didn't work, so nothing changed.")
            return 1
    finally:
        os.unlink(tmp)
    if not _works(run):
        say("It's installed, but the check still asks for a password. Nothing was switched on.")
        return 1
    _mark()
    say("Done. The next map of this machine names the program on every port.")
    return 0


def _works(run: Callable[..., Any]) -> bool:
    try:
        r = run(["sudo", "-n", ss_path(), ARGS], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _mark() -> None:
    m = marker()
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(json.dumps({"at": time.time(), "line": sudoers_line()}), encoding="utf-8")
