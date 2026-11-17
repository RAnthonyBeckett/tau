"""Ears on a synthetic WAV: wake (click-to-talk here) -> VAD -> transcription.

The cloud sandbox can't download a Whisper model (Hugging Face is blocked
there), so transcription uses the real faster-whisper model when
TAU_TEST_WHISPER_MODEL points at one, and a recording stub otherwise.
"""
import io
import os
import wave

import numpy as np
import pytest

from synth_speech import speech

pytest.importorskip("faster_whisper")
from tau.ears.pipeline import FRAME, RATE, Ears, SileroVAD, frames_from_array, to_wav_bytes  # noqa: E402


def _synthetic_wav() -> bytes:
    sil = np.zeros(RATE, np.int16)
    return to_wav_bytes(np.concatenate([sil, speech(1.6), np.zeros(int(RATE * 1.5), np.int16)]))


def _read_wav(b: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(b)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def test_vad_streams_speech_and_silence():
    vad = SileroVAD()
    audio = _read_wav(_synthetic_wav())
    probs = [vad.speech_prob(f.astype("float32") / 32768) for f in frames_from_array(audio)]
    assert max(probs[:10]) < 0.3          # first second is silence
    assert max(probs[14:30]) > 0.5        # synthetic voiced speech


def test_ears_gate_vad_and_transcription_on_synthetic_wav(tmp_path):
    model = os.environ.get("TAU_TEST_WHISPER_MODEL")
    got = {}
    if model:
        from tau.ears.pipeline import Transcriber
        tr = Transcriber(model)
    else:
        def tr(audio):
            got["seconds"] = len(audio) / RATE
            return "synthetic speech"
    texts, states, chimes = [], [], []
    ears = Ears(transcribe=tr, on_text=texts.append, vad=SileroVAD(),
                play_chime=lambda: chimes.append(1), on_state=states.append)
    audio = _read_wav(_synthetic_wav())
    frames = frames_from_array(audio)
    # dead air before the name: nothing happens, no transcription
    for f in frames[:10]:
        assert ears.feed(f) is None
    assert not texts
    ears.open_gate()                        # click-to-talk (same path as the wake word)
    assert chimes and states[-1] == "listening"
    result = None
    closed_at = None
    for i, f in enumerate(frames[10:], start=10):
        r = ears.feed(f)
        if r is not None:
            result, closed_at = r, i
            break
    assert result and texts == [result]
    assert states[-1] == "transcribing"     # then the assistant says "thinking with <model>"
    # gate closed about 800 ms after the speech ended (speech ends near 2.6 s)
    assert closed_at is not None and 2.6 + 0.6 < closed_at * FRAME / RATE < 2.6 + 1.6
    if not model:
        # the 2 s ring was kept, so the start of speech is in what was transcribed
        assert got["seconds"] > 2.0
    # nothing written to disk by the ears
    assert not any(p.suffix == ".wav" for p in tmp_path.rglob("*"))
