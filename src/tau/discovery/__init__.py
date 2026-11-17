"""Read-only lab discovery. ``run_discovery`` collects this machine and writes
it into the OKF lab map; nodes send their snapshot to the hub instead."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from tau.discovery.collect import collect, default_runner
from tau.discovery.labmap import LabMap


def run_discovery(lab_dir: Path, machine_name: Optional[str] = None, run=default_runner,
                  system: Optional[str] = None, include_tools: bool = True) -> Dict[str, Any]:
    snap = collect(run=run, system=system, machine_name=machine_name, include_tools=include_tools)
    LabMap(Path(lab_dir)).write_snapshot(snap)
    return snap


__all__ = ["run_discovery", "collect", "LabMap"]
