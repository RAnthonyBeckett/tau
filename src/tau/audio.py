"""Speakers and microphones: one row per physical device, stored exactly, opened at a rate
the device supports.

Windows shows each device once per host API (MME, DirectSound, WASAPI, WDM-KS). TAU lists
each physical device once, preferring the WASAPI entry and falling back to MME, with
"System default" first. A choice is stored as ``"<host API>|<index>|<name>"`` and matched
exactly: same index, host API and name, else the same host API and name, else the default.

Streams open in shared mode (sounddevice never asks for exclusive mode unless told to) at a
rate the device accepts. Kokoro's 24 kHz output is resampled to it, and the microphone is
recorded at its own rate and resampled to 16 kHz for VAD and Whisper.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

DEFAULT = "default"
DEFAULT_LABEL = "System default"

# Host APIs in the order TAU prefers them. Unknown ones come after these.
HOSTAPI_RANK = ("Windows WASAPI", "MME", "Core Audio", "PulseAudio", "ALSA", "JACK Audio Connection Kit",
                "Windows DirectSound", "Windows WDM-KS")
# Entries that are only another name for the default device.
_ALIASES = re.compile(r"^(Microsoft Sound Mapper|Primary Sound (Driver|Capture Driver))", re.I)
MME_NAME_LIMIT = 31     # MME cuts names to 31 characters


def _sd():
    import sounddevice as sd

    return sd


def _hostapi_names(sd) -> List[str]:
    try:
        return [h["name"] for h in sd.query_hostapis()]
    except Exception:
        return []


def _rank(name: str) -> int:
    return HOSTAPI_RANK.index(name) if name in HOSTAPI_RANK else len(HOSTAPI_RANK)


def _same_device(a: str, b: str) -> bool:
    """MME truncates names, so a name that is a 31-character prefix of another is the same device."""
    a, b = a.strip().lower(), b.strip().lower()
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= MME_NAME_LIMIT - 1 and long_.startswith(short)


def key(hostapi: str, index: int, name: str) -> str:
    return f"{hostapi}|{index}|{name}"


def parse(stored: Any) -> Optional[Tuple[str, Optional[int], str]]:
    """``(hostapi, index, name)``, or ``("", None, name)`` for a name saved by TAU 1.1, or None for the default."""
    if stored in (None, "", DEFAULT):
        return None
    if isinstance(stored, int):
        return ("", stored, "")
    s = str(stored)
    parts = s.split("|", 2)
    if len(parts) == 3 and parts[1].lstrip("-").isdigit():
        return (parts[0], int(parts[1]), parts[2])
    return ("", None, s)


def list_devices(sd=None) -> Dict[str, List[Dict[str, str]]]:
    """``{"mics": [...], "speakers": [...]}``, each ``{"value", "label"}``, System default first."""
    out: Dict[str, List[Dict[str, str]]] = {"mics": [], "speakers": []}
    try:
        sd = sd or _sd()
        devs = list(sd.query_devices())
        apis = _hostapi_names(sd)
    except Exception:
        return out
    for kind, chan in (("mics", "max_input_channels"), ("speakers", "max_output_channels")):
        cands = []
        for i, d in enumerate(devs):
            if d.get(chan, 0) <= 0 or _ALIASES.match(str(d["name"])):
                continue
            api = apis[d["hostapi"]] if 0 <= d.get("hostapi", -1) < len(apis) else ""
            cands.append((_rank(api), i, api, str(d["name"])))
        cands.sort()
        best_rank = cands[0][0] if cands else 0
        kept: List[Tuple[str, int, str]] = []
        for rank, i, api, name in cands:
            if any(_same_device(name, k[2]) for k in kept):
                continue
            # Behind WASAPI and MME, the other Windows host APIs only repeat the same devices.
            if rank >= _rank("Windows DirectSound") and best_rank < rank:
                continue
            kept.append((api, i, name))
        rows = [{"value": DEFAULT, "label": DEFAULT_LABEL}]
        rows += [{"value": key(api, i, name), "label": name} for api, i, name in kept]
        out[kind] = rows
    return out


def resolve(stored: Any, kind: str = "output", sd=None) -> Tuple[Optional[int], Optional[str]]:
    """The sounddevice index for a stored choice (None = system default), and a sentence if
    the stored device is gone and the default is used instead."""
    want = parse(stored)
    if want is None:
        return None, None
    sd = sd or _sd()
    chan = "max_input_channels" if kind == "input" else "max_output_channels"
    devs = list(sd.query_devices())
    apis = _hostapi_names(sd)

    def api_of(d):
        return apis[d["hostapi"]] if 0 <= d.get("hostapi", -1) < len(apis) else ""

    api, idx, name = want
    if idx is not None and 0 <= idx < len(devs):
        d = devs[idx]
        if d.get(chan, 0) > 0 and (not name or d["name"] == name) and (not api or api_of(d) == api):
            return idx, None
    matches = [(i, d) for i, d in enumerate(devs) if d.get(chan, 0) > 0 and name and d["name"] == name]
    if api:
        exact = [i for i, d in matches if api_of(d) == api]
        if exact:
            return exact[0], None
    if matches:  # a plain name from TAU 1.1, or the host API changed: the preferred host API wins
        matches.sort(key=lambda m: _rank(api_of(m[1])))
        return matches[0][0], None
    return None, f"I couldn't find {name or 'that device'}, so I'm using the system default."


def label(stored: Any) -> str:
    p = parse(stored)
    return DEFAULT_LABEL if p is None else (p[2] or f"device {p[1]}")


def pick_output(device: Optional[int], want: int, sd=None) -> Tuple[int, int]:
    """(rate, channels) the speaker accepts. Some Windows speakers only take stereo at their own rate."""
    sd = sd or _sd()
    tries = [want]
    try:
        info = sd.query_devices(device, "output") if device is not None else sd.query_devices(kind="output")
        tries.append(int(info["default_samplerate"]))
    except Exception:
        pass
    tries += [48000, 44100]
    for ch in (1, 2):
        for rate in dict.fromkeys(tries):
            try:
                sd.check_output_settings(device=device, samplerate=rate, channels=ch, dtype="float32")
                return rate, ch
            except Exception:
                continue
    return pick_rate(device, "output", want, sd), 1


def to_channels(audio: np.ndarray, ch: int) -> np.ndarray:
    mono = np.asarray(audio, "float32").reshape(-1, 1)
    return np.repeat(mono, ch, axis=1) if ch > 1 else mono


def pick_rate(device: Optional[int], kind: str, want: int, sd=None) -> int:
    """``want`` if the device accepts it, else the device's own default rate, else 48 kHz or 44.1 kHz."""
    sd = sd or _sd()
    check = sd.check_input_settings if kind == "input" else sd.check_output_settings
    tries = [want]
    own = None
    try:
        info = sd.query_devices(device, kind) if device is not None else sd.query_devices(kind=kind)
        own = int(info["default_samplerate"])
        tries.append(own)
    except Exception:
        pass
    tries += [48000, 44100]
    for rate in dict.fromkeys(tries):
        try:
            check(device=device, samplerate=rate, channels=1, dtype="float32")
            return rate
        except Exception:
            continue
    return own or want


