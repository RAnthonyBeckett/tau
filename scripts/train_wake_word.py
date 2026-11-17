"""Train a wake-word model for TAU's name (or any name) with openWakeWord features.

Why this exists: openWakeWord's stock wake models are licensed for
non-commercial use only, so TAU ships none of them. This script makes your own.

Pipeline
  1. Positive clips: the name (and "hey <name>") spoken by Kokoro TTS
     (Apache-2.0) in every English voice at several speeds, plus any of your own
     recordings in --positives-dir.
  2. Negative clips: words that sound close to the name (generated from it),
     other short phrases, noise, and any clips in --negatives-dir. A CC0 speech
     set such as Mozilla Common Voice is a good source of extra negatives.
  3. Light augmentation in memory: gain, background noise, a simple room echo.
  4. openWakeWord's shared feature models turn each clip into 16x96 embeddings.
  5. A small two-layer network is trained with NumPy and exported to ONNX in the
     shape openWakeWord loads ([1, 16, 96] -> [1, 1]).

Usage
  pip install -e "tau[voice]" onnx && pip install --no-deps -r requirements-kokoro.txt
  python scripts/train_wake_word.py --name tau --out tau.onnx
  python scripts/train_wake_word.py --name nova --out nova.onnx --negatives-dir ~/cv_clips
  # then set tau.wake_model in profile.yaml (or rerun `tau setup voice`)

  python scripts/train_wake_word.py --selftest --out /tmp/selftest.onnx
  # checks the pipeline end to end with synthetic audio, no TTS needed.
  # The self-test model is useless as a wake word; it only proves the plumbing.

Rename: change tau.name / tau.wake_word in the profile, run this script with the
new name, point tau.wake_model at the new file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

import numpy as np

RATE = 16000
CLIP_S = 2.0


# -- audio sources -----------------------------------------------------------------------
def confusables(name: str) -> List[str]:
    """Words that sound close to the name, for hard negatives."""
    n = name.lower()
    vowels = "aeiou"
    out = set()
    for c in "bcdfghklmnprstvwz":
        if n and n[0] not in vowels:
            out.add(c + n[1:])
    out |= {n + "s", n + "er", n[:-1] if len(n) > 2 else n + "o", "hey", "okay", "hello", "today",
            "how", "now", "towel", "tower", "toe", "tea", "tall", "ciao", "wow", "though", "tomorrow"}
    out.discard(n)
    return sorted(out)


def kokoro_clips(texts: List[str], speeds=(0.85, 1.0, 1.15)) -> List[np.ndarray]:
    """Speak each text with Kokoro (Apache-2.0) through TAU's espeak-free adapter."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from tau.kokoro_voice import KokoroDirect

    voices = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "am_adam", "am_michael",
              "am_fenrir", "am_puck", "bf_emma", "bf_isabella", "bm_george", "bm_lewis"]
    clips = []
    engines = {}
    for v in voices:
        british = v[:1] == "b"
        if british not in engines:
            engines[british] = KokoroDirect(v)
        k = engines[british]
        for t in texts:
            for sp in speeds:
                try:
                    chunks = list(k.stream(t, voice=v, speed=sp))
                except Exception:
                    continue
                if chunks:
                    clips.append(resample(np.concatenate(chunks), 24000, RATE))
    return clips


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return x
    n = int(len(x) * dst / src)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype("float32")


def load_dir(folder: Path) -> List[np.ndarray]:
    import soundfile as sf

    out = []
    for p in sorted(Path(folder).expanduser().rglob("*")):
        if p.suffix.lower() in (".wav", ".flac", ".ogg"):
            a, sr = sf.read(str(p), dtype="float32", always_2d=True)
            out.append(resample(a[:, 0], sr, RATE))
    return out


