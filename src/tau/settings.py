"""Every setting TAU has, readable and changeable from the display.

The display's Settings panel shows one section at a time. Each section is a
list of rows the page draws the same way every time:

* ``info``    a label and its current value (always visible, never hover-only)
* ``switch``  on or off
* ``choice``  one of a few buttons (the current one is lit)
* ``text``    a box and a Save button
* ``button``  one action
* ``list``    items, each with its own buttons
* ``meter``   a level from 0 to 1 (the microphone test)

A change is ``change(section, id, value)``. It writes the profile through the
same code ``tau setup`` uses (:func:`tau.setup.apply_values` and
:func:`tau.organs.grants.apply_level`), updates the running profile in place so
the display and every organ see it at once, and returns one sentence saying
what changed. ``tau setup`` stays for scripts.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from tau import paths
from tau.profile import LEVELS, get, iter_services, load_profile, save_profile

SECTIONS = (
    ("brain", "Brain"),
    ("watch", "What I watch"),
    ("permissions", "Permissions"),
    ("voice", "Voice and hearing"),
    ("names", "Name and how I speak to you"),
    ("pacing", "Pacing profile"),
    ("display", "Display"),
    ("lab", "Lab"),
)

# Ports the operating system hands out for a moment (IANA dynamic range). A service seen on one
# of these moves after every restart, so TAU never saves one, and its own ports are never in it.
EPHEMERAL_LOW = 49152

# Fixed defaults for TAU's own ports, offered at setup and changeable in Settings.
DEFAULT_DISPLAY_PORT = 47110
DEFAULT_HUB_PORT = 47111

# Kokoro's English voices (Apache-2.0 weights). a = American, b = British; f/m as Kokoro names them.
VOICES = (
    ("af_heart", "Heart (American)"), ("af_bella", "Bella (American)"), ("af_nicole", "Nicole (American)"),
    ("af_sarah", "Sarah (American)"), ("af_sky", "Sky (American)"), ("am_adam", "Adam (American)"),
    ("am_michael", "Michael (American)"), ("bf_emma", "Emma (British)"), ("bf_isabella", "Isabella (British)"),
    ("bm_george", "George (British)"), ("bm_lewis", "Lewis (British)"),
)
SAMPLE_TEXT = "Hello. This is how I sound."

# Senses shown in "What I watch": (organ, name, one line saying what it reads)
WATCH = (
    ("gpu", "Graphics card", "Reads free graphics memory with nvidia-smi, so I don't load a model that won't fit."),
    ("drives", "Drives", "Reads free space on each drive. Never looks inside slow or network drives."),
    ("models", "Brain's models", "Reads which models the model server has and which one is answering."),
    ("services", "Services", "Checks whether each service below answers on its port."),
    ("notifications", "Notifications", "Counts recent notifications. Reads the title only."),
    ("window", "Focused window", "Reads which app is in front, so I slow my display down during games."),
    ("processes", "Processes", "Reads the list of running programs when you ask what's running."),
)

PACING_SWITCHES = (
    ("hard_day", "Hard day", "Short, literal answers and no suggestions."),
    ("answer_first", "Answer first", "The answer comes before any explanation."),
    ("lists_allowed", "Lists allowed", "Off turns lists into plain sentences."),
    ("one_thing_per_reply", "One thing per reply", "One idea at a time."),
    ("one_question_at_a_time", "One question at a time", "I never ask two things at once."),
    ("restate_on_resume", "Remind me where we were", "When you come back, I say what we were doing."),
    ("sparse_numbers", "Few numbers", "Exact figures go to a pin card instead of the reply."),
    ("announce_before_touching", "Say before I touch anything", "I tell you before I change something."),
    ("never_assume_finished", "Never assume you've finished", "I ask before moving on."),
    ("never_push_next_step", "Don't push a next step", "I don't suggest what to do next unless you ask."),
)

LEVEL_LABELS = {"off": "Off", "sense": "Look", "suggest": "Suggest", "act": "Act"}
LEVEL_HELP = {"off": "is switched off", "sense": "can read this, never change it",
              "suggest": "will ask before changing it",
              "act": "may do this without asking (anything that deletes still asks)"}


# Round 7: every row says what it lets you do, with one short example. Keyed by row id, or by the
# part before ":" for rows made per item. Shown on the display under the row.
LEVEL_ACTIONS = ("Look: can read this. Suggest: will ask before changing it. "
                 "Act: may do this without asking (anything that deletes or can't be undone still asks).")
EXPLAIN = {
    "local": ("Shows the brain on this computer and whether it answers.", "For example: gemma on this computer (answering)."),
    "burst": ("Shows the cloud brain I can use when this computer is busy.", "For example: a bigger model for a long write-up."),
    "none": ("Tells you no brain is connected yet.", "For example: start your model server, then press Check again."),
    "prefer": ("Lets you pick which brain answers first.", "For example: This computer keeps everything private."),
    "check": ("Lets me look for model servers on this computer.", "For example: finds llama.cpp if it is running."),
    "found": ("Shows what my last look found.", "For example: llama.cpp on this computer."),
    "models": ("Lets you pick which model answers, ranked by what fits this computer.", "For example: Use this on a model that fits your graphics card."),
    "test_result": ("Shows the answer from the last brain test.", "For example: answered in 2 seconds."),
    "test": ("Lets you check the brain answers before you rely on it.", "For example: press it after changing models."),
    "sense": ("Lets me read this, never change it. Off means I don't look at all.", "For example: Drives on lets me warn you a disk is nearly full."),
    "services": ("Shows the programs I check are still running.", "For example: your database, up or down."),
    "add_service": ("Lets you add a program for me to check.", "For example: type postgres."),
    "look": ("Lets me look for programs running on this machine. Read-only.", "For example: finds a database you forgot to add."),
    "about": ("Explains what each permission level allows, by what I may do.", LEVEL_ACTIONS),
    "organ": ("Lets you choose how much I may do here. " + LEVEL_ACTIONS, "For example: Suggest on Files means I ask before saving anything."),
    "voice": ("Lets you pick how I sound. Picking one also fetches its pronunciation model.", "For example: George (British)."),
    "play_sample": ("Lets you hear the voice before you keep it.", f'For example: I say "{SAMPLE_TEXT}"'),
    "mic": ("Lets you pick which microphone I listen on.", "For example: your headset rather than the webcam."),
    "test_mic": ("Lets you check I can hear you. Listens for three seconds.", "For example: speak normally and watch the level."),
    "mic_level": ("Shows how loud I hear you.", "For example: very quiet means check it isn't muted."),
    "speaker": ("Lets you pick where I play sound.", "For example: your headphones."),
    "test_speaker": ("Lets you check you can hear me.", "For example: you should hear a short chime."),
    "speaker_result": ("Shows where the last chime played.", "For example: played on Headphones."),
    "hands_free": ("Lets me answer any speech, not just my name. I hear everything said nearby.", "For example: on while you're alone in the lab."),
    "wake": ("Shows whether I've learned to hear my name.", "For example: trained, I listen for 'tau'."),
    "train_wake": ("Lets me learn my name so you can say it instead of pressing Talk.", "For example: about 20 minutes, while you keep working."),
    "assistant_name": ("Lets you rename me.", "For example: Juniper."),
    "user_name": ("Lets you tell me your name.", "For example: Sam."),
    "speak_to_me_as": ("Lets you tell me how to address you.", "For example: by first name, plainly."),
    "pacing": ("Lets you shape every reply to how you read best today.", "For example: Answer first puts the answer before any explanation."),
    "accessibility": ("One switch for the easiest replies: answer first, short, no lists unless you ask, one question at a time.",
                      "For example: turn it on during a tired or painful day."),
    "corner": ("Lets you choose where the small window sits.", "For example: bottom right, away from your work."),
    "monitor": ("Lets you choose which screen the small window opens on.", "For example: Screen 2."),
    "popout": ("Lets you open the small always-on-top window now.", "For example: keep me in view while you work."),
    "display_port": ("Lets you change this page's address number.", "For example: change it if another program already uses the number."),
    "role": ("Lets you choose whether this machine runs the brain or reports to one.", "For example: Node on a laptop."),
    "hub_address": ("Shows the address other machines use to reach this one.", "For example: the address and port the other machine types."),
    "hub_port": ("Lets you change the number other machines connect on.", "For example: change it if another program already uses the number."),
    "paired": ("Shows the machines that report to this one.", "For example: the bench PC, connected."),
    "nickname": ("Lets you choose what you call a machine when you talk to me.", "For example: the big box."),
    "pair": ("Lets another machine join with a one-time code.", "For example: pair your laptop."),
    "pair_code": ("Shows the one-time code to type on the other machine.", "For example: 4821, for 10 minutes."),
    "pair_how": ("Tells you what to do on the other machine.", "For example: Settings, Lab, Node, then the code."),
    "join_hub": ("Lets this machine find the hub.", "For example: the address shown in the hub's Settings, Lab."),
    "join_code": ("Lets this machine join the hub with its code.", "For example: 4821."),
}


def explain(row: Dict[str, Any]) -> Dict[str, Any]:
    """Add the plain "What this lets me do" line and an example to a row."""
    key = row.get("id", "")
    got = EXPLAIN.get(key) or EXPLAIN.get(key.split(":", 1)[0]) or ("Shows " + row.get("label", "this").lower() + ".",
                                                                     "For example: " + str(row.get("value") or "see above") + ".")
    row.setdefault("does", got[0])
    row.setdefault("example", got[1])
    return row


def is_ephemeral(port: Any) -> bool:
    try:
        return int(port) >= EPHEMERAL_LOW or int(port) <= 0
    except (TypeError, ValueError):
        return False


def _row(kind: str, id: str = "", label: str = "", **kw: Any) -> Dict[str, Any]:
    return {"kind": kind, "id": id, "label": label, **kw}


class Settings:
    """Reads and changes settings. ``hooks`` reach the running TAU (voice, mic, hub)."""

    def __init__(self, profile: Dict[str, Any], profile_file: Optional[Path] = None,
                 live: Callable[[], Mapping[str, Any]] = lambda: {},
                 hooks: Optional[Dict[str, Callable[..., Any]]] = None,
                 find_brains: Optional[Callable[..., Any]] = None,
                 discover: Optional[Callable[[], Dict[str, Any]]] = None,
                 audio_devices: Optional[Callable[[], Dict[str, List[str]]]] = None,
                 monitors: Optional[Callable[[], List[Dict[str, Any]]]] = None):
        self.profile = profile
        self.profile_file = Path(profile_file) if profile_file else paths.profile_path()
        self.live = live
        self.hooks = hooks or {}
        self._find_brains = find_brains
        self._discover = discover
        self._audio = audio_devices
        self._monitors = monitors
        self.found: Optional[List[Any]] = None     # model servers from the last "check again"
        self.brain_test: Optional[str] = None
        self.suggestions: Optional[List[Dict[str, Any]]] = None
        self.mic_level: Optional[float] = None
        self.mic_peak = 0.0
        self.mic_live = False
        self.speaker_test: Optional[str] = None
        self.pairing: Optional[Dict[str, Any]] = None
        self.wake_training: Optional[str] = None
        self._lock = threading.Lock()

    # -- writing ------------------------------------------------------------------------------
    def _write(self, values: Optional[Mapping[str, Any]] = None,
               dotted: Optional[Mapping[str, Any]] = None) -> None:
        """Write answers to the profile file (the same path ``tau setup`` writes), then refresh."""
        from tau.setup import _set_dotted, apply_values

        with self._lock:
            data = load_profile(self.profile_file, check=False) if self.profile_file.exists() else dict(self.profile)
            if values:
                apply_values(data, values)
            for k, v in (dotted or {}).items():
                _set_dotted(data, k, v)
            save_profile(data, self.profile_file)
            self._refresh(data)

    def _refresh(self, data: Optional[Dict[str, Any]] = None) -> None:
        data = data if data is not None else load_profile(self.profile_file, check=False)
        self.profile.clear()
        self.profile.update(data)

    def _hook(self, name: str, *a: Any, **kw: Any) -> Any:
        fn = self.hooks.get(name)
        return fn(*a, **kw) if fn else None

    # -- reading ------------------------------------------------------------------------------
    def view(self, section: Optional[str] = None) -> Dict[str, Any]:
        ids = [s for s, _ in SECTIONS]
        section = section if section in ids else ids[0]
        rows = [explain(r) for r in getattr(self, f"_view_{section}")()]
        return {"sections": [{"id": s, "title": t} for s, t in SECTIONS], "section": section,
                "title": dict(SECTIONS)[section], "rows": rows}

    def _view_brain(self) -> List[Dict[str, Any]]:
        p = self.profile
        rows = []
        lane = get(p, "brain.prefer") or "local"
        url, model = get(p, "brain.local.url"), get(p, "brain.local.model")
        burl, bmodel = get(p, "brain.burst.url"), get(p, "brain.burst.model")
        ok = self.live().get("brain_ok")
        if url:
            word = "answering" if ok else ("not answering" if ok is False else "not checked yet")
            rows.append(_row("info", "local", "Brain on this computer",
                             value=f"{model or 'no model picked'} at {url} ({word})"))
        if burl:
            rows.append(_row("info", "burst", "Cloud brain", value=f"{bmodel} at {burl}"))
        if not url and not burl:
            rows.append(_row("info", "none", "Brain", value="Not connected yet. Press Check again to look."))
        if url and burl:
            rows.append(_row("choice", "prefer", "Which brain I use first", value=lane,
                             options=[{"value": "local", "label": "This computer"},
                                      {"value": "burst", "label": "Cloud"}]))
        rows.append(_row("button", "check", "Check again",
                         help="Looks for model servers on this computer and lists every model."))
        if self.found is not None:
            servers = [f for f in self.found if f.kind == "local"]
            rows.append(_row("info", "found", "Model servers found",
                             value=", ".join(f.label for f in servers) if servers else
                             "None. Start your model server, then press Check again."))
            for f in servers:
                rows.append(self._models_row(f, current=(url, model)))
        if self.brain_test:
            rows.append(_row("info", "test_result", "Last test", value=self.brain_test))
        if url or burl:
            rows.append(_row("button", "test", "Test the brain now", help="Sends one short message and waits for the answer."))
        return rows

    def _models_row(self, f: Any, current: tuple) -> Dict[str, Any]:
        from tau import engines
        from tau.setup import ARCH_WORDS

        ranked, _why = engines.rank_models(f.models, get(self.profile, "machine.gpu_vram_gb"),
                                           get(self.profile, "machine.ram_gb"), f.sizes, getattr(f, "meta", None))
        items = []
        for r in ranked:
            bits = [f"about {r.size_gb:g} GB" if r.size_gb is not None else "size not stated",
                    ARCH_WORDS.get(r.arch or "", "MoE or dense: not stated")]
            if r.fits is not None:
                bits.append("fits this computer" if r.fits else "may be too big for this computer")
            if (getattr(f, "loaded", {}) or {}).get(r.name):
                bits.append("in memory now")
            in_use = current == (f.url, r.name) or (current[1] == r.name and str(current[0] or "").startswith(f.url))
            items.append({"id": r.name, "label": r.name + (" (in use)" if in_use else ""), "detail": ", ".join(bits),
                          "size_gb": r.size_gb, "arch": r.arch,
                          "buttons": [] if in_use else [{"id": "use", "label": "Use this",
                                                         "value": {"engine": f.engine, "url": f.url, "model": r.name}}]})
        return _row("list", f"models:{f.url}", f"Models on {f.label}", items=items)

    def _view_watch(self) -> List[Dict[str, Any]]:
        from tau.organs import level_for

        rows = []
        for organ, name, what in WATCH:
            rows.append(_row("switch", f"sense:{organ}", name, value=level_for(self.profile, organ) != "off", help=what))
        health = self.live().get("services") or {}
        items = []
        for s in iter_services(self.profile):
            ok = health.get(s["name"])
            where = s.get("health_url") or (f"port {s['port']}" if s.get("port") else "no address")
            state = ("temporary port: remove it and add it by name" if is_ephemeral(s.get("port")) and not s.get("health_url")
                     else "up" if ok else "down" if ok is False else "not checked yet")
            items.append({"id": s["name"], "label": s["name"], "detail": f"{where}, {state}",
                          "buttons": [{"id": "remove_service", "label": "Remove", "value": s["name"]}]})
        rows.append(_row("list", "services", "Services I check",
                         items=items, empty="None yet. Add one below, or press Look for services."))
        rows.append(_row("text", "add_service", "Add a service",
                         placeholder="a program name, a port, or a web address",
                         help="By name, I find the port it listens on. A port or a web address works too.",
                         button="Add"))
        rows.append(_row("button", "look", "Look for services on this machine",
                         help="Read-only. Takes up to half a minute."))
        if self.suggestions is not None:
            have = {s["name"] for s in iter_services(self.profile)}
            items = [{"id": s["name"], "label": s["name"], "detail": f"found on this machine, port {s['port']}",
                      "buttons": [{"id": "watch_found", "label": "Watch", "value": s["name"]}]}
                     for s in self.suggestions if s["name"] not in have]
            rows.append(_row("list", "found", "Found on this machine", items=items,
                             empty="Nothing new found. Programs on temporary ports are left out."))
        return rows

    def _view_permissions(self) -> List[Dict[str, Any]]:
        from tau.display.model import ORGAN_NAMES
        from tau.organs import OrganRegistry, level_for

        rows = [_row("info", "about", "What the four levels mean",
                     value="Off: switched off. " + LEVEL_ACTIONS)]
        for o in OrganRegistry.all():
            lv = level_for(self.profile, o.name)
            rows.append(_row("choice", f"organ:{o.name}", ORGAN_NAMES.get(o.name, o.name), value=lv,
                             help=f"Lets me {o.description}.",
                             options=[{"value": v, "label": LEVEL_LABELS[v]} for v in LEVELS]))
        return rows

    def _view_voice(self) -> List[Dict[str, Any]]:
        p = self.profile
        dev = (self._audio or _audio_devices)()
        voice = get(p, "tau.voice_name") or "af_heart"
        rows = [_row("choice", "voice", "My voice", value=voice,
                     options=[{"value": v, "label": l} for v, l in VOICES]),
                _row("button", "play_sample", "Play a sample", help=f'I say "{SAMPLE_TEXT}"')]
        mics, spks = _options(dev.get("mics", [])), _options(dev.get("speakers", []))
        rows.append(_row("choice", "mic", "Microphone", value=_current(get(p, "audio.mic"), mics), options=mics,
                         empty="No microphone found. Plug one in, then reopen this section."))
        rows.append(_row("button", "test_mic", "Test the microphone",
                         help="Listens for three seconds and shows the level as it hears it."))
        if self.mic_level is not None:
            rows.append(_row("meter", "mic_level", "Microphone level", value=self.mic_level, live=self.mic_live,
                             text="listening..." if self.mic_live else
                             ("heard you" if self.mic_peak > 0.05 else "very quiet: check it isn't muted")))
        rows.append(_row("choice", "speaker", "Speaker", value=_current(get(p, "audio.speaker"), spks), options=spks,
                         empty="No speaker found."))
        rows.append(_row("button", "test_speaker", "Test the speaker", help="Plays my chime on the speaker above."))
        if self.speaker_test:
            rows.append(_row("info", "speaker_result", "Speaker test", value=self.speaker_test))
        from tau.setup import Setup

        rows.append(_row("switch", "hands_free", "Listen to all speech (not a wake word)", value=bool(get(p, "tau.listen_without_wake_word")),
                         help=Setup.HANDS_FREE_PRIVACY))
        rows.append(_row("info", "wake", "Wake word", value=self._wake_status()))
        if self._can_train() and not (get(p, "tau.wake_model") and Path(str(get(p, "tau.wake_model"))).exists()):
            rows.append(_row("button", "train_wake", f"Train my name now ({get(p, 'tau.wake_word') or 'tau'})",
                             help="Runs on this computer, about 20 minutes. You can keep using me meanwhile."))
        return rows

    def _wake_status(self) -> str:
        wm = get(self.profile, "tau.wake_model")
        if wm and Path(str(wm)).exists():
            return f"trained: I listen for '{get(self.profile, 'tau.wake_word') or 'tau'}'"
        if self.wake_training:
            return self.wake_training
        return ("not trained yet. Training needs the voice extras (Kokoro) and the onnx package, then about "
                "20 minutes on this computer: I record my name in every voice and learn it. "
                "Until then, click Talk or press Space.")

    def _can_train(self) -> bool:
        import importlib.util

        return bool(_train_script()) and all(importlib.util.find_spec(m) for m in ("kokoro", "onnx", "openwakeword"))

    def _view_names(self) -> List[Dict[str, Any]]:
        p = self.profile
        return [_row("text", "assistant_name", "My name", value=get(p, "tau.name") or "TAU", button="Save",
                     help="Shown on the display and used in speech. The wake word needs training again."),
                _row("text", "user_name", "Your name", value=get(p, "user.name") or "", button="Save"),
                _row("text", "speak_to_me_as", "How I speak to you", value=get(p, "user.speak_to_me_as") or "",
                     placeholder="for example: by first name, plainly", button="Save")]

    def _view_pacing(self) -> List[Dict[str, Any]]:
        pc = self.profile.get("pacing") or {}
        rows = [_row("switch", "pacing:accessibility", "Accessibility preset", value=bool(pc.get("accessibility")),
                     help="Answer first, short replies, no lists unless you ask, one question at a time. "
                          "Your own choices below come back when you turn it off."),
                _row("switch", "pacing:enabled", "Pacing profile", value=bool(pc.get("enabled")),
                     help="The rules below shape every reply, checked on this computer after each one."),
                _row("choice", "pacing:max_paragraphs", "Longest reply", value=str(pc.get("max_paragraphs") or "none"),
                     options=[{"value": str(n), "label": f"{n} paragraph{'s' if n > 1 else ''}"} for n in (1, 2, 3, 5)]
                     + [{"value": "none", "label": "No limit"}])]
        for key, name, what in PACING_SWITCHES:
            rows.append(_row("switch", f"pacing:{key}", name, value=bool(pc.get(key)), help=what))
        return rows

    def _view_display(self) -> List[Dict[str, Any]]:
        p = self.profile
        mons = (self._monitors or _monitors)()
        rows = [_row("choice", "corner", "Pop-out corner", value=get(p, "display.corner") or "top-right",
                     options=[{"value": c, "label": c.replace("-", " ").capitalize()}
                              for c in ("top-left", "top-right", "bottom-left", "bottom-right")])]
        if len(mons) > 1:
            rows.append(_row("choice", "monitor", "Pop-out screen", value=str(get(p, "display.monitor") or 0),
                             options=[{"value": str(m["index"]), "label": f"Screen {m['index'] + 1} ({m['w']}x{m['h']})"}
                                      for m in mons]))
        rows.append(_row("button", "popout", "Open the pop-out now",
                         help="A small always-on-top window in the corner above."))
        rows.append(_row("text", "display_port", "Display port", value=str(get(p, "display.port") or DEFAULT_DISPLAY_PORT),
                         button="Save", help=f"This page's address is http://127.0.0.1:<port>/. A fixed number "
                                             f"from 1024 to {EPHEMERAL_LOW - 1}. Takes effect next start."))
        return rows

    def _view_lab(self) -> List[Dict[str, Any]]:
        p = self.profile
        role = get(p, "tau.role") or "hub"
        rows = [_row("choice", "role", "This machine's role", value=role,
                     options=[{"value": "hub", "label": "Hub (runs my brain for the lab)"},
                              {"value": "node", "label": "Node (reports to a hub)"}])]
        if role == "hub":
            rows.append(_row("info", "hub_address", "Hub address",
                             value=f"{get(p, 'hub.host') or 'not set'}, port {get(p, 'hub.port') or 'not set'}"))
            rows.append(_row("text", "hub_port", "Hub port", value=str(get(p, "hub.port") or DEFAULT_HUB_PORT),
                             button="Save", help=f"Nodes connect here. A fixed number from 1024 to {EPHEMERAL_LOW - 1}. "
                                                 "Takes effect next start."))
            nodes = self._paired()
            live = self.live().get("nodes") or {}
            items = [{"id": n, "label": n, "detail": "connected" if (live.get(n) or {}).get("connected") else "away",
                      "buttons": []} for n in nodes]
            rows.append(_row("list", "paired", "Paired machines", items=items, empty="None yet."))
            nicks = get(p, "lab.nicknames") or {}
            from tau.discovery.labmap import nickname_list, short_name

            for n in nodes:
                have = nickname_list(nicks.get(n)) if n in nicks else [short_name(n)]
                rows.append(_row("text", f"nickname:{n}", f"Nicknames for {n}", value=", ".join(have),
                                 button="Save", placeholder="for example: hal, the big box",
                                 help="What you call this machine. Separate names with commas. Any case works."))
            rows.append(_row("button", "pair", "Pair a machine", help="Shows a one-time code for the other machine."))
            if self.pairing and self.pairing["expires"] > time.time():
                left = int((self.pairing["expires"] - time.time()) // 60) + 1
                rows.append(_row("info", "pair_code", "Pairing code", big=True,
                                 value=f"{self.pairing['code']}  (works once, for {left} more minute{'s' if left != 1 else ''})"))
                rows.append(_row("info", "pair_how", "On the other machine",
                                 value=f"Open TAU's Settings, Lab, choose Node, then type this code and "
                                       f"{get(p, 'hub.host')}:{get(p, 'hub.port')} as the hub address."))
        else:
            rows.append(_row("text", "join_hub", "Hub address", value=f"{get(p, 'hub.host') or ''}"
                             + (f":{get(p, 'hub.port')}" if get(p, "hub.port") else ""),
                             placeholder="address:port, shown in the hub's Settings, Lab", button="Save"))
            rows.append(_row("text", "join_code", "Pairing code from the hub", value="", button="Join",
                             help="Press Pair a machine on the hub to get one."))
        return rows

    def _paired(self) -> List[str]:
        try:
            from tau.hub import crypto

            return sorted((crypto.load_json(paths.state_dir() / "nodes.json") or {}).keys())
        except Exception:
            return []

    # -- changing ------------------------------------------------------------------------------
    def change(self, section: str, id: str, value: Any = None, confirm: bool = False) -> Dict[str, Any]:
        """Apply one change. Returns ``{"ok", "message"}`` and, for "act", a ``confirm`` question first."""
        try:
            out = self._change(section, id, value, confirm)
        except ValueError as exc:
            out = {"ok": False, "message": str(exc)}
        out["settings"] = self.view(section)
        return out

    def _change(self, section: str, id: str, value: Any, confirm: bool) -> Dict[str, Any]:
        key, _, arg = id.partition(":")
        fn = getattr(self, f"_do_{key}", None)
        if fn is None:
            raise ValueError("I don't know that setting.")
        return fn(value=value, arg=arg, confirm=confirm)

    @staticmethod
    def _ok(message: str, **kw: Any) -> Dict[str, Any]:
        return {"ok": True, "message": message, **kw}

    # brain
    def _do_check(self, **_: Any) -> Dict[str, Any]:
        from tau import detect

        mine = [str(u)[:-3] if str(u).rstrip("/").endswith("/v1") else str(u)
                for u in (get(self.profile, "brain.local.url"),) if u]
        found = (self._find_brains or detect.find_brains)(mine, None)
        for f in found:
            if f.kind == "local":
                try:
                    detect.model_details(f)
                except Exception:
                    pass
        self.found = found
        local = [f for f in found if f.kind == "local"]
        n = sum(len(f.models) for f in local)
        if not local:
            return self._ok("I didn't find a model server running. Start one, then press Check again.")
        return self._ok(f"Found {len(local)} model server{'s' if len(local) != 1 else ''} with {n} model{'s' if n != 1 else ''}.")

    def _do_use(self, value: Any, **_: Any) -> Dict[str, Any]:
        from tau.brainclient import BrainClient, BrainError
        from tau.setup import brain_values

        v = dict(value or {})
        c = BrainClient(v.get("engine") or "openai_compat", v["url"], v["model"])
        t0 = time.time()
        try:
            c.test_call()
        except BrainError as exc:
            self.brain_test = f"{v['model']} didn't answer ({str(exc).splitlines()[0][:80]}), so I kept the old one."
            return {"ok": False, "message": self.brain_test}
        self._write(values=brain_values(c))
        self.brain_test = f"{v['model']} answered in {time.time() - t0:.1f} s."
        self._hook("brain_changed")
        return self._ok(f"Now using {v['model']}. It answered the test in {time.time() - t0:.1f} seconds.")

    def _do_test(self, **_: Any) -> Dict[str, Any]:
        from tau.brainclient import BrainError
        from tau.lanes import lane_clients

        clients = lane_clients(self.profile)
        lane = get(self.profile, "brain.prefer") or "local"
        c = clients.get(lane) or next(iter(clients.values()), None)
        if c is None:
            raise ValueError("There's no brain to test yet. Press Check again.")
        t0 = time.time()
        try:
            c.test_call()
        except BrainError as exc:
            self.brain_test = f"{c.model} didn't answer: {str(exc).splitlines()[0][:80]}"
            return {"ok": False, "message": self.brain_test}
        self.brain_test = f"{c.model} answered in {time.time() - t0:.1f} s."
        return self._ok(self.brain_test)

    def _do_prefer(self, value: Any, **_: Any) -> Dict[str, Any]:
        if value not in ("local", "burst"):
            raise ValueError("Pick this computer or cloud.")
        self._write(values={"BRAIN_PREFER": value})
        return self._ok("I'll use the brain on this computer first." if value == "local" else "I'll use the cloud brain first.")

    # what I watch
    def _do_sense(self, value: Any, arg: str, **_: Any) -> Dict[str, Any]:
        from tau.organs.grants import apply_level
        from tau.organs import level_for

        names = {o: n for o, n, _ in WATCH}
        if arg not in names:
            raise ValueError("I don't watch that.")
        on = bool(value)
        cur = level_for(self.profile, arg)
        level = (cur if cur != "off" else "sense") if on else "off"
        apply_level(arg, level, profile_file=self.profile_file)
        self._refresh()
        if level != cur:
            self._receipt(f"{names[arg]}: {LEVEL_LABELS.get(cur, cur)} to {LEVEL_LABELS[level]}", "you, in Settings",
                          {"organ": arg, "level": level})
        self._hook("senses_changed")
        return self._ok(f"{names[arg]}: {'on. It shows on my rings now.' if on else 'off.'}")

    def _services(self) -> List[Dict[str, Any]]:
        return [dict(s) for s in iter_services(self.profile)]

    def _do_remove_service(self, value: Any, **_: Any) -> Dict[str, Any]:
        keep = [s for s in self._services() if s["name"] != value]
        self._write(dotted={"services": keep})
        self._hook("senses_changed")
        return self._ok(f"I've stopped checking {value}.")

    def _do_add_service(self, value: Any, **_: Any) -> Dict[str, Any]:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Type a program name, a port or a web address first.")
        have = self._services()
        if "://" in text:
            from urllib.parse import urlparse

            name = urlparse(text).hostname or text
            new = {"name": f"{name}:{urlparse(text).port}" if urlparse(text).port else name, "health_url": text,
                   "importance": 3, "found_by": "you"}
        elif text.isdigit():
            if is_ephemeral(text):
                raise ValueError(f"Port {text} is a temporary one; it changes every restart. "
                                 "Add the program by name instead.")
            new = {"name": f"port {text}", "port": int(text), "importance": 3, "found_by": "you"}
        else:
            hit = self._find_by_name(text)
            if hit is None:
                raise ValueError(f"I can't see a program called {text} listening on a fixed port. "
                                 "Start it, or type its port or web address.")
            new = dict(hit, found_by="you")
        if any(s["name"] == new["name"] for s in have):
            return self._ok(f"I already check {new['name']}.")
        self._write(dotted={"services": have + [new]})
        if "services" not in [o for o, _, _ in WATCH if self._level(o) != "off"]:
            self._do_sense(value=True, arg="services")
        self._hook("senses_changed")
        where = new.get("health_url") or f"port {new.get('port')}"
        return self._ok(f"Added {new['name']} ({where}). It shows on my rings now.")

    def _level(self, organ: str) -> str:
        from tau.organs import level_for

        return level_for(self.profile, organ)

    def _snapshot(self) -> Dict[str, Any]:
        if self._discover is not None:
            return self._discover()
        from tau.discovery import collect

        return collect(include_tools=False)

    def _find_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        from tau.senses.starter import starter_services

        rows = starter_services(self._snapshot(), get(self.profile, "machine.name") or "", limit=None,
                                skip_system=False)
        low = name.lower().removesuffix(".exe")
        for s in rows:
            if s["name"].lower() == low:
                return s
        for s in rows:
            if low in s["name"].lower():
                return s
        return None

    def _do_look(self, **_: Any) -> Dict[str, Any]:
        from tau.senses.starter import starter_services

        self.suggestions = starter_services(self._snapshot(), get(self.profile, "machine.name") or "")
        n = len(self.suggestions)
        return self._ok(f"I found {n} program{'s' if n != 1 else ''} worth watching." if n else
                        "I didn't find anything new listening on a fixed port.")

    def _do_watch_found(self, value: Any, **_: Any) -> Dict[str, Any]:
        hit = next((s for s in self.suggestions or [] if s["name"] == value), None)
        if hit is None:
            raise ValueError("That one isn't in the list any more. Press Look for services again.")
        have = self._services()
        if not any(s["name"] == hit["name"] for s in have):
            self._write(dotted={"services": have + [hit]})
        if self._level("services") == "off":
            self._do_sense(value=True, arg="services")
        self._hook("senses_changed")
        return self._ok(f"I'm checking {hit['name']} on port {hit['port']} now.")

    # permissions
    def _do_organ(self, value: Any, arg: str, confirm: bool, **_: Any) -> Dict[str, Any]:
        from tau.display.model import ORGAN_NAMES
        from tau.organs import OrganRegistry, level_for
        from tau.organs.grants import apply_level, describe_change

        if value not in LEVELS:
            raise ValueError("Pick Off, Look, Suggest or Act.")
        try:
            OrganRegistry.get(arg)
        except KeyError:
            raise ValueError("I don't have that kind of access.")
        name = ORGAN_NAMES.get(arg, arg)
        if value == "act" and not confirm:
            return {"ok": False, "confirm": describe_change(arg, "act", name=get(self.profile, "tau.name") or "TAU"),
                    "message": f"{name} at Act needs your yes."}
        before = level_for(self.profile, arg)
        apply_level(arg, value, profile_file=self.profile_file)
        self._refresh()
        self._receipt(f"{name}: {LEVEL_LABELS.get(before, before)} to {LEVEL_LABELS[value]}",
                      "you, in Settings" + (" (you said yes)" if value == "act" else ""), {"organ": arg, "level": value})
        self._hook("senses_changed")
        return self._ok(f"{name} is set to {LEVEL_LABELS[value]}: it {LEVEL_HELP[value]}.")

    log = None      # the running TAU's ActionLog; Settings opens the same file otherwise

    def _receipt(self, what: str, who: str, args: Optional[Dict[str, Any]] = None) -> None:
        from tau.actionlog import ActionLog

        log = self.log or ActionLog(paths.log_dir(self.profile))
        log.receipt(what, who, args=args, via="settings")

    # voice and hearing
    def _do_voice(self, value: Any, **_: Any) -> Dict[str, Any]:
        if value not in dict(VOICES):
            raise ValueError("Pick one of the voices listed.")
        self._write(values={"VOICE_NAME": value})
        self._hook("voice_changed", value)
        return self._ok(f"My voice is now {dict(VOICES)[value]}. I'll fetch it if it isn't here yet, "
                        "then say a short sample.")

    def _do_play_sample(self, **_: Any) -> Dict[str, Any]:
        if self.hooks.get("play_sample") is None:
            raise ValueError("My voice isn't installed on this computer, so I can only type.")
        self._hook("play_sample", SAMPLE_TEXT)
        return self._ok(f"Playing: {SAMPLE_TEXT}")

    def _do_mic(self, value: Any, **_: Any) -> Dict[str, Any]:
        from tau import audio

        self._write(values={"MIC_DEVICE": value or audio.DEFAULT})
        self._hook("devices_changed")
        return self._ok(f"Microphone set to {audio.label(value)}.")

    def _do_speaker(self, value: Any, **_: Any) -> Dict[str, Any]:
        from tau import audio

        self._write(values={"SPEAKER_DEVICE": value or audio.DEFAULT})
        self.speaker_test = None
        self._hook("devices_changed")
        return self._ok(f"Speaker set to {audio.label(value)}. Press Test the speaker to hear it.")

    def _do_test_mic(self, **_: Any) -> Dict[str, Any]:
        """Listen for three seconds in the background; the meter row updates as it hears."""
        fn = self.hooks.get("test_mic")
        dev = get(self.profile, "audio.mic")
        if fn is not None:      # a plain level (tests, or a running TAU that owns the microphone)
            level = fn(dev)
            if level is None:
                raise ValueError("I couldn't open the microphone. Check it's plugged in and allowed in your "
                                 "privacy settings.")
            self.mic_level = self.mic_peak = round(float(level), 2)
            self.mic_live = False
            return self._ok("I heard you." if self.mic_level > 0.05 else
                            "That was very quiet. Check the microphone isn't muted, or pick another.")
        if self.mic_live:
            return self._ok("Still listening.")
        try:
            levels = _mic_levels(dev)
            first = next(levels)
        except Exception as exc:
            from tau import audio

            raise ValueError(f"I couldn't open the microphone ({audio._why(exc)}). Check it's plugged in and "
                             "allowed in your privacy settings, or pick another.")
        self.mic_level, self.mic_peak, self.mic_live = first, first, True

        def run() -> None:
            try:
                for lv in levels:
                    self.mic_level = lv
                    self.mic_peak = max(self.mic_peak, lv)
            except Exception:
                pass
            finally:
                self.mic_live = False
                self.mic_level = self.mic_peak

        threading.Thread(target=run, daemon=True, name="tau-mic-test").start()
        return self._ok("Listening for three seconds. Say something.")

    def _do_test_speaker(self, **_: Any) -> Dict[str, Any]:
        from tau import audio

        fn = self.hooks.get("test_speaker") or _test_speaker
        dev = get(self.profile, "audio.speaker")
        try:
            out = fn(dev)
        except Exception as exc:
            self.speaker_test = f"didn't play: {audio._why(exc)}"
            raise ValueError(f"I couldn't play on {audio.label(dev)}: {audio._why(exc)}. Pick another, "
                             "or check it's switched on.")
        if out is False:
            self.speaker_test = "didn't play"
            raise ValueError(f"I couldn't play on {audio.label(dev)}. Pick another, or check it's switched on.")
        if isinstance(out, str):    # played, but on the system default instead
            self.speaker_test = f"played on the system default. {out}"
            return self._ok(out)
        self.speaker_test = f"played on {audio.label(dev)}"
        return self._ok(f"Played my chime on {audio.label(dev)}. If you didn't hear it, pick another speaker.")

    def _do_hands_free(self, value: Any, **_: Any) -> Dict[str, Any]:
        on = bool(value)
        if self.hooks.get("hands_free"):
            self._hook("hands_free", on)
            self._refresh()
        else:
            self._write(values={"LISTEN_WITHOUT_WAKE_WORD": on})
        return self._ok("Listen to all speech is on. I treat anything the microphone hears as talk to me, not just my name. "
                        "Switch it off or press Quiet to stop." if on else "Listen to all speech is off.")

    def _do_train_wake(self, **_: Any) -> Dict[str, Any]:
        import subprocess
        import sys

        script = _train_script()
        if not script or not self._can_train():
            raise ValueError("Training needs the voice extras and the onnx package on this computer.")
        word = str(get(self.profile, "tau.wake_word") or "tau")
        out = paths.tau_home() / "wake" / f"{word}.onnx"
        out.parent.mkdir(parents=True, exist_ok=True)
        self.wake_training = f"training now (started {time.strftime('%H:%M')}, about 20 minutes)"

        def run() -> None:
            log = paths.tau_home() / "logs"
            log.mkdir(parents=True, exist_ok=True)
            with open(log / "wake-training.log", "w", encoding="utf-8") as fh:
                r = subprocess.run([sys.executable, str(script), "--name", word, "--out", str(out)],
                                   stdout=fh, stderr=subprocess.STDOUT)
            if r.returncode == 0 and out.exists():
                self._write(values={"WAKE_MODEL_PATH": str(out)})
                self.wake_training = None
                self._hook("note", f"I've learned my name. Restart me to listen for '{word}'.")
            else:
                self.wake_training = "training stopped early; the details are in TAU's log"
                self._hook("note", "Training my name stopped early. The details are in TAU's log.")

        threading.Thread(target=run, daemon=True, name="tau-wake-training").start()
        return self._ok("Training my name. This takes about 20 minutes; keep using me meanwhile.")

    # lab
    def _do_nickname(self, value: Any, arg: str = "", **_: Any) -> Dict[str, Any]:
        from tau.discovery.labmap import nickname_list

        if not arg:
            raise ValueError("Which machine?")
        names = nickname_list(value)
        nicks = dict(get(self.profile, "lab.nicknames") or {})
        nicks[arg] = names
        self._write(dotted={"lab.nicknames": nicks})
        return self._ok(f"I'll know {arg} as {', '.join(names)} too." if names else
                        f"No nicknames for {arg} now; I still know its full name.")

    # names
    def _do_assistant_name(self, value: Any, **_: Any) -> Dict[str, Any]:
        name = str(value or "").strip() or "TAU"
        self._write(values={"ASSISTANT_NAME": name, "WAKE_WORD": name.lower()})
        return self._ok(f"My name is now {name}.")

    def _do_user_name(self, value: Any, **_: Any) -> Dict[str, Any]:
        self._write(values={"USER_NAME": str(value or "").strip() or None})
        return self._ok(f"I'll call you {value}." if value else "I won't use a name for you.")

    def _do_speak_to_me_as(self, value: Any, **_: Any) -> Dict[str, Any]:
        self._write(values={"USER_FORM_OF_ADDRESS": str(value or "").strip() or None})
        return self._ok(f"I'll speak to you {value}." if value else "Cleared.")

    # pacing
    def _do_pacing(self, value: Any, arg: str, **_: Any) -> Dict[str, Any]:
        keys = {k for k, _, _ in PACING_SWITCHES} | {"enabled", "max_paragraphs", "accessibility"}
        if arg not in keys:
            raise ValueError("I don't know that pacing rule.")
        if arg == "max_paragraphs":
            v: Any = None if value in (None, "none", "") else int(value)
            msg = f"Replies are at most {v} paragraph{'s' if v != 1 else ''} now." if v else "No length limit now."
        else:
            v = bool(value)
            name = dict((k, n) for k, n, _ in PACING_SWITCHES).get(
                arg, "Accessibility preset" if arg == "accessibility" else "Pacing profile")
            msg = f"{name}: {'on' if v else 'off'}."
        self._write(dotted={f"pacing.{arg}": v})
        self._hook("pacing_changed")
        return self._ok(msg)

    # display
    def _do_corner(self, value: Any, **_: Any) -> Dict[str, Any]:
        if value not in ("top-left", "top-right", "bottom-left", "bottom-right"):
            raise ValueError("Pick one of the four corners.")
        self._write(values={"DISPLAY_CORNER": value})
        return self._ok(f"The pop-out sits {value.replace('-', ' ')} now.")

    def _do_monitor(self, value: Any, **_: Any) -> Dict[str, Any]:
        self._write(values={"DISPLAY_MONITOR": int(value)})
        return self._ok(f"The pop-out opens on screen {int(value) + 1}.")

    def _do_popout(self, **_: Any) -> Dict[str, Any]:
        if self.hooks.get("popout") is None:
            raise ValueError("The pop-out needs the display extra (pywebview) on this computer.")
        self._hook("popout")
        return self._ok("Opening the pop-out.")

    def _port(self, value: Any) -> int:
        try:
            port = int(str(value).strip())
        except ValueError:
            raise ValueError("A port is a number.")
        if not 1024 <= port < EPHEMERAL_LOW:
            raise ValueError(f"Pick a fixed port from 1024 to {EPHEMERAL_LOW - 1}.")
        return port

    def _do_display_port(self, value: Any, **_: Any) -> Dict[str, Any]:
        port = self._port(value)
        self._write(values={"DISPLAY_PORT": port})
        return self._ok(f"Display port saved as {port}. It takes effect next time I start.")

    def _do_hub_port(self, value: Any, **_: Any) -> Dict[str, Any]:
        port = self._port(value)
        self._write(values={"HUB_PORT": port})
        return self._ok(f"Hub port saved as {port}. It takes effect next time I start.")

    # lab
    def _do_role(self, value: Any, **_: Any) -> Dict[str, Any]:
        if value not in ("hub", "node"):
            raise ValueError("Pick hub or node.")
        vals: Dict[str, Any] = {"ROLE": value}
        if value == "hub" and not get(self.profile, "hub.port"):
            from tau import detect

            vals.update({"HUB_HOST": detect.lan_address(), "HUB_BIND_ADDRESS": detect.lan_address(),
                         "HUB_PORT": DEFAULT_HUB_PORT})
        self._write(values=vals)
        return self._ok("This machine is the hub now. Restart me to start listening for nodes." if value == "hub"
                        else "This machine is a node now. Type the hub's address and a pairing code below.")

    def _do_pair(self, **_: Any) -> Dict[str, Any]:
        code = self._hook("pair_code")
        if code is None:
            from tau.hub.server import HubServer

            code = HubServer(get(self.profile, "hub.bind") or "127.0.0.1",
                             int(get(self.profile, "hub.port") or 0)).new_pairing_code()
        self.pairing = {"code": code, "expires": time.time() + 600}
        return self._ok(f"Pairing code: {code}. It works once, for ten minutes.")

    def _do_join_hub(self, value: Any, **_: Any) -> Dict[str, Any]:
        host, _, port = str(value or "").strip().rpartition(":")
        if not host or not port.isdigit():
            raise ValueError("Type the hub's address as address:port, for example my-hub:47111.")
        self._write(values={"HUB_HOST": host, "HUB_PORT": int(port)})
        return self._ok(f"Hub address saved: {host}:{port}.")

    def _do_join_code(self, value: Any, **_: Any) -> Dict[str, Any]:
        import socket

        host, port = get(self.profile, "hub.host"), get(self.profile, "hub.port")
        if not host or not port:
            raise ValueError("Save the hub's address first.")
        from tau.hub.node import NodeClient

        try:
            NodeClient(get(self.profile, "machine.name") or socket.gethostname()).pair(host, int(port), str(value).strip())
        except Exception as exc:  # wrong code, hub not reachable
            raise ValueError(f"Pairing didn't work ({str(exc)[:80] or type(exc).__name__}). Check the code and address.")
        return self._ok("Paired with the hub. Restart me to connect.")


def repair_profile(data: Dict[str, Any]) -> List[str]:
    """Fix what TAU 1.0.0 saved wrongly, in place. Returns one plain sentence per fix.

    * the display or hub port was a random (often temporary) one: use the fixed default;
    * a service found by discovery on a temporary port: drop it (it flashes "down" after every
      restart); the owner can add it again by name in Settings.
    """
    said: List[str] = []
    disp = get(data, "display.port")
    if disp in (None, 0) or is_ephemeral(disp) or (isinstance(disp, int) and disp < 1024):
        data.setdefault("display", {})["port"] = DEFAULT_DISPLAY_PORT
        if disp:
            said.append(f"My display moved from a temporary port ({disp}) to a fixed one ({DEFAULT_DISPLAY_PORT}).")
    hub = get(data, "hub.port")
    if hub is not None and (hub == 0 or is_ephemeral(hub)):
        data.setdefault("hub", {})["port"] = DEFAULT_HUB_PORT
        said.append(f"The hub moved from a temporary port ({hub}) to a fixed one ({DEFAULT_HUB_PORT}). "
                    "Paired machines need the new address.")
    services = data.get("services")
    if isinstance(services, list):
        dropped = [s.get("name") for s in services if isinstance(s, dict) and not s.get("health_url")
                   and is_ephemeral(s.get("port")) and s.get("found_by", "discovery") == "discovery"]
        if dropped:
            data["services"] = [s for s in services if not (isinstance(s, dict) and s.get("name") in dropped)]
            said.append(f"I stopped checking {', '.join(map(str, dropped))}: they were on temporary ports that "
                        "change every restart. Add them again by name in Settings, What I watch.")
    return said


# -- real-device helpers (replaced by hooks in the running TAU and in tests) ----------------------
def _options(items: List[Any]) -> List[Dict[str, str]]:
    """Device rows: dicts from :func:`tau.audio.list_devices`, or plain names (tests, older code)."""
    return [i if isinstance(i, dict) else {"value": str(i), "label": str(i)} for i in items]


def _current(stored: Any, options: List[Dict[str, str]]) -> Any:
    """The option that is lit: the stored one if it's listed (exact match), else what it resolves to."""
    from tau import audio

    vals = [o["value"] for o in options]
    if stored in vals:
        return stored
    if stored in (None, "") and audio.DEFAULT in vals:
        return audio.DEFAULT
    want = audio.parse(stored)
    if want:   # a name saved by TAU 1.1: light the one row with that name
        named = [v for v in vals if (audio.parse(v) or ("", None, ""))[2] == want[2]]
        if len(named) == 1:
            return named[0]
    return stored


