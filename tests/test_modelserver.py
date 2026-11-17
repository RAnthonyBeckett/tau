"""Tests for model server remembering, detection and starting."""

import pytest
import yaml
from pathlib import Path

from conftest import make_profile
from tau import modelserver
from tau.assistant import Assistant
from tau.brainclient import BrainError


def test_holds_secret():
    assert not modelserver.holds_secret(["ollama", "serve"])
    assert not modelserver.holds_secret(["llama-server", "-m", "model.gguf", "--port", "8080"])
    assert not modelserver.holds_secret([])

    assert modelserver.holds_secret(["serve", "--api-key", "secret123"])
    assert modelserver.holds_secret(["serve", "--token=xyz"])
    assert modelserver.holds_secret(["serve", "--secret", "abc"])
    assert modelserver.holds_secret(["serve", "--password", "pwd"])
    assert modelserver.holds_secret(["serve", "--passwd", "pwd"])
    assert modelserver.holds_secret(["serve", "--auth", "basic"])
    assert modelserver.holds_secret(["serve", "Bearer", "token"])


def test_remember_stores_and_writes_profile(tmp_path):
    prof_file = tmp_path / "profile.yaml"
    prof_file.write_text("machine:\n  name: my-hub\nbrain:\n  local:\n    url: http://127.0.0.1:8080\n")
    profile = {
        "machine": {"name": "my-hub"},
        "brain": {"local": {"url": "http://127.0.0.1:8080"}}
    }
    owner = lambda port: {"command": ["my-server", "--port", "8080"], "cwd": "/some/cwd"}
    probe = lambda engine, url, **kw: object()
    now = lambda: "2026-11-16T12:00:00"

    ok = modelserver.remember(profile, path=prof_file, owner=owner, probe=probe, now=now)
    assert ok
    stored = profile["brain"]["local"]["start"]
    assert stored["command"] == ["my-server", "--port", "8080"]
    assert stored["cwd"] == "/some/cwd"
    assert stored["seen"] == "2026-11-16T12:00:00"

    saved = yaml.safe_load(prof_file.read_text())
    assert saved["machine"]["name"] == "my-hub"
    assert saved["brain"]["local"]["start"] == stored

    # Second time for same command returns False
    ok_second = modelserver.remember(profile, path=prof_file, owner=owner, probe=probe, now=now)
    assert not ok_second


def test_remember_rejects_secret_or_unreachable_or_remote():
    owner_sec = lambda port: {"command": ["my-server", "--api-key", "secret"], "cwd": None}
    probe_ok = lambda engine, url, **kw: object()
    prof_sec = {"brain": {"local": {"url": "http://127.0.0.1:8080"}}}
    assert not modelserver.remember(prof_sec, owner=owner_sec, probe=probe_ok)

    owner_ok = lambda port: {"command": ["my-server"], "cwd": None}
    prof_remote = {"brain": {"local": {"url": "http://example.com:8080"}}}
    assert not modelserver.remember(prof_remote, owner=owner_ok, probe=probe_ok)

    prof_local = {"brain": {"local": {"url": "http://127.0.0.1:8080"}}}
    probe_down = lambda engine, url, **kw: None
    assert not modelserver.remember(prof_local, owner=owner_ok, probe=probe_down)


def test_port_owner():
    class FakeConn:
        def __init__(self, port, pid, status="LISTEN"):
            self.laddr = type("Addr", (), {"port": port})()
            self.pid = pid
            self.status = status

    class FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def cmdline(self):
            return ["my-llama", "--port", "9000"]

        def cwd(self):
            return "/home/user/models"

    conns = [
        FakeConn(8080, 101, status="LISTEN"),
        FakeConn(9000, 202, status="LISTEN"),
        FakeConn(9001, 303, status="ESTABLISHED"),
    ]
    owner = modelserver.port_owner(9000, connections=lambda **kw: conns, process=FakeProc)
    assert owner == {"command": ["my-llama", "--port", "9000"], "cwd": "/home/user/models"}

    assert modelserver.port_owner(7777, connections=lambda **kw: conns, process=FakeProc) is None

    class UnreadableProc:
        def __init__(self, pid):
            pass

        def cmdline(self):
            raise OSError("access denied")

        def cwd(self):
            return None

    assert modelserver.port_owner(9000, connections=lambda **kw: conns, process=UnreadableProc) is None


