"""Ears: wake word -> listening gate -> VAD -> transcription. Zero tokens on dead air.

* Audio arrives in 80 ms frames (16 kHz mono). The last 2 seconds stay in a
  ring buffer in memory. Nothing is written to disk, ever.
* openWakeWord watches for the name. When it fires, the gate opens and a short
  soft chime plays (the 2 s buffer is kept so the first word isn't lost).
* Silero VAD (the ONNX copy inside faster-whisper, MIT) tracks speech; 800 ms
  of silence after speech closes the gate.
* faster-whisper on CPU (int8) turns the gated audio into text, through
  OpenJarvis's ``FasterWhisperBackend`` when OpenJarvis is installed.
* Click-to-talk (the Talk button, Space, or the core): ``open_gate()`` does the
  same as hearing the name; clicking again calls ``close_gate()``.
* Hands-free (``hands_free``, off by default): with no wake word needed, a
  quarter-second of speech opens the gate. The trade-off: everything the mic
  hears is checked for speech, and any speech is transcribed on this machine.
* While TAU is speaking, hearing the name interrupts it.

No model call happens until there is a transcript.
"""

from __future__ import annotations

import collections
import io
import threading
import wave
from dataclasses import dataclass, field
from typing import Callable, Deque, List, Optional

import numpy as np

RATE = 16000
FRAME = 1280            # 80 ms, openWakeWord's frame size
VAD_CHUNK = 512         # Silero's window at 16 kHz
RING_SECONDS = 2.0
SILENCE_CLOSE_MS = 800
MAX_UTTERANCE_S = 30
HANDS_FREE_FRAMES = 3   # 240 ms of speech opens the gate in hands-free mode


class SileroVAD:
    """Streaming wrapper around faster-whisper's bundled Silero VAD."""

    CONTEXT = 64

    def __init__(self, threshold: float = 0.5):
        from faster_whisper.vad import get_vad_model

        self.session = get_vad_model().session
        self.threshold = threshold
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype="float32")
        self._c = np.zeros((1, 1, 128), dtype="float32")
        self._ctx = np.zeros(self.CONTEXT, dtype="float32")
        self._carry = np.zeros(0, dtype="float32")

    def speech_prob(self, frame_f32: np.ndarray) -> float:
        """Feed audio; returns the highest speech probability in it. State carries over."""
        buf = np.concatenate([self._carry, frame_f32.astype("float32")])
        best = 0.0
        while len(buf) >= VAD_CHUNK:
            chunk, buf = buf[:VAD_CHUNK], buf[VAD_CHUNK:]
            x = np.concatenate([self._ctx, chunk])[None, :]
            out, self._h, self._c = self.session.run(None, {"input": x, "h": self._h, "c": self._c})
            self._ctx = chunk[-self.CONTEXT:]
            best = max(best, float(np.max(out)))
        self._carry = buf
        return best

    def is_speech(self, frame_f32: np.ndarray) -> bool:
        return self.speech_prob(frame_f32) >= self.threshold


class WakeWord:
    """openWakeWord with TAU's own model only (stock models are never loaded)."""

    def __init__(self, model_path: str, threshold: float = 0.5):
        import openwakeword
        from openwakeword.model import Model

        try:  # openWakeWord >= 0.6
            self.model = Model(wakeword_models=[model_path], inference_framework="onnx")
        except TypeError:  # 0.4 / 0.5
            self.model = Model(wakeword_model_paths=[model_path])
        self.threshold = threshold
        _ = openwakeword

    def heard(self, frame_i16: np.ndarray) -> bool:
        scores = self.model.predict(frame_i16)
        return any(v >= self.threshold for v in scores.values())


class Transcriber:
    """faster-whisper on CPU (int8), fed straight from memory.

    OpenJarvis's ``FasterWhisperBackend`` takes bytes and writes a temp file
    first; TAU's rule is that audio never touches disk, so it calls the same
    faster-whisper library directly with a NumPy array.
    """

    def __init__(self, model: str = "base.en", compute_type: str = "int8"):
        self.model_name, self.compute_type = model, compute_type
        self._model = None

    def __call__(self, audio_i16: np.ndarray) -> str:
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.model_name, device="cpu", compute_type=self.compute_type)
        segs, _ = self._model.transcribe(audio_i16.astype("float32") / 32768.0, language="en",
                                         vad_filter=False, beam_size=1)
        return " ".join(s.text.strip() for s in segs).strip()


