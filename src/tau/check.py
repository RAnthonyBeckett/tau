"""``tau check``: a guided hardware check, one device at a time.

Each item ends in plain words: passed, failed (with a fix), or skipped (with
why). Results go to ``tau_brain/journal.md``; each failed item also gets a pin
card so the fix waits for the user. Also reachable by voice: "Tau, check yourself".

Devices are reached through a small ``Devices`` object so tests can use fakes
and the real one uses sounddevice.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

PASS, FAIL, SKIP = "passed", "failed", "skipped"
RATE = 16000
SENTENCE = "The quick brown fox jumps over the lazy dog"
READ_ACTIONS = {"window": "focused", "processes": "list", "heartbeat": "check", "cmdfail": "last_error",
                "drives": "list", "notifications": "recent", "services": "list", "appbridge": None}


@dataclass
class Result:
    name: str
    status: str
    detail: str = ""
    fix: str = ""

    def line(self) -> str:
        s = f"{self.name}: {self.status}"
        if self.detail:
            s += f" ({self.detail})"
        if self.status == FAIL and self.fix:
            s += f". Fix: {self.fix}"
        return s


class Devices:
    """The real microphone and speaker."""

    def __init__(self, mic=None, speaker=None):
        self.mic, self.speaker = mic, speaker

    def record(self, seconds: float) -> np.ndarray:
        from tau import audio

        rec = audio.record(seconds, RATE, self.mic)
        return (np.clip(rec, -1, 1) * 32767).astype("int16")

    def play(self, samples: np.ndarray, rate: int) -> None:
        from tau import audio

        audio.play(samples, rate, self.speaker)


def _rms(a: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float32) / 32768.0
    return float(np.sqrt(np.mean(a ** 2))) if a.size else 0.0


def _words(t: str) -> List[str]:
    import re
    return re.findall(r"[a-z']+", t.lower())


@dataclass
class HardwareCheck:
    profile: Dict[str, Any]
    asker: Any                                   # tau.setup.Asker (say / yes)
    devices: Optional[Devices] = None
    transcribe: Optional[Callable[[np.ndarray], str]] = None
    organs: Any = None
    lab: Any = None
    brain: Any = None
    pins: Any = None
    by_voice: bool = False                       # started by "Tau, check yourself"
    results: List[Result] = field(default_factory=list)

    # -- runner ---------------------------------------------------------------------------
    def run(self) -> List[Result]:
        steps = [self.microphone, self.speaker, self.click_to_talk, self.wake_word, self.transcription,
                 self.gpu, self.senses, self.nodes]
        self.asker.say("I'll check one thing at a time. Each one ends in passed, failed or skipped.")
        for step in steps:
            out = step()
            for r in (out if isinstance(out, list) else [out]):
                self.results.append(r)
                self.asker.say(r.line())
        self.record()
        return self.results

    def summary(self) -> str:
        n = {s: sum(r.status == s for r in self.results) for s in (PASS, FAIL, SKIP)}
        s = f"Hardware check: {n[PASS]} passed, {n[FAIL]} failed, {n[SKIP]} skipped."
        failed = [r.name for r in self.results if r.status == FAIL]
        return s + (f" Failed: {', '.join(failed)}." if failed else "")

    def record(self) -> None:
        if self.brain is not None:
            self.brain.journal(done=self.summary() + " " + "; ".join(r.line() for r in self.results),
                               next_="Fix the failed items (each has a pin card)." if any(
                                   r.status == FAIL for r in self.results) else "")
        if self.pins is not None:
            for r in self.results:
                if r.status == FAIL:
                    self.pins.pin(f"Check failed: {r.name}", f"{r.line()}", kind="check")

    # -- devices --------------------------------------------------------------------------
    def _need_audio(self, name: str) -> Optional[Result]:
        if self.devices is None:
            return Result(name, SKIP, "no audio packages", "")
        return None

    def microphone(self) -> Result:
        name = "Microphone level"
        r = self._need_audio(name)
        if r:
            r.detail = "the voice extra isn't installed (pip install tau[voice])"
            return r
        self.asker.say("Say a few words now, at your normal volume.")
        try:
            level = _rms(self.devices.record(2.0))
        except Exception as exc:
            return Result(name, FAIL, f"couldn't open the microphone ({type(exc).__name__})",
                          "check the microphone is plugged in and allowed in your system's privacy settings, "
                          "then run `tau setup devices`")
        if level < 0.003:
            return Result(name, FAIL, "I heard almost nothing",
                          "raise the input volume, or pick the right microphone with `tau setup devices`")
        return Result(name, PASS, f"level {level:.3f}")

    def speaker(self) -> Result:
        name = "Speaker"
        r = self._need_audio(name)
        if r:
            r.detail = "the voice extra isn't installed"
            return r
        from tau.ears.pipeline import chime

        try:
            self.devices.play(chime(), 24000)
        except Exception as exc:
            return Result(name, FAIL, f"couldn't play sound ({type(exc).__name__})",
                          "check the speaker is on and is the default output, or set it with `tau setup devices`")
        if self.by_voice:
            return Result(name, PASS, "the chime played; tell me if you didn't hear it")
        if self.asker.yes("Did you hear a short chime?", True):
            return Result(name, PASS, "you heard the chime")
        return Result(name, FAIL, "the chime played but you didn't hear it",
                      "turn the volume up, or choose the right speaker with `tau setup devices`")

    def _listen_once(self, seconds: float = 6.0) -> Optional[str]:
        from tau.ears.pipeline import FRAME, Ears, SileroVAD, frames_from_array

        texts: List[str] = []
        ears = Ears(transcribe=self.transcribe, on_text=texts.append, vad=SileroVAD())
        audio = self.devices.record(seconds)
        ears.open_gate()
        for f in frames_from_array(audio):
            if ears.feed(f) is not None:
                break
        if ears.gate_open:                 # still talking at the end of the window: close it
            ears._close()
        return texts[0] if texts else None

    def click_to_talk(self) -> Result:
        name = "Click-to-talk"
        if self.by_voice:
            return Result(name, PASS, "you just talked to me")
        r = self._need_audio(name)
        if r or self.transcribe is None:
            return Result(name, SKIP, "needs the voice extra and a Whisper model")
        self.asker.say("This is the same as clicking the core. Say anything short now.")
        try:
            text = self._listen_once()
        except Exception as exc:
            return Result(name, FAIL, f"didn't work ({type(exc).__name__})", "run `tau doctor` to see which voice part is missing")
        if not text:
            return Result(name, FAIL, "the gate opened but no words came back",
                          "speak a little louder, or check the microphone item above")
        return Result(name, PASS, f'heard "{text[:40]}"')

    def wake_word(self) -> Result:
        name = "Wake word"
        wm = (self.profile.get("tau") or {}).get("wake_model")
        if not wm or not Path(str(wm)).exists():
            return Result(name, SKIP, "no wake model set; train one with scripts/train_wake_word.py")
        if self.by_voice:
            return Result(name, PASS, "you just woke me")
        r = self._need_audio(name)
        if r:
            return r
        from tau.ears.pipeline import WakeWord, frames_from_array

        word = (self.profile.get("tau") or {}).get("wake_word") or "tau"
        self.asker.say(f'Say "{word}" now.')
        try:
            ww = WakeWord(str(wm))
            heard = any(ww.heard(f) for f in frames_from_array(self.devices.record(4.0)))
        except Exception as exc:
            return Result(name, FAIL, f"didn't work ({type(exc).__name__})", "retrain the model or set tau.wake_model again")
        if heard:
            return Result(name, PASS, f'heard "{word}"')
        return Result(name, FAIL, f'didn\'t hear "{word}"',
                      "move closer, or retrain the wake model with your own recordings (--positives-dir)")

    def transcription(self) -> Result:
        name = "Transcription"
        if self.by_voice:
            return Result(name, PASS, "I understood your request")
        r = self._need_audio(name)
        if r or self.transcribe is None:
            return Result(name, SKIP, "needs the voice extra and a Whisper model")
        self.asker.say(f'Please read this sentence aloud: "{SENTENCE}".')
        try:
            text = self._listen_once(7.0) or ""
        except Exception as exc:
            return Result(name, FAIL, f"didn't work ({type(exc).__name__})", "run `tau doctor` to see which voice part is missing")
        want = set(_words(SENTENCE))
        got = set(_words(text))
        score = len(want & got) / len(want)
        if score >= 0.6:
            return Result(name, PASS, f"{round(score * 100)}% of the words matched")
        return Result(name, FAIL, f'heard "{text[:50]}"',
                      "speak clearly near the microphone; if it keeps failing, try a larger Whisper model")

    # -- GPU, senses, nodes ------------------------------------------------------------------
    def gpu(self) -> Result:
        name = "GPU read"
        if self.organs is None or not self.organs.can_read("gpu"):
            return Result(name, SKIP, "the gpu organ isn't granted (tau grant gpu sense)")
        from tau.senses.gpu import read_gpus

        g = read_gpus()
        if not g:
            return Result(name, FAIL, "nvidia-smi gave nothing",
                          "install the NVIDIA driver so nvidia-smi works, or revoke the gpu organ")
        return Result(name, PASS, f"{g[0]['name']}, about {round(g[0]['free_mb'] / 1024)} GB free")

    def senses(self) -> List[Result]:
        out = []
        if self.organs is None:
            return [Result("Senses", SKIP, "TAU isn't loaded")]
        for organ, action in READ_ACTIONS.items():
            if action is None or not self.organs.enabled(organ):
                continue
            o = self.organs.call(organ, action, via="check")
            if o.status == "allowed":
                out.append(Result(f"Sense: {organ}", PASS, o.message[:60]))
            else:
                out.append(Result(f"Sense: {organ}", FAIL, o.message[:80],
                                  "run `tau doctor` for what this sense needs on this system"))
        return out or [Result("Senses", SKIP, "no senses granted")]

    def nodes(self) -> List[Result]:
        from tau import paths
        from tau.hub.crypto import load_json

        paired = load_json(paths.state_dir() / "nodes.json") or {}
        if not paired:
            return [Result("Nodes", SKIP, "no nodes paired")]
        out = []
        for name in sorted(paired):
            if self.lab is None:
                out.append(Result(f"Node: {name}", SKIP, "the hub isn't running here; run this from `tau run` "
                                                         "or say \"check yourself\""))
                continue
            try:
                t0 = time.perf_counter()
                self.lab.remote_call(name, "drives", "list", {}, timeout=10)
                out.append(Result(f"Node: {name}", PASS, f"answered in {round((time.perf_counter() - t0) * 1000)} ms"))
            except Exception as exc:
                out.append(Result(f"Node: {name}", FAIL, f"didn't answer ({type(exc).__name__})",
                                  f"on {name}, run `tau node` and check it can reach the hub's address"))
        return out
