"""Where TAU keeps its files.

Nothing here names a real folder. The home folder is, in order:
``$TAU_HOME``, then ``<OpenJarvis config dir>/tau`` (OpenJarvis decides that
from ``$OPENJARVIS_HOME`` / ``$XDG_DATA_HOME`` / the user's home), then
``~/.tau`` if OpenJarvis is not importable.
"""

from __future__ import annotations

import os
from pathlib import Path


def tau_home() -> Path:
    env = os.environ.get("TAU_HOME")
    if env:
        return Path(env).expanduser().resolve()
    try:
        from openjarvis.core.paths import get_config_dir

        return (get_config_dir() / "tau").resolve()
    except Exception:  # OpenJarvis missing or its home is invalid
        return (Path.home() / ".tau").resolve()


def profile_path() -> Path:
    return tau_home() / "profile.yaml"


def log_dir(profile: dict | None = None) -> Path:
    if profile:
        p = (profile.get("tau") or {}).get("log_dir")
        if p:
            return Path(p).expanduser()
    return tau_home() / "log"


def brain_dir(profile: dict | None = None) -> Path:
    if profile:
        p = (profile.get("tau") or {}).get("brain_dir")
        if p:
            return Path(p).expanduser()
    return tau_home() / "tau_brain"


def state_dir() -> Path:
    return tau_home() / "state"


def package_data(name: str) -> Path:
    return Path(__file__).parent / "data" / name


def shipped_file(name: str) -> Path:
    """A file kept at the top of the ``tau/`` source folder.

    Wheels copy these into ``tau/data/`` (see pyproject ``force-include``);
    a source checkout reads them in place.
    """
    inside = package_data(name)
    if inside.exists():
        return inside
    return Path(__file__).resolve().parents[2] / name


def example_profile() -> Path:
    return shipped_file("profile.example.yaml")