def resample(audio: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Linear resampling, with a short moving average first when going down (enough for speech)."""
    audio = np.asarray(audio, dtype="float32").reshape(-1)
    if src == dst or len(audio) == 0:
        return audio
    if dst < src:
        k = int(round(src / dst))
        if k > 1:
            audio = np.convolve(audio, np.ones(k, "float32") / k, mode="same").astype("float32")
    n = max(1, int(round(len(audio) * dst / src)))
    x = np.linspace(0, len(audio) - 1, n)
    return np.interp(x, np.arange(len(audio)), audio).astype("float32")


class Resampler:
    """Streaming resampler that keeps the fractional position between blocks, so joins don't click."""

    def __init__(self, src: int, dst: int):
        self.src, self.dst = src, dst
        self._tail = np.zeros(0, "float32")
        self._pos = 0.0

    def __call__(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, dtype="float32").reshape(-1)
        if self.src == self.dst:
            return block
        buf = np.concatenate([self._tail, block])
        step = self.src / self.dst
        xs = np.arange(self._pos, len(buf) - 1, step)
        out = np.interp(xs, np.arange(len(buf)), buf).astype("float32")
        nxt = self._pos + len(xs) * step
        keep = int(nxt)
        self._tail = buf[keep:]
        self._pos = nxt - keep
        return out


def play(audio: np.ndarray, rate: int, stored: Any = None, sd=None) -> Optional[str]:
    """Play once and wait. Returns None, or a sentence if the default was used instead.
    Raises if nothing could play."""
    sd = sd or _sd()
    dev, note = resolve(stored, "output", sd)
    try:
        r, ch = pick_output(dev, rate, sd)
        sd.play(to_channels(resample(audio, rate, r), ch), r, device=dev)
        sd.wait()
        return note
    except Exception as exc:
        if dev is None:
            raise
        r, ch = pick_output(None, rate, sd)
        sd.play(to_channels(resample(audio, rate, r), ch), r, device=None)
        sd.wait()
        return f"{label(stored)} didn't play ({_why(exc)}), so I used the system default."


def record(seconds: float, rate: int = 16000, stored: Any = None, sd=None) -> np.ndarray:
    """Record mono float32 at ``rate`` (resampled from what the device supports)."""
    sd = sd or _sd()
    dev, _ = resolve(stored, "input", sd)
    r = pick_rate(dev, "input", rate, sd)
    rec = sd.rec(int(r * seconds), samplerate=r, channels=1, dtype="float32", device=dev)
    sd.wait()
    return resample(np.asarray(rec).reshape(-1), r, rate)


def _why(exc: BaseException) -> str:
    s = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return s[:160]


__all__ = ["DEFAULT", "DEFAULT_LABEL", "list_devices", "resolve", "parse", "label", "pick_rate", "pick_output", "to_channels", "resample",
           "Resampler", "play", "record", "key"]
