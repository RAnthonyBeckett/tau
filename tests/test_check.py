"""tau check against fakes: pass, fail and skip paths, journal and pin cards."""
import numpy as np
import pytest

from conftest import MockModelServer, make_profile
from synth_speech import speech
from tau.assistant import Assistant
from tau.check import FAIL, PASS, SENTENCE, SKIP, HardwareCheck
from tau.setup import ScriptedAsker

pytest.importorskip("faster_whisper")


class FakeDevices:
    def __init__(self, audio=None, fail_record=False, fail_play=False):
        self.audio, self.fail_record, self.fail_play, self.played = audio, fail_record, fail_play, 0

    def record(self, seconds):
        if self.fail_record:
            raise OSError("no input device")
        if self.audio is None:
            return np.zeros(int(seconds * 16000), np.int16)
        return self.audio[: int(seconds * 16000)] if len(self.audio) >= seconds * 16000 else \
            np.concatenate([self.audio, np.zeros(int(seconds * 16000) - len(self.audio), np.int16)])

    def play(self, audio, rate):
        if self.fail_play:
            raise OSError("no output device")
        self.played += 1


def talking():
    return np.concatenate([np.zeros(4000, np.int16), speech(1.6), np.zeros(int(16000 * 2.5), np.int16)])


def _asst(home, **kw):
    data = make_profile(home, **kw)
    a = Assistant(data, profile_file=home / "profile.yaml")
    a.brain.ensure(data)
    return a


def by_name(results):
    return {r.name: r for r in results}


def test_pass_path(tau_home, fake_smi, monkeypatch):
    a = _asst(tau_home)
    a.profile["organs"]["gpu"] = "sense"
    a.profile["organs"]["drives"] = "sense"
    wm = tau_home / "wake.onnx"
    wm.write_bytes(b"x")
    a.profile["tau"]["wake_model"] = str(wm)

    class FakeWake:
        def __init__(self, path):
            pass

        def heard(self, frame):
            return np.abs(frame).max() > 1000

    monkeypatch.setattr("tau.ears.pipeline.WakeWord", FakeWake)
    asker = ScriptedAsker(answers=[True])           # "Did you hear a short chime?" -> yes
    hc = HardwareCheck(a.profile, asker, devices=FakeDevices(talking()), transcribe=lambda audio: SENTENCE,
                       organs=a.organs, brain=a.brain, pins=a.pins)
    res = by_name(hc.run())
    for name in ("Microphone level", "Speaker", "Click-to-talk", "Wake word", "Transcription", "GPU read",
                 "Sense: drives", "Sense: services", "Sense: notifications"):
        assert res[name].status == PASS, (name, res[name])
    assert res["Nodes"].status == SKIP
    assert "Hardware check:" in a.brain.top_journal_entry() and "0 failed" in a.brain.top_journal_entry()
    assert a.pins.waiting() == []
    assert any("passed" in s for s in asker.said)


def test_fail_path_gives_fix_hints_journal_and_pins(tau_home, monkeypatch, tmp_path):
    a = _asst(tau_home)
    a.profile["organs"]["gpu"] = "sense"
    monkeypatch.setenv("TAU_NVIDIA_SMI", str(tmp_path / "no-such-smi"))
    (tau_home / "state").mkdir(exist_ok=True)
    (tau_home / "state" / "nodes.json").write_text('{"workshop": {"key": "00"}}')

    class DeadLab:
        def remote_call(self, *a, **k):
            raise ConnectionError("workshop isn't connected right now")

    asker = ScriptedAsker(answers=[])
    hc = HardwareCheck(a.profile, asker, devices=FakeDevices(audio=None), transcribe=lambda audio: "",
                       organs=a.organs, lab=DeadLab(), brain=a.brain, pins=a.pins)
    res = by_name(hc.run())
    assert res["Microphone level"].status == FAIL and "input volume" in res["Microphone level"].fix
    assert res["GPU read"].status == FAIL and "nvidia-smi" in res["GPU read"].fix
    assert res["Node: workshop"].status == FAIL and "tau node" in res["Node: workshop"].fix
    assert res["Wake word"].status == SKIP
    # silence never opens a transcript: both speech items fail with a plain hint
    assert res["Click-to-talk"].status == FAIL and res["Transcription"].status == FAIL
    journal = a.brain.top_journal_entry()
    assert "failed" in journal and "Microphone level" in journal
    titles = [p["title"] for p in a.pins.waiting()]
    assert "Check failed: Microphone level" in titles and "Check failed: Node: workshop" in titles
    # a speaker that can't open, and a microphone that can't open
    hc2 = HardwareCheck(a.profile, ScriptedAsker(), devices=FakeDevices(fail_record=True, fail_play=True),
                        organs=a.organs)
    r2 = by_name(hc2.run())
    assert r2["Speaker"].status == FAIL and r2["Microphone level"].status == FAIL
    assert "privacy settings" in r2["Microphone level"].fix


def test_skip_path_without_audio(tau_home):
    a = _asst(tau_home)
    hc = HardwareCheck(a.profile, ScriptedAsker(), devices=None, organs=a.organs, brain=a.brain, pins=a.pins)
    res = by_name(hc.run())
    for name in ("Microphone level", "Speaker", "Click-to-talk", "Wake word", "Transcription", "GPU read", "Nodes"):
        assert res[name].status == SKIP, name
    assert "voice extra" in res["Microphone level"].detail


def test_by_voice_with_zero_model_calls(tau_home):
    server = MockModelServer()
    try:
        a = _asst(tau_home, LOCAL_ENGINE="openai_compat", LOCAL_LLM_URL=server.url, LOCAL_MODEL="m")
        a.check_devices = FakeDevices(talking())
        r = a.handle("Tau, check yourself")
        assert r.reflex == "selfcheck" and r.say.startswith("Hardware check:")
        assert "Hardware check:" in a.brain.top_journal_entry()
        assert server.chat_bodies() == []
    finally:
        server.close()
