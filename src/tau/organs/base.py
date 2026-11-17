"""Organs: each capability is a plugin with a permission level.

Levels, lowest to highest:

* ``off``     not loaded.
* ``sense``   may read state.
* ``suggest`` may propose a change and wait for a yes.
* ``act``     may change things and report in one sentence.

Destructive actions always wait for a yes, whatever the level.
"""

from __future__ import annotations

import fnmatch
import itertools
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from tau.actionlog import ActionLog
from tau.profile import LEVELS, as_list, get

RANK = {lv: i for i, lv in enumerate(LEVELS)}

ALLOWED = "allowed"
NEEDS_YES = "needs_yes"
DENIED = "denied"
OFF = "off"
ERROR = "error"


@dataclass
class ActionSpec:
    name: str
    kind: str  # "read" or "change"
    description: str
    handler: Optional[Callable[..., Any]] = None
    destructive: bool = False


@dataclass
class Organ:
    name: str
    description: str
    actions: Dict[str, ActionSpec] = field(default_factory=dict)
    runs_commands: bool = False
    touches_files: bool = False

    def action(self, name: str, kind: str, description: str,
               destructive: bool = False) -> Callable:
        def deco(fn: Callable) -> Callable:
            self.actions[name] = ActionSpec(name, kind, description, fn, destructive)
            return fn
        return deco


class OrganRegistry:
    """All organs known to this install (built-in and user-added)."""

    _organs: Dict[str, Organ] = {}

    @classmethod
    def add(cls, organ: Organ) -> Organ:
        cls._organs[organ.name] = organ
        return organ

    @classmethod
    def get(cls, name: str) -> Organ:
        if name not in cls._organs:
            raise KeyError(f"no organ named '{name}'")
        return cls._organs[name]

    @classmethod
    def names(cls) -> List[str]:
        return sorted(cls._organs)

    @classmethod
    def all(cls) -> List[Organ]:
        return [cls._organs[n] for n in cls.names()]


def level_for(profile: Mapping[str, Any], organ: str, machine: Optional[str] = None) -> str:
    # "work" is new in 1.3.0: when a profile doesn't mention it, every send needs its own yes
    # (the Send button or "send it"), which is never wider than what the user approves each time.
    raw = (profile.get("organs") or {}).get(organ, "suggest" if organ == "work" else "off")
    if isinstance(raw, dict):
        if machine and machine in (raw.get("machines") or {}):
            return raw["machines"][machine]
        return raw.get("default", "off")
    return raw if raw in RANK else "off"


def decide(level: str, kind: str, destructive: bool = False) -> str:
    """The pure permission rule. Used everywhere; tested directly."""
    if level == "off":
        return OFF
    if kind == "read":
        return ALLOWED if RANK[level] >= RANK["sense"] else DENIED
    # change
    if level == "sense":
        return DENIED
    if level == "suggest" or destructive:
        return NEEDS_YES
    return ALLOWED


# TAU's own output (speaking, replying, drawing the display) is not an action on the lab.
NO_RECEIPT = frozenset({"voice", "display", "chat"})


@dataclass
class Outcome:
    status: str
    message: str
    data: Any = None
    pending_id: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == ALLOWED


@dataclass
class Pending:
    id: str
    organ: str
    action: str
    args: Dict[str, Any]
    machine: Optional[str]
    summary: str