def to_wav_bytes(audio_i16: np.ndarray, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(audio_i16.astype("<i2").tobytes())
    return buf.getvalue()


def chime(rate: int = 24000, ms: int = 140) -> np.ndarray:
    """A short soft two-tone chime, generated in memory (no audio file shipped)."""
    t = np.arange(int(rate * ms / 1000)) / rate
    env = np.sin(np.pi * t / t[-1]) ** 2
    tone = 0.12 * env * (np.sin(2 * np.pi * 880 * t) + 0.5 * np.sin(2 * np.pi * 1320 * t))
    return tone.astype("float32")


@dataclass
class Ears:
    transcribe: Callable[[np.ndarray], str]
    on_text: Callable[[str], None]
    vad: Optional[SileroVAD] = None
    wake: Optional[WakeWord] = None
    play_chime: Callable[[], None] = lambda: None
    on_wake_while_speaking: Callable[[], None] = lambda: None
    is_speaking: Callable[[], bool] = lambda: False
    on_state: Callable[[str], None] = lambda s: None
    ring: Deque[np.ndarray] = field(default_factory=lambda: collections.deque(
        maxlen=int(RING_SECONDS * RATE / FRAME)))
    hands_free: bool = False            # open the gate on speech alone (opt-in)
    _gate_open: bool = False
    _speech_run: int = 0
    _utterance: List[np.ndarray] = field(default_factory=list)
    _heard_speech: bool = False
    _silence_ms: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    model_calls_on_silence: int = 0  # stays 0: nothing is sent anywhere until there is text

    def open_gate(self) -> None:
        """Click-to-talk, or the wake word."""
        with self._lock:
            if self._gate_open:
                return
            self._gate_open = True
            self._utterance = list(self.ring)  # keep the 2 s before the name
            self._heard_speech = False
            self._silence_ms = 0.0
        self.play_chime()
        self.on_state("listening")

    def feed(self, frame_i16: np.ndarray) -> Optional[str]:
        """Feed one 80 ms frame. Returns a transcript when the gate closes."""
        frame_i16 = np.asarray(frame_i16, dtype=np.int16)
        self.ring.append(frame_i16)
        if not self._gate_open:
            if self.wake is not None and self.wake.heard(frame_i16):
                if self.is_speaking():
                    self.on_wake_while_speaking()
                self.open_gate()
            elif self.hands_free and self.vad is not None and not self.is_speaking():
                if self.vad.is_speech(frame_i16.astype("float32") / 32768.0):
                    self._speech_run += 1
                    if self._speech_run >= HANDS_FREE_FRAMES:
                        self._speech_run = 0
                        self.open_gate()
                        self._heard_speech = True
                else:
                    self._speech_run = 0
            return None
        self._utterance.append(frame_i16)
        speech = self.vad.is_speech(frame_i16.astype("float32") / 32768.0) if self.vad else True
        frame_ms = 1000 * len(frame_i16) / RATE
        if speech:
            self._heard_speech = True
            self._silence_ms = 0.0
        else:
            self._silence_ms += frame_ms
        total_s = sum(len(f) for f in self._utterance) / RATE
        if (self._heard_speech and self._silence_ms >= SILENCE_CLOSE_MS) or total_s > MAX_UTTERANCE_S \
                or (not self._heard_speech and self._silence_ms >= 4000):
            return self._close()
        return None

    def close_gate(self) -> Optional[str]:
        """Stop listening now (a second click on Talk) and use what was heard."""
        if not self._gate_open:
            return None
        return self._close()

    def _close(self) -> Optional[str]:
        with self._lock:
            audio = np.concatenate(self._utterance) if self._utterance else np.zeros(0, np.int16)
            heard = self._heard_speech
            self._gate_open = False
            self._utterance = []
        self.on_state("transcribing" if heard else "idle")
        if not heard:
            return None
        text = self.transcribe(audio)
        del audio  # nothing kept
        if text:
            self.on_text(text)
        return text

    @property
    def gate_open(self) -> bool:
        return self._gate_open


def frames_from_array(audio_i16: np.ndarray) -> List[np.ndarray]:
    n = len(audio_i16) // FRAME
    return [audio_i16[i * FRAME:(i + 1) * FRAME] for i in range(n)]


def run_microphone(ears: Ears, device=None, stop: Optional[threading.Event] = None, sd=None) -> None:
    """Feed the microphone into the ears until ``stop`` is set.

    The microphone records at a rate it supports (shared mode); each 80 ms block is
    resampled to 16 kHz before the ears see it."""
    from tau import audio

    if sd is None:
        import sounddevice as sd

    stop = stop or threading.Event()
    dev, _ = audio.resolve(device, "input", sd)
    rate = audio.pick_rate(dev, "input", RATE, sd)
    block = int(round(rate * FRAME / RATE))
    rs = audio.Resampler(rate, RATE)
    pending = np.zeros(0, "float32")
    with sd.InputStream(samplerate=rate, channels=1, dtype="float32", blocksize=block, device=dev) as s:
        while not stop.is_set():
            data, _ = s.read(block)
            pending = np.concatenate([pending, rs(np.asarray(data).reshape(-1))])
            while len(pending) >= FRAME:
                frame, pending = pending[:FRAME], pending[FRAME:]
                ears.feed((np.clip(frame, -1, 1) * 32767).astype("int16"))