def _mic_levels(device: Any, seconds: float = 3.0):
    """Yield the microphone level (0..1) every 100 ms for ``seconds``. Nothing is kept."""
    import numpy as np
    import sounddevice as sd

    from tau import audio

    dev, _ = audio.resolve(device, "input", sd)
    rate = audio.pick_rate(dev, "input", 16000, sd)
    block = rate // 10
    with sd.InputStream(samplerate=rate, channels=1, dtype="float32", blocksize=block, device=dev) as s:
        for _ in range(int(seconds * 10)):
            data, _o = s.read(block)
            yield round(min(1.0, float(np.sqrt(np.mean(np.asarray(data) ** 2)) * 8)), 2)


def _audio_devices() -> Dict[str, List[Any]]:
    from tau import detect

    return detect.audio_devices()


def _monitors() -> List[Dict[str, Any]]:
    from tau import detect

    try:
        return detect.monitors()
    except Exception:
        return []


def _test_speaker(device: Any) -> Any:
    """Play the chime on ``device``. True, or a sentence if the default played instead; raises the
    exact failure if nothing played."""
    from tau import audio
    from tau.ears.pipeline import chime

    note = audio.play(chime(), 24000, device)
    return note or True


def _train_script() -> Optional[Path]:
    p = Path(__file__).resolve().parents[2] / "scripts" / "train_wake_word.py"
    return p if p.exists() else None


__all__ = ["Settings", "repair_profile", "SECTIONS", "DEFAULT_DISPLAY_PORT", "DEFAULT_HUB_PORT", "EPHEMERAL_LOW", "is_ephemeral"]
