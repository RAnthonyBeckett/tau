"""The built-in organs. Handlers import their modules lazily, so an organ that
is off never loads its dependencies."""

from __future__ import annotations

import subprocess
from typing import Any, Dict, Optional

from tau.organs.base import Organ, OrganRegistry, Organs, split_command
from tau.profile import get, iter_services


def _local_name(o: Organs) -> Optional[str]:
    return get(o.profile, "machine.name")


def remote_or_local(o: Organs, machine: Optional[str], organ: str, action: str,
                    args: Dict[str, Any], local):
    """Run on this machine, or forward to the node that owns ``machine``.

    The hub has already checked the permission; the node carries it out.
    """
    lab = o.context.get("lab")
    if machine and machine != _local_name(o) and lab is not None:
        return lab.remote_call(machine, organ, action, args)
    return local()


def run_command(o: Organs, command: str, trusted: bool, timeout: float = 60) -> Dict[str, Any]:
    o.check_command(command, trusted=trusted)
    proc = subprocess.run(split_command(command), capture_output=True, text=True, timeout=timeout)
    return {"ok": proc.returncode == 0, "code": proc.returncode,
            "stderr": proc.stderr[-2000:], "stdout": proc.stdout[-2000:]}


def find_service(o: Organs, name: str) -> Dict[str, Any]:
    name_l = name.lower().strip()
    for s in iter_services(o.profile):
        if s["name"].lower() == name_l:
            return s
    for s in iter_services(o.profile):
        if name_l in s["name"].lower():
            return s
    raise LookupError(f"there's no service called {name} in your profile")


# ---------------------------------------------------------------------------
voice = OrganRegistry.add(Organ("voice", "speak replies out loud"))
display = OrganRegistry.add(Organ("display", "show state on the triangle display"))
chat = OrganRegistry.add(Organ("chat", "answer in text"))
services = OrganRegistry.add(Organ("services", "see and run system and lab services", runs_commands=True))
notifications = OrganRegistry.add(Organ("notifications", "read recent system notifications"))
gpu = OrganRegistry.add(Organ("gpu", "read GPU memory and free it"))
window = OrganRegistry.add(Organ("window", "see the focused window"))
processes = OrganRegistry.add(Organ("processes", "see and stop processes"))
heartbeat = OrganRegistry.add(Organ("heartbeat", "check service health URLs"))
cmdfail = OrganRegistry.add(Organ("cmdfail", "see commands that failed in wrapped shells"))
drives = OrganRegistry.add(Organ("drives", "see drives, never walking slow or network ones"))
appbridge = OrganRegistry.add(Organ("appbridge", "talk to apps that have a local API"))
models = OrganRegistry.add(Organ("models", "switch the brain's model"))
apps = OrganRegistry.add(Organ("apps", "prepare the machine for heavy apps", runs_commands=True))
handoff = OrganRegistry.add(Organ("handoff", "send coding work to other AI agents", runs_commands=True))
clipboard = OrganRegistry.add(Organ("clipboard", "put text on the clipboard"))
files = OrganRegistry.add(Organ("files", "read and write files in your workspaces", touches_files=True))
knowledge = OrganRegistry.add(Organ("knowledge", "search and store knowledge"))
gesture = OrganRegistry.add(Organ("gesture", "follow hand gestures from the webcam"))


# -- voice / display / chat ---------------------------------------------------
@voice.action("speak", "change", "speak")
def _speak(o: Organs, machine=None, text: str = ""):
    sink = o.context.get("voice")
    if sink:
        sink.say(text)
    return {"say": text}


@voice.action("interrupt", "change", "stop speaking")
def _interrupt(o: Organs, machine=None):
    sink = o.context.get("voice")
    if sink:
        sink.interrupt()
    return {"say": ""}


@display.action("show", "change", "update the display")
def _show(o: Organs, machine=None, state: Dict[str, Any] | None = None):
    hub = o.context.get("display")
    if hub:
        hub.publish(state or {})
    return {"say": ""}


@chat.action("reply", "change", "reply in text")
def _reply(o: Organs, machine=None, text: str = ""):
    return {"say": text}


# -- services -----------------------------------------------------------------
@services.action("list", "read", "list services")
def _svc_list(o: Organs, machine=None, query: str = ""):
    from tau.discovery.labmap import LabMap

    lab_map: LabMap = o.context["lab_map"]
    return lab_map.answer_services(machine=machine, query=query)


@services.action("port", "read", "say what owns a port")
def _svc_port(o: Organs, machine=None, port: int = 0):
    return o.context["lab_map"].answer_port(int(port), machine=machine)


