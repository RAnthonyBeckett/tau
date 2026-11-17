"""Focused window and processes.

Windows: ``GetForegroundWindow`` + ``GetWindowTextW`` via ctypes (no extra
packages). Linux/X11: ``xdotool`` if present. Wayland gives no portable way to
read the focused window, so TAU says it can't rather than guessing.

UI Automation cannot see inside canvas apps (game engines, video editors);
TAU only uses the window title and owning process, never the controls inside.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import Any, Dict, List


def focused_window() -> Dict[str, Any]:
    if platform.system() == "Windows":
        return _win_focused()
    if shutil.which("xdotool") and os.environ.get("DISPLAY"):
        try:
            wid = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True,
                                 timeout=2).stdout.strip()
            title = subprocess.run(["xdotool", "getwindowname", wid], capture_output=True, text=True,
                                   timeout=2).stdout.strip()
            pid = subprocess.run(["xdotool", "getwindowpid", wid], capture_output=True, text=True,
                                 timeout=2).stdout.strip()
            return {"title": title, "pid": int(pid) if pid else None, "process": _proc_name(pid),
                    "fullscreen": False}
        except Exception:
            pass
    return {"title": None, "pid": None, "process": None, "fullscreen": False}


def _proc_name(pid: Any) -> Any:
    try:
        import psutil
        return psutil.Process(int(pid)).name()
    except Exception:
        return None


def _win_focused() -> Dict[str, Any]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    full = rect.left <= 0 and rect.top <= 0 and rect.right >= sw and rect.bottom >= sh
    return {"title": buf.value, "pid": pid.value, "process": _proc_name(pid.value), "fullscreen": full}


def top_processes(top: int = 5) -> List[Dict[str, Any]]:
    import psutil

    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            procs.append({"pid": p.info["pid"], "name": p.info["name"],
                          "rss_mb": round(p.info["memory_info"].rss / 2**20)})
        except Exception:
            continue
    return sorted(procs, key=lambda x: x["rss_mb"], reverse=True)[:top]
