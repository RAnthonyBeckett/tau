"""Other devices on the local network, from the OS neighbour (ARP) table and
mDNS announcements only. TAU never port-scans a machine that has no node."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from typing import Dict, List

_IPV4 = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")
_MAC = re.compile(r"([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})")


def _run(cmd: List[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=8).stdout
    except Exception:
        return ""


def parse_neighbour_table(text: str) -> List[Dict[str, str]]:
    out, seen = [], set()
    for line in text.splitlines():
        ip = _IPV4.search(line)
        mac = _MAC.search(line)
        if not ip or not mac:
            continue
        m = mac.group(1).lower().replace("-", ":")
        if m in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00") or ip.group(1) in seen:
            continue
        if ip.group(1).startswith(("224.", "239.", "255.")):
            continue
        seen.add(ip.group(1))
        out.append({"address": ip.group(1), "mac": m})
    return out


def list_neighbours(mdns: bool = True) -> List[Dict[str, str]]:
    if platform.system() == "Windows":
        rows = parse_neighbour_table(_run(["arp", "-a"]))
    else:
        rows = parse_neighbour_table(_run(["ip", "neigh"]) or _run(["arp", "-an"]))
    if mdns and shutil.which("avahi-browse"):
        names = _run(["avahi-browse", "-atrp", "--no-db-lookup"])  # passive listen, ends itself
        for line in names.splitlines():
            parts = line.split(";")
            if len(parts) > 7 and parts[0] == "=":
                for r in rows:
                    if r["address"] == parts[7]:
                        r["name"] = parts[6]
    return rows
