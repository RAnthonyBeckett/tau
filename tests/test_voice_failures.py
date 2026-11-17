"""Round 5: a voice that can't speak says so; picking a voice fetches it and plays a sample."""

import numpy as np

from tau.voice import TTSEngine, Voice, failure_reason


class MissingVoice(TTSEngine):
    rate = 24000

    def stream(self, text, voice):
        raise FileNotFoundError(f"voices/{voice}.pt is not in the local cache (offline mode)")
        yield  # pragma: no cover


def test_speak_failure_is_reported_not_silent():
    got = []
    v = Voice(MissingVoice(), sink=None, voice="bf_emma", on_error=got.append)
    v.say("hello", wait=True)
    assert got and v.last_error is got[0] and not v.speaking
    why = failure_reason(got[0], "bf_emma")
    assert "bf_emma" in why and "Settings" in why


def test_speaker_failure_reason_is_plain():
    class PortAudioError(Exception):
        pass

    why = failure_reason(PortAudioError("Error opening OutputStream: Invalid sample rate [PaErrorCode -9997]"))
    assert why.startswith("the speaker wouldn't play") and "Invalid sample rate" in why


def test_runtime_flashes_and_pins_on_speak_failure_and_fetches_picked_voice(monkeypatch, tmp_path):
    import threading

    from tau import kokoro_voice
    from tau.runtime import Runtime

    rt = Runtime.__new__(Runtime)
    flashes, notes, pins, said = [], [], [], []
    rt.display = type("D", (), {"flash": lambda s, t, kind="warning": flashes.append(t),
                                "note": lambda s, t: notes.append(t)})()
    rt.assistant = type("A", (), {"pins": type("P", (), {"pin": lambda s, *a, **k: pins.append(a)})()})()
    done = threading.Event()
    rt.voice = type("V", (), {"voice": "af_heart", "say": lambda s, t: (said.append(t), done.set())})()
    rt._speak_failed(FileNotFoundError("voices/bf_emma.pt"))
    assert flashes and "on screen as text" in flashes[-1] and pins and pins[-1][0] == "I couldn't speak"

    fetched = []
    monkeypatch.setattr(kokoro_voice, "voice_on_disk", lambda v: False)
    monkeypatch.setattr(kokoro_voice, "download_voice", lambda v: fetched.append(v) or "x")
    monkeypatch.setattr(kokoro_voice, "g2p_on_disk", lambda v: False)    # round 7: its pronunciation model too
    monkeypatch.setattr(kokoro_voice, "download_g2p", lambda v: fetched.append(kokoro_voice.pronunciation_repo(v)) or "x")
    rt._voice_changed("bf_emma")
    assert done.wait(5)
    assert fetched == ["bf_emma", "PeterReid/graphemes_to_phonemes_en_gb"] and said and any("Downloading" in n for n in notes)


def test_fetch_models_downloads_every_voice_settings_offers():
    from tau import fetch
    from tau.settings import VOICES

    names = fetch.voice_names("bm_lewis")
    assert names[0] == "bm_lewis" and set(names) == {v for v, _ in VOICES}
    labels = [i.name for i in fetch.items("af_heart")]
    assert any(f"{len(VOICES)} voices" in n for n in labels)
    assert sum("Unknown-word helper" in n for n in labels) == 2      # American and British voices
