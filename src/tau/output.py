"""Where a reply goes. Nothing ever disappears on a timer.

* A path, a command or a one-liner -> the clipboard ("it's on your clipboard").
* A short answer -> spoken, and a **pin card** that waits until the user opens
  or clears it.
* Heavy work (code, several files, debugging) -> the agent hand-off. Code is
  never dumped into the display.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from tau import paths

CLIPBOARD_BACKEND: Optional[Callable[[str], None]] = None  # tests and plugins can set this

_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|~?/|\.{1,2}[\\/]|\\\\)[^\n]*$")
_CMD_START = re.compile(r"^(?:\$ |> |PS> )?(?:sudo |cd |git |pip |python |npm |docker |kubectl |ssh |curl |"
                        r"tau |jarvis |winget |choco |apt |systemctl |ls |dir |cat |type )", re.I)
_CODE_FENCE = re.compile(r"```")
_HEAVY_HINT = re.compile(r"\b(refactor|debug|stack ?trace|traceback|fix (the|this|my) (bug|code|build|test)|"
                         r"write (a|the) (script|function|class|program|module)|implement|several files|"
                         r"multi-?file|unit tests?)\b", re.I)


def copy_to_clipboard(text: str) -> None:
    if CLIPBOARD_BACKEND is not None:
        CLIPBOARD_BACKEND(text)
        return
    system = platform.system()
    if system == "Windows":
        subprocess.run(["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
                       input=text, text=True, check=True)
        return
    if system == "Darwin":
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
        return
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, input=text, text=True, check=True)
            return
    raise RuntimeError("no clipboard tool found (install wl-clipboard or xclip)")


# -- pin cards ------------------------------------------------------------------------
class PinBoard:
    """Pin cards persist in ``state/pins.json`` until opened or cleared."""

    def __init__(self, file: Optional[Path] = None, on_change: Callable[[], None] = lambda: None):
        self.file = Path(file) if file else paths.state_dir() / "pins.json"
        self.on_change = on_change
        self._lock = threading.Lock()

    def _load(self) -> List[Dict[str, Any]]:
        if not self.file.exists():
            return []
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except ValueError:
            return []

    def _save(self, pins: List[Dict[str, Any]]) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(pins, indent=1), encoding="utf-8")

    def pin(self, title: str, text: str, kind: str = "answer") -> Dict[str, Any]:
        card = {"id": uuid.uuid4().hex[:8], "title": title[:80], "text": text, "kind": kind,
                "at": time.time(), "opened": False}
        with self._lock:
            pins = self._load()
            pins.append(card)
            self._save(pins)
        self.on_change()
        return card

    def all(self) -> List[Dict[str, Any]]:
        return self._load()

    def waiting(self) -> List[Dict[str, Any]]:
        return [p for p in self._load() if not p["opened"]]

    def open(self, pin_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            pins = self._load()
            for p in pins:
                if p["id"] == pin_id:
                    p["opened"] = True
                    self._save(pins)
                    self.on_change()
                    return p
        return None

    def clear(self, pin_id: Optional[str] = None) -> int:
        with self._lock:
            pins = self._load()
            keep = [p for p in pins if pin_id is not None and p["id"] != pin_id]
            self._save(keep)
        self.on_change()
        return len(pins) - len(keep)


# -- routing ------------------------------------------------------------------------------
@dataclass
class Routed:
    kind: str                 # clipboard | pin | speak | handoff
    say: str
    clipboard: Optional[str] = None
    pin: Optional[Dict[str, Any]] = None


def looks_like_one_liner(text: str) -> Optional[str]:
    t = text.strip()
    fence = re.findall(r"```[\w-]*\n?(.*?)```", t, re.S)
    if len(fence) == 1 and fence[0].strip().count("\n") == 0:
        return fence[0].strip()
    m = re.fullmatch(r"`([^`\n]+)`\.?", t)
    if m:
        return m.group(1)
    if "\n" not in t and len(t) < 300 and (_PATH.match(t) or _CMD_START.match(t)):
        return t.lstrip("$> ").removeprefix("PS> ")
    return None


def is_heavy(request: str, reply: str = "") -> bool:
    if _HEAVY_HINT.search(request):
        return True
    blocks = re.findall(r"```", reply)
    if len(blocks) >= 4:
        return True
    code = re.findall(r"```[\w-]*\n(.*?)```", reply, re.S)
    return any(c.count("\n") >= 6 for c in code)


def first_sentences(text: str, n: int = 2) -> str:
    s = re.split(r"(?<=[.!?])\s+", re.sub(r"```.*?```", "", text, flags=re.S).strip())
    return " ".join(s[:n]).strip()


def route(reply: str, request: str, board: PinBoard, clipboard_ok: bool,
          short_chars: int = 280, extra_pin: str = "") -> Routed:
    one = looks_like_one_liner(reply)
    if one and clipboard_ok:
        copy_to_clipboard(one)
        return Routed("clipboard", "It's on your clipboard.", clipboard=one)
    if is_heavy(request, reply):
        return Routed("handoff", "That's a job for a coding agent.")
    clean = reply.strip()
    if "```" in clean:  # never put code on the display; keep it on a pin card
        card = board.pin(first_sentences(clean, 1) or "Code", clean, kind="code")
        return Routed("pin", first_sentences(clean) + " The details are on a pin card.", pin=card)
    if len(clean) <= short_chars and not extra_pin:
        card = board.pin(clean[:60], clean)
        return Routed("pin", clean, pin=card)
    card = board.pin(first_sentences(clean, 1)[:60], clean + ("\n\n" + extra_pin if extra_pin else ""))
    return Routed("pin", first_sentences(clean) + " The rest is on a pin card.", pin=card)
