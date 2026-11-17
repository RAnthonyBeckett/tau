"""Voice out. Kokoro (Apache-2.0) on CPU by default, through TAU's own
espeak-free adapter (``tau.kokoro_voice``). OpenJarvis's ``KokoroTTSBackend``
is not used: it goes through Kokoro's ``KPipeline``, which loads GPL espeak
code. Other TTS engines plug in through ``TTS_ENGINES``.

Speech plays in 40 ms blocks so a click on the core or hearing the name stops
it within one block. While it plays, the loudness of each block is passed to
``on_level`` so the display's rings pulse with the voice.
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, Optional, Tuple

import numpy as np


class TTSEngine:
    """Return (mono float32 samples, sample rate)."""

    def synth(self, text: str, voice: str) -> Tuple[np.ndarray, int]:  # pragma: no cover
        raise NotImplementedError


class KokoroEngine(TTSEngine):
    """Kokoro through TAU's own adapter (``tau.kokoro_voice``): no KPipeline,
    no espeak, no GPL code. Yields audio chunk by chunk."""

    rate = 24000

    def __init__(self, voice: str = "af_heart"):
        from tau.kokoro_voice import KokoroDirect

        self._k = KokoroDirect(voice)

    def stream(self, text: str, voice: str):
        return self._k.stream(text, voice or None)

    def synth(self, text: str, voice: str) -> Tuple[np.ndarray, int]:
        parts = list(self.stream(text, voice))
        return (np.concatenate(parts) if parts else np.zeros(0, "float32")), self.rate


TTS_ENGINES: Dict[str, Callable[[], TTSEngine]] = {"kokoro": KokoroEngine}


class SoundDeviceSink:
    """Plays blocks on the chosen speaker, in shared mode, at a rate the speaker supports.

    The device is stored as ``tau.audio`` writes it. If it won't open, the system default is
    used and ``on_problem`` gets one sentence saying so (the display shows it)."""

    def __init__(self, device=None, on_problem: Callable[[str], None] = lambda msg: None):
        self.device = device
        self.on_problem = on_problem
        self._stream = None
        self._rate = None          # the rate blocks arrive at
        self._resample = None

    def set_device(self, device) -> None:
        self.close()
        self.device = device
        self._rate = None

    def _open_on(self, sd, dev, rate: int):
        from tau import audio

        out, ch = audio.pick_output(dev, rate, sd)
        stream = sd.OutputStream(samplerate=out, channels=ch, dtype="float32", device=dev)
        stream.start()
        self._ch = ch
        return stream, out

    def open(self, rate: int):
        import sounddevice as sd

        from tau import audio

        if self._stream is not None and self._rate == rate:
            return
        self.close()
        dev, note = audio.resolve(self.device, "output", sd)
        if note:
            self.on_problem(note)
        try:
            self._stream, out = self._open_on(sd, dev, rate)
        except Exception as exc:
            if dev is None:
                raise
            self.on_problem(f"{audio.label(self.device)} wouldn't play ({audio._why(exc)}), "
                            "so I'm using the system default.")
            self._stream, out = self._open_on(sd, None, rate)
        self._rate = rate
        self._resample = audio.Resampler(rate, out)

    def write(self, block: np.ndarray):
        data = self._resample(block) if self._resample else np.asarray(block, "float32")
        if len(data):
            from tau import audio
            self._stream.write(audio.to_channels(data, getattr(self, "_ch", 1)))

    def close(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
                self._rate = None


def failure_reason(exc: BaseException, voice: str = "") -> str:
    """One plain sentence saying why speaking failed."""
    name, msg = type(exc).__name__, (str(exc).strip().splitlines() or [""])[0][:200]
    if ("EntryNotFound" in name or "OfflineMode" in name or "voices/" in msg or "offline" in msg.lower()
            or isinstance(exc, FileNotFoundError)):
        return (f"the voice file{' for ' + voice if voice else ''} isn't on this computer. Pick the voice again "
                "in Settings, Voice and hearing, to download it, or run: tau fetch-models")
    if "PortAudio" in name or "PaErrorCode" in msg or "OutputStream" in msg:
        return f"the speaker wouldn't play ({msg or name}). Pick another in Settings, Voice and hearing"
    return f"{name}: {msg}" if msg else name


class Voice:
    BLOCK_S = 0.04

    def __init__(self, engine: TTSEngine, sink=None, voice: str = "af_heart",
                 on_level: Callable[[float], None] = lambda v: None,
                 on_state: Callable[[str], None] = lambda s: None,
                 on_error: Callable[[BaseException], None] = lambda exc: None):
        self.engine, self.sink, self.voice = engine, sink, voice
        self.on_level, self.on_state, self.on_error = on_level, on_state, on_error
        self.last_error: Optional[BaseException] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.speaking = False
        self.interrupted = False

    def say(self, text: str, wait: bool = False) -> None:
        self.interrupt()
        self._stop.clear()
        self.interrupted = False
        self._thread = threading.Thread(target=self._play, args=(text,), daemon=True)
        self.speaking = True
        self._thread.start()
        if wait:
            self._thread.join()

    def _chunks(self, text: str):
        if hasattr(self.engine, "stream"):
            rate = getattr(self.engine, "rate", 24000)
            for part in self.engine.stream(text, self.voice):
                yield part, rate
        else:
            yield self.engine.synth(text, self.voice)

    def _play(self, text: str) -> None:
        try:
            opened = None
            for audio, rate in self._chunks(text):
                if self._stop.is_set():
                    self.interrupted = True
                    return
                if opened is None:
                    self.on_state("speaking")
                if self.sink and opened != rate:
                    self.sink.open(rate)
                opened = rate
                n = max(1, int(rate * self.BLOCK_S))
                for i in range(0, len(audio), n):
                    if self._stop.is_set():
                        self.interrupted = True
                        return
                    block = audio[i:i + n]
                    self.on_level(float(min(1.0, np.sqrt(np.mean(block ** 2)) * 4)) if len(block) else 0.0)
                    if self.sink:
                        self.sink.write(block)
        except Exception as exc:   # a missing voice file, no speaker: never silent, never only the log
            import logging

            logging.getLogger("tau.voice").exception("speaking failed")
            self.last_error = exc
            self.on_error(exc)
        finally:
            self.speaking = False
            self.on_level(0.0)
            self.on_state("idle")

    def interrupt(self) -> None:
        if self.speaking:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=1)

    def is_speaking(self) -> bool:
        return self.speaking