@services.action("status", "read", "check a service")
def _svc_status(o: Organs, machine=None, name: str = ""):
    from tau.senses.heartbeat import check_port, check_url

    s = find_service(o, name)
    if not s.get("health_url") and not s.get("port"):
        return {"say": f"{s['name']} has no health URL or port in the profile.", "ok": None}
    ok = check_url(s["health_url"]) if s.get("health_url") else check_port(s["port"])
    return {"say": f"{s['name']} is {'up' if ok else 'down'}.", "ok": ok}


def _svc_run(o: Organs, machine, name: str, which: str):
    s = find_service(o, name)
    cmd = s.get(which)
    if not cmd:
        raise LookupError(f"{s['name']} has no {which} command in the profile")
    target = machine or s.get("machine")
    res = remote_or_local(o, target, "services", which, {"name": s["name"], "command": cmd},
                          lambda: run_command(o, cmd, trusted=True))
    verb = {"start": "started", "stop": "stopped", "restart": "restarted"}[which]
    if res.get("ok"):
        return {"say": f"{s['name']} {verb}.", "ok": True}
    return {"say": f"{s['name']} didn't {which}: {res.get('stderr', '').strip()[:120]}", "ok": False}


@services.action("start", "change", "start a service")
def _svc_start(o: Organs, machine=None, name: str = ""):
    return _svc_run(o, machine, name, "start")


@services.action("stop", "change", "stop a service")
def _svc_stop(o: Organs, machine=None, name: str = ""):
    return _svc_run(o, machine, name, "stop")


@services.action("restart", "change", "restart a service")
def _svc_restart(o: Organs, machine=None, name: str = ""):
    return _svc_run(o, machine, name, "restart")


# -- notifications ------------------------------------------------------------
@notifications.action("recent", "read", "read recent notifications")
def _notif(o: Organs, machine=None, limit: int = 5):
    from tau.senses.notifications import recent

    items = remote_or_local(o, machine, "notifications", "recent", {"limit": limit},
                            lambda: recent(limit))
    if not items:
        return {"say": "No recent notifications.", "items": []}
    lines = [f"{n.get('app', '?')}: {n.get('title', '')}" for n in items[:limit]]
    return {"say": "; ".join(lines), "items": items}


# -- gpu ----------------------------------------------------------------------
@gpu.action("vram", "read", "read GPU memory")
def _vram(o: Organs, machine=None):
    from tau.senses.gpu import read_gpus, say_vram

    gpus = remote_or_local(o, machine, "gpu", "vram", {}, read_gpus)
    return {"say": say_vram(gpus), "gpus": gpus}


@gpu.action("unload", "change", "ask the local model server to unload its model")
def _unload(o: Organs, machine=None):
    from tau.lanes import request_unload

    ok = request_unload(o.profile)
    return {"say": "Asked the local server to unload." if ok else "The local server didn't unload.", "ok": ok}


# -- window / processes -------------------------------------------------------
@window.action("focused", "read", "see the focused window")
def _focused(o: Organs, machine=None):
    from tau.senses.window import focused_window

    w = remote_or_local(o, machine, "window", "focused", {}, focused_window)
    return {"say": f"{w.get('title') or 'nothing'} ({w.get('process') or '?'})", **w}


@processes.action("list", "read", "list processes")
def _procs(o: Organs, machine=None, top: int = 5):
    from tau.senses.window import top_processes

    procs = remote_or_local(o, machine, "processes", "list", {"top": top}, lambda: top_processes(top))
    return {"say": ", ".join(p["name"] for p in procs), "processes": procs}


@processes.action("kill", "change", "stop a process", destructive=True)
def _kill(o: Organs, machine=None, pid: int = 0):
    import psutil

    def local():
        psutil.Process(int(pid)).terminate()
        return {"ok": True}

    remote_or_local(o, machine, "processes", "kill", {"pid": pid}, local)
    return {"say": f"Stopped process {pid}."}


# -- heartbeat / cmdfail / drives ----------------------------------------------
@heartbeat.action("check", "read", "check service health")
def _hb(o: Organs, machine=None):
    from tau.senses.heartbeat import check_all

    res = check_all(o.profile)
    down = [n for n, ok in res.items() if ok is False]
    say = "Everything with a health URL is up." if not down else "Down: " + ", ".join(down)
    return {"say": say, "results": res}


@cmdfail.action("last_error", "read", "read the last failed command")
def _lasterr(o: Organs, machine=None):
    from tau.senses.cmdfail import last_failure

    f = remote_or_local(o, machine, "cmdfail", "last_error", {}, last_failure)
    if not f:
        return {"say": "No failed commands captured.", "failure": None}
    return {"say": f"`{f['command']}` exited {f['code']}.", "failure": f}


