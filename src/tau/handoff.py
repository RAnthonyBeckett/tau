"""Agent hand-off: package the request, the state header and the error text,
and send them through a command template from the profile.

Template fields: ``{prompt}`` (the whole package, quoted for the shell),
``{prompt_file}`` (a file holding the package), ``{workdir}``, ``{target}``.
Adapters ship as templates only: Claude Code CLI, Codex CLI, a generic
command, and multi-agent coordinators such as OpenRig's ``rig send``.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from tau import paths
from tau.profile import as_list, get

LABELS = {"claude_code": "Claude Code", "codex": "Codex", "generic": "your agent command",
          "openrig": "OpenRig"}


def package(request: str, state_header: str = "", error_text: str = "", workdir: str = "") -> str:
    parts = ["# Task from TAU", "", request.strip()]
    if workdir:
        parts += ["", f"Working folder: {workdir}"]
    if state_header:
        parts += ["", "## Machine state", "", "```json", state_header, "```"]
    if error_text:
        parts += ["", "## Error text", "", "```", error_text.strip()[-4000:], "```"]
    return "\n".join(parts) + "\n"


def _quote(s: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([s])
    return shlex.quote(s)


def build_command(template: str, prompt: str, prompt_file: str, workdir: str = "",
                  target: str = "") -> List[str]:
    """Fill the template and split it into argv (no shell is used)."""
    marker = {"prompt": "\x00P\x00", "prompt_file": "\x00F\x00", "workdir": "\x00W\x00", "target": "\x00T\x00"}
    filled = template.format(**marker)
    argv = shlex.split(filled, posix=True)
    sub = {marker["prompt"]: prompt, marker["prompt_file"]: prompt_file,
           marker["workdir"]: workdir, marker["target"]: target}
    out = []
    for a in argv:
        for k, v in sub.items():
            a = a.replace(k, v)
        out.append(a)
    return out


def adapters(profile: Mapping[str, Any]) -> Dict[str, str]:
    raw = get(profile, "handoff.adapters", {}) or {}
    return {k: v["template"] for k, v in raw.items() if isinstance(v, dict) and v.get("template")}


def send(organs, request: str, adapter: Optional[str] = None, error_text: str = "",
         state_header: str = "", workdir: str = "", target: str = "", dry_run: bool = False) -> Dict[str, Any]:
    profile = organs.profile
    ads = adapters(profile)
    name = adapter or get(profile, "handoff.default") or (next(iter(ads)) if ads else None)
    if not name or name not in ads:
        return {"say": "No agent hand-off is set up. Add a template under handoff.adapters.", "ok": False}
    if not error_text and organs.can_read("cmdfail"):
        from tau.senses.cmdfail import last_failure
        f = last_failure()
        if f and time.time() - f.get("at", 0) < 3600:
            error_text = f"$ {f['command']}\n(exit {f['code']})\n{f.get('stderr', '')}"
    workdir = workdir or next((str(r) for r in as_list(get(profile, "workspaces.roots"))), "") or os.getcwd()
    text = package(request, state_header, error_text, workdir)
    folder = paths.state_dir() / "handoff"
    folder.mkdir(parents=True, exist_ok=True)
    pf = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.md"
    pf.write_text(text, encoding="utf-8")
    argv = build_command(ads[name], text, str(pf), workdir, target)
    label = LABELS.get(name, name)
    if dry_run:
        return {"say": f"Would send it to {label}.", "argv": argv, "ok": True, "prompt_file": str(pf)}
    organs.check_command(" ".join(argv[:1]), trusted=True)  # templates are the user's own
    log = folder / (pf.stem + ".log")
    with open(log, "w", encoding="utf-8") as fh:
        subprocess.Popen(argv, cwd=workdir if Path(workdir).is_dir() else None, stdout=fh,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    return {"say": f"Sent it to {label}; it's working in {Path(workdir).name or workdir}.",
            "argv": argv, "ok": True, "prompt_file": str(pf), "log": str(log)}
