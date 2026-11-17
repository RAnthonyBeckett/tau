"""Round 4 item 3: the conversation panel is the main surface (both sides, times,
copy buttons, pin cards inline, what's being spoken), and the status line says
what TAU is doing at every moment, including waiting for a model to load."""
import os
import time
from pathlib import Path

from test_display_browser import _launch, running_tau, sync_api  # noqa: F401  (fixture)

SHOTS = os.environ.get("TAU_SCREENSHOTS")


def test_status_line_for_every_state(tau_home):
    from conftest import make_profile
    from tau.display.model import status_line

    p = make_profile(tau_home, LOCAL_MODEL="gemma-4-26B", LOCAL_LLM_URL="http://model-server/v1", BRAIN_PREFER="local")
    assert status_line(p, {"state": "listening"}).startswith("Listening")
    assert status_line(p, {"state": "transcribing"}).startswith("Working out what you said")
    assert status_line(p, {"state": "thinking"}).startswith("Thinking with gemma-4-26B")
    assert status_line(p, {"state": "thinking", "wait_text": "Loading gemma-4-26B, about a minute."}) \
        .startswith("Loading gemma-4-26B, about a minute")
    assert 'Speaking: "Hello there."' in status_line(p, {"state": "speaking", "speaking_text": "Hello there."})
    assert status_line(p, {"state": "idle"}).startswith("Ready")


def test_model_load_wait_is_said(tau_home):
    from conftest import make_profile
    from tau.assistant import Assistant
    from tau.display.server import DisplayHub

    p = make_profile(tau_home, LOCAL_MODEL="gemma-4-26B", LOCAL_LLM_URL="http://model-server/v1", BRAIN_PREFER="local")
    hub = DisplayHub(p)
    a = Assistant(p, display=hub)
    a.LOAD_CHECK_S, a.STILL_WAITING_S = 0.05, 0.3
    a._model_loaded = lambda lane, model: False
    done = a._watch_load("local", "gemma-4-26B")
    for _ in range(40):
        if hub.live.get("wait_text"):
            break
        time.sleep(0.05)
    done.set()
    assert hub.live["wait_text"].startswith("Loading gemma-4-26B, about a minute")
    # the server says whether a model is in memory (llama.cpp router "status", Ollama /api/ps)
    from tau.detect import _status_loaded

    assert _status_loaded({"status": {"value": "unloaded"}}) is False
    assert _status_loaded({"status": {"value": "loaded"}}) is True and _status_loaded({}) is None


def test_conversation_from_the_page(running_tau):
    rt, url = running_tau
    rt._save_display_state(help_seen=True)
    rt.display.publish({"help_seen": True})
    with sync_api.sync_playwright() as p:
        browser = _launch(p)
        ctx = browser.new_context(viewport={"width": 1366, "height": 860})
        try:
            ctx.grant_permissions(["clipboard-read", "clipboard-write"])
        except Exception:
            pass
        page = ctx.new_page()
        page.goto(url)
        page.fill("#askin", "hello there")
        page.press("#askin", "Enter")
        page.locator(".msg.tau .text", has_text="ready when you are").first.wait_for(timeout=30000)
        msgs = page.locator("#msgs .msg")
        assert msgs.count() >= 2
        for i in range(msgs.count()):                       # both sides, each with a time and a Copy button
            m = msgs.nth(i)
            assert m.locator(".when").inner_text().strip(), m.inner_html()
            assert m.locator("button.copy").is_visible()
        assert page.locator(".msg.you .who").first.inner_text().lower() == "you"
        page.locator(".msg.tau button.copy").first.click()
        page.locator(".msg.tau button.copy", has_text="Copied").wait_for(timeout=20000)
        # a pin card appears inline, where it was made
        rt.assistant.pins.pin("Microphone isn't working", "How to fix it: plug it in.", kind="fix")
        page.locator(".msg.pin", has_text="How to fix it").wait_for(timeout=20000)
        assert "Pin card: Microphone isn't working" in page.locator(".msg.pin").inner_text()
        # every state shows on the status line
        rt.display.publish({"state": "listening"})
        page.locator("#status", has_text="Listening").wait_for(timeout=20000)
        rt.display.publish({"state": "transcribing"})
        page.locator("#status", has_text="Working out what you said").wait_for(timeout=20000)
        rt.display.publish({"state": "thinking", "model": "mock-model"})
        page.locator("#status", has_text="Thinking with mock-model").wait_for(timeout=20000)
        rt.display.publish({"wait_text": "Loading mock-model, about a minute."})
        page.locator("#status", has_text="Loading mock-model, about a minute").wait_for(timeout=20000)
        rt.display.publish({"state": "speaking", "wait_text": None, "speaking_text": "Hello. I'm ready when you are."})
        page.locator("#status", has_text="Speaking:").wait_for(timeout=20000)
        page.locator(".msg.speaking", has_text="ready when you are").wait_for(timeout=20000)
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(SHOTS) / "tau_r4_conversation.png"))
        rt.display.publish({"state": "idle", "speaking_text": None})
        page.locator("#status", has_text="Ready").wait_for(timeout=20000)
        assert page.locator(".msg.speaking").count() == 0
        browser.close()
