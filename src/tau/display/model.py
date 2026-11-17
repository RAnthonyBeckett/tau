"""Everything the display shows, built from the profile and live state.

The display holds all of TAU's visual information (owner's ruling, round 3):

* **Rings.** Each segment is one thing TAU tracks. ``ring`` 0 is innermost
  and brightest; less important things fade outward. ``status``: ``lit``
  (fine), ``dim`` (asleep / off), ``attention`` (pulsing amber), ``empty``
  (nothing there yet: drawn faintly with a label saying what will appear).
  A fresh install always has the core slots, so it is never an empty picture.
* **Organ ticks** along the top band of the frame: one per organ, showing its
  permission level. Hover says what it is and what it may do.
* **Corner switches** at the two top corners of the frame: Quiet and Hard day.
* **The pin** at the bottom tip: glows while pin cards wait; click opens them.
* **Status line**: what TAU is doing right now, in words, with an estimate
  for anything slow.
* **Conversation**: the last few exchanges, typed or spoken.
* **Flash**: a warning or notice that flashes the frame and shows one line.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from tau.profile import get, iter_services

LEVEL_WORDS = {"off": "off", "sense": "can look, can't change anything",
               "suggest": "can look and suggest; asks before changing anything",
               "act": "can look and act"}

ORGAN_NAMES = {"voice": "Voice", "display": "Display", "chat": "Chat", "services": "Services",
               "notifications": "Notifications", "gpu": "Graphics card", "window": "Focused window",
               "processes": "Processes", "heartbeat": "Service health", "cmdfail": "Failed commands",
               "drives": "Drives", "appbridge": "App bridges", "models": "Models", "apps": "App prep",
               "handoff": "Coding hand-off", "clipboard": "Clipboard", "files": "Files",
               "knowledge": "Knowledge", "gesture": "Gestures"}


OFF_VALUE = "off \u2014 turn on in Settings"


def segment(id: str, label: str, importance: int, status: str = "lit", action: Optional[str] = None,
            accent: Optional[str] = None, hint: str = "", name: Optional[str] = None,
            value: Optional[str] = None, settings: Optional[str] = None,
            detail: Optional[List[str]] = None, buttons: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """One ring segment. ``name`` and ``value`` are always shown as text beside the rings;
    ``settings`` names the Settings section a click on an "off" segment opens."""
    if name is None or value is None:
        n, _, v = label.partition(": ")
        name, value = name or n, value if value is not None else v
    return {"id": id, "label": label, "name": name, "value": value, "importance": importance, "status": status,
            "action": action, "accent": accent, "hint": hint, "settings": settings,
            "detail": detail or [], "buttons": buttons or []}


def _level(profile: Mapping[str, Any], organ: str) -> str:
    from tau.organs import level_for

    try:
        return level_for(profile, organ)
    except Exception:
        return "off"


def _off(id: str, name: str, importance: int, section: str, what: str) -> Dict[str, Any]:
    return segment(id, f"{name}: {OFF_VALUE}", importance, "empty", name=name, value=OFF_VALUE,
                   settings=section, hint=what, detail=[what, "Switched off. Open Settings to turn it on."])


def _ago(t: Optional[float]) -> str:
    import time

    if not t:
        return "not checked yet"
    s = max(0, int(time.time() - t))
    return "checked just now" if s < 5 else f"checked {s} seconds ago" if s < 90 else f"checked {s // 60} minutes ago"


def build_segments(profile: Mapping[str, Any], live: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    live = live or {}
    segs: List[Dict[str, Any]] = []
    checked = _ago(live.get("sensed_at"))

    # ring 0: brain and hearing
    lane = live.get("lane")
    has_brain = bool(get(profile, "brain.local.url") or get(profile, "brain.burst.url"))
    reach = live.get("brain_ok")
    if not has_brain:
        segs.append(segment("brain", "Brain: not connected yet", 1, "attention", "models.current",
                            hint="Open Settings, Brain, to connect one", settings="brain",
                            detail=["I have no brain connected, so I can only do simple things like checking services.",
                                    "Open Settings, Brain, and press Check again."]))
    else:
        which = lane or get(profile, "brain.prefer") or "local"
        model = get(profile, f"brain.{which}.model") or ""
        url = get(profile, f"brain.{which}.url") or ""
        state = "lit" if reach is not False else "attention"
        word = "answering" if reach else ("not answering" if reach is False else "connected")
        segs.append(segment("brain", f"Brain: {model or lane or 'ready'} ({word})", 1, state, "models.current",
                            accent="cyan" if lane == "burst" else None, hint="Click for the model and its server",
                            name="Brain", value=f"{model or which}, {word}", settings="brain",
                            detail=[f"Model: {model or 'not picked'}",
                                    f"Where: {'the cloud' if which == 'burst' else 'this computer'} ({url})",
                                    f"Status: {word}, {checked}"]))
    mic = live.get("mic") or {}
    muted = live.get("muted")
    if mic.get("error"):
        segs.append(segment("mic", f"Microphone: {mic['error']}", 1, "attention", "voice.toggle_quiet",
                            hint="The fix is on a pin card", settings="voice",
                            detail=["I can't open the microphone. The fix is on a pin card.",
                                    "You can pick another microphone in Settings, Voice and hearing."]))
    elif muted:
        segs.append(segment("mic", "Microphone: quiet (not listening)", 1, "dim", "voice.toggle_quiet",
                            hint="Click to listen again", settings="voice",
                            detail=["Quiet is on: I'm not listening or speaking.", "Switch Quiet off to listen again."]))
    elif mic.get("ready"):
        how = {"wake": "listening for my name", "vad": "listening to all speech"}.get(mic.get("mode"), "hotword unavailable, use Talk or press Space")
        segs.append(segment("mic", f"Microphone: on ({how})", 1, "lit", "voice.toggle_quiet",
                            hint="Click to make me quiet", name="Microphone", value=f"on, {how}", settings="voice",
                            detail=[f"Microphone: {get(profile, 'audio.mic') or 'the default one'}", f"Mode: {how}"]))
    else:
        segs.append(segment("mic", "Microphone: not set up (type instead)", 1, "empty", settings="voice",
                            hint="Hearing appears here once a microphone works",
                            detail=["Hearing isn't set up on this computer, so type below instead.",
                                    "Open Settings, Voice and hearing, to pick a microphone."]))

    # ring 1: pins and the graphics card
    pins = int(live.get("pins") or 0)
    segs.append(segment("pins", f"{pins} pin card{'s' if pins != 1 else ''} waiting" if pins else
                        "Pin cards: none waiting", 2, "attention" if pins else "dim", "pins.open",
                        hint="Answers I keep for you appear here", name="Pin cards",
                        value=f"{pins} waiting" if pins else "none waiting",
                        detail=["Answers and fixes I've kept for you.", "Click Open to read them."]))
    if _level(profile, "gpu") != "off":
        free = live.get("vram_free_gb")
        total = live.get("vram_total_gb")
        low = free is not None and free < float(get(profile, "brain.gpu.min_free_vram_gb", 0) or 0)
        val = f"{free:g} GB free" if free is not None else "reading..."
        if live.get("gpu_none"):
            segs.append(segment("gpu", "Graphics card: none found", 2, "dim", "gpu.vram", settings="watch",
                                detail=["I couldn't read a graphics card (nvidia-smi found none)."]))
        else:
            segs.append(segment("gpu", f"Graphics card: {val}", 2, "attention" if low else "lit", "gpu.vram",
                                accent="cyan" if live.get("gpu_yielded") else None, name="GPU", value=val,
                                settings="watch",
                                detail=[f"Free memory: {val}" + (f" of {total:g} GB" if total else ""),
                                        f"I move to the cloud brain below {get(profile, 'brain.gpu.min_free_vram_gb', 0)} GB free.",
                                        checked.capitalize() + "."]))
    else:
        segs.append(_off("gpu", "GPU", 2, "watch", "Free graphics memory, so I don't load a model that won't fit."))

    # ring 2: services
    health = live.get("services") or {}
    svcs = list(iter_services(profile))
    allowed = _level(profile, "services") in ("suggest", "act")
    for s in svcs:
        ok = health.get(s["name"])
        st = "lit" if ok else ("attention" if ok is False else "dim")
        word = "up" if ok else "down" if ok is False else "checking..."
        where = s.get("health_url") or (f"port {s['port']}" if s.get("port") else "no address")
        btns = []
        if allowed and s.get("start") and ok is False:
            btns.append({"label": "Start it", "action": {"service": s["name"], "do": "start"}})
        if allowed and s.get("stop") and ok:
            btns.append({"label": "Stop it", "action": {"service": s["name"], "do": "stop"}})
        segs.append(segment(f"svc:{s['name']}", f"{s['name']}: {word}", int(s.get("importance", 3)), st,
                            f"services.toggle:{s['name']}", name=s["name"], value=word, settings="watch",
                            detail=[f"Address: {where}", f"Status: {word}, {checked}"], buttons=btns))
    if not svcs:
        segs.append(segment("svc:none", "Services: none watched yet", 3, "empty", settings="watch",
                            name="Services", value="none yet \u2014 add them in Settings",
                            hint="Services you choose appear here, lit when up",
                            detail=["No services are being checked.", "Open Settings, What I watch, to add some."]))

    # ring 3: drives, notifications, hand-off
    if _level(profile, "drives") != "off":
        drives = live.get("drives")
        if drives is None:
            segs.append(segment("drives", "Drives: reading...", 4, "dim", "drives.list", settings="watch"))
        for d in (drives or [])[:6]:
            free, total = d.get("free_gb"), d.get("total_gb")
            low = free is not None and total and free / total < 0.05
            mount = str(d.get("mount") or "").rstrip(":\\") or str(d.get("mount"))
            if free is not None and total:
                val = f"{free / total:.0%} free ({free:,.0f} GB)"
            else:
                val = f"{free:,.0f} GB free" if free is not None else "free space unknown"
            note = " (never walked)" if d.get("never_walk") else ""
            segs.append(segment(f"drive:{d.get('mount')}", f"Drive {d.get('mount')}: {val}{note}", 4,
                                "attention" if low else "lit", "drives.list", name=f"Drive {mount}", value=val,
                                settings="watch",
                                detail=[f"Free: {free:,.1f} GB of {total:,.1f} GB" if free is not None and total
                                        else "Free space unknown",
                                        "Slow or network drive: I never look inside it." if d.get("never_walk")
                                        else "I only read free space, never the files.", checked.capitalize() + "."]))
    else:
        segs.append(_off("drives", "Drives", 4, "watch", "Free space on each drive."))
    if _level(profile, "notifications") != "off":
        n = live.get("notifications")
        val = "reading..." if n is None else f"{n} recent" if n else "none recent"
        segs.append(segment("notifications", f"Notifications: {val}", 4, "lit" if n else "dim",
                            "notifications.recent", name="Notifications", value=val, settings="watch",
                            detail=["How many notifications arrived recently (titles only).", checked.capitalize() + "."]))
    else:
        segs.append(_off("notifications", "Notifications", 4, "watch", "How many recent notifications there are."))
    if _level(profile, "handoff") != "off":
        jobs = int(live.get("handoff_jobs") or 0)
        segs.append(segment("handoff", f"{jobs} hand-off job{'s' if jobs != 1 else ''} running" if jobs
                            else "Coding hand-off: idle", 4, "lit" if jobs else "dim", "handoff.list",
                            name="Coding hand-off", value=f"{jobs} running" if jobs else "idle", settings="permissions"))

    # ring 4: other machines
    nodes = live.get("nodes") or {}
    for name, st in nodes.items():
        word = "connected" if st.get("connected") else "away"
        segs.append(segment(f"node:{name}", f"{name}: {word}", 5, "lit" if st.get("connected") else "dim",
                            name=name, value=word, settings="lab", detail=[f"{name} is {word}."]))
    if not nodes:
        segs.append(segment("node:none", "Other machines: none paired", 5, "empty", settings="lab",
                            name="Other machines", value="none paired \u2014 pair one in Settings",
                            hint="Machines you pair appear here",
                            detail=["No other machines are paired.", "Open Settings, Lab, and press Pair a machine."]))

    segs.sort(key=lambda s: s["importance"])
    ranks = sorted({s["importance"] for s in segs})
    for s in segs:
        s["ring"] = ranks.index(s["importance"])
    return segs


def build_organs(profile: Mapping[str, Any], live: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """One tick per organ along the frame's top band."""
    from tau.organs import OrganRegistry

    warn = (live or {}).get("organ_warnings") or {}
    out = []
    for o in OrganRegistry.all():
        lv = _level(profile, o.name)
        name = ORGAN_NAMES.get(o.name, o.name)
        label = f"{name}: {LEVEL_WORDS.get(lv, lv)}"
        if o.name in warn:
            label += f". {warn[o.name]}"
        out.append({"id": o.name, "name": name, "level": lv, "label": label, "what": o.description,
                    "warn": o.name in warn})
    return out


