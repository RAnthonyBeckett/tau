"""Read-only discovery of one machine. Parsers take raw command output so the
tests can feed faked Windows and Linux listings."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional

NETWORK_FS = {"nfs", "nfs4", "cifs", "smbfs", "smb3", "sshfs", "fuse.sshfs", "9p", "afs",
              "davfs", "fuse.rclone", "glusterfs", "ceph", "fuse.s3fs", "drvfs"}

Runner = Callable[[List[str]], str]


def default_runner(cmd: List[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ""


def _ps(script: str) -> List[str]:
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]


# -- Linux -----------------------------------------------------------------------------
def parse_systemctl(text: str) -> List[Dict[str, Any]]:
    out = []
    for line in text.splitlines():
        line = line.strip().lstrip("●* ").strip()
        if not line:
            continue
        parts = line.split(None, 4)
        if len(parts) < 4 or not parts[0].endswith(".service"):
            continue
        out.append({"name": parts[0][: -len(".service")], "display": parts[4] if len(parts) > 4 else "",
                    "load": parts[1], "state": parts[3], "active": parts[2], "start_mode": None,
                    "pid": None, "kind": "systemd"})
    return out


_SS_PROC = re.compile(r'\("([^"]+)",pid=(\d+)')


def parse_ss(text: str) -> List[Dict[str, Any]]:
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] not in ("tcp", "udp"):
            continue
        local = parts[4]
        addr, _, port = local.rpartition(":")
        try:
            port_n = int(port)
        except ValueError:
            continue
        m = _SS_PROC.search(line)
        out.append({"proto": parts[0], "address": addr.strip("[]"), "port": port_n,
                    "process": m.group(1) if m else None, "pid": int(m.group(2)) if m else None})
    return out


def rotational(device: str) -> Optional[bool]:
    base = re.sub(r"p?\d+$", "", os.path.basename(device))
    f = f"/sys/block/{base}/queue/rotational"
    try:
        with open(f) as fh:
            return fh.read().strip() == "1"
    except OSError:
        return None


# -- Windows ------------------------------------------------------------------------------
WIN_SERVICES = ("Get-CimInstance Win32_Service | "
                "Select-Object Name,DisplayName,State,StartMode,ProcessId | ConvertTo-Json -Compress")
WIN_PORTS = ("Get-NetTCPConnection -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess | "
             "ConvertTo-Json -Compress")
# Win32_Process carries each process's parent, so a child on a temporary port maps to its parent's fixed one.
WIN_PROCS = "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name | ConvertTo-Json -Compress"
WIN_DRIVES = ("Get-CimInstance Win32_LogicalDisk | Select-Object DeviceID,DriveType,FileSystem,Size,"
              "FreeSpace,ProviderName | ConvertTo-Json -Compress")
WIN_MEDIA = "Get-PhysicalDisk | Select-Object FriendlyName,MediaType | ConvertTo-Json -Compress"


def _json_list(text: str) -> List[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]


def parse_win_services(text: str) -> List[Dict[str, Any]]:
    return [{"name": s.get("Name"), "display": s.get("DisplayName") or "",
             "state": str(s.get("State") or "").lower(), "start_mode": s.get("StartMode"),
             "pid": s.get("ProcessId") or None, "kind": "windows"} for s in _json_list(text) if s.get("Name")]


def parse_win_procs(text: str) -> Dict[str, Dict[str, Any]]:
    """pid -> {name, ppid}, from Win32_Process (or Get-Process, which has no parent)."""
    out = {}
    for p in _json_list(text):
        pid = p.get("ProcessId", p.get("Id"))
        if pid is None:
            continue
        name = re.sub(r"\.exe$", "", str(p.get("Name") or p.get("ProcessName") or ""), flags=re.I)
        out[str(pid)] = {"name": name, "ppid": p.get("ParentProcessId")}
    return out


def parse_win_ports(ports_text: str, procs_text: str) -> List[Dict[str, Any]]:
    names = {int(k): v["name"] for k, v in parse_win_procs(procs_text).items()}
    seen = set()
    out = []
    for p in _json_list(ports_text):
        key = (p.get("LocalAddress"), p.get("LocalPort"))
        if key in seen:
            continue
        seen.add(key)
        pid = p.get("OwningProcess")
        out.append({"proto": "tcp", "address": p.get("LocalAddress"), "port": int(p.get("LocalPort")),
                    "pid": pid, "process": names.get(pid)})
    return out


def parse_win_drives(text: str, media_text: str = "") -> List[Dict[str, Any]]:
    hdd = any(str(m.get("MediaType")).upper() == "HDD" for m in _json_list(media_text))
    out = []
    for d in _json_list(text):
        dtype = int(d.get("DriveType") or 0)  # 2 removable, 3 fixed, 4 network, 5 optical
        network = dtype == 4
        out.append({"mount": d.get("DeviceID"), "fstype": d.get("FileSystem"),
                    "total_gb": round((d.get("Size") or 0) / 2**30, 1),
                    "free_gb": round((d.get("FreeSpace") or 0) / 2**30, 1),
                    "network": network, "remote": d.get("ProviderName"),
                    # Logical disks don't map cleanly to physical ones; with an HDD present
                    # a fixed disk is "maybe slow" (None) and the user can mark it.
                    "slow": True if dtype in (2, 5) else (None if hdd else False),
                    "never_walk": network or dtype in (2, 5)})
    return out


def parse_docker(text: str) -> List[Dict[str, Any]]:
    out = []
    for line in text.splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        out.append({"name": c.get("Names"), "image": c.get("Image"), "state": c.get("State"),
                    "ports": c.get("Ports"), "status": c.get("Status")})
    return out


# -- collection -----------------------------------------------------------------------------
def collect(run: Runner = default_runner, system: Optional[str] = None,
            machine_name: Optional[str] = None, include_tools: bool = True) -> Dict[str, Any]:
    """One read-only pass over this machine."""
    from tau import detect

    system = system or platform.system()
    snap: Dict[str, Any] = {"platform": system}
    basics = detect.machine_basics() if include_tools else {}
    snap["machine"] = {
        "name": machine_name or basics.get("MACHINE_NAME") or platform.node(),
        "os": basics.get("OS_NAME"), "cpu": basics.get("CPU_NAME"), "ram_gb": basics.get("RAM_GB"),
        "gpus": detect.gpus() if include_tools else [],
    }
    if system == "Windows":
        snap["services"] = parse_win_services(run(_ps(WIN_SERVICES)))
        procs_text = run(_ps(WIN_PROCS))
        snap["ports"] = parse_win_ports(run(_ps(WIN_PORTS)), procs_text)
        snap["procs"] = parse_win_procs(procs_text)
        snap["drives"] = parse_win_drives(run(_ps(WIN_DRIVES)), run(_ps(WIN_MEDIA)))
    else:
        snap["services"] = parse_systemctl(run(["systemctl", "list-units", "--type=service", "--all",
                                                "--no-pager", "--no-legend", "--plain"]))
        snap["ports"] = parse_ss(run(["ss", "-ltnupH"])) or psutil_ports()
        if any(not p.get("process") for p in snap["ports"]):
            from tau.discovery import portowners    # optional: names other users' programs

            snap["ports"] = portowners.fill_owners(snap["ports"], portowners.read(run))
        snap["procs"] = psutil_parents([p.get("pid") for p in snap["ports"]])
        snap["drives"] = linux_drives()
    snap["containers"] = parse_docker(run(["docker", "ps", "-a", "--format", "{{json .}}"]))
    if include_tools:
        snap["agent_clis"] = detect.agent_clis()
        snap["model_servers"] = [{"engine": b.engine, "url": b.url, "models": b.models}
                                 for b in detect.find_brains(env={}) if b.kind == "local"]
    else:
        snap.setdefault("agent_clis", [])
        snap.setdefault("model_servers", [])
    return snap


def psutil_ports() -> List[Dict[str, Any]]:
    """Listening TCP ports via psutil, for systems without ``ss``."""
    try:
        import psutil

        conns = psutil.net_connections(kind="inet")
    except Exception:
        return []
    out, seen = [], set()
    for c in conns:
        if c.status != "LISTEN" or not c.laddr:
            continue
        key = (c.laddr.ip, c.laddr.port)
        if key in seen:
            continue
        seen.add(key)
        name = None
        if c.pid:
            try:
                name = psutil.Process(c.pid).name()
            except Exception:
                pass
        out.append({"proto": "tcp", "address": c.laddr.ip, "port": c.laddr.port, "pid": c.pid, "process": name})
    return out


def psutil_parents(pids: List[Any]) -> Dict[str, Dict[str, Any]]:
    """pid -> {name, ppid} for these processes and their parents (a few levels up)."""
    try:
        import psutil
    except ImportError:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    todo = [p for p in pids if p]
    while todo and len(out) < 500:
        pid = todo.pop()
        if str(pid) in out:
            continue
        try:
            pr = psutil.Process(int(pid))
            out[str(pid)] = {"name": pr.name(), "ppid": pr.ppid()}
            if pr.ppid():
                todo.append(pr.ppid())
        except Exception:
            out[str(pid)] = {"name": None, "ppid": None}
    return out


SYSTEM_MOUNTS = {"/", "/home", "/usr", "/var"}


def linux_drives() -> List[Dict[str, Any]]:
    try:
        import psutil
    except ImportError:
        return []
    out = []
    for p in psutil.disk_partitions(all=True):
        if p.fstype in ("proc", "sysfs", "tmpfs", "devtmpfs", "cgroup", "cgroup2", "overlay",
                        "squashfs", "devpts", "mqueue", "debugfs", "tracefs", "securityfs",
                        "pstore", "bpf", "configfs", "fusectl", "hugetlbfs", "autofs", "binfmt_misc",
                        "nsfs", "ramfs", "rpc_pipefs", "efivarfs", ""):
            continue
        network = p.fstype in NETWORK_FS
        slow = None if network else rotational(p.device)
        try:
            # statvfs only; never walks the drive
            u = psutil.disk_usage(p.mountpoint) if not network else None
        except OSError:
            u = None
        out.append({"mount": p.mountpoint, "device": p.device, "fstype": p.fstype,
                    "total_gb": round(u.total / 2**30, 1) if u else None,
                    "free_gb": round(u.free / 2**30, 1) if u else None,
                    # The system drive is never marked never-walk even when it spins (or a VM's disk
                    # says it does): it's where the user's folders live, and folders are opt-in anyway.
                    "network": network, "slow": slow,
                    "never_walk": bool(network or (slow and p.mountpoint not in SYSTEM_MOUNTS))})
    return out
