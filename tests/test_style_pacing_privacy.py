import json
from pathlib import Path

from conftest import MockModelServer, make_profile
from tau import paths
from tau.assistant import Assistant
from tau.pacing import PacingRules, overwhelm_match, save_reasons
from tau.postcheck import banned_hits, check_reply, load_banned, style_guard
from tau.setup import ScriptedAsker, Setup

BANNED = paths.shipped_file("banned_words.example.txt")


def test_style_guard_catches_banned_word_and_rewrites_once():
    banned = load_banned(BANNED)
    assert "leverage" in banned and "seamless" in banned
    text = "We can leverage the cache. The rest is fine."
    assert banned_hits(text, banned) == ["leverage"]
    calls = []

    def rewrite(sentence, hits):
        calls.append(sentence)
        return "We can use the cache."

    res = style_guard(text, banned, rewrite)
    assert res.hits == ["leverage"] and res.rewrites == 1 and len(calls) == 1
    assert res.text == "We can use the cache. The rest is fine."
    # a rewrite that still hits falls back to the local fix (never a second model call)
    res = style_guard("It is a seamless and robust setup.", banned, lambda s, h: "Still seamless.")
    assert res.rewrites == 1 and not banned_hits(res.text, banned)
    # whole words only
    assert banned_hits("the realmless thing", ["realm"]) == []


def test_pacing_post_check_enforces_length_lists_numbers():
    r = PacingRules(enabled=True, max_paragraphs=2, lists_allowed=False, sparse_numbers=True)
    text = "First answer.\n\n- one\n- two\n\nThird para with 12345 items and 3.14159 ratio.\n\nFourth."
    out = check_reply(text, [], r)
    assert "- one" not in out.text and "One; two." in out.text
    assert out.text.count("\n\n") <= 1
    assert "12345" in out.overflow or "Fourth" in out.overflow
    hard = PacingRules(enabled=True, hard_day=True)
    out = check_reply("The server is down. You could also restart the router. Maybe check cables.", [], hard)
    assert out.text == "The server is down."


def test_overwhelm_signals_match_users_words():
    sig = ["sudden caps", "swearing"]
    assert overwhelm_match("WHY IS THIS STILL BROKEN", sig) == "sudden caps"
    assert overwhelm_match("this is shit", sig) == "swearing"
    assert overwhelm_match("thanks, that helps", sig) is None


def _assistant_with_pacing(home, server, cloud_choice="keep"):
    data = make_profile(home, BURST_LLM_URL=server.url, BURST_MODEL="cloud-model", BURST_API_KEY_ENV="X",
                        BRAIN_PREFER="burst", IDENTITY_FILES=[])
    data["pacing"].update({"enabled": True, "max_paragraphs": 3, "lists_allowed": False,
                           "overwhelm_signals": ["sudden caps"], "cloud_choice": cloud_choice})
    a = Assistant(data, profile_file=home / "profile.yaml")
    a.brain.ensure(data)
    return a


def test_reasons_never_appear_in_any_outgoing_request(tau_home):
    server = MockModelServer(reply="Sure. That is done.")
    try:
        a = _assistant_with_pacing(tau_home, server)
        secret_reason = "Because of my fibromyalgia diagnosis and long-COVID fatigue"
        save_reasons(a.brain.root, secret_reason)
        a.brain.add_rule("keep replies under three paragraphs")
        for text in ("how do I check disk space", "WHY IS THIS STILL SO SLOW", "explain what a pin card is"):
            a.handle(text)
        bodies = server.chat_bodies()
        assert bodies, "the cloud lane was called"
        everything = "\n".join(bodies)
        for fragment in ("fibromyalgia", "long-COVID", "diagnosis", "pacing_reasons", secret_reason):
            assert fragment not in everything
        # the neutral rules do go out (the user was told they would)
        assert "Do not use bulleted or numbered lists" in everything
        # the overwhelm instruction went with the message after the caps
        assert any("too fast, too dense or unexpected" in json.loads(b)["messages"][0]["content"] for b in bodies)
        # the reasons file exists, locally, as valid OKF in private/
        assert (a.brain.root / "private" / "pacing_reasons.md").exists()
    finally:
        server.close()


def test_local_only_choice_keeps_requests_off_the_cloud(tau_home):
    cloud, local = MockModelServer(reply="cloud"), MockModelServer(reply="local")
    try:
        data = make_profile(tau_home, BURST_LLM_URL=cloud.url, BURST_MODEL="c", BURST_API_KEY_ENV="X",
                            LOCAL_ENGINE="openai_compat", LOCAL_LLM_URL=local.url, LOCAL_MODEL="l",
                            BRAIN_PREFER="burst")
        data["pacing"].update({"enabled": True, "cloud_choice": "local_only"})
        a = Assistant(data, profile_file=tau_home / "profile.yaml")
        a.brain.ensure(data)
        r = a.handle("tell me a fact")
        assert r.lane == "local" and cloud.chat_bodies() == []
    finally:
        cloud.close()
        local.close()


def test_cloud_notice_shown_at_setup_and_choice_recorded(tau_home, monkeypatch, mock_server):
    from tau import detect
    monkeypatch.setattr(detect, "default_engine_hosts", lambda: {})
    monkeypatch.setattr(detect, "agent_clis", lambda: [])
    env = {"OPENAI_API_KEY": "sk-not-real"}
    # look=no; brain: options are [openai key, oj_install, paste, skip] -> 0, model id;
    # names x3; hub yes; corner default; hands-free no; starter senses yes;
    # pacing yes + 9 answers + signals + reasons; cloud choice index 1
    answers = [False, 0, "gpt-test", "TAU", "Sam", "plainly", True, None, False, True,
               True, 2, False, True, False, True, False, True, True, True, "sudden caps",
               "my own private reason", 1]
    asker = ScriptedAsker(answers=answers)
    monkeypatch.setitem(detect.CLOUD_KEYS, "OPENAI_API_KEY", ("openai", mock_server.url + "/v1"))
    pf = Setup(asker, env=env).run()
    assert any("never sent" in s for s in asker.said)
    import yaml
    data = yaml.safe_load(pf.read_text())
    assert data["pacing"]["enabled"] and data["pacing"]["cloud_choice"] == "local_only"
    assert data["pacing"]["max_paragraphs"] == 2 and data["pacing"]["lists_allowed"] is False
    rules = (Path(data["tau"]["brain_dir"]) / "rules.md").read_text()
    assert "local_only" in rules
    assert "my own private reason" not in pf.read_text()
    assert "my own private reason" in (Path(data["tau"]["brain_dir"]) / "private" / "pacing_reasons.md").read_text()