class Organs:
    """Runs organ actions through the permission check and logs each one."""

    _ids = itertools.count(1)

    def __init__(self, profile: Mapping[str, Any], log: ActionLog,
                 context: Optional[Dict[str, Any]] = None):
        self.profile = profile
        self.log = log
        self.context = context or {}
        self.pending: Dict[str, Pending] = {}
        self.on_pending: Optional[Any] = None    # the display shows a Yes / No card for a proposal

    # -- permission helpers -------------------------------------------------
    def level(self, organ: str, machine: Optional[str] = None) -> str:
        return level_for(self.profile, organ, machine)

    def enabled(self, organ: str, machine: Optional[str] = None) -> bool:
        return self.level(organ, machine) != "off"

    def can_read(self, organ: str, machine: Optional[str] = None) -> bool:
        return decide(self.level(organ, machine), "read") == ALLOWED

    def loaded(self) -> List[Organ]:
        return [o for o in OrganRegistry.all() if self.enabled(o.name) or
                any(self._machine_levels(o.name))]

    def _machine_levels(self, organ: str) -> List[str]:
        raw = (self.profile.get("organs") or {}).get(organ)
        if isinstance(raw, dict):
            return [v for v in (raw.get("machines") or {}).values() if v != "off"]
        return []

    # -- the call path ------------------------------------------------------
    def call(self, organ: str, action: str, machine: Optional[str] = None,
             confirmed: bool = False, via: str = "", **args: Any) -> Outcome:
        try:
            spec = OrganRegistry.get(organ).actions[action]
        except KeyError:
            return Outcome(ERROR, f"I don't have an action '{action}' on '{organ}'.")
        decision = decide(self.level(organ, machine), spec.kind, spec.destructive)
        if decision == NEEDS_YES and confirmed:
            decision = ALLOWED
        if decision == OFF:
            self.log.write(organ, action, OFF, args, machine=machine, via=via)
            return Outcome(OFF, f"The {organ} organ is off. Grant it with `tau grant {organ} sense`.")
        if decision == DENIED:
            self.log.write(organ, action, DENIED, args, machine=machine, via=via)
            need = "sense" if spec.kind == "read" else "suggest"
            return Outcome(DENIED, f"I can't {spec.description} yet; that needs {organ} at {need} or higher.")
        if decision == NEEDS_YES:
            pid = f"p{next(self._ids)}"
            summary = f"{spec.description}" + (f" ({_short(args)})" if args else "")
            self.pending[pid] = Pending(pid, organ, action, dict(args), machine, summary)
            self.log.write(organ, action, NEEDS_YES, args, machine=machine, via=via,
                           receipt=None if organ in NO_RECEIPT else f"Proposed: {summary}", who="waiting for your yes")
            if self.on_pending is not None:
                try:
                    self.on_pending(self.pending[pid])
                except Exception:
                    pass
            return Outcome(NEEDS_YES, f"Shall I {summary}?", pending_id=pid)
        rc = None
        what = f"{spec.description}" + (f" ({_short(args)})" if args else "")
        if spec.kind != "read" and organ not in NO_RECEIPT:     # a change at suggest or act level leaves a receipt
            rc = (f"you said yes{' (' + via + ')' if via else ''}" if confirmed else
                  f"{organ} is at Act, so no yes was needed")
        try:
            data = spec.handler(self, machine=machine, **args) if spec.handler else None
        except PermissionError as exc:
            self.log.write(organ, action, DENIED, args, result=str(exc), machine=machine, via=via,
                           receipt=f"Refused: {what} ({exc})" if rc else None, who=rc)
            return Outcome(DENIED, str(exc))
        except Exception as exc:  # report, never crash the assistant
            self.log.write(organ, action, ERROR, args, result=str(exc), machine=machine, via=via,
                           receipt=f"Tried: {what}, but it failed ({exc})" if rc else None, who=rc)
            import logging

            logging.getLogger("tau.organs").exception("%s.%s failed", organ, action)
            return Outcome(ERROR, f"I couldn't {spec.description} ({type(exc).__name__}). "
                                  "The details are in TAU's log.")
        self.log.write(organ, action, ALLOWED, args, result=_brief(data), machine=machine, via=via,
                       receipt=what if rc else None, who=rc)
        msg = data.get("say") if isinstance(data, dict) and "say" in data else "Done."
        return Outcome(ALLOWED, msg, data)

    def confirm(self, pending_id: Optional[str] = None, via: str = "") -> Outcome:
        if not self.pending:
            return Outcome(ERROR, "There's nothing waiting for a yes.")
        pid = pending_id or list(self.pending)[-1]
        p = self.pending.pop(pid, None)
        self._pending_changed()
        if p is None:
            return Outcome(ERROR, "That request has already gone.")
        return self.call(p.organ, p.action, machine=p.machine, confirmed=True, via=via, **p.args)

    def _pending_changed(self) -> None:
        if self.on_pending is not None:
            try:
                self.on_pending(self.pending[list(self.pending)[-1]] if self.pending else None)
            except Exception:
                pass

    def decline(self, pending_id: Optional[str] = None) -> Outcome:
        if not self.pending:
            return Outcome(ERROR, "There's nothing waiting.")
        pid = pending_id or list(self.pending)[-1]
        p = self.pending.pop(pid, None)
        self._pending_changed()
        if p:
            self.log.write(p.organ, p.action, "declined", p.args, machine=p.machine,
                           receipt=f"Not done: {p.summary}", who="you said no")
        return Outcome(ALLOWED, "Okay, I won't.")

    # -- allow-lists --------------------------------------------------------
    def check_command(self, command: str, trusted: bool = False) -> None:
        """Raise PermissionError unless the command is on the allow-list.

        ``trusted`` is for commands the user wrote into the profile themself
        (service start/stop, app-prep launch): those are allowed by being there.
        """
        if trusted:
            return
        patterns = [str(p) for p in as_list(get(self.profile, "allow.commands"))]
        if not any(fnmatch.fnmatch(command, p) for p in patterns):
            raise PermissionError(f"'{command}' is not on the command allow-list.")

    def check_path(self, path: str | os.PathLike) -> Path:
        roots = as_list(get(self.profile, "allow.paths")) or as_list(get(self.profile, "workspaces.roots"))
        target = Path(path).expanduser().resolve()
        for root in roots:
            r = Path(str(root)).expanduser().resolve()
            if target == r or r in target.parents:
                return target
        raise PermissionError(f"{target} is outside the folders I'm allowed to touch.")


def _short(args: Mapping[str, Any]) -> str:
    return ", ".join(f"{v}" for v in args.values())[:80]


def _brief(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k in ("say", "status", "ok", "count")}
    return None


def split_command(command: str) -> List[str]:
    return shlex.split(command, posix=os.name != "nt")


_GRANT_LINE = re.compile(r"^(\s+)([a-z_]+):\s*(\"?)(off|sense|suggest|act)\3\s*(#.*)?$")


def set_level_in_text(text: str, organ: str, level: str) -> Optional[str]:
    """Change one organ's level inside profile text, keeping comments.

    Returns None when the organ line is not a simple ``name: level`` line
    (for example a per-machine mapping); the caller then rewrites the YAML.
    """
    lines = text.splitlines(keepends=True)
    in_organs = False
    for i, line in enumerate(lines):
        if re.match(r"^organs:\s*(#.*)?$", line):
            in_organs = True
            continue
        if in_organs and line.strip() and not line.startswith((" ", "\t")):
            break
        if in_organs:
            m = _GRANT_LINE.match(line.rstrip("\n"))
            if m and m.group(2) == organ:
                quoted = '"off"' if level == "off" else level
                lines[i] = f"{m.group(1)}{organ}: {quoted}\n"
                return "".join(lines)
    return None
