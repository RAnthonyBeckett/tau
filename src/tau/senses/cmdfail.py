"""Command-failure capture, at run time only.

Two ways in:

* ``tau wrap -- <command ...>`` runs the command, passes its output through
  unchanged, keeps the last 4 KB of stderr in memory, and records a failure
  (command, exit code, stderr tail) when it exits non-zero.
* Shell hooks (``tau hook bash|zsh|powershell``) record the command line and
  exit code of failed commands. A hook cannot see stderr, so those records
  have no error text. TAU can't recover stderr after the fact and doesn't
  claim to.

Records live in ``<TAU home>/state/cmdfail.jsonl`` (last 50 kept).
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from tau import paths

KEEP = 50


def _file() -> Path:
    return paths.state_dir() / "cmdfail.jsonl"


def record(command: str, code: int, stderr_tail: str = "", cwd: str = "", source: str = "wrap") -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    rows = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
    rows.append(json.dumps({"at": time.time(), "command": command, "code": code,
                            "stderr": stderr_tail[-4000:], "cwd": cwd, "source": source}))
    f.write_text("\n".join(rows[-KEEP:]) + "\n", encoding="utf-8")


def last_failure() -> Optional[Dict[str, Any]]:
    f = _file()
    if not f.exists():
        return None
    rows = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    return json.loads(rows[-1]) if rows else None


def recent_failures(n: int = 5) -> List[Dict[str, Any]]:
    f = _file()
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()[-n:] if l.strip()]


def wrap(argv: List[str]) -> int:
    """Run argv, mirror its output, record a failure with the stderr tail."""
    import os

    tail: List[str] = []
    proc = subprocess.Popen(argv, stdout=None, stderr=subprocess.PIPE, text=True, errors="replace")

    def pump():
        for line in proc.stderr:
            sys.stderr.write(line)
            sys.stderr.flush()
            tail.append(line)
            while sum(len(x) for x in tail) > 4000:
                tail.pop(0)

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    code = proc.wait()
    t.join(timeout=2)
    if code != 0:
        record(subprocess.list2cmdline(argv), code, "".join(tail), cwd=os.getcwd(), source="wrap")
    return code


HOOKS = {
    "bash": r'''# TAU: record failed commands (command + exit code; a hook cannot see stderr)
__tau_rec() { local c=$?; [ $c -ne 0 ] && tau _record --code $c --source bash -- "$(history 1 | sed 's/^ *[0-9]* *//')" >/dev/null 2>&1; return $c; }
PROMPT_COMMAND="__tau_rec${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
''',
    "zsh": r'''# TAU: record failed commands (command + exit code; a hook cannot see stderr)
__tau_rec() { local c=$?; [ $c -ne 0 ] && tau _record --code $c --source zsh -- "$(fc -ln -1)" >/dev/null 2>&1; }
precmd_functions+=(__tau_rec)
''',
    "powershell": r'''# TAU: record failed commands (command + exit code; a hook cannot see stderr)
$global:__tauPrompt = $function:prompt
function global:prompt {
  if (-not $?) {
    $h = Get-History -Count 1
    if ($h) { tau _record --code $LASTEXITCODE --source powershell -- "$($h.CommandLine)" *> $null }
  }
  & $global:__tauPrompt
}
''',
}
