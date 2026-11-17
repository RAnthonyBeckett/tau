"""Download every voice and hearing model up front (``tau fetch-models``).

The installer runs this, so nothing downloads in the middle of a
conversation. Each model gets one plain line saying what it is for and about
how big it is, then a progress line every few seconds. Each download uses the
same loader TAU uses later, so exactly the files TAU needs land in the cache.

When everything is on disk a small marker file is written; from then on TAU's
processes run Hugging Face in offline mode (no network checks while talking).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from tau.engines import progress_line


@dataclass
class Item:
    name: str
    what: str
    approx_mb: float
    repo: Optional[str]                  # Hugging Face repo, used to watch progress on disk
    load: Callable[[], None]


def _kokoro_model() -> None:
    from huggingface_hub import hf_hub_download

    from tau.kokoro_voice import REPO_ID

    for f in ("config.json", "kokoro-v1_0.pth"):     # the files KModel(repo_id=...) loads
        hf_hub_download(repo_id=REPO_ID, filename=f)


def _voice_file(voice: str) -> Callable[[], None]:
    def go() -> None:
        from huggingface_hub import hf_hub_download

        from tau.kokoro_voice import REPO_ID

        hf_hub_download(repo_id=REPO_ID, filename=f"voices/{voice}.pt")
    return go


def _g2p_for(british: bool) -> Callable[[], None]:
    def go() -> None:
        from tau.kokoro_voice import BartFallback

        BartFallback(british=british)
    return go


def _whisper() -> None:
    from faster_whisper import download_model

    download_model("base.en")


def _vad() -> None:
    from faster_whisper.vad import get_vad_model   # ships inside faster-whisper; nothing to download

    get_vad_model()


def _all_voices(first: str) -> Callable[[], None]:
    def go() -> None:
        for v in voice_names(first):
            _voice_file(v)()
    return go


def voice_names(first: str = "af_heart") -> List[str]:
    """Every voice Settings offers, the chosen one first."""
    from tau.settings import VOICES

    names = [v for v, _ in VOICES]
    return [first] + [v for v in names if v != first] if first in names else [first] + names


def items(voice: str = "af_heart") -> List[Item]:
    from tau.kokoro_voice import FALLBACK_REPOS, REPO_ID

    british_first = voice[:1] == "b"
    n = len(voice_names(voice))
    return [
        Item("Kokoro voice model", "speaks TAU's replies", 327, REPO_ID, _kokoro_model),
        Item(f"Voice files ({n} voices, {voice} first)", "every voice you can pick in Settings", n * 0.5,
             REPO_ID, _all_voices(voice)),
        Item("Unknown-word helper", "says words that aren't in the dictionary", 30,
             FALLBACK_REPOS[british_first], _g2p_for(british_first)),
        Item("Unknown-word helper (" + ("American" if british_first else "British") + ")",
             "the same, for the " + ("American" if british_first else "British") + " voices", 30,
             FALLBACK_REPOS[not british_first], _g2p_for(not british_first)),
        Item("Whisper base.en", "turns your speech into text", 145, "Systran/faster-whisper-base.en", _whisper),
        Item("Silero VAD", "notices when you start and stop talking (comes with Whisper)", 2, None, _vad),
    ]


def _cache_mb(repo: Optional[str]) -> float:
    if not repo:
        return 0.0
    try:
        from huggingface_hub import constants

        d = Path(constants.HF_HUB_CACHE) / ("models--" + repo.replace("/", "--")) / "blobs"
        return sum(f.stat().st_size for f in d.iterdir() if f.is_file()) / 1e6 if d.exists() else 0.0
    except Exception:
        return 0.0


def marker() -> Path:
    from tau import paths

    return paths.state_dir() / "models_ready.json"


def fetch_all(say: Callable[[str], None] = print, voice: str = "af_heart", every: float = 3.0,
              item_list: Optional[List[Item]] = None) -> bool:
    todo = item_list if item_list is not None else items(voice)
    total = sum(i.approx_mb for i in todo)
    say(f"    {len(todo)} models, about {total:,.0f} MB in all.")
    ok_all, report = True, {}
    for it in todo:
        say(f"    {it.name}: {it.what} (about {it.approx_mb:,.0f} MB)")
        before = _cache_mb(it.repo)
        err: List[BaseException] = []
        t = threading.Thread(target=lambda: _run(it.load, err), daemon=True)
        start = time.monotonic()
        t.start()
        last = start
        while t.is_alive():
            t.join(0.5)
            now = time.monotonic()
            if t.is_alive() and now - last >= every:
                last = now
                got = max(0.0, _cache_mb(it.repo) - before)
                if got > 0.5:
                    say(progress_line(it.name, int(got * 1e6), int(max(it.approx_mb, got) * 1e6), now - start))
                else:
                    say(f"    {it.name}: still working ({int(now - start)} seconds so far)")
        if err:
            ok_all = False
            say(f"    {it.name}: didn't download ({type(err[0]).__name__}). TAU will try again when it's needed.")
            report[it.name] = "failed"
        else:
            got = max(0.0, _cache_mb(it.repo) - before)
            size = f", {got:,.0f} MB" if got >= 1 else ""
            say(f"    {it.name}: ready ({int(time.monotonic() - start)} s{size})")
            report[it.name] = "ready"
    if ok_all:
        m = marker()
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps({"at": time.time(), "voice": voice, "items": report}), encoding="utf-8")
        say("    All models are on this computer. TAU won't need to download anything while you talk.")
    return ok_all


def _run(fn: Callable[[], None], err: List[BaseException]) -> None:
    try:
        fn()
    except BaseException as exc:  # noqa: BLE001 - reported in plain words, logged in full
        import logging

        logging.getLogger("tau.fetch").exception("model download failed")
        err.append(exc)
