"""The assistant loop: text in, a short spoken/shown reply out.

Order of work for each thing the user says:
  1. Overwhelm check (pacing profile).
  2. Reflex router: zero tokens.
  3. Heavy work goes to the agent hand-off.
  4. Otherwise one model call through a brain lane, with a ~60-token state
     header prepended to the user's words.
  5. Post-check (style guard + pacing rules), then output routing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from tau import okf, paths
from tau.actionlog import ActionLog
from tau.brain import Brain
from tau.brainclient import BrainError
from tau.discovery.labmap import LabMap
from tau.lanes import Lanes, state_header
from tau.organs import Organs
from tau.organs.grants import apply_level, describe_change
from tau.output import PinBoard, Routed, is_heavy, route
from tau.pacing import OVERWHELM_PROMPT, PacingRules, overwhelm_match
from tau.postcheck import check_reply, load_banned
from tau.profile import as_list, assistant_name, get, wake_word
from tau.reflex import Match, ReflexResult, ReflexRouter, organ_from_words, run_reflex


@dataclass
class Reply:
    say: str
    kind: str = "speak"          # reflex | speak | clipboard | pin | handoff | needs_yes | error
    lane: Optional[str] = None
    reflex: Optional[str] = None
    pin: Optional[Dict[str, Any]] = None
    notes: List[str] = field(default_factory=list)


class Assistant:
    def __init__(self, profile: Dict[str, Any], lanes: Optional[Lanes] = None,
                 voice=None, display=None, lab=None, router: Optional[ReflexRouter] = None,
                 profile_file: Optional[Path] = None):
        self.profile = profile
        self.profile_file = profile_file or paths.profile_path()
        self.name = assistant_name(profile)
        self.log = ActionLog(paths.log_dir(profile))
        self.brain = Brain(paths.brain_dir(profile), f"human:{get(profile, 'user.os_user', 'user')}")
        self.lab_map = LabMap(self.brain.root / "lab", nicknames=lambda: get(self.profile, "lab.nicknames") or {})
        self.pins = PinBoard(on_change=self._pins_changed)
        self.voice, self.display, self.lab = voice, display, lab
        self.organs = Organs(profile, self.log, {"lab_map": self.lab_map, "voice": voice,
                                                 "display": display, "lab": lab})
        self.lanes = lanes or Lanes(profile, self.organs)
        self.lanes.organs = self.organs
        self.organs.context["lanes"] = self.lanes
        self.router = router or ReflexRouter(names=[self.name, wake_word(profile)])
        self.banned = load_banned(get(profile, "style.banned_words_file"))
        self.muted = False
        self.pending_grant: Optional[Dict[str, Any]] = None
        self.pending_start: Optional[Dict[str, Any]] = None
        self._remembered_server: bool = False
        self.overwhelm_next = False
        self.history: List[Dict[str, str]] = []
        self.workspace: Optional[str] = None
        from tau.work import WorkStore

        self.work = WorkStore(profile)
        self.organs.context["work"] = self.work
        from tau.connect import LookStore

        self.look_store = LookStore(paths.state_dir() / "looks")

    # -- helpers -----------------------------------------------------------------------
    @property
    def pacing(self) -> PacingRules:
        return PacingRules.from_profile(self.profile)

    def _pins_changed(self) -> None:
        if self.display is not None:
            self.display.publish({"pins": len(self.pins.waiting())})

    def _set_state(self, state: str) -> None:
        if self.display is not None:
            self.display.publish({"state": state})

    def _speak(self, text: str) -> None:
        if text and not self.muted and self.voice is not None and self.organs.level("voice") != "off":
            self.organs.call("voice", "speak", via="reply", text=text)

    # -- main entry ----------------------------------------------------------------------
    def handle(self, text: str) -> Reply:
        text = (text or "").strip()
        if not text:
            return Reply("", "speak")
        rules = self.pacing
        if rules.active and overwhelm_match(text, rules.overwhelm_signals):
            self.overwhelm_next = True
        m = self.router.match(text)
        if m is not None:
            res = run_reflex(m, self.organs, self._specials())
            kind = "needs_yes" if res.status == "needs_yes" else "reflex"
            reply = Reply(res.say, kind, reflex=res.name)
            self._speak(reply.say)
            return reply
        w = self._work(text)
        if w is not None:
            self._speak(w.say)
            return w
        # 1.3: heavy requests become a draft card in _work (never a launch); the old
        # fire-and-forget hand-off isn't used from conversation any more.
        return self._think(text)

    # -- work: folders, workers and jobs ----------------------------------------------------
    def _work(self, text: str) -> Optional[Reply]:
        """Open a work flow, make a draft, say how a job is going, or pass a correction on.

        A draft is never sent from here: Send on the task card (or "send it") does that."""
        from tau.work import _EXPLORE, open_intent, work_intent

        kind = open_intent(text)
        if kind == "mywork":
            return Reply(self.work.status_line(), "reflex", reflex="work.status")
        if kind:
            if self.display is not None:
                self.display.publish({"open_work": {"flow": kind, "at": __import__("time").time()}})
            says = {"folder": "Opening Add a folder.", "app": "Opening Add an app.",
                    "setup": "Opening Set up work.", "check": "Checking TAU and connections."}
            return Reply(says[kind], "reflex", reflex=f"work.open.{kind}")
        low = text.lower().strip()
        active = self.work.data["active"].get("job")
        if active and re.match(r"^(send it|yes,? send it|go ahead and send it)\b", low):
            card = self.work.card(active)
            if card["state"] != "Draft" or card.get("ask"):
                return Reply("There's no draft ready to send.", "reflex")
            out = self.organs.call("work", "send", via="you said send it", confirmed=True,
                                   job=active, approval=card.get("approval", ""))
            return Reply(out.message, "handoff")
        if active and re.match(r"^(stop|cancel) (that|the) job\b", low):
            return Reply(self.work.cancel(active)["say"], "reflex")
        if re.match(r"^(continue where we were|what'?s unfinished|what was i doing|where were we)\b", low):
            return Reply(self.work.reminder(), "reflex", reflex="work.reminder")
        if active and re.match(r"^(tell it|use this file too|i meant)\b", low):
            card = self.work.follow_up(text, active)
            self._show_card(card)
            return Reply(card.get("say", ""), "work_card")
        app_req = self._app_request(low)
        if app_req is not None:
            return app_req
        intent = work_intent(text)
        if intent is None and is_heavy(text) and not _EXPLORE.search(text) and not text.strip().endswith("?"):
            intent = {"worker": "", "request": text}
        if intent is None:
            return None
        names = self.work.find_project(text)
        card = self.work.draft(intent["request"], project=names[0]["id"] if len(names) == 1 else None,
                               worker=intent["worker"] or None)
        self._show_card(card)
        if card["ask"] == "project":
            if not self.work.data["projects"]:
                return Reply("Which folder should that work happen in? Use Add a folder and I'll keep your request.",
                             "work_card")
            return Reply("Which project is that for? Pick one on the card.", "work_card")
        if card["ask"] == "worker":
            return Reply("Who should do it? Connect a worker with Add an app; your request is kept.", "work_card")
        return Reply(card["summary"] + " Press Send when you're happy, or change anything on the card.", "work_card")

    def _app_request(self, low: str) -> Optional[Reply]:
        """"ask <app> to <one of its listed actions>": only an action the app described, and
        the usual permission step (a Yes / No card at Suggest) before it runs."""
        m = re.match(r"^(?:ask|tell|get) (.+?) to (.+?)[.!]?$", low)
        if not m:
            return None
        for aid, a in (self.work.data.get("apps") or {}).items():
            if a["name"].lower() != m.group(1).strip():
                continue
            want = m.group(2).strip()
            hit = [x for x in a["actions"] if x["label"].lower() == want or x["id"].lower() == want]
            if not hit:
                return Reply(f"{a['name']} doesn't offer that. It can: "
                             + ", ".join(x["label"] for x in a["actions"]) + ".", "reflex")
            out = self.organs.call("work", "app_do", via="assistant", app=aid, what=hit[0]["id"])
            return Reply(out.message, "needs_yes" if out.status == "needs_yes" else "reflex")
        return None

    def _show_card(self, card: Dict[str, Any]) -> None:
        if self.display is not None:
            self.display.publish({"work_card": card, "work": self.work.summary()})

    # -- model path ------------------------------------------------------------------------
    def system_prompt(self) -> str:
        """Built only from neutral material. Never reads brain/private/."""
        lines = [f"You are {self.name}, a voice assistant that lives in the user's home lab. "
                 "Answer briefly and plainly; your words are spoken aloud. "
                 "Never put code in a spoken reply; say it will go to a coding agent."]
        uname = get(self.profile, "user.name")
        form = get(self.profile, "user.speak_to_me_as")
        if uname:
            lines.append(f"The user's name is {uname}." + (f" Speak to them {form}." if form else ""))
        for f in as_list(get(self.profile, "user.identity_files")):
            p = Path(str(f)).expanduser()
            if p.exists() and "private" not in p.parts:
                lines.append("From the user's own notes about themself:\n" + _clip(p.read_text(encoding="utf-8"), 2000))
        rules = self.brain.rules()
        if rules:
            lines.append("Standing rules from the user:\n" + "\n".join(f"- {r}" for r in rules[-20:]))
        lab = self._lab_lines()
        if lab:
            lines.append(lab)
        top = self.brain.top_journal_entry()
        if top:
            lines.append("Latest journal entry:\n" + _clip(top, 600))
        lines += self.pacing.prompt_lines()
        if self.overwhelm_next:
            lines.append(OVERWHELM_PROMPT)
        return "\n\n".join(lines)

    def _lab_lines(self) -> str:
        """The lab map in a few lines: every machine TAU knows, whether its node is connected now."""
        try:
            machines = self.lab_map.machines()
        except Exception:
            return ""
        if not machines:
            return ""
        live = set()
        try:
            live = set((getattr(self.lab, "nodes", None) or {}).keys()) if self.lab is not None else set()
        except Exception:
            pass
        me = str(get(self.profile, "machine.name") or "")
        known = [c.title for c in machines]
        live = {self.lab_map.resolve(n, known) or n for n in live}
        me = self.lab_map.resolve(me, known) or me
        nicks = self.lab_map._nicks()
        rows = []
        for c in machines[:16]:
            name = c.title
            also = nicks.get(name) or []
            if name.lower() == me.lower():
                status = "this machine (the hub)"
            elif name in live:
                status = "connected now"
            else:
                status = "paired, not connected right now"
            m = c.meta
            gen = (m.get("generated") or {}).get("at", "") if isinstance(m.get("generated"), dict) else ""
            called = f" (also called {', '.join(also)})" if also else ""
            rows.append(f"- {name}{called}: {status}. {m.get('description', '')}" + (f" (map from {gen})" if gen else ""))
        return ("Machines in this lab, from your own lab map (you can see all of these; "
                "answer questions about them from this list; names and nicknames match in any case, "
                "and a short form like the first few letters means the same machine):\n" + "\n".join(rows))

    def remember_model_server(self) -> None:
        try:
            from tau import modelserver

            modelserver.remember(self.profile, path=self.profile_file)
        except Exception:
            pass

    def _think(self, text: str, retried: bool = False) -> Reply:
        rules = self.pacing
        local_only = rules.active and rules.cloud_choice == "local_only"
        hdr = state_header(self.profile, self.organs, {"ws": self.workspace})
        msgs = [{"role": "system", "content": self.system_prompt()}]
        msgs += self.history[-6:]
        msgs.append({"role": "user", "content": f"[state] {hdr}\n{text}"})
        lane_now = getattr(self.lanes.choose(privacy_local_only=local_only), "lane", None) or "local"
        model = get(self.profile, f"brain.{lane_now}.model") or ""
        if self.display is not None:
            self.display.publish({"state": "thinking", "model": model, "wait_text": None})
        else:
            self._set_state("thinking")
        waiting = self._watch_load(lane_now, model)
        try:
            answer, lane = self.lanes.chat(msgs, privacy_local_only=local_only)
        except BrainError as exc:
            waiting.set()
            self._set_state("error")
            import logging

            logging.getLogger("tau.brain").warning("brain call failed: %s", exc)
            return self._brain_down(text, retried)
        waiting.set()
        if lane == "local" and not self._remembered_server:
            self._remembered_server = True
            import threading

            threading.Thread(target=self.remember_model_server, daemon=True, name="tau-remember-server").start()
        if self.display is not None and self.display.live.get("wait_text"):
            self.display.publish({"wait_text": None})
        self.overwhelm_next = False

        def rewrite(sentence: str, hits: List[str]) -> str:
            out, _ = self.lanes.chat([
                {"role": "system", "content": "Rewrite the sentence in plain words. Reply with the sentence only."},
                {"role": "user", "content": f"Avoid these words: {', '.join(hits)}.\n\n{sentence}"}],
                privacy_local_only=local_only, max_tokens=120)
            return out.strip()

        checked = check_reply(answer, self.banned, rules, rewrite if self.banned else None)
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": checked.text}]
        clip_ok = self.organs.level("clipboard") in ("act",)
        routed: Routed = route(checked.text, text, self.pins, clip_ok, extra_pin=checked.overflow)
        if routed.kind == "handoff" and self.organs.enabled("handoff"):
            out = self.organs.call("handoff", "send", via="assistant", request=text, state_header=hdr)
            routed = Routed("handoff", out.message)
        elif routed.kind == "handoff":
            card = self.pins.pin("Needs a coding agent", checked.text, kind="code")
            routed = Routed("pin", "That's a coding job. I've put my notes on a pin card; "
                                   "grant the handoff organ to send work to an agent.", pin=card)
        self._set_state("idle")
        self._speak(routed.say)
        return Reply(routed.say, routed.kind, lane=lane, pin=routed.pin, notes=checked.notes)

    LOAD_CHECK_S = 3.0
    STILL_WAITING_S = 10.0

    def _watch_load(self, lane: str, model: str) -> "threading.Event":
        """While a reply is slow, say why on the status line: a model loading, or just a long think."""
        import threading

        done = threading.Event()
        if self.display is None:
            return done

        def watch() -> None:
            if done.wait(self.LOAD_CHECK_S):
                return
            loaded = self._model_loaded(lane, model)
            if loaded is False:
                self.display.publish({"wait_text": f"Loading {model}, about a minute. Your question waits; "
                                                   "nothing is lost."})
            if done.wait(self.STILL_WAITING_S - self.LOAD_CHECK_S):
                return
            if loaded is not False:
                self.display.publish({"wait_text": f"Still thinking with {model}. If it's loading, this can "
                                                   "take about a minute."})

        threading.Thread(target=watch, daemon=True, name="tau-wait").start()
        return done

    def _model_loaded(self, lane: str, model: str):
        """True, False, or None when the server doesn't say."""
        if lane != "local":
            return None
        try:
            from tau import detect

            url = get(self.profile, "brain.local.url") or ""
            engine = get(self.profile, "brain.local.engine") or "openai_compat"
            f = detect.probe_model_server(engine if engine == "ollama" else "openai_compat", url, timeout=1.5)
            if f is None:
                return None
            detect.model_details(f, timeout=1.5)
            return f.loaded.get(model) if f.loaded else None
        except Exception:
            return None

    def _brain_down(self, text: str, retried: bool = False) -> Reply:
        from tau import modelserver

        lane_in_use = getattr(self.lanes.last_choice, "lane", None) if self.lanes.last_choice else "local"
        if lane_in_use != "local" or modelserver.reachable(self.profile) or retried:
            return Reply("I couldn't reach my brain. Check the model server is running, then ask again. "
                         "The details are in TAU's log.", "error")

        way = modelserver.find_way(self.profile)
        if way is None:
            name = modelserver.seen_but_unknown(self.profile)
            if name:
                say = (f"My model server isn't running. I can see {name} on this machine, "
                       "but I don't know yet how you start it. Start it once the way you usually do. "
                       "I'll remember how, and after that I can start it myself.")
            else:
                say = ("My model server isn't running, and I couldn't find one on this machine "
                       "that I know how to start. Start it, then ask again.")
            return Reply(say, "error")

        level = self.organs.level("services")
        if level == "act":
            if self.display is not None:
                self.display.publish({"wait_text": "Starting my model server. This can take a few minutes."})
            ok = self._start_model_server(way)
            if ok:
                return self._think(text, retried=True)
            return Reply("I tried to start my model server and it didn't answer in time. The details are in TAU's log.", "error")
        elif level == "suggest":
            self.pending_start = {"way": way}
            return Reply(f"My model server isn't running. I know how to start it: {way.label}. Shall I? Say yes or no.", "needs_yes")
        else:
            return Reply("My model server isn't running. I know how to start it, but starting services is switched off for me. Start it, then ask again.", "error")

    def _start_model_server(self, way) -> bool:
        from tau import modelserver, paths

        log_file = paths.log_dir(self.profile) / "model_server.log"
        try:
            ok = modelserver.start(way, self.profile, log_file=log_file)
        finally:
            if self.display is not None and getattr(self.display, "live", {}).get("wait_text"):
                self.display.publish({"wait_text": None})
        if ok:
            self.log.receipt("model server started", who="you", args={"how": way.source}, via="voice")
        return ok

    # -- specials ------------------------------------------------------------------------------
    def _specials(self) -> Dict[str, Callable[[Match], ReflexResult]]:
        return {
            "confirm": self._confirm, "decline": self._decline, "quiet": self._quiet,
            "unquiet": self._unquiet, "hard_day_on": lambda m: self._hard_day(True),
            "hard_day_off": lambda m: self._hard_day(False), "grant": self._grant,
            "correction": self._correction, "rediscover": self._rediscover, "pins": self._pins,
            "selfcheck": self._selfcheck, "connections": self._connections,
            "start_brain": self._start_brain,
        }

    # set by the runtime when audio is available
    check_devices = None
    check_transcribe = None

    def _start_brain(self, m: Match) -> ReflexResult:
        from tau import modelserver

        r_name = m.reflex.name if (m and m.reflex) else "start_model_server"
        if modelserver.reachable(self.profile):
            return ReflexResult(True, "My model server is already running.", r_name, "allowed")

        way = modelserver.find_way(self.profile)
        if way is None:
            name = modelserver.seen_but_unknown(self.profile)
            if name:
                say = (f"My model server isn't running. I can see {name} on this machine, "
                       "but I don't know yet how you start it. Start it once the way you usually do. "
                       "I'll remember how, and after that I can start it myself.")
            else:
                say = ("My model server isn't running, and I couldn't find one on this machine "
                       "that I know how to start. Start it, then ask again.")
            return ReflexResult(True, say, r_name, "error")

        level = self.organs.level("services")
        if level == "act":
            if self.display is not None:
                self.display.publish({"wait_text": "Starting my model server. This can take a few minutes."})
            ok = self._start_model_server(way)
            if ok:
                return ReflexResult(True, "It's running now. Ask me again.", r_name, "allowed")
            return ReflexResult(True, "I tried to start my model server and it didn't answer in time. The details are in TAU's log.", r_name, "error")
        elif level == "suggest":
            self.pending_start = {"way": way}
            return ReflexResult(True, f"My model server isn't running. I know how to start it: {way.label}. Shall I? Say yes or no.", r_name, "needs_yes")
        else:
            return ReflexResult(True, "My model server isn't running. I know how to start it, but starting services is switched off for me. Start it, then ask again.", r_name, "error")

    def _selfcheck(self, m: Match) -> ReflexResult:
        from tau.check import HardwareCheck

        lines: List[str] = []

        class _Quiet:
            def say(self_, text):
                lines.append(text)

            def yes(self_, q, recommended=True):
                return recommended

        hc = HardwareCheck(self.profile, _Quiet(), devices=self.check_devices, transcribe=self.check_transcribe,
                           organs=self.organs, lab=self.lab, brain=self.brain, pins=self.pins, by_voice=True)
        hc.run()
        return ReflexResult(True, hc.summary(), "selfcheck", "allowed", data=[r.__dict__ for r in hc.results])

    def _confirm(self, m: Match) -> ReflexResult:
        if self.pending_start:
            p, self.pending_start = self.pending_start, None
            way = p["way"]
            if self.display is not None:
                self.display.publish({"wait_text": "Starting my model server. This can take a few minutes."})
            ok = self._start_model_server(way)
            if ok:
                return ReflexResult(True, "It's running now. Ask me again.", "confirm", "allowed")
            return ReflexResult(True, "I tried to start it and it didn't answer in time. The details are in TAU's log.", "confirm", "error")
        if self.pending_grant:
            g, self.pending_grant = self.pending_grant, None
            apply_level(g["organ"], g["level"], g.get("machine"), self.profile_file)
            organs = self.profile.setdefault("organs", {})
            organs[g["organ"]] = g["level"]
            self.log.receipt(f"{g['organ']} set to {g['level']}", who="you, by voice", machine=g.get("machine"),
                             args={"organ": g["organ"], "level": g["level"]}, via="voice")
            return ReflexResult(True, f"Done. {g['organ']} is at {g['level']} now.", "confirm", "allowed")
        out = self.organs.confirm(via="voice")
        return ReflexResult(True, out.message, "confirm", out.status)

    def _decline(self, m: Match) -> ReflexResult:
        if self.pending_start:
            self.pending_start = None
            return ReflexResult(True, "Okay, I won't start it.", "decline", "allowed")
        if self.pending_grant:
            self.pending_grant = None
            return ReflexResult(True, "Okay, nothing changed.", "decline", "allowed")
        return ReflexResult(True, self.organs.decline().message, "decline", "allowed")

    def _quiet(self, m: Match) -> ReflexResult:
        if self.voice is not None:
            self.voice.interrupt()
        self.muted = True
        return ReflexResult(True, "", "quiet", "allowed")

    def _unquiet(self, m: Match) -> ReflexResult:
        self.muted = False
        return ReflexResult(True, "I'm back.", "unquiet", "allowed")

    def _hard_day(self, on: bool) -> ReflexResult:
        self.profile.setdefault("pacing", {})["hard_day"] = on
        if on and not self.profile["pacing"].get("enabled"):
            self.profile["pacing"]["enabled"] = True
        return ReflexResult(True, "Hard day mode on. Short and literal." if on else "Hard day mode off.",
                            "hard_day", "allowed")

    def _grant(self, m: Match) -> ReflexResult:
        what = m.groups.get("what", "")
        organ = organ_from_words(what)
        if not organ:
            return ReflexResult(True, f"I'm not sure which organ '{what}' means. Try `tau grant <organ> <level>`.",
                                "grant", "error")
        level = m.reflex.args.get("level", "act")
        self.pending_grant = {"organ": organ, "level": level}
        return ReflexResult(True, describe_change(organ, level, name=self.name), "grant", "needs_yes")

    def _correction(self, m: Match) -> ReflexResult:
        p = self.brain.record_correction(_raw_text(m))
        where = "your notes" if p.name == "user.md" else "my rules"
        return ReflexResult(True, f"Got it. I've put that in {where}.", "correction", "allowed")

    def _rediscover(self, m: Match) -> ReflexResult:
        from tau.discovery import run_discovery

        snap = run_discovery(self.lab_map.root, machine_name=get(self.profile, "machine.name"))
        n = len(snap.get("services", []))
        if self.lab is not None:
            for node in list(getattr(self.lab, "nodes", {})):
                try:
                    self.lab_map.write_snapshot(self.lab.remote_call(node, "discover", "run", {}, timeout=120))
                except Exception:
                    pass
        return ReflexResult(True, f"Re-checked. I see {n} services here.", "rediscover", "allowed")

    def _pins(self, m: Match) -> ReflexResult:
        if _raw_text(m).lower().startswith("clear"):
            n = self.pins.clear()
            return ReflexResult(True, f"Cleared {n} pin card{'s' if n != 1 else ''}.", "pins", "allowed")
        w = self.pins.waiting()
        if not w:
            return ReflexResult(True, "No pin cards waiting.", "pins", "allowed")
        return ReflexResult(True, f"{len(w)} waiting. Latest: {w[-1]['title']}", "pins", "allowed")

    def _connections(self, m: Match) -> ReflexResult:
        from tau import connect

        say = connect.spoken_summary(self.look_store)
        return ReflexResult(True, say, "connections", "allowed")


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + "..."


def _raw_text(m: Match) -> str:
    return m.groups.get("_raw", "")