@drives.action("list", "read", "list drives")
def _drives(o: Organs, machine=None):
    from tau.senses.drives import list_drives

    ds = remote_or_local(o, machine, "drives", "list", {}, list_drives)
    slow = [d["mount"] for d in ds if d.get("never_walk")]
    return {"say": f"{len(ds)} drives" + (f"; never walked: {', '.join(slow)}" if slow else ""), "drives": ds}


# -- app bridge ------------------------------------------------------------------
@appbridge.action("query", "read", "ask an app for its status")
def _bridge_q(o: Organs, machine=None, app: str = ""):
    from tau.senses.appbridge import bridges_from_profile

    b = bridges_from_profile(o.profile)[app]
    data = b.status()
    return {"say": b.summarize(data), "data": data}


@appbridge.action("command", "change", "send a command to an app")
def _bridge_c(o: Organs, machine=None, app: str = "", command: str = ""):
    from tau.senses.appbridge import bridges_from_profile

    b = bridges_from_profile(o.profile)[app]
    return {"say": b.command(command)}


# -- models / apps ------------------------------------------------------------------
@models.action("current", "read", "say which model is in use")
def _model_now(o: Organs, machine=None):
    lanes = o.context.get("lanes")
    return {"say": lanes.describe() if lanes else "No brain lanes are set up."}


@models.action("switch", "change", "switch the model")
def _model_switch(o: Organs, machine=None, name: str = ""):
    lanes = o.context["lanes"]
    return {"say": lanes.switch_model(name)}


@apps.action("prep", "change", "prepare for an app")
def _prep(o: Organs, machine=None, app: str = ""):
    from tau.apps import prep_for

    return prep_for(o, app)


# -- handoff / clipboard / files / knowledge ---------------------------------------------
@handoff.action("send", "change", "hand the job to an agent")
def _handoff(o: Organs, machine=None, request: str = "", adapter: str = "",
             error_text: str = "", state_header: str = ""):
    from tau.handoff import send

    return send(o, request=request, adapter=adapter or None,
                error_text=error_text, state_header=state_header)


@clipboard.action("copy", "change", "put text on the clipboard")
def _clip(o: Organs, machine=None, text: str = ""):
    from tau.output import copy_to_clipboard

    copy_to_clipboard(text)
    return {"say": "It's on your clipboard."}


@files.action("read", "read", "read a file")
def _fread(o: Organs, machine=None, path: str = ""):
    p = o.check_path(path)
    return {"say": f"Read {p.name}.", "text": p.read_text(encoding="utf-8", errors="replace")[:20000]}


@files.action("write", "change", "write a file")
def _fwrite(o: Organs, machine=None, path: str = "", text: str = ""):
    p = o.check_path(path)
    p.write_text(text, encoding="utf-8")
    return {"say": f"Wrote {p.name}."}


@files.action("delete", "change", "delete a file", destructive=True)
def _fdel(o: Organs, machine=None, path: str = ""):
    p = o.check_path(path)
    p.unlink()
    return {"say": f"Deleted {p.name}."}


@knowledge.action("search", "read", "search knowledge")
def _ksearch(o: Organs, machine=None, query: str = "", limit: int = 5):
    from tau.knowledge.search import search_all

    hits = search_all(o.profile, query, limit=limit)
    return {"say": f"{len(hits)} matches.", "hits": hits}


@gesture.action("track", "read", "follow hand gestures")
def _gesture(o: Organs, machine=None):
    return {"say": "Gesture tracking isn't built yet; see the build notes."}


# -- work: folders, connections and jobs (round 6) --------------------------------------
work = OrganRegistry.add(Organ("work", "send work to connected AI workers and bring back results",
                               runs_commands=True))


@work.action("status", "read", "say how the work is going")
def _work_status(o: Organs, machine=None, job: str = ""):
    store = o.context.get("work")
    return {"say": store.status_line(job or None) if store else "Work isn't set up."}


@work.action("send", "change", "send a job to a worker")
def _work_send(o: Organs, machine=None, job: str = "", approval: str = "", accept_unenforced: bool = False):
    """The Send button is the yes; the approval hash ties it to the card the user saw."""
    store = o.context.get("work")
    if store is None:
        raise RuntimeError("work isn't set up")
    return store.send(job, approval, accept_unenforced=accept_unenforced)


@work.action("app_do", "change", "ask a connected app to do something")
def _work_app_do(o: Organs, machine=None, app: str = "", what: str = ""):
    """Only actions the app itself described; the reply is the app's own outcome."""
    from tau import appconnect

    store = o.context.get("work")
    conn = (store.data.get("apps") or {}).get(app) if store else None
    if conn is None:
        raise LookupError("that app isn't connected")
    return appconnect.do(conn, what)
