"""Kokoro without espeak: TAU's own small adapter.

Kokoro's ``KPipeline`` imports ``misaki.espeak`` at module load, which needs
phonemizer and espeak-ng (GPL-3.0). Kokoro's ``__init__`` imports the
pipeline too, so even ``import kokoro.model`` would pull it in. This adapter:

* loads ``kokoro.model`` under a bare package stub, so ``kokoro/__init__.py``
  and ``kokoro.pipeline`` never run (checked by a test through ``sys.modules``);
* turns text into phonemes with ``misaki.en.G2P(trf=False, fallback=...)``
  (``misaki.en`` imports no espeak);
* handles words outside misaki's dictionary with a BART grapheme-to-phoneme
  model (``PeterReid/graphemes_to_phonemes_en_us`` / ``_gb``, Apache-2.0), the
  same approach as misaki's unreleased ``FallbackNetwork``, written here so
  TAU can use the released misaki 0.9.4 from PyPI;
* splits the phonemes into chunks Kokoro accepts (510 phonemes) at sentence,
  then clause, then word boundaries, and yields audio chunk by chunk so
  playback can start early and be interrupted between chunks.

Kokoro and misaki are Apache-2.0. Install them with
``pip install --no-deps -r requirements-kokoro.txt`` (see that file).
"""

from __future__ import annotations

import importlib
import importlib.util
import re
import sys
import types
from typing import Iterator, List, Optional, Tuple

import numpy as np

REPO_ID = "hexgrad/Kokoro-82M"
FALLBACK_REPOS = {False: "PeterReid/graphemes_to_phonemes_en_us", True: "PeterReid/graphemes_to_phonemes_en_gb"}
MAX_PHONEMES = 510          # Kokoro's context is 512 tokens including the two pads
RATE = 24000


def import_kokoro_model():
    """Return ``kokoro.model.KModel`` without executing ``kokoro/__init__.py``."""
    if "kokoro" in sys.modules and not getattr(sys.modules["kokoro"], "__tau_stub__", False):
        if "kokoro.pipeline" in sys.modules:
            raise RuntimeError("kokoro was already imported the normal way (with its espeak pipeline)")
    if "kokoro" not in sys.modules:
        spec = importlib.util.find_spec("kokoro")   # finding a top-level package runs nothing
        if spec is None or not spec.submodule_search_locations:
            raise ImportError("kokoro is not installed; pip install --no-deps -r requirements-kokoro.txt")
        stub = types.ModuleType("kokoro")
        stub.__path__ = list(spec.submodule_search_locations)
        stub.__spec__ = spec
        stub.__tau_stub__ = True
        sys.modules["kokoro"] = stub
    try:
        from loguru import logger
        logger.disable("kokoro")
    except Exception:
        pass
    return importlib.import_module("kokoro.model").KModel


class BartFallback:
    """Grapheme-to-phoneme for words misaki doesn't know (Apache-2.0 BART model)."""

    def __init__(self, british: bool = False):
        import torch
        from transformers import BartForConditionalGeneration

        self.torch = torch
        self.model = BartForConditionalGeneration.from_pretrained(FALLBACK_REPOS[british]).eval()
        cfg = self.model.config
        self.g2t = {g: i for i, g in enumerate(cfg.grapheme_chars)}
        self.t2p = {i: p for i, p in enumerate(cfg.phoneme_chars)}

    def __call__(self, token) -> Tuple[str, int]:
        ids = [1] + [self.g2t.get(g, 3) for g in token.text] + [2]
        with self.torch.no_grad():
            out = self.model.generate(input_ids=self.torch.tensor([ids]))
        return "".join(self.t2p.get(t, "") for t in out[0].tolist() if t > 3), 1


def make_g2p(british: bool = False, fallback=True):
    from misaki import en

    fb = BartFallback(british) if fallback is True else (fallback or None)
    return en.G2P(trf=False, british=british, fallback=fb)


_SENT = re.compile(r"(?<=[.!?])\s+")
_CLAUSE = re.compile(r"(?<=[,;:—])\s+")


def chunk_phonemes(phonemes: str, limit: int = MAX_PHONEMES) -> List[str]:
    """Split a phoneme string into pieces of at most ``limit`` characters,
    preferring sentence ends, then clause marks, then spaces."""
    phonemes = phonemes.strip()
    if len(phonemes) <= limit:
        return [phonemes] if phonemes else []
    out: List[str] = []
    for splitter in (_SENT, _CLAUSE, re.compile(r"\s+")):
        parts = [p for p in splitter.split(phonemes) if p]
        if len(parts) > 1:
            cur = ""
            for p in parts:
                if len(p) > limit:
                    if cur:
                        out.append(cur)
                        cur = ""
                    out.extend(chunk_phonemes(p, limit))
                elif not cur:
                    cur = p
                elif len(cur) + 1 + len(p) <= limit:
                    cur += " " + p
                else:
                    out.append(cur)
                    cur = p
            if cur:
                out.append(cur)
            return out
    return [phonemes[i:i + limit] for i in range(0, len(phonemes), limit)]   # one huge word


