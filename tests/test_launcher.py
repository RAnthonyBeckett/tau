"""`tau` on PATH and a TAU menu entry with no console (bug 8). The Windows half
(user PATH, Start-menu shortcut) runs for real in CI's install job."""
import os
import platform
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tau import launcher

linux = pytest.mark.skipif(platform.system() != "Linux", reason="the Linux launcher")


def fake_venv(tmp_path):
    venv = tmp_path / "tau" / "venv"
    b = venv / "bin"
    b.mkdir(parents=True)
    (b / "tau").write_text("#!/bin/sh\necho tau-from-venv \"$@\"\n")
    (b / "tau").chmod(0o755)
    (b / "python").symlink_to(sys.executable)
    return venv


@linux
def test_linux_shim_and_desktop_entry(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    venv = fake_venv(tmp_path)
    done = launcher.install(home=home, venv=venv)
    shim = home / ".local" / "bin" / "tau"
    assert os.access(shim, os.X_OK)
    # A fresh login shell finds `tau` and it runs the environment's tau.
    out = subprocess.run(["bash", "-lc", "tau status"], capture_output=True, text=True,
                         env={"HOME": str(home), "PATH": "/usr/bin:/bin"}).stdout
    assert out.strip() == "tau-from-venv status"
    desktop = (home / ".local" / "share" / "applications" / "tau.desktop").read_text()
    assert "Terminal=false" in desktop and "Name=TAU" in desktop
    exec_argv = shlex.split(next(l for l in desktop.splitlines() if l.startswith("Exec="))[5:])
    assert Path(exec_argv[0]).exists() and exec_argv[1:] == launcher.RUN_ARGS
    assert "--no-console" in exec_argv and "--window" in exec_argv
    icon = next(l for l in desktop.splitlines() if l.startswith("Icon="))[5:]
    assert Path(icon).read_bytes().startswith(b"\x89PNG")
    assert any("PATH" in d for d in done)

    gone = launcher.remove(home=home, venv=venv)
    assert not shim.exists() and not (home / ".local/share/applications/tau.desktop").exists()
    assert "TAU" not in (home / ".profile").read_text() and gone


def test_icon_files_are_valid():
    png = launcher.icon_png(32)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and b"IEND" in png[-12:]
    ico = launcher.icon_ico(png, 32)
    assert ico[:4] == b"\x00\x00\x01\x00" and ico.endswith(png)


def test_windows_bin_folder_is_not_the_scripts_folder(tmp_path, monkeypatch):
    # Scripts\\ holds python.exe and pip.exe; putting it on PATH would replace the user's python.
    monkeypatch.setattr(launcher, "_windows", lambda: True)
    venv = tmp_path / "TAU" / "venv"
    bin_dir, lnk, ico = launcher._win_paths(venv)
    assert bin_dir == tmp_path / "TAU" / "bin" and bin_dir != launcher.scripts_dir(venv)
    assert lnk.name == "TAU.lnk"
