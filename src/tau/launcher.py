"""Put ``tau`` on the PATH and add a "TAU" menu entry, without admin rights.

``python -m tau.launcher install`` / ``python -m tau.launcher remove``. Both
installers call it; uninstalling calls ``remove``.

* Windows: a small ``bin`` folder next to TAU's environment holds ``tau.cmd``,
  and that folder (not the environment's ``Scripts`` folder) goes on the user
  PATH. ``Scripts`` also holds ``python.exe`` and ``pip.exe``, and putting it
  on the PATH would quietly replace the user's own ``python``. A Start-menu
  shortcut "TAU" runs ``pythonw.exe -m tau run --window --no-console``, so the
  display opens with no console window.
* Linux: ``~/.local/bin/tau`` (a two-line shim) and
  ``~/.local/share/applications/tau.desktop`` with ``Terminal=false``.
"""

from __future__ import annotations

import os
import platform
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import List, Optional

RUN_ARGS = ["-m", "tau", "run", "--window", "--no-console"]
MARK = "# added by TAU's installer"


def venv_dir() -> Path:
    return Path(sys.prefix)


def _windows() -> bool:
    return platform.system() == "Windows"


def scripts_dir(venv: Optional[Path] = None) -> Path:
    venv = venv or venv_dir()
    return venv / ("Scripts" if _windows() else "bin")


# -- icon (drawn here, so no image file ships) --------------------------------------------
def icon_png(size: int = 64) -> bytes:
    """A small TAU icon: red inverted triangle outline around an amber ring on black."""
    import math

    px = bytearray()
    cx, cy = size / 2, size * 0.44
    top, bottom = size * 0.12, size * 0.92
    half = size * 0.46

    def edge_dist(x: float, y: float) -> float:
        a = (cx - half, top)
        b = (cx + half, top)
        c = (cx, bottom)
        d = 1e9
        for (x1, y1), (x2, y2) in ((a, b), (b, c), (c, a)):
            dx, dy = x2 - x1, y2 - y1
            t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
            d = min(d, math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)))
        return d

    for y in range(size):
        px.append(0)   # PNG filter byte
        for x in range(size):
            r, g, b, a = 6, 3, 3, 255
            d = edge_dist(x + 0.5, y + 0.5)
            if d < size * 0.035:
                r, g, b = 255, 40, 40
            ring = abs(math.hypot(x + 0.5 - cx, y + 0.5 - cy) - size * 0.15)
            if ring < size * 0.03:
                r, g, b = 255, 181, 71
            px += bytes((r, g, b, a))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(px), 9)) + chunk(b"IEND", b""))


def icon_ico(png: bytes, size: int = 64) -> bytes:
    """An .ico holding one PNG image (Windows Vista and later read these)."""
    return struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(png), 22) + png


# -- install / remove ----------------------------------------------------------------------
def install(home: Optional[Path] = None, venv: Optional[Path] = None) -> List[str]:
    venv = venv or venv_dir()
    return _install_windows(venv) if _windows() else _install_linux(venv, home or Path.home())


def remove(home: Optional[Path] = None, venv: Optional[Path] = None) -> List[str]:
    venv = venv or venv_dir()
    return _remove_windows(venv) if _windows() else _remove_linux(venv, home or Path.home())


def _linux_paths(home: Path):
    data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return home / ".local" / "bin" / "tau", data / "applications" / "tau.desktop", data / "icons" / "tau.png"


