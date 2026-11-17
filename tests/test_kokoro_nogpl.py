"""Kokoro with no GPL code: no phonemizer/espeak packages, kokoro.pipeline never imported.

Model downloads (Kokoro weights, voice packs, the BART fallback) come from
Hugging Face. Where that is blocked, the download tests skip; set
TAU_TEST_KOKORO_DOWNLOADS=1 on a machine that can reach it to run them.
"""
import importlib.metadata as md
import importlib.util
import os
import sys

import numpy as np
import pytest

from tau.kokoro_voice import MAX_PHONEMES, chunk_phonemes

GPL_G2P = ("phonemizer", "phonemizer-fork", "espeakng-loader", "espeakng_loader")
DOWNLOADS = os.environ.get("TAU_TEST_KOKORO_DOWNLOADS") == "1"
HAVE_KOKORO = importlib.util.find_spec("kokoro") is not None and importlib.util.find_spec("misaki") is not None


def test_no_gpl_g2p_packages_installed():
    names = {d.metadata["Name"].lower().replace("_", "-") for d in md.distributions()}
    assert not names & {n.replace("_", "-") for n in GPL_G2P}, names & set(GPL_G2P)


def test_chunking_respects_kokoro_limit_and_boundaries():
    sent = "ðɪs ɪz ə sˈɛntəns, wɪð ə klˈɔz. "
    long = sent * 40
    parts = chunk_phonemes(long)
    assert len(parts) > 1 and all(len(p) <= MAX_PHONEMES for p in parts)
    assert all(p.rstrip().endswith((".", ",")) for p in parts[:-1])
    assert chunk_phonemes("") == [] and chunk_phonemes("ab") == ["ab"]
    assert all(len(p) <= 20 for p in chunk_phonemes("x" * 55, 20))


@pytest.mark.skipif(not HAVE_KOKORO, reason="kokoro/misaki not installed (pip install --no-deps -r requirements-kokoro.txt)")
def test_kokoro_model_loads_without_pipeline_or_espeak():
    from tau.kokoro_voice import import_kokoro_model

    KModel = import_kokoro_model()
    assert KModel.__name__ == "KModel"
    for mod in ("kokoro.pipeline", "misaki.espeak", "phonemizer", "espeakng_loader"):
        assert mod not in sys.modules, mod


@pytest.mark.skipif(not HAVE_KOKORO, reason="kokoro/misaki not installed")
def test_misaki_g2p_offline_with_fallback_wiring():
    pytest.importorskip("en_core_web_sm", reason="spaCy English model not installed")
    from tau.kokoro_voice import make_g2p

    seen = []

    def fake_fallback(token):
        seen.append(token.text)
        return "zˈɔɹblɪk", 1

    g2p = make_g2p(british=False, fallback=fake_fallback)
    ps, _ = g2p("Hello there, the zorblicks are ready.")
    assert "zˈɔɹblɪk" in ps and any("zorblick" in s.lower() for s in seen)
    assert "misaki.espeak" not in sys.modules and "phonemizer" not in sys.modules


@pytest.mark.skipif(not (HAVE_KOKORO and DOWNLOADS),
                    reason="needs Hugging Face downloads (blocked in the cloud sandbox); set TAU_TEST_KOKORO_DOWNLOADS=1")
def test_made_up_word_through_bart_fallback_and_audio():
    from tau.kokoro_voice import KokoroDirect

    k = KokoroDirect("af_heart")
    ps = k.phonemes("The flurbanix is ready.")
    assert ps and "❓" not in ps
    audio = np.concatenate(list(k.stream("Hello. The flurbanix is ready.")))
    assert audio.size > 24000 * 0.5 and np.abs(audio).max() > 0.01
    assert "kokoro.pipeline" not in sys.modules
