"""First-run setup: connect a brain first, then ask only what can't be detected.

One plain question at a time, each with a recommended answer. Every question
can be skipped and answered later with ``tau setup <topic>``. A brain is only
saved after one real test call works.
"""

from __future__ import annotations

import os
import sys
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

import yaml

from tau import detect, engines, paths
from tau.brainclient import BrainClient, BrainError
from tau.settings import DEFAULT_DISPLAY_PORT, DEFAULT_HUB_PORT
from tau.profile import (PLACEHOLDER, check_profile, fill_template, find_placeholders,
                         load_profile, save_profile, template_text)

TOPICS = ("brain", "names", "hub", "lab", "senses", "devices", "display", "pacing", "voice", "handoff", "ports")

SKIP = object()


class Asker:
    """How setup talks to the user. The console version is below; tests script it."""

    def say(self, text: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def ask(self, question: str, recommended: Any = None,
            choices: Optional[Sequence[str]] = None, why: Optional[str] = None) -> Any:  # pragma: no cover
        """One question. With ``choices``, ``recommended`` is the choice Enter picks, and
        ``why`` (a few words) is the only thing that earns it a "recommended" tag."""
        raise NotImplementedError

    def yes(self, question: str, recommended: bool = True) -> bool:  # pragma: no cover
        raise NotImplementedError

    def secret(self, question: str) -> Optional[str]:  # pragma: no cover
        raise NotImplementedError


def _read(prompt: str) -> Optional[str]:
    """input() that treats end of input (a script, a pipe, Ctrl+D) as "skip"."""
    try:
        # Prompt on stdout: input() writes it to stderr on a console, and TAU sends stderr to its log.
        sys.stdout.write(prompt)
        sys.stdout.flush()
        return input()
    except EOFError:
        print()
        return None


class ConsoleAsker(Asker):
    def say(self, text: str) -> None:
        print(text, flush=True)

    def ask(self, question, recommended=None, choices=None, why=None):
        if choices:
            print(question)
            default = (list(choices).index(recommended) + 1) if recommended in choices else None
            for i, c in enumerate(choices, 1):
                tag = f"   <- recommended: {why}" if (i == default and why) else ""
                print(f"  {i}. {c}{tag}")
            hint = f"Type a number (Enter picks {default}), or s to skip: " if default else \
                f"Type a number from 1 to {len(choices)}, or s to skip: "
            for _ in range(3):
                raw = _read(hint)
                if raw is None:
                    return SKIP
                raw = raw.strip().lower()
                if raw == "s":
                    return SKIP
                if not raw and default:
                    return choices[default - 1]
                if raw.isdigit() and 1 <= int(raw) <= len(choices):
                    return choices[int(raw) - 1]
                hint = f"That isn't one of the numbers. Type 1 to {len(choices)}, or s to skip: "
            return SKIP
        hint = f" [{recommended}]" if recommended not in (None, "") else ""
        raw = _read(f"{question}{hint} (Enter accepts, s skips): ")
        if raw is None:
            return SKIP
        raw = raw.strip()
        if raw.lower() == "s":
            return SKIP
        return raw or recommended

    def yes(self, question, recommended=True):
        hint = "Y/n" if recommended else "y/N"
        raw = _read(f"{question} [{hint}] ")
        if raw is None:
            return recommended          # end of input: same as pressing Enter
        raw = raw.strip().lower()
        if not raw:
            return recommended
        return raw.startswith("y")

    def secret(self, question):
        import getpass

        try:
            v = getpass.getpass(f"{question} (input hidden, Enter skips): ").strip()
        except EOFError:
            return None
        return v or None


@dataclass
class ScriptedAsker(Asker):
    """Answers from a list, in order. Used by tests and unattended installs."""

    answers: List[Any] = field(default_factory=list)
    said: List[str] = field(default_factory=list)
    asked: List[str] = field(default_factory=list)
    menus: List[Dict[str, Any]] = field(default_factory=list)

    def _next(self, q: str, default: Any) -> Any:
        self.asked.append(q)
        if not self.answers:
            return default
        a = self.answers.pop(0)
        return default if a is None else a

    def say(self, text):
        self.said.append(text)

    def ask(self, question, recommended=None, choices=None, why=None):
        if choices:
            self.menus.append({"question": question, "choices": list(choices),
                               "recommended": recommended, "why": why})
            # Out of answers: Enter on a menu picks the recommended choice, or skips if there's none.
            a = self._next(question, recommended if recommended in choices else "skip")
        else:
            a = self._next(question, recommended)
        if a == "skip":
            return SKIP
        if choices and isinstance(a, int):
            return choices[a]
        return a

    def yes(self, question, recommended=True):
        a = self._next(question, recommended)
        return bool(a) if not isinstance(a, str) else a.lower().startswith("y")

    def secret(self, question):
        a = self._next(question, None)
        return None if a in (None, "skip") else a


# ---------------------------------------------------------------------------------
def placeholder_paths() -> Dict[str, str]:
    """Map each placeholder name to its dotted path in the profile."""
    data = yaml.safe_load(template_text())
    out: Dict[str, str] = {}
    for hit in find_placeholders(data):
        path, value = hit.split(": ", 1)
        for m in PLACEHOLDER.finditer(value):
            out.setdefault(m.group(1), path)
    return out


def _set_dotted(data: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = data
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def apply_values(data: Dict[str, Any], values: Mapping[str, Any]) -> Dict[str, Any]:
    """Write setup answers (keyed by placeholder name) into a loaded profile, in place.

    ``tau setup <topic>`` and the display's Settings panel both write through here.
    """
    where = placeholder_paths()
    for k, v in values.items():
        if k in where:
            _set_dotted(data, where[k], v)
    return data


def brain_values(c: BrainClient, key_env: Optional[str] = None) -> Dict[str, Any]:
    """The profile answers for a brain that passed its test call."""
    if c.cloud:
        return {"BURST_LLM_URL": c.url, "BURST_MODEL": c.model, "BURST_API_KEY_ENV": key_env,
                "BRAIN_PREFER": "burst"}
    return {"LOCAL_ENGINE": c.engine, "LOCAL_LLM_URL": c.url, "LOCAL_MODEL": c.model, "BRAIN_PREFER": "local",
            "MODEL_CHOICES": [{"name": c.model, "lane": "local", "model": c.model}]}


@dataclass
class Setup:
    asker: Asker
    env: Optional[Dict[str, str]] = None
    extra_urls: List[str] = field(default_factory=list)
    profile_file: Optional[Path] = None
    discover: Optional[Callable[..., Any]] = None   # injectable for tests
    values: Dict[str, Any] = field(default_factory=dict)
    brain_client: Optional[BrainClient] = None
    find_brains: Optional[Callable[..., Any]] = None      # injectable for tests
    install_ollama: Optional[Callable[..., bool]] = None
    pull_model: Optional[Callable[..., bool]] = None

    @property
    def home(self) -> Path:
        return (self.profile_file.parent if self.profile_file else paths.tau_home())

    # -- top level ------------------------------------------------------------------
    def run(self, topic: Optional[str] = None) -> Path:
        pf = self.profile_file or paths.profile_path()
        if topic:
            return self._run_topic(topic, pf)
        a = self.asker
        a.say("Hello. I'll set myself up with as few questions as I can. You can skip any of them "
              "and answer later with `tau setup <topic>`.")
        look = a.yes("First, may I look around this machine? It's read-only: hardware, devices, "
                     "services, open ports and installed tools.", True)
        if look:
            self.values.update(detect.machine_basics())
        self.topic_brain()
        self.topic_names()
        self.topic_hub()
        self.topic_devices()
        self.topic_display()
        self.topic_voice()
        self.topic_handoff()
        self._defaults()
        from tau.brain import Brain

        Brain(Path(self.values["BRAIN_DIR"])).ensure(
            {"user": {"name": self.values.get("USER_NAME"),
                      "speak_to_me_as": self.values.get("USER_FORM_OF_ADDRESS")}})
        if look:
            self.topic_lab(asked=True)
        self.topic_senses(may_look=look)
        self.topic_pacing()
        self.topic_ports()
        text = fill_template(self.values, skip_missing=True)
        data = yaml.safe_load(text)
        self._apply_pacing(data)
        self._apply_organs(data)
        problems = check_profile(data)
        if problems:
            raise RuntimeError("setup left problems: " + "; ".join(problems))
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(text, encoding="utf-8")
        if self._pacing or self._organs:
            save_profile(data, pf)
        self._make_brain(data)
        a.say(f"All set. Your profile is at {pf}. Run `tau run` to start, or `tau doctor` to check.")
        return pf

    _topic_only = False

    def _run_topic(self, topic: str, pf: Path) -> Path:
        self._topic_only = True
        if topic not in TOPICS:
            raise ValueError(f"unknown topic; pick one of {', '.join(TOPICS)}")
        data = load_profile(pf, check=False)
        self.values = {}
        if topic == "ports":
            from tau.profile import get as _get

            self.values.update({"DISPLAY_PORT": _get(data, "display.port"), "HUB_PORT": _get(data, "hub.port"),
                                "ROLE": _get(data, "tau.role") or "hub"})
        getattr(self, f"topic_{topic}")()
        apply_values(data, self.values)
        if topic == "pacing":
            self._apply_pacing(data)
        if topic == "senses":
            self._apply_organs(data)
        if topic == "brain" and self.values.get("BURST_LLM_URL") and (data.get("pacing") or {}).get("enabled"):
            # a cloud lane was added while the pacing profile is on: show the privacy notice again
            from tau.pacing import choose_cloud, record_cloud_choice

            choice = choose_cloud(self.asker)
            data["pacing"]["cloud_choice"] = choice
            if choice == "off":
                data["pacing"]["enabled"] = False
            record_cloud_choice(Path(data["tau"]["brain_dir"]), choice)
        save_profile(data, pf)
        if topic == "lab":
            self._make_brain(data)
        return pf

    # -- brain ---------------------------------------------------------------------
    MAX_BRAIN_ROUNDS = 8

    def topic_brain(self) -> None:
        """Always ends with a brain that answered a real test call, or a clear skip.

        Every round looks for model servers again, so a server started while
        setup waits is found after "check again". Nothing here tells the user to
        go and run another command.
        """
        a = self.asker
        a.say("I need a brain to think with: a model server on this computer or your network, "
              "or a cloud service. Let me look for one. This takes a few seconds.")
        failed: set = set()
        declined_install = False
        agents_said = False
        for _round in range(self.MAX_BRAIN_ROUNDS):
            found = (self.find_brains or detect.find_brains)(self.extra_urls, self.env)
            agents = [f for f in found if f.kind == "agent_cli"]
            if agents and not agents_said:
                agents_said = True
                a.say("I also found these coding tools I can hand big jobs to: "
                      + ", ".join(x.engine for x in agents) + ".")
            options: List[tuple] = []
            servers = [f for f in found if f.kind == "local" and f.models and f.url not in failed]
            for f in servers:
                n = len(f.models)
                options.append((f"Use the model server I found: {f.label} ({n} model{'s' if n != 1 else ''})", f))
            for f in found:
                if f.kind == "cloud" and f.engine not in failed:
                    options.append((f"Use {f.engine}, with the key already in {f.key_env}", f))
            starter = engines.pick_starter(self.values.get("GPU_VRAM_GB"), self.values.get("RAM_GB"))
            install_label = (f"Install Ollama and a small model for me ({starter.tag}; about "
                             f"{(engines.OLLAMA_SETUP_MB / 1000 if os.name == 'nt' else 0) + starter.size_gb:.1f} GB "
                             "to download)")
            if not servers and not engines.ollama_running():
                options.append((install_label, "install"))
            options.append(("Type the address of a model server (llama.cpp, LM Studio, vLLM, Ollama or "
                            "any OpenAI-compatible one)", "url"))
            options.append(("Paste a cloud service's address and key", "paste"))
            options.append(("Check again (pick this after you start a model server)", "again"))
            options.append(("Skip for now (you can do this later with: tau setup brain)", "skip"))
            labels = [o[0] for o in options]
            if servers:
                rec, why = labels[0], "it's already running here"
            elif not declined_install and any(o[1] == "install" for o in options):
                rec, why = install_label, "nothing else is set up on this computer yet"
            else:
                rec, why = None, None
            n = len(servers)
            a.say(f"I found {n} model server{'s' if n != 1 else ''}." if n else
                  "I didn't find a model server running on this computer.")
            pick = a.ask("Which brain should I use?", rec, labels, why=why)
            if pick is SKIP or pick is None:
                a.say("Skipped. I can still do the things that need no brain, like checking services.")
                return
            choice = dict(options).get(pick, "skip")
            if choice == "skip":
                a.say("Skipped. I can still do the things that need no brain, like checking services.")
                return
            if choice == "again":
                a.say("Looking again.")
                continue
            if choice == "install":
                client = self._install_starter(starter)
                if client is None:
                    declined_install = True
                    continue
            else:
                client = self._client_for(choice)
                if client is None:
                    continue
            a.say("Sending one short test message to that brain. This can take up to a minute "
                  "while it loads.")
            try:
                client.test_call()
            except BrainError as exc:
                a.say(f"That brain didn't answer ({_short(exc)}), so I won't save it. "
                      "Pick another, or start it and choose 'check again'.")
                failed.add(getattr(choice, "url", None) or getattr(choice, "engine", None) or client.url)
                continue
            self._save_brain(client)
            a.say("That brain answered. Saved.")
            return
        a.say("Let's leave the brain for now. When you're ready: tau setup brain")

    def _install_starter(self, starter: "engines.Starter") -> Optional[BrainClient]:
        a = self.asker
        size = (engines.OLLAMA_SETUP_MB if os.name == "nt" else 0) + starter.size_gb * 1000
        a.say(f"Ollama is a free model server (MIT licence). With {starter.tag} ({starter.why}) that's "
              f"about {size / 1000:.1f} GB to download, roughly {engines.minutes_for(size)} minutes "
              "on a typical home connection.")
        if not a.yes("Shall I install them now?", False):
            return None
        install = self.install_ollama or engines.install_ollama
        if not install(a.say):
            return None
        url = engines.ollama_url()
        a.say(f"Downloading {starter.tag} (about {starter.size_gb:g} GB).")
        if not (self.pull_model or engines.pull_model)(starter.tag, a.say, url):
            return None
        return BrainClient("ollama", url, starter.tag)

    def _client_for(self, choice: Any) -> Optional[BrainClient]:
        a = self.asker
        env = self.env if self.env is not None else os.environ
        if choice == "url":
            url = a.ask("The server's address (for example http://this-computer:port)?", None)
            if url in (SKIP, None, ""):
                return None
            url = str(url).strip()
            if "://" not in url:
                url = "http://" + url
            for engine in ("openai_compat", "ollama"):
                hit = detect.probe_model_server(engine, url, timeout=3)
                if hit and hit.models:
                    return self._client_for(hit)
            a.say(f"Nothing answered at {url}. Check the address and that the server is running.")
            return None
        if choice == "paste":
            url = a.ask("The service's address (OpenAI-compatible, ending in /v1)?", None)
            if url in (SKIP, None):
                return None
            var = a.ask("A name for the key's environment variable?", "TAU_BURST_API_KEY")
            key = a.secret("Paste the key")
            self.values["_PASTED_KEY_ENV"] = var
            if key:
                self._store_secret(var, key)
            model = a.ask("Which model id?", None)
            if model in (SKIP, None):
                return None
            return BrainClient("openai_compat", url, model, api_key=key or env.get(var), cloud=True)
        f = choice
        if f.kind == "local":
            try:
                detect.model_details(f)
            except Exception:
                pass
            ranked, why = engines.rank_models(f.models, self.values.get("GPU_VRAM_GB"),
                                              self.values.get("RAM_GB"), f.sizes, getattr(f, "meta", None))
            names = [r.name for r in ranked]
            model = names[0]
            if len(names) > 1:
                labels = [_model_label(r) for r in ranked]
                a.say(f"That server has {len(names)} models." + (
                      " I've put the ones that fit this computer first." if why else ""))
                m = a.ask("Which model should I use?", labels[0] if why else None, labels, why=why)
                if m is SKIP or m is None:
                    return None
                model = names[labels.index(m)] if m in labels else names[0]
            return BrainClient(f.engine, f.url, model)
        if f.kind == "cloud":
            model = a.ask(f"Which {f.engine} model id?", None)
            if model in (SKIP, None):
                return None
            return BrainClient("openai_compat", f.url, model, api_key=env.get(f.key_env), cloud=True)
        return None

    def _save_brain(self, c: BrainClient) -> None:
        self.brain_client = c
        self.values.update(brain_values(c, self._key_env_for(c) if c.cloud else None))

    def _key_env_for(self, c: BrainClient) -> Optional[str]:
        env = self.env if self.env is not None else os.environ
        for var, (_p, url) in detect.CLOUD_KEYS.items():
            if url == c.url and env.get(var):
                return var
        return self.values.get("_PASTED_KEY_ENV")

    def _store_secret(self, var: str, key: str) -> None:
        """Pasted keys go to a private .env file, never into the profile."""
        env_file = self.home / ".env"
        env_file.parent.mkdir(parents=True, exist_ok=True)
        lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
        lines = [l for l in lines if not l.startswith(f"{var}=")] + [f"{var}={key}"]
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            os.chmod(env_file, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        self.values["SECRETS_ENV_FILE"] = str(env_file)
        self.values["_PASTED_KEY_ENV"] = var

    # -- names, hub, devices ---------------------------------------------------------------
    def topic_names(self) -> None:
        a = self.asker
        name = a.ask("What should I be called? (display name and wake word)", "TAU")
        name = "TAU" if name in (SKIP, None) else str(name)
        self.values.update({"ASSISTANT_NAME": name, "WAKE_WORD": name.lower(), "VOICE_NAME": "af_heart"})
        user = a.ask("What's your name?", self.values.get("OS_USER"))
        if user not in (SKIP, None):
            self.values["USER_NAME"] = user
        form = a.ask("How would you like me to speak to you? (for example: by first name, plainly)",
                     "by first name, plainly")
        if form not in (SKIP, None):
            self.values["USER_FORM_OF_ADDRESS"] = form

    def topic_hub(self) -> None:
        a = self.asker
        is_hub = a.yes("Is this the machine that should run my brain for the whole lab (the hub)?", True)
        self.values["ROLE"] = "hub" if is_hub else "node"
        if is_hub:
            self.values.update({"HUB_HOST": detect.lan_address(), "HUB_BIND_ADDRESS": detect.lan_address(),
                                "HUB_PORT": detect.fixed_port(DEFAULT_HUB_PORT, detect.lan_address())})
        else:
            host = a.ask("The hub's address on your network?", None)
            port = a.ask("The hub's port (shown by `tau status` on the hub)?", None)
            if host not in (SKIP, None):
                self.values["HUB_HOST"] = host
            if port not in (SKIP, None):
                self.values["HUB_PORT"] = int(port)

    def topic_devices(self) -> None:
        cams = detect.cameras()
        import platform

        if platform.system() == "Windows":
            from tau.senses.notifications import request_access

            self.asker.say("Windows will now ask whether I may read your notifications.")
            got = request_access()
            self.asker.say("Allowed. I can show your notifications on my display." if got == "allowed" else
                           "Not allowed, so I won't read notifications. You can change this later in "
                           "Windows Settings > Privacy & security > Notifications.")
        self.values.update({"MIC_DEVICE": "default",          # "System default"; change it in Settings
                            "SPEAKER_DEVICE": "default",
                            "CAMERA_DEVICE": cams[0] if cams else None})

    def topic_display(self) -> None:
        corner = self.asker.ask("Which screen corner should my pop-out sit in?", "top-right",
                                ["top-right", "top-left", "bottom-right", "bottom-left"])
        self.values.update({"DISPLAY_CORNER": "top-right" if corner is SKIP else corner,
                            "DISPLAY_MONITOR": 0, "DISPLAY_PORT": detect.fixed_port(DEFAULT_DISPLAY_PORT)})
        if self._topic_only:
            self.topic_ports()

    def topic_ports(self) -> None:
        """TAU's own ports: a fixed default the owner can change. Never 0, never a temporary port."""
        a = self.asker
        a.say("I use fixed port numbers, so my addresses stay the same after a restart.")
        self.values["DISPLAY_PORT"] = self._ask_port("Which port should my display use?",
                                                     self.values.get("DISPLAY_PORT") or detect.fixed_port(DEFAULT_DISPLAY_PORT))
        if self.values.get("ROLE", "hub") == "hub" and self.values.get("HUB_PORT"):
            self.values["HUB_PORT"] = self._ask_port("Which port should other machines use to reach this hub?",
                                                     self.values["HUB_PORT"])

    def _ask_port(self, question: str, default: int) -> int:
        from tau.settings import EPHEMERAL_LOW

        for _ in range(3):
            raw = self.asker.ask(question, int(default))
            if raw in (SKIP, None, ""):
                return int(default)
            try:
                port = int(str(raw).strip())
            except ValueError:
                self.asker.say("A port is a number.")
                continue
            if 1024 <= port < EPHEMERAL_LOW:
                return port
            self.asker.say(f"Pick a number from 1024 to {EPHEMERAL_LOW - 1}; others are reserved or temporary.")
        return int(default)

    HANDS_FREE_PRIVACY = ("In hands-free mode I check everything the microphone hears for speech and turn "
                          "any speech into text on this computer, so nearby talk may be picked up; nothing "
                          "is saved or sent anywhere until it becomes a message.")

    def topic_voice(self) -> None:
        a = self.asker
        shipped = Path(__file__).parent / "ears" / "models" / "tau.onnx"
        self.values["WAKE_MODEL_PATH"] = str(shipped) if shipped.exists() else None
        if not shipped.exists():
            a.say("To talk to me, click Talk on my display or press Space, then speak. "
                  "I stop listening when you pause.")
        a.say(self.HANDS_FREE_PRIVACY)
        on = a.yes("Would you like hands-free mode, so you can just start talking? Most people leave it off.",
                   False)
        self.values["LISTEN_WITHOUT_WAKE_WORD"] = bool(on)

    def topic_handoff(self) -> None:
        clis = {c["name"] for c in detect.agent_clis()}
        self.values.update({
            "CLAUDE_CODE_TEMPLATE": "claude -p {prompt}" if "claude" in clis else None,
            "CODEX_TEMPLATE": "codex exec {prompt}" if "codex" in clis else None,
            "OPENRIG_TEMPLATE": "rig send {target} {prompt_file}" if "rig" in clis else None,
            "GENERIC_AGENT_TEMPLATE": None,
            "HANDOFF_DEFAULT": "claude_code" if "claude" in clis else ("codex" if "codex" in clis else None),
        })

    def topic_lab(self, asked: bool = False) -> None:
        a = self.asker
        if not asked and not a.yes("May I map this machine's services, ports and drives? Read-only.", True):
            return
        brain_dir = Path(self.values.get("BRAIN_DIR") or self.home / "tau_brain")
        run = self.discover
        if run is None:
            from tau.discovery import run_discovery as run
        try:
            a.say("Looking at this machine's services, ports and drives. This takes up to half a minute.")
            snap = run(brain_dir / "lab", machine_name=self.values.get("MACHINE_NAME"))
            self._snapshot = snap
        except Exception as exc:
            import logging

            logging.getLogger("tau.setup").exception("discovery failed")
            a.say(f"Looking around stopped early ({type(exc).__name__}). I'll carry on without it; "
                  "the details are in TAU's log.")
            return
        self.values["SLOW_OR_NETWORK_DRIVES"] = [d["mount"] for d in snap.get("drives", [])
                                                 if d.get("never_walk")]
        if a.yes("Shall I also list other devices on your network? I only read the neighbour table "
                 "and local announcements; I don't scan anything.", True):
            from tau.discovery.neighbours import list_neighbours

            n = list_neighbours()
            a.say(f"I can see {len(n)} other device{'s' if len(n) != 1 else ''}.")

    _snapshot: Optional[Dict[str, Any]] = None
    _organs: Optional[Dict[str, str]] = None

    def topic_senses(self, may_look: bool = True) -> None:
        """What TAU watches, read-only, so the display shows real data straight away."""
        from tau.senses.starter import SENSES, starter_services

        a = self.asker
        snap = self._snapshot
        if snap is None and may_look and self.values.get("MACHINE_NAME") is None:
            if a.yes("May I look at what's running on this machine, so I can suggest what to watch? "
                     "It's read-only.", True):
                from tau.discovery import collect

                a.say("Looking. This takes up to half a minute.")
                try:
                    snap = collect()
                except Exception:
                    snap = None
        services = starter_services(snap or {}, self.values.get("MACHINE_NAME") or "")
        has_gpu = bool(self.values.get("GPU_NAME"))
        starter = (["gpu"] if has_gpu else []) + ["drives", "models", "notifications"] + \
            (["services"] if services else [])
        words = {o: w for o, w, _ in SENSES}
        a.say("Next, what I watch. All of it is read-only: I look, I never change anything.")
        if services:
            shown = ", ".join(s["name"] for s in services[:3]) + (" and others" if len(services) > 3 else "")
            a.say(f"I found {len(services)} programs serving on this machine that look worth watching: {shown}.")
        listed = [words[o] if o != "services" else f"those {len(services)} services" for o in starter]
        chosen: List[str] = []
        if a.yes(f"Shall I watch the starter set: {', '.join(listed[:-1])} and {listed[-1]}? "
                 "You can change any of them later.", True):
            chosen = list(starter)
        else:
            for organ, what, shows in SENSES:
                if organ == "services" and not services:
                    continue
                if a.yes(f"Watch {what}? I'd show {shows}.", organ in starter):
                    chosen.append(organ)
        self._organs = {o: ("sense" if o in chosen else "off") for o, _, _ in SENSES}
        if "services" in chosen:
            self.values["SERVICES"] = services
        a.say("I'll watch " + (", ".join(words[o] for o in chosen) if chosen else "nothing for now")
              + ". They'll show on my display's rings.")

    def _apply_organs(self, data: Dict[str, Any]) -> None:
        if self._organs:
            data.setdefault("organs", {}).update(self._organs)
        if self.values.get("SERVICES") and not data.get("services"):
            data["services"] = self.values["SERVICES"]

    def topic_pacing(self) -> None:
        from tau.pacing import offer_pacing

        self._pacing = offer_pacing(self.asker, cloud_lane=bool(self.values.get("BURST_LLM_URL")),
                                    brain_dir=Path(self.values.get("BRAIN_DIR") or self.home / "tau_brain"))

    _pacing: Optional[Dict[str, Any]] = None

    def _apply_pacing(self, data: Dict[str, Any]) -> None:
        if self._pacing:
            data.setdefault("pacing", {}).update(self._pacing)

    def _defaults(self) -> None:
        home = self.home
        d = {
            "BRAIN_DIR": str(home / "tau_brain"), "LOG_DIR": str(home / "log"),
            "MIN_FREE_VRAM_GB": 2, "HEAVY_APPS": [], "ALLOWED_COMMANDS": [],
            "WORKSPACE_ROOTS": [], "ALLOWED_PATHS": [], "SERVICES": [], "APP_PREP": [],
            "APP_BRIDGES": [], "OKF_FOLDERS": [], "FTS_FOLDERS": [], "MCP_SERVERS": [],
            "IDENTITY_FILES": [], "MODEL_CHOICES": [],
        }
        for k, v in d.items():
            self.values.setdefault(k, v)

    def _make_brain(self, data: Dict[str, Any]) -> None:
        from tau.brain import Brain

        b = Brain(Path(data["tau"]["brain_dir"]), f"human:{data.get('user', {}).get('os_user') or 'user'}")
        b.ensure(data)
        for cli in detect.agent_clis():
            b.set_component(cli["name"], path=cli["path"], launch=cli["name"], used_by="agent hand-off")
        lb = data.get("brain", {}).get("local") or {}
        if lb.get("url"):
            b.set_component(f"{lb.get('engine')} model server", path=lb.get("url"), port=None,
                            launch="(already running)", used_by="TAU local lane")
        b.journal(done="First-run setup finished.", next_="Say my name to talk to me.")


def _short(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:80]


ARCH_WORDS = {"MoE": "MoE, mixture of experts", "dense": "dense"}


def _model_label(r: "engines.Ranked") -> str:
    arch = ARCH_WORDS.get(getattr(r, "arch", None) or "", "")
    if r.size_gb is None:
        return f"{r.name} ({arch})" if arch else r.name
    note = "" if r.fits is None else (", fits this computer" if r.fits else ", may be too big for this computer")
    return f"{r.name} (about {r.size_gb:g} GB" + (f", {arch}" if arch else "") + f"{note})"