def test_find_way():
    profile_rem = {
        "brain": {
            "local": {
                "engine": "ollama",
                "start": {"command": ["my-runner", "--model", "qwen"], "cwd": "/run"}
            }
        }
    }
    way_rem = modelserver.find_way(profile_rem, which=lambda name: "/bin/my-runner" if name == "my-runner" else None)
    assert way_rem is not None
    assert way_rem.source == "remembered"
    assert way_rem.label == "the way it was started last time"
    assert way_rem.command == ["my-runner", "--model", "qwen"]
    assert way_rem.cwd == "/run"

    # Command whose program is gone falls through
    way_fall = modelserver.find_way(profile_rem, which=lambda name: None, ollama=lambda: "/opt/ollama")
    assert way_fall is not None
    assert way_fall.source == "ollama"
    assert way_fall.label == "Ollama"
    assert way_fall.command == ["/opt/ollama", "serve"]

    # Ollama engine
    profile_ollama = {"brain": {"local": {"engine": "ollama"}}}
    way_ollama = modelserver.find_way(profile_ollama, which=lambda name: None, ollama=lambda: "/usr/bin/ollama")
    assert way_ollama is not None
    assert way_ollama.source == "ollama"
    assert way_ollama.command == ["/usr/bin/ollama", "serve"]

    # LM Studio engine
    profile_lms = {"brain": {"local": {"engine": "lmstudio"}}}
    way_lms = modelserver.find_way(profile_lms, which=lambda name: "/usr/local/bin/lms" if name == "lms" else None)
    assert way_lms is not None
    assert way_lms.source == "lmstudio"
    assert way_lms.command == ["/usr/local/bin/lms", "server", "start"]

    # None
    profile_none = {"brain": {"local": {"engine": "other"}}}
    assert modelserver.find_way(profile_none, which=lambda name: None) is None


def test_seen_but_unknown():
    profile = {"brain": {"local": {"engine": "other"}}}
    which_llama = lambda name: "/bin/llama-server" if name == "llama-server" else None
    assert modelserver.seen_but_unknown(profile, which=which_llama) == "llama.cpp's server"

    which_ollama = lambda name: "/bin/ollama" if name == "ollama" else None
    assert modelserver.seen_but_unknown(profile, which=which_ollama) == "Ollama"

    which_lms = lambda name: "/bin/lms" if name == "lms" else None
    assert modelserver.seen_but_unknown(profile, which=which_lms) == "LM Studio"

    assert modelserver.seen_but_unknown(profile, which=lambda name: None) is None


def test_start():
    way = modelserver.Way("Ollama", ["/bin/ollama", "serve"], cwd="/work", source="ollama")
    profile = {"brain": {"local": {"url": "http://127.0.0.1:11434"}}}

    popen_calls = []

    def popen(cmd, cwd=None, **kw):
        popen_calls.append({"cmd": cmd, "cwd": cwd})
        return type("Proc", (), {"poll": lambda self: None})()

    checks = {"n": 0}

    def is_up():
        checks["n"] += 1
        return checks["n"] >= 3

    ok = modelserver.start(way, profile, popen=popen, is_up=is_up, sleep=lambda s: None, poll=1.0, wait_s=10.0)
    assert ok
    assert popen_calls[0]["cmd"] == ["/bin/ollama", "serve"]
    assert popen_calls[0]["cwd"] == "/work"

    ok_fail = modelserver.start(way, profile, popen=popen, is_up=lambda: False, sleep=lambda s: None, wait_s=0.001)
    assert not ok_fail

    popen_empty = []
    ok_up = modelserver.start(way, profile, popen=lambda *a, **k: popen_empty.append(1), is_up=lambda: True)
    assert ok_up
    assert not popen_empty


def _make_assistant(tau_home, services_level="suggest"):
    data = make_profile(tau_home, LOCAL_LLM_URL="http://127.0.0.1:8080", LOCAL_MODEL="test-model")
    data["organs"]["services"] = services_level
    asst = Assistant(data, profile_file=tau_home / "profile.yaml")
    return asst