def _install_linux(venv: Path, home: Path) -> List[str]:
    shim, desktop, icon = _linux_paths(home)
    target = scripts_dir(venv) / "tau"
    python = scripts_dir(venv) / "python"
    done = []
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(f'#!/bin/sh\n{MARK}\nexec "{target}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    done.append(f"the tau command: {shim}")
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes(icon_png())
    desktop.parent.mkdir(parents=True, exist_ok=True)
    exec_line = " ".join(_desktop_quote(str(a)) for a in [python, *RUN_ARGS])
    desktop.write_text("\n".join([
        "[Desktop Entry]", "Type=Application", "Name=TAU", "Comment=Voice-first lab assistant",
        f"Exec={exec_line}", f"Icon={icon}", "Terminal=false", "Categories=Utility;", MARK, ""]),
        encoding="utf-8")
    done.append(f"a TAU entry in your apps menu: {desktop}")
    if str(shim.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        prof = home / ".profile"
        line = f'export PATH="$HOME/.local/bin:$PATH"  {MARK}'
        text = prof.read_text(encoding="utf-8") if prof.exists() else ""
        if MARK not in text:
            prof.write_text(text + ("\n" if text and not text.endswith("\n") else "") + line + "\n", encoding="utf-8")
        done.append("~/.local/bin added to your PATH (open a new terminal to use `tau`)")
    return done


def _desktop_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"' if any(c in s for c in ' "\\') else s


def _remove_linux(venv: Path, home: Path) -> List[str]:
    done = []
    for p in _linux_paths(home):
        if p.exists():
            p.unlink()
            done.append(f"removed {p}")
    prof = home / ".profile"
    if prof.exists():
        text = prof.read_text(encoding="utf-8")
        if MARK in text:
            prof.write_text("".join(l for l in text.splitlines(True) if MARK not in l), encoding="utf-8")
            done.append("removed the PATH line from ~/.profile")
    return done


def _win_paths(venv: Path):
    base = venv.parent
    start = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return base / "bin", start / "TAU.lnk", base / "tau.ico"


def other_users_profile(target: Path, home: Optional[Path] = None) -> Optional[str]:
    """The name of another user's profile folder that ``target`` sits in, or None.

    Round 4 bug: the owner's Start-menu shortcut pointed into
    the Administrator account's profile folder, because install.ps1 ran in a window opened with "Run as
    administrator" while signed in as the separate Administrator account. %LOCALAPPDATA% was
    that account's, so the environment (sys.prefix here) was too, and the owner's own launch
    did nothing. The installer now refuses an elevated window, and this check refuses to make
    a shortcut into someone else's profile.
    """
    home = Path(home or Path.home())
    users_root = home.parent
    t = Path(os.path.normcase(os.path.abspath(str(target))))
    root = Path(os.path.normcase(os.path.abspath(str(users_root))))
    mine = Path(os.path.normcase(os.path.abspath(str(home))))
    try:
        rel = t.relative_to(root)
    except ValueError:
        return None             # not under the users folder at all (a custom TAU_INSTALL_DIR): fine
    try:
        t.relative_to(mine)
        return None
    except ValueError:
        if not rel.parts:
            return None
        try:
            raw_target = Path(os.path.abspath(str(target)))
            raw_root = Path(os.path.abspath(str(users_root)))
            return raw_target.relative_to(raw_root).parts[0]
        except ValueError:
            return rel.parts[0]


def _install_windows(venv: Path) -> List[str]:
    bin_dir, lnk, ico = _win_paths(venv)
    other = other_users_profile(venv)
    if other:
        raise RuntimeError(f"TAU's environment is in another account's folder ({other}). Run the installer "
                           "again in a normal PowerShell window (not 'Run as administrator').")
    done = []
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "tau.cmd").write_text(f'@echo off\r\n"{scripts_dir(venv) / "tau.exe"}" %*\r\n', encoding="utf-8")
    if _user_path_add(str(bin_dir)):
        done.append(f"{bin_dir} added to your PATH (new windows will find `tau`)")
    else:
        done.append(f"the tau command: {bin_dir / 'tau.cmd'}")
    ico.write_bytes(icon_ico(icon_png()))
    pythonw = scripts_dir(venv) / "pythonw.exe"
    if not pythonw.exists():
        pythonw = scripts_dir(venv) / "python.exe"
    args = " ".join(RUN_ARGS)
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:TAU_LNK); "
          "$s.TargetPath = $env:TAU_TARGET; $s.Arguments = $env:TAU_ARGS; "
          "$s.WorkingDirectory = $env:USERPROFILE; $s.IconLocation = $env:TAU_ICO; "
          "$s.Description = 'TAU, a voice-first lab assistant'; $s.Save()")
    lnk.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TAU_LNK=str(lnk), TAU_TARGET=str(pythonw), TAU_ARGS=args, TAU_ICO=str(ico))
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not lnk.exists():
        raise RuntimeError("couldn't make the Start-menu shortcut")
    done.append("a Start-menu shortcut: TAU")
    return done


def _remove_windows(venv: Path) -> List[str]:
    bin_dir, lnk, ico = _win_paths(venv)
    done = []
    if lnk.exists():
        lnk.unlink()
        done.append("removed the Start-menu shortcut")
    if _user_path_remove(str(bin_dir)):
        done.append(f"removed {bin_dir} from your PATH")
    for p in (bin_dir / "tau.cmd", ico):
        if p.exists():
            p.unlink()
    return done


def _user_path_get():
    import winreg

    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE)
    try:
        value, kind = winreg.QueryValueEx(key, "Path")
    except FileNotFoundError:
        value, kind = "", winreg.REG_EXPAND_SZ
    return key, value, kind


def _broadcast() -> None:
    try:
        import ctypes

        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)
    except Exception:
        pass


def _user_path_add(folder: str) -> bool:
    import winreg

    key, value, kind = _user_path_get()
    parts = [p for p in value.split(";") if p]
    if any(os.path.normcase(p.rstrip("\\")) == os.path.normcase(folder.rstrip("\\")) for p in parts):
        winreg.CloseKey(key)
        return False
    winreg.SetValueEx(key, "Path", 0, kind or winreg.REG_EXPAND_SZ, ";".join(parts + [folder]))
    winreg.CloseKey(key)
    _broadcast()
    return True


def _user_path_remove(folder: str) -> bool:
    import winreg

    key, value, kind = _user_path_get()
    parts = [p for p in value.split(";") if p]
    keep = [p for p in parts if os.path.normcase(p.rstrip("\\")) != os.path.normcase(folder.rstrip("\\"))]
    if keep != parts:
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(keep))
    winreg.CloseKey(key)
    if keep != parts:
        _broadcast()
    return keep != parts


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    what = argv[0] if argv else "install"
    try:
        done = install() if what == "install" else remove() if what == "remove" else None
    except Exception as exc:
        import logging

        logging.getLogger("tau.launcher").exception("launcher step failed")
        print(f"    I couldn't finish this step ({type(exc).__name__}).")
        return 1
    if done is None:
        print("    Use: python -m tau.launcher install | remove")
        return 2
    for line in done:
        print(f"    {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
