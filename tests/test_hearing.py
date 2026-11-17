"""Bug 12: TAU doesn't hear. Listening must be obvious and reliable: a visible
talk control and a hotkey reach the mic gate, a mic failure flashes the
display and pins the fix, and hands-free mode is off by default and, when on,
opens the gate on voice activity alone."""
import numpy as np

from conftest import make_profile
from tau.ears.pipeline import FRAME, HANDS_FREE_FRAMES, Ears
from tau.setup import ScriptedAsker, Setup


class FakeVAD:
    def __init__(self):
        self.speech = False

    def is_speech(self, frame):
        return self.speech


def _ears(**kw):
    heard = []
    vad = FakeVAD()
    e = Ears(transcribe=lambda audio: "what's my VRAM", on_text=heard.append, vad=vad, **kw)
    return e, vad, heard


def frame():
    return np.zeros(FRAME, dtype=np.int16)


def test_hands_free_is_off_by_default_and_speech_alone_does_nothing():
    e, vad, heard = _ears()
    assert e.hands_free is False
    vad.speech = True
    for _ in range(20):
        e.feed(frame())
    assert not e.gate_open and heard == []


def test_hands_free_opens_on_voice_activity_and_closes_on_silence():
    states = []
    e, vad, heard = _ears(hands_free=True, on_state=states.append)
    vad.speech = True
    for _ in range(HANDS_FREE_FRAMES):
        e.feed(frame())
    assert e.gate_open and states[-1] == "listening"
    vad.speech = False
    for _ in range(12):               # 960 ms of silence
        e.feed(frame())
    assert not e.gate_open and heard == ["what's my VRAM"]


def test_hands_free_does_not_trigger_on_tau_s_own_voice():
    e, vad, heard = _ears(hands_free=True, is_speaking=lambda: True)
    vad.speech = True
    for _ in range(10):
        e.feed(frame())
    assert not e.gate_open


def test_second_click_stops_listening_and_uses_what_was_heard():
    e, vad, heard = _ears()
    e.open_gate()
    vad.speech = True
    e.feed(frame())
    assert e.close_gate() == "what's my VRAM" and not e.gate_open


class FakeGateEars:
    def __init__(self):
        self.wake, self.hands_free, self.gate_open, self.opened = None, False, False, 0

    def open_gate(self):
        self.gate_open, self.opened = True, self.opened + 1

    def close_gate(self):
        self.gate_open = False


def _runtime(tau_home):
    from tau.runtime import Runtime

    rt = Runtime(make_profile(tau_home)).build(want_voice=False)
    rt.ears = FakeGateEars()
    return rt


def test_talk_control_reaches_the_gate_and_toggles(tau_home):
    rt = _runtime(tau_home)
    assert rt.mic_info() == {"ready": True, "mode": "click", "error": None}
    assert rt.on_action({"core": True})["did"] == "listening" and rt.ears.gate_open
    assert rt.on_action({"core": True})["did"] == "stopped listening" and not rt.ears.gate_open


def test_mic_failure_flashes_the_display_and_pins_the_fix(tau_home):
    rt = _runtime(tau_home)
    rt.mic_failed(OSError("Error opening InputStream: Invalid device"))
    snap = rt.display.snapshot()
    assert snap["flash"]["kind"] == "warning" and "microphone" in snap["flash"]["text"]
    assert snap["talk"]["available"] is False
    mic = next(s for s in snap["segments"] if s["id"] == "mic")
    assert mic["status"] == "attention"
    pins = rt.assistant.pins.waiting()
    assert pins and pins[-1]["title"] == "Microphone isn't working" and "How to fix it" in pins[-1]["text"]
    # and clicking Talk now says so on the display instead of doing nothing
    assert rt.on_action({"core": True})["ok"] is False
    assert "type below" in rt.display.snapshot()["flash"]["text"]


def test_hands_free_switch_from_the_display(tau_home):
    rt = _runtime(tau_home)
    rt.set_hands_free(True)
    assert rt.ears.hands_free and rt.display.snapshot()["talk"]["hands_free"]
    rt.set_hands_free(False)
    assert not rt.ears.hands_free


def test_setup_explains_hands_free_in_one_sentence_and_defaults_to_off(tau_home):
    asker = ScriptedAsker()
    s = Setup(asker, env={})
    s.topic_voice()
    assert s.values["LISTEN_WITHOUT_WAKE_WORD"] is False
    privacy = Setup.HANDS_FREE_PRIVACY
    assert privacy in asker.said and privacy.count(". ") == 0 and privacy.endswith(".")
