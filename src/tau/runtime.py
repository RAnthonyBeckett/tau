"""``tau run``: start the hub (or a node), the display, ears and voice.

Everything optional degrades plainly: no audio packages -> text only; no
pywebview -> docked page only; no wake model -> click-to-talk only.
"""

from __future__ import annotations

import sys

import importlib.util
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from tau.connect import LookStore

from tau import paths
from tau.assistant import Assistant
from tau.display.server import DisplayHub
from tau.oj_bridge import bind, register_with_openjarvis
from tau.pacing import CLOUD_CHOICES, CLOUD_NOTICE, record_cloud_choice
from tau.profile import get, load_profile


def _has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


class Runtime:
    def __init__(self, profile: Optional[Dict[str, Any]] = None):
        self.profile = profile or load_profile()
        self.display = DisplayHub(self.profile, on_action=self.on_action, on_text=self.on_text,
                                  on_work=self.on_work, on_connections=self.on_connections)
        self.voice = None
        self.ears = None
        self.lab = None
        self.assistant: Optional[Assistant] = None
        self.stop = threading.Event()
        self._mic_restart = threading.Event()

    # -- wiring -------------------------------------------------------------------------
    repairs: list = []

    def build(self, want_voice: bool = True) -> "Runtime":
        self._repair()
        if want_voice and _has("sounddevice") and _has("kokoro") and _has("misaki"):
            from tau.voice import KokoroEngine, SoundDeviceSink, Voice

            self.voice = Voice(KokoroEngine(get(self.profile, "tau.voice_name", "af_heart")), SoundDeviceSink(get(self.profile, "audio.speaker"),
                                                                                                    on_problem=self._sound_problem),
                               voice=get(self.profile, "tau.voice_name", "af_heart"),
                               on_level=lambda v: self.display.publish({"level": v}),
                               on_state=lambda s: self.display.publish(
                                   {"state": s} if s == "speaking" else {"state": s, "speaking_text": None}),
                               on_error=self._speak_failed)
        if get(self.profile, "tau.role") == "hub" and get(self.profile, "hub.bind") and get(self.profile, "hub.port"):
            from tau.hub.server import HubServer

            lan_ok = "hub" in (get(self.profile, "network.lan_opt_in", []) or [])
            self.lab = HubServer(get(self.profile, "hub.bind"), int(get(self.profile, "hub.port")),
                                 lan_opt_in=lan_ok)
        self.assistant = Assistant(self.profile, voice=self.voice, display=self.display, lab=self.lab)
        self.assistant.look_store = self.look_store()
        if want_voice and _has("sounddevice"):
            from tau.check import Devices

            self.assistant.check_devices = Devices(get(self.profile, "audio.mic"), get(self.profile, "audio.speaker"))
        if self.lab is not None:
            self.lab.on_snapshot = self.assistant.lab_map.write_snapshot
            self.lab.on_paired = self._paired
            self.lab.start()
        self.assistant.work.on_change = lambda summary: self.display.publish({"work": summary})
        self.assistant.work.on_done = self._job_done
        self.assistant.brain.ensure(self.profile)
        self._wire_settings()
        self._pins_inline()
        register_with_openjarvis()
        bind(self.assistant.organs)
        if want_voice and _has("faster_whisper") and _has("sounddevice"):
            from tau.ears.pipeline import Ears, SileroVAD, Transcriber, WakeWord, chime

            wm = get(self.profile, "tau.wake_model")
            wake = WakeWord(str(wm)) if wm and Path(str(wm)).exists() and _has("openwakeword") else None

            def play_chime():
                if self.voice is not None and self.voice.sink is not None:
                    try:
                        self.voice.sink.open(24000)
                        self.voice.sink.write(chime())
                    except Exception:
                        pass

            transcriber = Transcriber()
            self.assistant.check_transcribe = transcriber
            self.ears = Ears(transcribe=transcriber, on_text=self._heard, vad=SileroVAD(), wake=wake,
                             play_chime=play_chime,
                             on_wake_while_speaking=lambda: self.voice and self.voice.interrupt(),
                             is_speaking=lambda: bool(self.voice and self.voice.is_speaking()),
                             on_state=lambda s: self.display.publish({"state": s}),
                             hands_free=bool(get(self.profile, "tau.listen_without_wake_word")))
        return self

    def _repair(self) -> None:
        """Fix ports and services 1.0.0 saved wrongly; say so on the display once it's up."""
        from tau.settings import repair_profile

        self.repairs = repair_profile(self.profile)
        if self.repairs:
            try:
                from tau.profile import load_profile, save_profile

                data = load_profile(check=False)
                repair_profile(data)
                save_profile(data)
            except Exception:
                pass

    # -- settings on the display ---------------------------------------------------------------
    def _wire_settings(self) -> None:
        from tau.settings import Settings

        hooks = {"note": self.display.note, "brain_changed": self._brain_changed,
                 "senses_changed": self._senses_changed, "voice_changed": self._voice_changed,
                 "pacing_changed": lambda: self.display.publish({}), "devices_changed": self._devices_changed,
                 "popout": self._open_popout}
        if self.voice is not None:
            hooks["play_sample"] = lambda text: self.voice.say(text)
        hooks["hands_free"] = self.set_hands_free
        if self.lab is not None:
            hooks["pair_code"] = self.lab.new_pairing_code
        self.settings = Settings(self.profile, self.assistant.profile_file, live=lambda: self.display.live,
                                 hooks=hooks)
        self.settings.log = self.assistant.log
        self.display.settings = self.settings
        # receipts: read from log/actions.jsonl, shown in the side panel's Recent actions
        self.assistant.log.on_write = lambda entry: self.display.publish({"receipts": self.assistant.log.receipts()})
        self.assistant.organs.on_pending = lambda p: self.display.publish(
            {"proposal": {"id": p.id, "text": f"Shall I {p.summary}?", "machine": p.machine or ""} if p else None})
        self.display.publish({"receipts": self.assistant.log.receipts()})

    _answer_pinned: Optional[Dict[str, Any]] = None

    def _pins_inline(self) -> None:
        """A new pin card also shows in the conversation, where it was made."""
        board = self.assistant.pins
        original = board.pin

        def pin(title: str, text: str, kind: str = "answer") -> Dict[str, Any]:
            card = original(title, text, kind)
            if kind == "answer":
                self._answer_pinned = card     # the reply itself is marked "kept on a pin card"
            else:
                self.display.add_message("tau", text, kind="pin", title=title, pin_id=card["id"])
            return card

        board.pin = pin

    def _brain_changed(self) -> None:
        from tau.lanes import lane_clients

        self.assistant.lanes.clients = lane_clients(self.profile)
        threading.Thread(target=self.sense_once, daemon=True).start()

    def _senses_changed(self) -> None:
        threading.Thread(target=self.sense_once, daemon=True).start()

    def _paired(self, name: str) -> None:
        """A new machine: its short hostname becomes its first nickname (changeable in Settings, Lab)."""
        from tau.discovery.labmap import short_name

        nicks = get(self.profile, "lab.nicknames") or {}
        if name not in nicks and getattr(self, "settings", None) is not None:
            self.settings.change("lab", f"nickname:{name}", short_name(name))

    def _sound_problem(self, msg: str) -> None:
        """The chosen speaker failed and the default is used: say so on the display."""
        self.display.flash(msg, kind="notice")

    def _devices_changed(self) -> None:
        """A new speaker or microphone from Settings takes effect now, not at the next start."""
        spk, mic = get(self.profile, "audio.speaker"), get(self.profile, "audio.mic")
        if self.voice is not None and self.voice.sink is not None and hasattr(self.voice.sink, "set_device"):
            self.voice.sink.set_device(spk)
        if self.assistant is not None and getattr(self.assistant, "check_devices", None) is not None:
            self.assistant.check_devices.mic, self.assistant.check_devices.speaker = mic, spk
        self._mic_restart.set()      # the listening loop reopens on the new microphone

    def _speak_failed(self, exc: BaseException) -> None:
        """Speaking failed: flash the display and pin the plain reason. The reply is already text."""
        from tau.voice import failure_reason

        why = failure_reason(exc, getattr(self.voice, "voice", ""))
        self.display.flash(f"I couldn't speak: {why}. My reply is on screen as text.")
        if self.assistant is not None:
            self.assistant.pins.pin("I couldn't speak", f"I couldn't speak my last reply because {why}.\n\n"
                                    "Replies still appear as text while this is fixed.", kind="fix")

    def _voice_changed(self, voice: str) -> None:
        """A voice picked in Settings: download it if it isn't here yet, then play a short sample."""
        if self.voice is None:
            return
        before = self.voice.voice

        def fetch_then_sample() -> None:
            from tau.kokoro_voice import ensure_voice
            from tau.settings import SAMPLE_TEXT

            try:
                got = ensure_voice(voice, self.display.note)
            except Exception as exc:
                self.voice.voice = before          # keep the voice that works
                self._speak_failed(exc)
                return
            self.voice.voice = voice
            if got:
                self.display.note(f"Downloaded the voice {voice}. Here's how it sounds.")
            self.voice.say(SAMPLE_TEXT)

        threading.Thread(target=fetch_then_sample, daemon=True, name="tau-voice-fetch").start()

    url: Optional[str] = None

    def _open_popout(self) -> None:
        import subprocess

        if not _has("webview") or not self.url:
            raise ValueError("The pop-out needs the display extra (pywebview) on this computer.")
        subprocess.Popen([sys.executable, "-m", "tau.display.popout", self.url + "?mode=popout"])

    def _heard(self, text: str) -> None:
        r = self._converse(text)
        print(f"you: {text}\n{self.assistant.name.lower()}: {r.say}")

    def _converse(self, text: str):
        """One exchange; both sides land in the display's conversation."""
        self.display.add_message("you", text)
        try:
            r = self.assistant.handle(text)
        except Exception as exc:
            from tau import quiet

            msg = quiet.plain_failure(exc)
            self.display.publish({"state": "idle"})
            self.display.flash(msg)
            self.display.add_message("tau", msg)
            from tau.assistant import Reply
            return Reply(msg, "error")
        say = r.say or ("Okay." if r.kind == "reflex" else "")
        pinned, self._answer_pinned = self._answer_pinned, None
        if say:
            self.display.add_message("tau", say, pinned=bool(pinned))
            if self.voice is not None and self.voice.is_speaking():
                self.display.publish({"speaking_text": say})
        if r.kind == "error":
            self.display.flash(r.say)
        return r

    # -- work: folders, connections, jobs ---------------------------------------------------
    def _job_done(self, job: Dict[str, Any]) -> None:
        """Say once that a job ended, and leave a result card until it's dismissed."""
        from tau.work import result_text

        title, body = result_text(self.assistant.work, job)
        self.assistant.pins.pin(title, body, kind="result")
        self.display.add_message("tau", title)
        self.assistant._speak(title)

    def on_work(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Clicks from Add a folder, Add an app, Set up work and My work.

        Slow parts (worker checks, folder dialogs) run here on the request thread, never in the
        audio callback; the job itself runs in its own thread."""
        from tau.work import handle_op

        return handle_op(self.assistant, data)

    _look_store: Optional[LookStore] = None

    def look_store(self) -> LookStore:
        if self._look_store is None:
            from tau.connect import LookStore

            self._look_store = LookStore(paths.state_dir() / "looks")
        return self._look_store

    def on_connections(self, data: Dict[str, Any]) -> Dict[str, Any]:
        from tau import connect

        actionlog = self.assistant.log if self.assistant is not None else None
        name = self.assistant.name if self.assistant is not None else str(get(self.profile, "tau.name", "TAU"))
        reply = connect.handle_connections(
            self.look_store(),
            data,
            hub=self.lab,
            actionlog=actionlog,
            name=name,
        )
        if reply.get("ok") and data.get("op") in ("switch", "all_off", "look") and reply.get("say"):
            self.display.note(reply["say"])
        return reply

    # -- display callbacks ----------------------------------------------------------------
    def on_text(self, text: str) -> Dict[str, Any]:
        r = self._converse(text)
        return {"ok": True, "say": r.say, "kind": r.kind}

    def _tell(self, text: str) -> Dict[str, Any]:
        """An answer to a click: shown in the conversation, spoken if voice is on."""
        self.display.add_message("tau", text)
        self.assistant._speak(text)
        return {"ok": True, "say": text}

    # -- hearing -------------------------------------------------------------------------------
    def mic_info(self) -> Dict[str, Any]:
        if self.ears is None:
            return {"ready": False, "mode": "click", "error": self._mic_error}
        mode = "wake" if self.ears.wake is not None else ("vad" if self.ears.hands_free else "click")
        return {"ready": self._mic_error is None, "mode": mode, "error": self._mic_error}

    _mic_error: Optional[str] = None

    def mic_failed(self, exc: BaseException) -> None:
        """A microphone failure: flash the display, and pin the fix (never only the console)."""
        import logging
        import platform

        logging.getLogger("tau.ears").error("microphone failed: %r", exc)
        self._mic_error = "not working"
        if platform.system() == "Windows":
            fix = ("1. Open Windows Settings > Privacy & security > Microphone.\n"
                   "2. Turn on 'Let desktop apps access your microphone'.\n"
                   "3. Check the microphone is plugged in and not muted, then close and reopen TAU.\n"
                   "To pick a different microphone: Settings, Voice and hearing")
        else:
            fix = ("1. Check the microphone is plugged in and not muted (your sound settings).\n"
                   "2. Close and reopen TAU.\n"
                   "To pick a different microphone: Settings, Voice and hearing")
        from tau.audio import _why, label

        self.assistant.pins.pin("Microphone isn't working", f"I couldn't open the microphone "
                                f"{label(get(self.profile, 'audio.mic'))} ({_why(exc)}).\n\nHow to fix it:\n{fix}", kind="fix")
        self.display.flash("I can't hear the microphone. The fix is on a pin card. You can still type.")
        self.display.publish({"mic": self.mic_info(), "state": "idle"})

    def _listen(self) -> None:
        from tau.ears.pipeline import run_microphone

        rt = self

        class _Either:            # stop for good, or stop to reopen on a new microphone
            def is_set(self) -> bool:
                return rt.stop.is_set() or rt._mic_restart.is_set()

        while not self.stop.is_set():
            self._mic_restart = threading.Event()
            try:
                run_microphone(self.ears, get(self.profile, "audio.mic"), _Either())
            except Exception as exc:  # no device, permission refused, unplugged
                self.mic_failed(exc)
                self._mic_restart.wait()      # try again once a microphone is picked in Settings
                if self.stop.is_set():
                    return
                self._mic_error = None
                self.display.publish({"mic": self.mic_info()})

    def on_action(self, a: Dict[str, Any]) -> Dict[str, Any]:
        asst = self.assistant
        if a.get("help_seen"):
            self._save_display_state(help_seen=True)
            self.display.publish({"help_seen": True})
            return {"ok": True}
        if a.get("flash_clear"):
            self.display.publish({"flash": None})
            return {"ok": True}
        if "hands_free" in a:
            return self.set_hands_free(bool(a["hands_free"]))
        if a.get("switch") == "quiet":
            asst.muted = not asst.muted
            if asst.muted and self.voice is not None:
                self.voice.interrupt()
            self.display.publish({"muted": asst.muted})
            return {"ok": True}
        if a.get("switch") == "hard_day":
            on = not bool((self.profile.get("pacing") or {}).get("hard_day"))
            msg = asst._hard_day(on).say
            self.display.publish({"hard_day": on})
            return self._tell(msg)
        if a.get("organ"):
            from tau.display.model import build_organs

            o = next((x for x in build_organs(self.profile, self.display.live) if x["id"] == a["organ"]), None)
            if o is None:
                return {"ok": False}
            more = "" if o["level"] != "off" else " To switch it on: tau grant " + o["id"] + " sense"
            return self._tell(f"{o['label']}. It lets me {o['what']}.{more}")
        if a.get("core"):
            if self.voice is not None and self.voice.is_speaking():
                self.voice.interrupt()
                return {"ok": True, "did": "interrupt"}
            if self.ears is not None and self._mic_error is None:
                if self.ears.gate_open:
                    self.ears.close_gate()
                    return {"ok": True, "did": "stopped listening"}
                self.ears.open_gate()
                return {"ok": True, "did": "listening"}
            self.display.flash("No microphone is working, so type below instead. "
                               "The fix is on a pin card." if self._mic_error else
                               "Hearing isn't set up on this computer, so type below instead.", kind="notice")
            return {"ok": False}
        if a.get("service") and a.get("do") in ("start", "stop"):
            out = asst.organs.call("services", a["do"], via="display", name=str(a["service"]))
            self.display.note(out.message)
            threading.Thread(target=self.sense_once, daemon=True).start()
            return {"ok": out.ok, "say": out.message}
        if a.get("proposal") and a.get("answer") in ("yes", "no"):
            org = asst.organs
            out = org.confirm(str(a["proposal"]), via="display") if a["answer"] == "yes" else org.decline(str(a["proposal"]))
            self.display.publish({"proposal": None})
            return self._tell(out.message)
        if a.get("pin_clear"):
            return {"ok": bool(asst.pins.clear(a["pin_clear"]))}
        if a.get("cloud_choice") in CLOUD_CHOICES.values():
            ch = a["cloud_choice"]
            self.profile.setdefault("pacing", {})["cloud_choice"] = ch
            if ch == "off":
                self.profile["pacing"]["enabled"] = False
            record_cloud_choice(asst.brain.root, ch)
            self.display.publish({"cloud_notice": None})
            return {"ok": True}
        seg = str(a.get("segment", ""))
        if seg == "pins":
            pins = asst.pins.waiting()
            for p in pins:
                asst.pins.open(p["id"])
            return {"ok": True, "pins": pins}
        if seg == "mic":
            asst.muted = not asst.muted
            self.display.publish({"muted": asst.muted})
            return {"ok": True}
        if seg == "brain":
            if not (get(self.profile, "brain.local.url") or get(self.profile, "brain.burst.url")):
                return self._tell("I'm not connected to a brain yet, so I can only do simple things like "
                                  "checking services. To connect one: tau setup brain")
            up = self.sense_once().get("brain_ok")
            lane = get(self.profile, "brain.prefer") or "local"
            model, url = get(self.profile, f"brain.{lane}.model"), get(self.profile, f"brain.{lane}.url")
            if up is None:
                return self._tell(f"I'm using {model} at {url}.")
            return self._tell(f"{model} at {url} is answering." if up else
                              f"{model} at {url} isn't answering. Check the model server is running.")
        if seg in ("gpu", "drives", "notifications") or seg.startswith("drive:"):
            organ = "drives" if seg.startswith("drive") else seg
            action = {"gpu": "vram", "drives": "list", "notifications": "recent"}[organ]
            return self._tell(asst.organs.call(organ, action, via="display").message)
        if seg.endswith(":none") or seg == "pins" and not asst.pins.waiting():
            from tau.display.model import build_segments

            s = next((x for x in build_segments(self.profile, self.display.live) if x["id"] == seg), None)
            return self._tell(f"{s['label']}. {s.get('hint') or ''}".strip() if s else "Nothing here yet.")
        if seg.startswith("svc:"):
            name = seg[4:]
            # A click only reads. Starting or stopping is its own button on the detail card.
            st = asst.organs.call("services", "status", via="display", name=name)
            return self._tell(st.message)
        return {"ok": False}

    def set_hands_free(self, on: bool) -> Dict[str, Any]:
        """Listen for any speech without the wake word (voice activity only). Off by default."""
        if self.ears is not None:
            self.ears.hands_free = on
        self.profile.setdefault("tau", {})["listen_without_wake_word"] = on
        try:
            from tau.profile import load_profile, save_profile

            data = load_profile(check=False)
            data.setdefault("tau", {})["listen_without_wake_word"] = on
            save_profile(data)
        except Exception:
            pass
        self.display.publish({"mic": self.mic_info()})
        return self._tell("Listen to all speech is on. I'll treat any speech as talk to me, not just my name. "
                          "Switch it off or press Quiet to stop." if on else "Listen to all speech is off.")

    # -- small display state kept between runs (the help overlay was seen) --------------------
    def _display_state_file(self) -> Path:
        return paths.state_dir() / "display.json"

    def _load_display_state(self) -> Dict[str, Any]:
        import json

        try:
            return json.loads(self._display_state_file().read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_display_state(self, **kw: Any) -> None:
        import json

        d = self._load_display_state()
        d.update(kw)
        f = self._display_state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(d), encoding="utf-8")

    # -- senses: fill the rings from real data (no model calls) --------------------------------
    SENSE_EVERY = 30.0

    def sense_once(self) -> Dict[str, Any]:
        from tau.senses.heartbeat import check_all

        asst, upd = self.assistant, {}
        upd["services"] = check_all(self.profile)
        prev = self.display.live.get("services") or {}
        for name, ok in upd["services"].items():
            if prev.get(name) and ok is False:
                self.display.flash(f"{name} stopped answering.")
        if asst.organs.can_read("gpu"):
            try:
                from tau.senses.gpu import read_gpus

                g = read_gpus()
                upd["gpu_none"] = not g
                if g:
                    upd["vram_free_gb"] = round(sum(x.get("free_mb", 0) for x in g) / 1024, 1)
                    upd["vram_total_gb"] = round(sum(x.get("total_mb", 0) for x in g) / 1024, 1)
            except Exception:
                upd["gpu_none"] = True
        if asst.organs.can_read("drives"):
            try:
                from tau.senses.drives import list_drives

                upd["drives"] = list_drives()
            except Exception:
                upd["drives"] = []
        if asst.organs.can_read("notifications"):
            try:
                from tau.senses.notifications import recent

                upd["notifications"] = len(recent(10))
            except Exception:
                upd["notifications"] = 0
        url = get(self.profile, "brain.local.url")
        if url:
            from tau.detect import probe_model_server

            upd["brain_ok"] = probe_model_server(get(self.profile, "brain.local.engine") or "openai_compat",
                                                 url, timeout=2) is not None
        if self.lab is not None:
            upd["nodes"] = {n: {"connected": True} for n in getattr(self.lab, "nodes", {})}
        upd["pins"] = len(asst.pins.waiting())
        import time

        upd["sensed_at"] = time.time()
        self.display.publish(upd)
        return upd

    def _sense_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.sense_once()
            except Exception:
                import logging

                logging.getLogger("tau.senses").exception("sensing failed")
            if self.stop.wait(self.SENSE_EVERY):
                return

    # -- start ---------------------------------------------------------------------------------
    def start(self, popout: bool = False, open_browser: bool = True, console: bool = True,
              window: bool = False) -> None:
        port = self.serve_display()
        if port is None:                       # TAU is already open on its port: show that one
            url = f"http://127.0.0.1:{self.display_port()}/"
            if open_browser and not (popout or window):
                webbrowser.open(url)
            print(f"{self.assistant.name} is already running. Display: {url}")
            return
        url = self.url = f"http://127.0.0.1:{port}/"
        print(f"{self.assistant.name} is up. Display: {url}")
        if self.lab is not None:
            print(f"Hub listening for nodes on {self.lab.bind}:{self.lab.port}. Pair a node with `tau pair`.")
        if self._needs_cloud_notice():
            self.display.publish({"cloud_notice": {"text": CLOUD_NOTICE, "choices": [
                {"label": k, "value": v} for k, v in CLOUD_CHOICES.items()]}})
        top = self.assistant.brain.top_journal_entry()
        if top and console:
            print("Last journal entry:\n" + top)
        if self.assistant.organs.can_read("window"):
            threading.Thread(target=self._watch_focus, daemon=True).start()
        for line in self.repairs:
            self.display.add_message("tau", line, kind="pin", title="Fixed at start")
        if self.repairs:
            self.display.note(self.repairs[0])
        self.display.publish({"mic": self.mic_info(), "help_seen": bool(self._load_display_state().get("help_seen")),
                              "pins": len(self.assistant.pins.waiting())})
        threading.Thread(target=self._sense_loop, daemon=True, name="tau-senses").start()
        threading.Thread(target=self.assistant.remember_model_server, daemon=True).start()
        if self.ears is not None:
            threading.Thread(target=self._listen, daemon=True, name="tau-ears").start()
        if (popout or window) and _has("webview"):
            if console:
                threading.Thread(target=self._console, daemon=True).start()
            if popout:
                from tau.detect import monitors
                from tau.display.popout import open_popout

                open_popout(url, self.profile, monitors())
            else:
                from tau.display.popout import open_window

                open_window(url, self.profile)
            self.stop.set()
            return
        if open_browser:
            webbrowser.open(url)
        if console:
            self._console()
        else:
            self.stop.wait()

    def display_port(self) -> int:
        """The saved display port, or the fixed default. Never 0 and never a temporary port."""
        from tau.settings import DEFAULT_DISPLAY_PORT, is_ephemeral

        try:
            port = int(get(self.profile, "display.port") or 0)
        except (TypeError, ValueError):
            port = 0
        return DEFAULT_DISPLAY_PORT if (port < 1024 or is_ephemeral(port)) else port

    def serve_display(self, tries: int = 20) -> Optional[int]:
        """Serve on the fixed port. If TAU already answers there, return None. If something else
        holds it, use the next free fixed port and save it, so the address stays put next time."""
        from tau.settings import EPHEMERAL_LOW

        want = self.display_port()
        for port in range(want, min(want + tries, EPHEMERAL_LOW)):
            try:
                got = self.display.serve(port)
            except OSError:
                if port == want and self._is_tau(port):
                    return None
                continue
            if got != get(self.profile, "display.port"):
                try:
                    from tau.setup import apply_values
                    from tau.profile import load_profile, save_profile

                    data = load_profile(check=False)
                    apply_values(data, {"DISPLAY_PORT": got})
                    save_profile(data)
                    self.profile.setdefault("display", {})["port"] = got
                except Exception:
                    pass
            return got
        raise OSError(f"no free port from {want} to {want + tries - 1} for the display")

    @staticmethod
    def _is_tau(port: int) -> bool:
        from tau.brainclient import get_json

        try:
            return "segments" in get_json(f"http://127.0.0.1:{port}/state", timeout=1.5)
        except Exception:
            return False

    def _watch_focus(self, every: float = 3.0) -> None:
        """Drop the display to 10 fps while a full-screen or heavy app has focus (no model calls)."""
        from tau.lanes import _norm_proc
        from tau.profile import as_list
        from tau.senses.window import focused_window

        heavy = {_norm_proc(a) for a in as_list(get(self.profile, "brain.gpu.heavy_apps"))}
        last = None
        while not self.stop.wait(every):
            try:
                w = focused_window()
            except Exception:
                continue
            busy = bool(w.get("fullscreen")) or _norm_proc(w.get("process") or "") in heavy
            if busy != last:
                self.display.publish({"throttle": busy})
                last = busy

    def _needs_cloud_notice(self) -> bool:
        pc = self.profile.get("pacing") or {}
        return bool(pc.get("enabled") and get(self.profile, "brain.burst.url") and not pc.get("cloud_choice"))

    def _console(self) -> None:
        print("Type to talk (Ctrl+C to stop).")
        try:
            while not self.stop.is_set():
                text = (sys.stdout.write("> "), sys.stdout.flush(), input())[2].strip()
                if text:
                    print(self._converse(text).say)
        except (EOFError, KeyboardInterrupt):
            self.stop.set()
