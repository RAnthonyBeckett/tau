"""TAU: a voice-first lab assistant built as an extension of OpenJarvis.

TAU adds a permission model ("organs"), a hub/node lab map, a reflex router,
ears and voice, and a triangle display on top of OpenJarvis engines, tools,
speech backends and MCP client. See README.md.
"""

__version__ = "1.3.0"


def _no_console_windows(popen=None, nt=None) -> bool:
    """On Windows, stop every helper command TAU runs from flashing a console window.

    TAU's Start-menu launcher runs pythonw.exe (no console). Every subprocess then opened
    its own black console window, about every 30 seconds while the senses refreshed. Adding
    CREATE_NO_WINDOW to every Popen fixes it in one place. Returns True when it patched.
    """
    import os
    import subprocess

    popen = popen or subprocess.Popen
    nt = (os.name == "nt") if nt is None else nt
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000 if nt else 0)
    if not nt or not flag or getattr(popen, "_tau_quiet", False):
        return False
    original = popen.__init__

    def __init__(self, *args, **kwargs):
        kwargs["creationflags"] = (kwargs.get("creationflags") or 0) | flag
        original(self, *args, **kwargs)

    popen.__init__ = __init__
    popen._tau_quiet = True
    return True


_no_console_windows()