def voice_on_disk(voice: str, repo_id: str = REPO_ID) -> bool:
    """True if this voice file is already downloaded (no network)."""
    try:
        from huggingface_hub import try_to_load_from_cache

        got = try_to_load_from_cache(repo_id=repo_id, filename=f"voices/{voice}.pt")
        return isinstance(got, str)
    except Exception:
        return False


def pronunciation_repo(voice: str) -> str:
    """The unknown-word (G2P) model a voice needs: British voices (b...) need the en_gb one."""
    return FALLBACK_REPOS[voice[:1] == "b"]


def g2p_on_disk(voice: str) -> bool:
    """True if this voice's pronunciation model is already downloaded (no network)."""
    try:
        from huggingface_hub import try_to_load_from_cache

        return isinstance(try_to_load_from_cache(repo_id=pronunciation_repo(voice), filename="config.json"), str)
    except Exception:
        return False


def download_g2p(voice: str) -> str:
    """Download this voice's pronunciation model now (about 30 MB)."""
    import os

    from huggingface_hub import constants, snapshot_download

    old_env, old_const = os.environ.get("HF_HUB_OFFLINE"), getattr(constants, "HF_HUB_OFFLINE", False)
    os.environ["HF_HUB_OFFLINE"] = "0"
    constants.HF_HUB_OFFLINE = False
    try:
        return snapshot_download(repo_id=pronunciation_repo(voice))
    finally:
        constants.HF_HUB_OFFLINE = old_const
        if old_env is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = old_env


def ensure_voice(voice: str, note=lambda _m: None) -> List[str]:
    """Fetch everything a voice needs: its voice file and its pronunciation model.
    Returns what was downloaded. Raises if either fetch fails, so the caller can keep the old voice."""
    got = []
    if not voice_on_disk(voice):
        note(f"Downloading the voice {voice} (about half a megabyte)...")
        download_voice(voice)
        got.append(f"voices/{voice}.pt")
    if not g2p_on_disk(voice):
        note(f"Downloading how to say unknown words for {voice} (about 30 MB)...")
        download_g2p(voice)
        got.append(pronunciation_repo(voice))
    return got


def download_voice(voice: str, repo_id: str = REPO_ID) -> str:
    """Download one voice file now, even though TAU runs Hugging Face offline while talking.
    Voice files are about half a megabyte."""
    import os

    from huggingface_hub import constants, hf_hub_download

    old_env, old_const = os.environ.get("HF_HUB_OFFLINE"), getattr(constants, "HF_HUB_OFFLINE", False)
    os.environ["HF_HUB_OFFLINE"] = "0"
    constants.HF_HUB_OFFLINE = False
    try:
        return hf_hub_download(repo_id=repo_id, filename=f"voices/{voice}.pt")
    finally:
        constants.HF_HUB_OFFLINE = old_const
        if old_env is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = old_env


class KokoroDirect:
    """Text -> 24 kHz audio, one chunk at a time, with no GPL code loaded."""

    def __init__(self, voice: str = "af_heart", repo_id: str = REPO_ID, g2p=None, fallback=True):
        self.voice = voice
        self.repo_id = repo_id
        self.british = voice[:1] == "b"
        self._g2p = g2p
        self._fallback = fallback
        self._model = None
        self._packs = {}

    @property
    def g2p(self):
        if self._g2p is None:
            self._g2p = make_g2p(self.british, self._fallback)
        return self._g2p

    def phonemes(self, text: str) -> str:
        ps, _tokens = self.g2p(text)
        return ps

    def _pack(self, voice: str):
        if voice not in self._packs:
            import torch
            from huggingface_hub import hf_hub_download

            path = hf_hub_download(repo_id=self.repo_id, filename=f"voices/{voice}.pt")
            self._packs[voice] = torch.load(path, weights_only=True)
        return self._packs[voice]

    @property
    def model(self):
        if self._model is None:
            KModel = import_kokoro_model()
            self._model = KModel(repo_id=self.repo_id).eval()
        return self._model

    def stream(self, text: str, voice: Optional[str] = None, speed: float = 1.0) -> Iterator[np.ndarray]:
        import torch

        voice = voice or self.voice
        pack = self._pack(voice)
        for piece in chunk_phonemes(self.phonemes(text)):
            ref_s = pack[len(piece) - 1]
            with torch.no_grad():
                audio = self.model(piece, ref_s, speed)
            yield audio.detach().cpu().numpy().astype("float32").reshape(-1)
