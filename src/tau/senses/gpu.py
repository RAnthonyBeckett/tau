"""GPU memory from ``nvidia-smi`` (read-only).

``TAU_NVIDIA_SMI`` can point at another executable with the same output,
which is how the tests fake a card.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict, List


def _smi() -> str | None:
    return os.environ.get("TAU_NVIDIA_SMI") or shutil.which("nvidia-smi")


def _call(args: List[str]) -> str:
    exe = _smi()
    if not exe:
        return ""
    cmd = [exe] + args
    if exe.endswith(".py"):
        import sys
        cmd = [sys.executable] + cmd
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""


def read_gpus() -> List[Dict[str, Any]]:
    out = _call(["--query-gpu=index,name,memory.total,memory.used,memory.free",
                 "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            gpus.append({"index": int(parts[0]), "name": parts[1], "total_mb": int(float(parts[2])),
                         "used_mb": int(float(parts[3])), "free_mb": int(float(parts[4]))})
        except ValueError:
            continue
    return gpus


def gpu_processes() -> List[Dict[str, Any]]:
    """Processes holding GPU memory (compute apps)."""
    out = _call(["--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"])
    procs = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            name = os.path.basename(parts[1].replace("\\", "/"))
            procs.append({"pid": int(parts[0]), "name": name, "used_mb": int(float(parts[2]))})
        except ValueError:
            continue
    return procs


def say_vram(gpus: List[Dict[str, Any]]) -> str:
    if not gpus:
        return "I can't see a GPU."
    g = gpus[0]
    free_gb = round(g["free_mb"] / 1024)
    total_gb = round(g["total_mb"] / 1024)
    return f"About {free_gb} of {total_gb} gigs free on the GPU."