def augment(clip: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    total = int(CLIP_S * RATE)
    clip = clip[:total]
    start = rng.integers(0, max(1, total - len(clip)))
    out = np.zeros(total, dtype="float32")
    out[start:start + len(clip)] = clip * rng.uniform(0.3, 1.0)
    if rng.random() < 0.5:  # simple room echo
        d = int(rng.uniform(0.02, 0.08) * RATE)
        out[d:] += 0.3 * out[:-d]
    out += rng.normal(0, rng.uniform(0.0005, 0.01), total).astype("float32")
    return np.clip(out, -1, 1)


# -- features and model ------------------------------------------------------------------------
def embed(clips: List[np.ndarray]) -> np.ndarray:
    from openwakeword.utils import AudioFeatures

    f = AudioFeatures(inference_framework="onnx") if _accepts_framework() else AudioFeatures()
    x = np.stack([(c * 32767).astype(np.int16) for c in clips])
    emb = f.embed_clips(x, batch_size=64)       # (N, frames, 96)
    return emb[:, -16:, :].astype("float32")    # the last 16 frames, as openWakeWord scores them


def _accepts_framework() -> bool:
    import inspect

    from openwakeword.utils import AudioFeatures

    return "inference_framework" in inspect.signature(AudioFeatures.__init__).parameters


def train(xp: np.ndarray, xn: np.ndarray, epochs: int = 300, hidden: int = 32, seed: int = 0):
    rng = np.random.default_rng(seed)
    X = np.concatenate([xp, xn]).reshape(len(xp) + len(xn), -1)
    y = np.concatenate([np.ones(len(xp)), np.zeros(len(xn))]).astype("float32")
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Xn = (X - mu) / sd
    w_pos = len(xn) / max(1, len(xp))
    W1 = rng.normal(0, 0.05, (X.shape[1], hidden)).astype("float32")
    b1 = np.zeros(hidden, "float32")
    W2 = rng.normal(0, 0.05, (hidden, 1)).astype("float32")
    b2 = np.zeros(1, "float32")
    lr = 0.05
    for _ in range(epochs):
        h = np.maximum(0, Xn @ W1 + b1)
        p = 1 / (1 + np.exp(-(h @ W2 + b2)[:, 0]))
        wt = np.where(y == 1, w_pos, 1.0)
        g = (p - y) * wt / len(y)
        gW2 = h.T @ g[:, None]
        gb2 = g.sum(keepdims=True)
        gh = (g[:, None] @ W2.T) * (h > 0)
        gW1 = Xn.T @ gh
        gb1 = gh.sum(0)
        W1 -= lr * gW1; b1 -= lr * gb1; W2 -= lr * gW2; b2 -= lr * gb2
    # fold the normalisation into the first layer
    W1f = (W1 / sd[:, None]).astype("float32")
    b1f = (b1 - (mu / sd) @ W1).astype("float32")
    h = np.maximum(0, X @ W1f + b1f)
    p = 1 / (1 + np.exp(-(h @ W2 + b2)[:, 0]))
    acc = float(((p > 0.5) == (y == 1)).mean())
    return (W1f, b1f, W2.astype("float32"), b2.astype("float32")), acc


def export_onnx(params, out: Path, name: str) -> None:
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    W1, b1, W2, b2 = params
    nodes = [
        helper.make_node("Reshape", ["x", "shape"], ["flat"]),
        helper.make_node("Gemm", ["flat", "W1", "b1"], ["h"]),
        helper.make_node("Relu", ["h"], ["hr"]),
        helper.make_node("Gemm", ["hr", "W2", "b2"], ["logit"]),
        helper.make_node("Sigmoid", ["logit"], ["score"]),
    ]
    inits = [numpy_helper.from_array(np.array([1, 16 * 96], dtype=np.int64), "shape"),
             numpy_helper.from_array(W1, "W1"), numpy_helper.from_array(b1, "b1"),
             numpy_helper.from_array(W2, "W2"), numpy_helper.from_array(b2, "b2")]
    graph = helper.make_graph(nodes, f"tau_wake_{name}",
                              [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 16, 96])],
                              [helper.make_tensor_value_info("score", TensorProto.FLOAT, [1, 1])], inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)],
                              producer_name="tau-train-wake-word")
    model.ir_version = 8
    onnx.checker.check_model(model)
    out.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(out))


# -- self-test ---------------------------------------------------------------------------------
def _selftest_clips(rng):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from synth_speech import V, vowel

    def word(pattern, seed):
        r = np.random.default_rng(seed)
        parts = [vowel(0.18, 120 + 10 * r.random(), V[c], r) for c in pattern]
        a = np.concatenate(parts)
        return (a / np.max(np.abs(a)) * 0.5).astype("float32")

    pos = [word("ao", s) for s in range(60)]
    neg = [word(p, s) for s in range(40) for p in ("ie", "ue", "eo")]
    return pos, neg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="tau")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--positives-dir", type=Path)
    ap.add_argument("--negatives-dir", type=Path)
    ap.add_argument("--augment", type=int, default=3, help="augmented copies per clip")
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    rng = np.random.default_rng(0)
    if a.selftest:
        pos, neg = _selftest_clips(rng)
    else:
        n = a.name.lower()
        pos = kokoro_clips([n, f"hey {n}", f"{n}."])
        neg = kokoro_clips(confusables(n) + ["what's the weather", "turn it off", "good morning",
                                             "I'm going out", "can you hear me"])
        if a.positives_dir:
            pos += load_dir(a.positives_dir)
        if a.negatives_dir:
            neg += load_dir(a.negatives_dir)
    neg += [rng.normal(0, s, int(CLIP_S * RATE)).astype("float32") for s in (0.001, 0.01, 0.05) for _ in range(5)]
    pos = [augment(c, rng) for c in pos for _ in range(a.augment)]
    neg = [augment(c, rng) for c in neg for _ in range(max(1, a.augment // 2))]
    print(f"{len(pos)} positive and {len(neg)} negative clips; computing features...")
    xp, xn = embed(pos), embed(neg)
    params, acc = train(xp, xn, a.epochs)
    export_onnx(params, a.out, a.name)
    print(f"training accuracy {acc:.2f}; wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
