import yaml

from conftest import MockModelServer
from tau import detect, okf
from tau.brain import Brain
from tau.profile import check_profile, load_profile
from tau.setup import ScriptedAsker, Setup


def _no_defaults(monkeypatch):
    # keep the test from probing real well-known ports on the test machine
    monkeypatch.setattr(detect, "default_engine_hosts", lambda: {})
    monkeypatch.setattr(detect, "agent_clis", lambda: [])
    from tau import engines
    monkeypatch.setattr(engines, "ollama_running", lambda *a, **k: False)


def _fake_discovery(lab_dir, machine_name=None):
    from tau.discovery.labmap import LabMap

    snap = {"platform": "Linux", "machine": {"name": machine_name or "m", "os": "Linux", "gpus": []},
            "services": [], "ports": [], "drives": [], "containers": []}
    LabMap(lab_dir).write_snapshot(snap)
    return snap


def test_setup_detects_mock_server_and_key_and_saves_working_brain(tau_home, monkeypatch, mock_server):
    _no_defaults(monkeypatch)
    env = {"OPENAI_API_KEY": "sk-test-not-real"}
    found = detect.find_brains([mock_server.url], env)
    kinds = {(f.kind, f.engine) for f in found}
    assert ("local", "openai_compat") in kinds and ("cloud", "openai") in kinds
    # answers: look=yes, brain=first option (the mock server), name, user, form, hub yes,
    # display corner, (lab asked inside run) neighbours=no, pacing=no
    asker = ScriptedAsker(answers=[True, 0, "TAU", "Sam", "plainly", True, None, False, False])
    s = Setup(asker, env=env, extra_urls=[mock_server.url], discover=_fake_discovery)
    pf = s.run()
    data = load_profile(pf)
    assert check_profile(data) == []
    assert data["brain"]["local"]["url"].startswith(mock_server.url)
    assert data["brain"]["local"]["model"] == "mock-model"
    assert any(r["method"] == "POST" for r in mock_server.requests), "a real test call was made"
    assert "sk-test-not-real" not in pf.read_text()
    # brain folder created as valid OKF
    b = data["tau"]["brain_dir"]
    rep = okf.validate(__import__("pathlib").Path(b))
    assert rep.ok, rep.errors
    for f in ("journal.md", "user.md", "rules.md", "workspaces.md", "components.md", "index.md", "log.md"):
        assert (__import__("pathlib").Path(b) / f).exists(), f


def test_setup_refuses_brain_that_fails_test_call(tau_home, monkeypatch):
    _no_defaults(monkeypatch)
    bad = MockModelServer(fail=True)
    try:
        asker = ScriptedAsker(answers=[True, 0])  # pick the (failing) server; then defaults
        s = Setup(asker, env={}, extra_urls=[bad.url], discover=_fake_discovery)
        pf = s.run()
        data = load_profile(pf)
        assert data["brain"]["local"]["url"] is None
        assert any("won't save it" in x for x in asker.said)
    finally:
        bad.close()


def test_pasted_key_goes_to_env_file_not_profile(tau_home, monkeypatch, mock_server):
    _no_defaults(monkeypatch)
    # options with nothing found: [install, url, paste, again, skip] -> pick paste (index 2)
    asker = ScriptedAsker(answers=[False, 2, mock_server.url + "/v1", "MY_KEY_VAR", "sk-secret-123",
                                   "cloud-model"])
    s = Setup(asker, env={}, discover=_fake_discovery)
    pf = s.run()
    text = pf.read_text()
    assert "sk-secret-123" not in text
    data = yaml.safe_load(text)
    assert data["brain"]["burst"]["api_key_env"] == "MY_KEY_VAR"
    assert "sk-secret-123" in (tau_home / ".env").read_text()


def test_spoken_correction_lands_in_rules(tau_home):
    b = Brain(tau_home / "tau_brain")
    b.ensure()
    b.record_correction("never delete anything without asking me first")
    b.record_correction("call me Sam, not sir")
    assert "never delete anything without asking me first" in b.read("rules")
    assert "call me Sam" in b.read("user")
    assert okf.validate(tau_home / "tau_brain").ok
    # user edits are authoritative: TAU inserts into the edited file, never overwrites it
    p = tau_home / "tau_brain" / "rules.md"
    p.write_text(p.read_text() + "\n- my own hand-written rule\n")
    b.add_rule("use the local model first")
    t = p.read_text()
    assert "my own hand-written rule" in t and "use the local model first" in t


