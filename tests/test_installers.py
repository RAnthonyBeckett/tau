"""Installers: install.sh dry run here; the full clean-container run and the
PowerShell parse/dry-run are opt-in because they need Docker images."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


posix_only = pytest.mark.skipif(os.name == "nt", reason="install.sh is the Linux/macOS installer")


@posix_only
def test_install_sh_parses_and_dry_runs(tmp_path):
    assert subprocess.run(["bash", "-n", str(ROOT / "install.sh")]).returncode == 0
    env = dict(os.environ, TAU_INSTALL_DIR=str(tmp_path / "tau"), HOME=str(tmp_path),
               PATH="/usr/bin:/bin")       # no uv on PATH: the installer fetches its own
    r = subprocess.run(["bash", str(ROOT / "install.sh"), "--dry-run"], capture_output=True, text=True, env=env,
                       timeout=60)
    out = r.stdout
    assert r.returncode == 0, out + r.stderr
    for line in ("[1/7] Python tools", "[2/7] OpenJarvis", "[3/7] TAU", "[4/7] Voice packages",
                 "[5/7] Voice and hearing models", "[6/7] The tau command", "[7/7] Setup",
                 "would run:", "tau setup", "fetch-models", "tau.launcher install", "and downloads about 1.8 GB"):
        assert line in out, line
    assert "requirements-kokoro.txt" in out and "--no-deps" in out
    assert "venv --seed --python 3.12" in out
    # The default path never runs OpenJarvis's installer, never compiles Rust, never installs Ollama.
    low = out.lower()
    for banned in ("open-jarvis.github.io", "rustup", "cargo", "ollama"):
        assert banned not in low, banned
    assert not (tmp_path / "tau").exists()          # a dry run changes nothing


@posix_only
def test_install_sh_openjarvis_installer_only_when_asked(tmp_path):
    env = dict(os.environ, TAU_INSTALL_DIR=str(tmp_path / "tau"), HOME=str(tmp_path),
               TAU_OPENJARVIS_INSTALLER=str(tmp_path / "missing.sh"))
    r = subprocess.run(["bash", str(ROOT / "install.sh"), "--dry-run", "--use-openjarvis-installer"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert "OpenJarvis's own installer" in r.stdout


@posix_only
def test_install_sh_unknown_option_is_one_plain_sentence():
    r = subprocess.run(["bash", str(ROOT / "install.sh"), "--frobnicate"], capture_output=True, text=True)
    assert r.returncode == 2 and r.stdout.strip().count("\n") == 0


DOCKER = shutil.which("docker") and os.environ.get("TAU_TEST_DOCKER") == "1"


@pytest.mark.skipif(not DOCKER, reason="set TAU_TEST_DOCKER=1 with a working Docker to run")
def test_install_ps1_parses_and_dry_runs_under_pwsh7():
    script = ('$e=$null;$t=$null;[System.Management.Automation.Language.Parser]::ParseFile("/t/install.ps1",'
              '[ref]$t,[ref]$e)|Out-Null; "ERRORS=" + $e.Count; $env:LOCALAPPDATA="/tmp/l"; $env:TEMP="/tmp";'
              '$env:TAU_OPENJARVIS_INSTALLER="/nope.ps1"; & /t/install.ps1 -DryRun; "EXIT=$LASTEXITCODE"')
    r = subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:/t:ro", "mcr.microsoft.com/powershell:lts-ubuntu-22.04",
                        "pwsh", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=300)
    assert "ERRORS=0" in r.stdout and "EXIT=0" in r.stdout and "would run:" in r.stdout, r.stdout + r.stderr