def test_assistant_suggest_confirm_and_decline(tau_home, monkeypatch):
    asst = _make_assistant(tau_home, services_level="suggest")
    way = modelserver.Way("Ollama", ["/bin/ollama", "serve"], source="ollama")

    monkeypatch.setattr("tau.modelserver.reachable", lambda prof: False)
    monkeypatch.setattr("tau.modelserver.find_way", lambda prof: way)

    def fail_chat(*a, **k):
        raise BrainError("connection refused")

    monkeypatch.setattr(asst.lanes, "chat", fail_chat)

    reply = asst.handle("what is my name?")
    assert reply.kind == "needs_yes"
    assert reply.say == "My model server isn't running. I know how to start it: Ollama. Shall I? Say yes or no."
    assert asst.pending_start == {"way": way}
    assert "/bin/ollama" not in reply.say

    start_calls = []

    def fake_start(w, prof, **kw):
        start_calls.append(w)
        return True

    monkeypatch.setattr(modelserver, "start", fake_start)

    conf_reply = asst.handle("yes")
    assert conf_reply.say == "It's running now. Ask me again."
    assert asst.pending_start is None
    assert len(start_calls) == 1
    assert "/bin/ollama" not in conf_reply.say

    asst.pending_start = {"way": way}
    dec_reply = asst.handle("no")
    assert dec_reply.say == "Okay, I won't start it."
    assert asst.pending_start is None
    assert "/bin/ollama" not in dec_reply.say


def test_assistant_no_way_known_program(tau_home, monkeypatch):
    asst = _make_assistant(tau_home, services_level="suggest")

    monkeypatch.setattr("tau.modelserver.reachable", lambda prof: False)
    monkeypatch.setattr("tau.modelserver.find_way", lambda prof: None)
    monkeypatch.setattr("tau.modelserver.seen_but_unknown", lambda prof: "llama.cpp's server")

    def fail_chat(*a, **k):
        raise BrainError("down")

    monkeypatch.setattr(asst.lanes, "chat", fail_chat)

    reply = asst.handle("what is my name?")
    assert reply.kind == "error"
    expected = ("My model server isn't running. I can see llama.cpp's server on this machine, "
                "but I don't know yet how you start it. Start it once the way you usually do. "
                "I'll remember how, and after that I can start it myself.")
    assert reply.say == expected
    assert "llama-server" not in reply.say


def test_assistant_act_starts_and_answers(tau_home, monkeypatch):
    asst = _make_assistant(tau_home, services_level="act")
    way = modelserver.Way("the way it was started last time", ["llama-server", "-m", "model.gguf"], source="remembered")

    monkeypatch.setattr("tau.modelserver.reachable", lambda prof: False)
    monkeypatch.setattr("tau.modelserver.find_way", lambda prof: way)

    start_calls = []

    def fake_start(w, prof, **kw):
        start_calls.append(w)
        return True

    monkeypatch.setattr(modelserver, "start", fake_start)

    chat_calls = {"n": 0}

    def fake_chat(msgs, **kw):
        chat_calls["n"] += 1
        if chat_calls["n"] == 1:
            raise BrainError("server down")
        return "You are Sam.", "local"

    monkeypatch.setattr(asst.lanes, "chat", fake_chat)

    reply = asst.handle("who am I?")
    assert len(start_calls) == 1
    assert reply.say == "You are Sam."
    assert "llama-server" not in reply.say


def test_assistant_reachable_true_returns_old_sentence(tau_home, monkeypatch):
    asst = _make_assistant(tau_home, services_level="act")

    monkeypatch.setattr("tau.modelserver.reachable", lambda prof: True)

    def fail_chat(*a, **k):
        raise BrainError("model not found")

    monkeypatch.setattr(asst.lanes, "chat", fail_chat)

    reply = asst.handle("hello")
    assert reply.kind == "error"
    assert reply.say == ("I couldn't reach my brain. Check the model server is running, then ask again. "
                         "The details are in TAU's log.")


def test_assistant_reflex_start_brain(tau_home, monkeypatch):
    asst = _make_assistant(tau_home, services_level="suggest")

    monkeypatch.setattr("tau.modelserver.reachable", lambda prof: True)
    reply1 = asst.handle("start the model server")
    assert reply1.say == "My model server is already running."

    way = modelserver.Way("Ollama", ["/bin/ollama", "serve"], source="ollama")
    monkeypatch.setattr("tau.modelserver.reachable", lambda prof: False)
    monkeypatch.setattr("tau.modelserver.find_way", lambda prof: way)

    reply2 = asst.handle("start your brain")
    assert reply2.kind == "needs_yes"
    assert "Shall I? Say yes or no." in reply2.say
    assert asst.pending_start == {"way": way}
    assert "/bin/ollama" not in reply2.say