# -- round 3: the brain step always ends with a working brain or a clear skip (bugs 6, 9) ----------
def _brain_setup(asker, find, **kw):
    s = Setup(asker, env={}, find_brains=find, discover=_fake_discovery, **kw)
    s.topic_brain()
    return s


def test_local_option_is_never_a_dead_end(tau_home, monkeypatch, mock_server):
    _no_defaults(monkeypatch)
    calls = []

    def install(say):
        calls.append("install")
        return True

    def pull(tag, say, url=None):
        calls.append(tag)
        return True

    from tau import engines
    monkeypatch.setattr(engines, "ollama_url", lambda: mock_server.url)
    asker = ScriptedAsker(answers=[0, True])        # pick "Install Ollama...", then yes
    s = _brain_setup(asker, lambda *a: [], install_ollama=install, pull_model=pull)
    menu = asker.menus[0]
    assert menu["choices"][0].startswith("Install Ollama and a small model")
    assert menu["recommended"] == menu["choices"][0] and menu["why"]
    assert "GB" in menu["choices"][0]
    assert any("roughly" in x and "minutes" in x for x in asker.said)    # size and time said first
    assert calls[0] == "install" and calls[1].startswith("qwen3:")
    assert s.values["LOCAL_ENGINE"] == "ollama" and s.values["LOCAL_MODEL"] == calls[1]
    joined = " ".join(asker.said).lower()
    assert "jarvis init" not in joined and "run the" not in joined


def test_declining_the_install_removes_the_recommendation_and_skip_is_clear(tau_home, monkeypatch):
    _no_defaults(monkeypatch)
    asker = ScriptedAsker(answers=[0, False])        # pick install, then say no
    s = _brain_setup(asker, lambda *a: [], install_ollama=lambda say: 1 / 0)
    assert asker.menus[1]["recommended"] is None      # second round: no reason to push anything
    assert "LOCAL_LLM_URL" not in s.values
    assert any("Skipped" in x for x in asker.said)


def test_check_again_reprobes_and_finds_a_server_started_meanwhile(tau_home, monkeypatch, mock_server):
    _no_defaults(monkeypatch)
    rounds = []

    def find(*a):
        rounds.append(1)
        return [] if len(rounds) == 1 else detect.find_brains([mock_server.url], {})

    first_choices = []
    asker = ScriptedAsker(answers=["Check again (pick this after you start a model server)", 0])
    s = _brain_setup(asker, find)
    assert len(rounds) == 2
    assert asker.menus[1]["choices"][0].startswith("Use the model server I found:")
    assert "(1 model)" in asker.menus[1]["choices"][0]
    assert s.values["LOCAL_LLM_URL"].startswith(mock_server.url)


def test_model_list_is_sorted_by_fit_not_alphabet(tau_home, monkeypatch):
    _no_defaults(monkeypatch)
    srv = MockModelServer(models=("aardvark-70b", "llama-8b-instruct", "tiny-0.5b"))
    try:
        asker = ScriptedAsker(answers=[0])           # the server; then Enter on the model menu
        s = Setup(asker, env={}, extra_urls=[srv.url], discover=_fake_discovery)
        s.values.update({"GPU_VRAM_GB": 8, "RAM_GB": 32})
        s.topic_brain()
        server_menu, model_menu = asker.menus[0], asker.menus[1]
        assert "(3 models)" in server_menu["choices"][0]
        assert model_menu["choices"][0].startswith("llama-8b-instruct")
        assert model_menu["recommended"] == model_menu["choices"][0]
        assert "8 GB" in model_menu["why"]
        assert s.values["LOCAL_MODEL"] == "llama-8b-instruct"     # not the alphabetical first
    finally:
        srv.close()


def test_console_tags_recommended_only_with_a_reason(monkeypatch, capsys):
    from tau.setup import ConsoleAsker

    inputs = iter(["", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": (print(prompt, end=""), next(inputs))[1])
    a = ConsoleAsker()
    assert a.ask("Corner?", "top-right", ["top-right", "top-left"]) == "top-right"
    out = capsys.readouterr().out
    assert "recommended" not in out and "Enter picks 1" in out
    assert a.ask("Brain?", "b", ["a", "b"], why="it's running here") == "b"
    out = capsys.readouterr().out
    assert "2. b   <- recommended: it's running here" in out and "1. a\n" in out
