"""Bug 10: library noise and surprise downloads mid-conversation.

* A voice reply whose engine emits the same kinds of noise as torch, Hugging
  Face and native libraries prints nothing but TAU's own words; the noise is
  in TAU's log file.
* ``tau fetch-models`` downloads everything up front, one plain line per model.

Hugging Face is blocked in the cloud sandbox, so the real-Kokoro variant only
runs with TAU_TEST_KOKORO_DOWNLOADS=1 (CI sets it); here the download path is
tested with stub loaders.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")
NOISE = ("Warning", "UserWarning", "FutureWarning", "DeprecationWarning", "Traceback")

NOISY_REPLY = r'''
import logging, os, sys, warnings
from tau import quiet
quiet.install(sys.argv[1])
import numpy as np
from tau.voice import Voice

class NoisyEngine:
    rate = 24000
    def stream(self, text, voice):
        warnings.warn("torch.nn.utils.weight_norm is deprecated", FutureWarning)
        warnings.warn("dropout option adds dropout after all but last recurrent layer", UserWarning)
        warnings.warn("old API", DeprecationWarning)
        hf = logging.getLogger("huggingface_hub.file_download")
        hf.addHandler(logging.StreamHandler())      # libraries add their own stderr handlers
        hf.warning("Warning: You are sending unauthenticated requests to the HF Hub.")
        os.write(2, b"UserWarning: written straight to file descriptor 2 by a native library\n")
        print("Warning: printed to sys.stderr", file=sys.stderr)
        yield np.zeros(2400, dtype="float32")

v = Voice(NoisyEngine(), sink=None)
v.say("hello", wait=True)
print("tau: hello")
'''

KOKORO_REPLY = r'''
import sys
from tau import quiet
quiet.install(sys.argv[1])
from tau.voice import KokoroEngine, Voice
v = Voice(KokoroEngine("af_heart"), sink=None)
v.say("Hello. I'm ready when you are.", wait=True)
print("tau: Hello. I'm ready when you are.")
'''


def _run(code, tmp_path):
    log = tmp_path / "tau.log"
    env = dict(os.environ, PYTHONPATH=SRC, TAU_HOME=str(tmp_path / "home"))
    env.pop("TAU_DEBUG", None)
    r = subprocess.run([sys.executable, "-c", code, str(log)], capture_output=True, text=True, env=env,
                       timeout=900)
    return r, log


def test_voice_reply_shows_no_library_noise(tmp_path):
    r, log = _run(NOISY_REPLY, tmp_path)
    assert r.returncode == 0, log.read_text() if log.exists() else r.stderr
    assert r.stdout == "tau: hello\n"
    for word in NOISE:
        assert word not in r.stdout and word not in r.stderr, word
    text = log.read_text()
    for kept in ("FutureWarning", "UserWarning", "unauthenticated", "file descriptor 2", "printed to sys.stderr"):
        assert kept in text, kept


@pytest.mark.skipif(os.environ.get("TAU_TEST_KOKORO_DOWNLOADS") != "1",
                    reason="needs Hugging Face for the real Kokoro model (CI sets TAU_TEST_KOKORO_DOWNLOADS=1)")
def test_real_kokoro_reply_shows_no_library_noise(tmp_path):
    pytest.importorskip("kokoro")
    r, log = _run(KOKORO_REPLY, tmp_path)
    assert r.returncode == 0, log.read_text() if log.exists() else r.stderr
    assert r.stdout.strip() == "tau: Hello. I'm ready when you are."
    for word in NOISE:
        assert word not in r.stdout and word not in r.stderr, word


def test_fetch_all_says_each_model_and_writes_the_marker(monkeypatch):
    from tau import fetch

    loaded = []
    stub = [fetch.Item("Model A", "does a thing", 10, None, lambda: loaded.append("a")),
            fetch.Item("Model B", "does another", 2, None, lambda: loaded.append("b"))]
    said = []
    assert fetch.fetch_all(said.append, item_list=stub)
    assert loaded == ["a", "b"]
    assert said[0] == "    2 models, about 12 MB in all."
    assert "    Model A: does a thing (about 10 MB)" in said
    assert fetch.marker().exists()


def test_fetch_failure_is_one_plain_line_and_no_marker():
    from tau import fetch

    def boom():
        raise OSError("blocked")

    said = []
    assert not fetch.fetch_all(said.append, item_list=[fetch.Item("Model C", "x", 1, None, boom)])
    assert said[-1] == "    Model C: didn't download (OSError). TAU will try again when it's needed."
    assert not fetch.marker().exists()


def test_every_model_tau_downloads_is_fetched_up_front():
    from tau import fetch, licences

    fetched = {i.repo for i in fetch.items() if i.repo}
    refs = {r for r in licences.model_refs() if not r.startswith(("explosion/", "Qwen/"))}
    refs.discard("PeterReid/graphemes_to_phonemes_en_gb")    # only for British voices
    assert refs <= fetched, refs - fetched