def build_switches(profile: Mapping[str, Any], live: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    live = live or {}
    muted = bool(live.get("muted"))
    hard = bool((profile.get("pacing") or {}).get("hard_day"))
    return [
        {"id": "quiet", "corner": "left", "on": muted,
         "label": f"Quiet: {'on, I am not listening or speaking' if muted else 'off'}. Click to switch."},
        {"id": "hard_day", "corner": "right", "on": hard,
         "label": f"Hard day: {'on, short and literal answers' if hard else 'off'}. Click to switch."},
    ]


def status_line(profile: Mapping[str, Any], live: Mapping[str, Any]) -> str:
    """What TAU is doing right now, in words."""
    if live.get("status"):
        return str(live["status"])
    st = live.get("state", "idle")
    mic = live.get("mic") or {}
    if st == "listening":
        return "Listening. Speak now; I stop when you pause."
    if st == "transcribing":
        return "Working out what you said (on this computer)."
    if st == "thinking":
        if live.get("wait_text"):
            return str(live["wait_text"])
        model = live.get("model") or get(profile, f"brain.{get(profile, 'brain.prefer') or 'local'}.model")
        return f"Thinking with {model}. This usually takes a few seconds." if model else \
            "Thinking. This usually takes a few seconds."
    if st == "speaking":
        said = str(live.get("speaking_text") or "")
        short = (said[:70] + "...") if len(said) > 70 else said
        return f'Speaking: "{short}" Click Stop or press Esc to stop me.' if short else \
            "Speaking. Click Stop or press Esc to stop me."
    if st == "error":
        return str(live.get("error_text") or "Something went wrong. The details are on a pin card.")
    if live.get("muted"):
        return "Quiet. I'm not listening. Click the Quiet corner to switch it off."
    if mic.get("ready") and mic.get("mode") == "wake":
        name = get(profile, "tau.name", "TAU")
        return f"Ready. Say '{name}', click Talk, press Space, or type below."
    if mic.get("ready"):
        return "Ready. Click Talk or press Space, then speak. Or type below."
    return "Ready. Type below and press Enter."


def display_state(profile: Mapping[str, Any], live: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    live = live or {}
    mic = live.get("mic") or {}
    return {
        "name": get(profile, "tau.name", "TAU"),
        "open_work": live.get("open_work"),         # a flow opened by voice or typing ("add a folder")
        "work_card": live.get("work_card"),         # the task card for the current draft
        "work": live.get("work"),                   # folders, connections and jobs (tau.work)
        "state": live.get("state", "idle"),       # idle | listening | transcribing | thinking | speaking | error
        "model": live.get("model"),
        "speaking_text": live.get("speaking_text"),
        "level": float(live.get("level", 0.0)),   # voice loudness 0..1 while speaking
        "burst": live.get("lane") == "burst",
        "throttle": bool(live.get("throttle")),   # full-screen or heavy app has focus -> 10 fps
        "segments": build_segments(profile, live),
        "organs": build_organs(profile, live),
        "switches": build_switches(profile, live),
        "pins": int(live.get("pins") or 0),
        "status": status_line(profile, live),
        "conversation": list(live.get("conversation") or [])[-60:],
        "note": live.get("note"),
        "flash": live.get("flash"),
        "talk": {"available": bool(mic.get("ready")), "mode": mic.get("mode") or "click",
                 "hands_free": mic.get("mode") == "vad"},
        "first_run": not live.get("help_seen", False),
        "cloud_notice": live.get("cloud_notice"),
        "machines": build_machines(profile, live),
        "receipts": list(live.get("receipts") or [])[-30:],
        "proposal": live.get("proposal"),
    }


def build_machines(profile: Mapping[str, Any], live: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """One row per machine for the side panel: paired nodes, connected or not, with nicknames."""
    from tau.discovery.labmap import nickname_list

    nicks = get(profile, "lab.nicknames") or {}
    out = []
    for name, st in (live.get("nodes") or {}).items():
        on = bool((st or {}).get("connected"))
        out.append({"name": name, "connected": on, "state": "connected now" if on else "paired, not connected",
                    "also": ", ".join(nickname_list(nicks.get(name)))})
    return out
