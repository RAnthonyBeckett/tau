"""Round 7: every Settings row says what it lets me do, with an example; permissions are
explained by action and match tau.organs.base.decide; accessibility preset; voice models."""
import re

from conftest import make_profile


def _settings(tau_home):
    from tau.profile import load_profile
    from tau.settings import Settings

    make_profile(tau_home)
    pf = tau_home / "profile.yaml"
    pf = pf if pf.exists() else next(tau_home.rglob("profile.y*ml"))
    return Settings(load_profile(pf, check=False), profile_file=pf,
                    audio_devices=lambda: {"mics": ["Mic"], "speakers": ["Spk"]}, monitors=lambda: [])


JARGON = re.compile(r"\b(organs?|lanes?|templates?|senses?)\b", re.I)


def test_every_row_explains_itself(tau_home):
    from tau.settings import SECTIONS

    s = _settings(tau_home)
    for sec, _ in SECTIONS:
        for r in s.view(sec)["rows"]:
            assert r["does"].strip() and r["example"].strip(), (sec, r["id"])
            for text in (r["label"], r["does"], r["example"], r.get("help") or ""):
                assert not JARGON.search(text), (sec, r["id"], text)


def test_permission_words_match_enforcement():
    from tau.organs.base import ALLOWED, DENIED, NEEDS_YES, decide
    from tau.settings import LEVEL_ACTIONS, LEVEL_HELP

    assert "can read this" in LEVEL_ACTIONS and decide("sense", "read") == ALLOWED
    assert decide("sense", "change") == DENIED and "never change" in LEVEL_HELP["sense"]
    assert "ask before changing" in LEVEL_ACTIONS and decide("suggest", "change") == NEEDS_YES
    assert "without asking" in LEVEL_ACTIONS and decide("act", "change") == ALLOWED
    assert "still asks" in LEVEL_ACTIONS and decide("act", "change", destructive=True) == NEEDS_YES


def test_accessibility_preset_is_one_switch(tau_home):
    from tau.pacing import PacingRules

    s = _settings(tau_home)
    rows = {r["id"]: r for r in s.view("pacing")["rows"]}
    assert rows["pacing:accessibility"]["kind"] == "switch" and not rows["pacing:accessibility"]["value"]
    assert s.change("pacing", "pacing:accessibility", True)["ok"]
    r = PacingRules.from_profile(s.profile)
    assert r.active
    lines = " ".join(r.prompt_lines())
    assert "first sentence" in lines and "at most 2" in lines and "lists" in lines and "one question" in lines
    s._do_pacing(value=False, arg="accessibility")
    assert not PacingRules.from_profile(s.profile).accessibility


def test_british_voice_fetches_its_pronunciation_model(monkeypatch):
    from tau import kokoro_voice as kv

    calls = []
    monkeypatch.setattr(kv, "voice_on_disk", lambda v, **_: False)
    monkeypatch.setattr(kv, "g2p_on_disk", lambda v: False)
    monkeypatch.setattr(kv, "download_voice", lambda v, **_: calls.append(("voice", v)))
    monkeypatch.setattr(kv, "download_g2p", lambda v: calls.append(("g2p", kv.pronunciation_repo(v))))
    got = kv.ensure_voice("bf_emma")
    assert ("g2p", "PeterReid/graphemes_to_phonemes_en_gb") in calls and ("voice", "bf_emma") in calls
    assert "PeterReid/graphemes_to_phonemes_en_gb" in got
    assert kv.pronunciation_repo("af_heart") == "PeterReid/graphemes_to_phonemes_en_us"


def test_fetch_models_fetches_both_pronunciation_models():
    from tau.fetch import items

    for voice in ("af_heart", "bm_george"):
        repos = {i.repo for i in items(voice)}
        assert {"PeterReid/graphemes_to_phonemes_en_us", "PeterReid/graphemes_to_phonemes_en_gb"} <= repos
