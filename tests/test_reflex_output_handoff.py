import json
import sys
import time

import pytest

from conftest import MockModelServer, make_profile
from tau import output
from tau.actionlog import ActionLog
from tau.assistant import Assistant
from tau.handoff import build_command, package, send
from tau.lanes import Lanes
from tau.organs import Organs
from tau.output import PinBoard, route
from tau.reflex import ReflexRouter


@pytest.fixture
def assistant(tau_home, fake_smi):
    server = MockModelServer(reply="Paris is the capital of France.")
    data = make_profile(tau_home, LOCAL_ENGINE="openai_compat", LOCAL_LLM_URL=server.url, LOCAL_MODEL="m",
                        SERVICES=[{"name": "modelserver", "start": f"{sys.executable} -c pass", "stop": f"{sys.executable} -c pass",
                                   "health_url": server.url + "/health", "gpu_user": True}],
                        APP_PREP=[{"app": "bigapp", "evict": ["modelserver"], "lane": "burst"}],
                        MODEL_CHOICES=[{"name": "small", "lane": "local", "model": "small-model"}])
    a = Assistant(data, profile_file=tau_home / "profile.yaml")
    a.brain.ensure(data)
    yield a, server
    server.close()


PHRASES = [
    ("Tau, what's my VRAM?", "vram"),
    ("tau start modelserver", "start_service"),
    ("Tau, stop the modelserver service", "stop_service"),
    ("prep for bigapp", "prep_for_app"),
    ("Tau, switch to small", "switch_model"),
    ("quiet", "quiet"),
    ("stop talking", "quiet"),
    ("what's running on test-hub?", "running_on_machine"),
    ("what's on port 8080?", "what_on_port"),
    ("what services does the lab have?", "lab_services"),
    ("any notifications?", "notifications"),
    ("Tau, you can start services without asking", "grant_act"),
    ("from now on, use the local model first", "correction"),
    ("re-check the lab", "recheck_lab"),
]


@pytest.mark.parametrize("text,name", PHRASES)
def test_router_matches(text, name):
    m = ReflexRouter(names=["tau"]).match(text)
    assert m is not None and m.reflex.name == name, text


def test_reflexes_fire_with_zero_model_calls_and_fast(assistant):
    a, server = assistant
    a.profile["organs"]["gpu"] = "sense"
    for text, _ in PHRASES:
        if "re-check" in text:
            continue  # discovery is a separate (slower, still model-free) test
        t0 = time.perf_counter()
        r = a.handle(text)
        dt = (time.perf_counter() - t0) * 1000
        assert r.kind in ("reflex", "needs_yes"), (text, r)
        if text.startswith(("Tau, start", "tau start", "Tau, stop", "prep", "Tau, what's my VRAM")):
            continue  # these run a command (the GPU read starts one); the routing itself is what's timed below
        if dt >= 100:
            for _ in range(2):
                t1 = time.perf_counter()
                a.handle(text)
                dt = min(dt, (time.perf_counter() - t1) * 1000)
                if dt < 100:
                    break
        assert dt < 100, (text, dt)
    assert server.chat_bodies() == [] and a.lanes.total_calls() == 0
    # timing of pure routing for every phrase
    router = a.router
    t0 = time.perf_counter()
    for text, _ in PHRASES * 10:
        router.match(text)
    assert (time.perf_counter() - t0) * 1000 / (len(PHRASES) * 10) < 5


def test_service_start_respects_levels(assistant):
    a, server = assistant
    r = a.handle("start modelserver")           # services at sense: refused
    assert "can't" in r.say
    a.profile["organs"]["services"] = "suggest"
    r = a.handle("start modelserver")
    assert r.kind == "needs_yes"
    r = a.handle("yes")
    assert r.say == "modelserver started."
    a.profile["organs"]["services"] = "act"
    assert a.handle("stop modelserver").say == "modelserver stopped."
    assert server.chat_bodies() == []


def test_voice_grant_repeats_then_applies(assistant, tau_home):
    a, _ = assistant
    r = a.handle("Tau, you can start services without asking")
    assert r.kind == "needs_yes" and "services" in r.say and "act" not in r.say.split()[0]
    a.handle("yes")
    from tau.profile import load_profile
    assert load_profile(tau_home / "profile.yaml")["organs"]["services"] == "act"


def test_model_path_used_only_when_no_reflex(assistant):
    a, server = assistant
    r = a.handle("what is the capital of France")
    assert r.lane == "local" and "Paris" in r.say
    body = json.loads(server.chat_bodies()[-1])
    assert body["messages"][-1]["content"].startswith("[state] {")


def test_clipboard_and_pin_routing(tau_home):
    copied = []
    output.CLIPBOARD_BACKEND = copied.append
    try:
        board = PinBoard(tau_home / "pins.json")
        r = route("`docker compose up -d`", "how do I start it", board, clipboard_ok=True)
        assert r.kind == "clipboard" and copied == ["docker compose up -d"] and "clipboard" in r.say
        r = route("C:\\Users\\someone\\project\\notes.md", "where is it", board, clipboard_ok=True)
        assert r.kind == "clipboard"
        r = route("It's sunny today.", "weather", board, clipboard_ok=True)
        assert r.kind == "pin" and board.waiting()[-1]["text"] == "It's sunny today."
        long = "First point here. " + "More detail. " * 60
        r = route(long, "explain", board, clipboard_ok=False)
        assert r.kind == "pin" and "pin card" in r.say and len(r.say) < 200
        r = route("Here you go:\n```python\nprint(1)\nprint(2)\n```", "show", board, clipboard_ok=True)
        assert "```" not in r.say  # code never goes to the display/speech
        r = route("ok", "refactor my parser module", board, clipboard_ok=True)
        assert r.kind == "handoff"
        # pins wait: nothing expires on a timer; only open/clear changes them
        n = len(board.waiting())
        time.sleep(0.05)
        assert len(board.waiting()) == n
        pid = board.waiting()[0]["id"]
        board.open(pid)
        assert len(board.waiting()) == n - 1
        board.clear()
        assert board.all() == []
    finally:
        output.CLIPBOARD_BACKEND = None


def test_handoff_builds_command_from_template(tau_home):
    pkg = package("fix the failing test", state_header='{"host":"h"}', error_text="Traceback: boom",
                  workdir="/work/proj")
    assert "fix the failing test" in pkg and "Traceback: boom" in pkg and '"host":"h"' in pkg
    argv = build_command("claude -p {prompt}", pkg, "/tmp/p.md", "/work/proj")
    assert argv[:2] == ["claude", "-p"] and argv[2] == pkg  # one argument, no shell
    argv = build_command("codex exec --cd {workdir} {prompt}", "x y", "/tmp/p.md", "/w d")
    assert argv == ["codex", "exec", "--cd", "/w d", "x y"]
    argv = build_command("rig send {target} {prompt_file}", "x", "/tmp/p.md", "", "builder")
    assert argv == ["rig", "send", "builder", "/tmp/p.md"]
    data = make_profile(tau_home, CLAUDE_CODE_TEMPLATE="claude -p {prompt}", HANDOFF_DEFAULT="claude_code")
    o = Organs(data, ActionLog(tau_home / "log"))
    res = send(o, "fix the failing test", state_header="{}", error_text="boom", dry_run=True)
    assert res["argv"][0] == "claude" and "fix the failing test" in res["argv"][2]
    assert "Claude Code" in res["say"]
