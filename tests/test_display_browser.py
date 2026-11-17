"""Bug 11: use TAU from the display alone. A headless browser (Playwright,
Apache-2.0) loads the real page served by the real runtime, finds every
control, types a message and sees the reply appear on the display."""
import os
from pathlib import Path

import pytest

from conftest import make_profile

if os.environ.get("TAU_REQUIRE_BROWSER") == "1":
    from playwright import sync_api
else:
    sync_api = pytest.importorskip("playwright.sync_api", reason="pip install playwright (Apache-2.0)")


def _launch(p):
    exe = os.environ.get("TAU_TEST_CHROMIUM")
    for cand in ([exe] if exe else []) + [None, "/opt/pw-browsers/chromium-1243/chrome-linux64/chrome"]:
        try:
            kw = {"args": ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]}
            if cand:
                if not Path(cand).exists():
                    continue
                kw["executable_path"] = cand
            return p.chromium.launch(**kw)
        except Exception as exc:  # noqa: BLE001
            last = exc
    if os.environ.get("TAU_REQUIRE_BROWSER") == "1":      # CI: a skip here would hide a missing check
        raise last
    pytest.skip(f"no headless Chromium here ({type(last).__name__}); CI installs one")


@pytest.fixture
def running_tau(tau_home, mock_server):
    from tau.runtime import Runtime

    mock_server.reply = "Hello. I'm ready when you are."
    data = make_profile(tau_home, LOCAL_ENGINE="openai_compat", LOCAL_LLM_URL=mock_server.url + "/v1",
                        LOCAL_MODEL="mock-model", BRAIN_PREFER="local")
    rt = Runtime(data).build(want_voice=False)
    port = rt.display.serve(0)
    rt.display.publish({"mic": rt.mic_info(), "help_seen": False})
    rt.sense_once()
    yield rt, f"http://127.0.0.1:{port}/"
    rt.stop.set()
    rt.display.stop()


def test_display_alone_is_enough(running_tau):
    rt, url = running_tau
    with sync_api.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1100, "height": 820})
        page.goto(url)
        # First run: the help overlay names each control, then a click dismisses it.
        help_box = page.locator("#help")
        help_box.wait_for(state="visible", timeout=15000)
        for name in ("Talk button", "Type box", "Status line", "Rings", "Top corners", "Bottom tip"):
            assert name in help_box.inner_text()
        page.click("#helpok")
        help_box.wait_for(state="hidden")
        for _ in range(100):                     # the page saves it in the background
            if rt._load_display_state().get("help_seen"):
                break
            page.wait_for_timeout(100)
        assert rt._load_display_state().get("help_seen") is True
        # Every control is visible without hovering or knowing a shortcut.
        for sel in ("#talk", "#askin", "#status", "#helpbtn", "#pinbtn", "#convo"):
            assert page.locator(sel).is_visible(), sel
        assert page.locator("#status").inner_text().startswith("Ready.")
        # No microphone in this test: the talk control says so instead of hiding.
        assert page.locator("#talk").inner_text() == "Talk (no microphone)"
        # "?" reopens the help.
        page.click("#helpbtn")
        help_box.wait_for(state="visible")
        page.click("#helpok")
        # Type a message; the reply appears on the display.
        page.fill("#askin", "hello there")
        page.press("#askin", "Enter")
        page.locator(".msg.tau .text", has_text="ready when you are").wait_for(timeout=30000)
        you = page.locator(".msg.you .text").first.inner_text()
        assert you == "hello there"
        # The rings were filled from real data on first run (brain answered, slots labelled).
        state = page.evaluate("window.__tau && window.__tau.ready")
        assert state is True
        browser.close()
    segs = {s["id"]: s for s in rt.display.snapshot()["segments"]}
    assert "answering" in segs["brain"]["label"]
    assert segs["svc:none"]["status"] == "empty" and segs["gpu"]["status"] == "empty"


def test_warning_flash_and_pin_reach_the_page(running_tau):
    rt, url = running_tau
    rt._save_display_state(help_seen=True)
    rt.display.publish({"help_seen": True})
    with sync_api.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1100, "height": 820})
        page.goto(url)
        page.locator("#status").wait_for()
        rt.mic_failed(OSError("no input device"))
        page.locator("#flash", has_text="can't hear the microphone").wait_for(timeout=10000)
        page.locator("#pinbtn.glow", has_text="Pin cards: 1").wait_for(timeout=10000)
        page.click("#pinbtn")
        page.locator("#pins", has_text="How to fix it").wait_for(timeout=10000)
        browser.close()


def test_space_and_talk_button_reach_the_mic_gate(running_tau):
    from test_hearing import FakeGateEars

    rt, url = running_tau
    rt.ears = FakeGateEars()
    rt._save_display_state(help_seen=True)
    rt.display.publish({"help_seen": True, "mic": rt.mic_info()})
    with sync_api.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1100, "height": 820})
        page.goto(url)
        page.locator("#talk", has_text="Talk").wait_for(timeout=10000)
        assert page.locator("#talk").is_enabled()
        page.locator("body").click(position={"x": 5, "y": 120})   # focus the page, not the type box or side tab
        page.keyboard.press("Space")
        page.wait_for_function("() => true")
        for _ in range(50):
            if rt.ears.opened:
                break
            page.wait_for_timeout(100)
        assert rt.ears.opened == 1
        rt.display.publish({"state": "listening"})
        page.locator("#talk", has_text="Listening...").wait_for(timeout=10000)
        assert page.locator("#status").inner_text().startswith("Listening.")
        page.click("#talk")
        for _ in range(50):
            if not rt.ears.gate_open:
                break
            page.wait_for_timeout(100)
        assert not rt.ears.gate_open
        browser.close()
