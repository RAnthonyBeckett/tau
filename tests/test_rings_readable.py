"""Round 4 item 2: every ring segment shows its name and current value as readable
text (round 5: in a slide-out panel, with no numbers on the rings); "off" pieces open their Settings
section; a click on any piece opens a details card; "?" names every control."""
import os
from pathlib import Path

from conftest import make_profile
from test_display_browser import _launch, running_tau, sync_api  # noqa: F401  (fixture)

SHOTS = os.environ.get("TAU_SCREENSHOTS")


def test_segments_carry_name_value_and_settings(tau_home):
    from tau.display.model import OFF_VALUE, build_segments

    data = make_profile(tau_home, SERVICES=[{"name": "llama-server", "port": 8080}])
    data["organs"].update({"gpu": "sense", "drives": "sense", "notifications": "sense"})
    live = {"vram_free_gb": 6.1, "vram_total_gb": 24.0, "services": {"llama-server": True}, "notifications": 3,
            "drives": [{"mount": "C:", "free_gb": 41.0, "total_gb": 100.0}], "brain_ok": True}
    segs = {s["id"]: s for s in build_segments(data, live)}
    assert (segs["gpu"]["name"], segs["gpu"]["value"]) == ("GPU", "6.1 GB free")
    assert (segs["svc:llama-server"]["name"], segs["svc:llama-server"]["value"]) == ("llama-server", "up")
    assert (segs["drive:C:"]["name"], segs["drive:C:"]["value"]) == ("Drive C", "41% free (41 GB)")
    assert segs["notifications"]["value"] == "3 recent"
    for s in segs.values():
        assert s["name"] and s["value"], s
    data["organs"]["gpu"] = "off"
    off = {s["id"]: s for s in build_segments(data, live)}["gpu"]
    assert off["value"] == OFF_VALUE and off["settings"] == "watch" and off["status"] == "empty"


def test_rings_read_from_the_page(running_tau):
    rt, url = running_tau
    rt._save_display_state(help_seen=True)
    rt.display.publish({"help_seen": True})
    with sync_api.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1366, "height": 860})
        page.goto(url)
        page.locator("#rdlist .rd").first.wait_for(state="attached", timeout=30000)
        # round 5 ruling: no numbers on the rings, and What I see is closed until its tab is pressed
        assert page.locator("#badges, .badge").count() == 0
        assert not page.locator("#readings").is_visible()
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            page.wait_for_timeout(800)
            page.screenshot(path=str(Path(SHOTS) / "tau_r5_display_clean.png"))
        page.click("#sidetab")
        page.locator("#readings").wait_for(state="visible")
        segs = [x for x in rt.display.snapshot()["segments"] if not x["id"].startswith("node:")]
        rows = page.locator("#rdlist .rd")
        assert rows.count() == len(segs)
        for i, s in enumerate(segs):                       # name and value written out, no numbers
            t = rows.nth(i).inner_text()
            assert s["name"] in t and s["value"] in t and not t.strip()[:1].isdigit(), (t, s)
        assert "answering" in page.locator('#rdlist .rd[data-seg="brain"]').inner_text()
        for sec in ("Status", "Machines", "Pins", "Recent actions"):
            assert page.locator("#readings summary", has_text=sec).count() == 1, sec
        page.reload()                                      # remembered open
        page.locator("#readings").wait_for(state="visible", timeout=30000)
        # readable: nothing on the display under 14 px
        small = page.evaluate("""() => [...document.querySelectorAll('body *')].filter(e =>
            e.offsetParent !== null && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) &&
            parseFloat(getComputedStyle(e).fontSize) < 14).map(e => e.outerHTML.slice(0, 80))""")
        assert small == []
        if SHOTS:
            page.screenshot(path=str(Path(SHOTS) / "tau_r5_sidebar_open.png"))
        # an "off" piece opens its Settings section
        page.click('#rdlist .rd[data-seg="gpu"]')
        page.locator("#settitle", has_text="What I watch").wait_for(timeout=20000)
        page.click("#setclose")
        # a live piece opens a details card; Check now answers inside it
        page.click('#rdlist .rd[data-seg="brain"]')
        card = page.locator("#card")
        card.wait_for(state="visible")
        assert "Brain" in card.inner_text() and "answering" in card.inner_text()
        if SHOTS:
            page.screenshot(path=str(Path(SHOTS) / "tau_r4_detail_card.png"))
        card.locator("button", has_text="Check now").click()
        page.locator("#card .said", has_text="mock-model").wait_for(timeout=20000)
        card.locator("button", has_text="Close").click()
        page.keyboard.press("Escape")
        card.wait_for(state="hidden")
        # "?" names every control on screen
        page.click("#helpbtn")
        text = page.locator("#help").inner_text()
        for name in ("Status line", "Settings", "Pin cards", "What I see", "Rings", "Details card", "Centre of the rings",
                     "Triangle", "Ticks along the top edge", "Top corners", "Bottom tip", "Conversation",
                     "Talk button", "Type box", "Listen to all speech", "?"):
            assert name in text, name
        if SHOTS:
            page.screenshot(path=str(Path(SHOTS) / "tau_r4_help.png"))
        browser.close()


def test_rings_pulse_with_the_voice(running_tau):
    """While TAU speaks the rings brighten and swell with the voice level, then settle (no sparks)."""
    rt, url = running_tau
    rt._save_display_state(help_seen=True)
    rt.display.publish({"help_seen": True})
    js = (Path(__file__).resolve().parents[1] / "src/tau/display/static/tau.js").read_text(encoding="utf-8")
    assert "spark" not in js.lower()
    with sync_api.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1366, "height": 860})
        page.goto(url)
        page.wait_for_function("() => window.__tau && window.__tau.ready", timeout=30000)
        rest = page.evaluate("() => window.__tau.ringScale()")
        rt.display.publish({"state": "speaking", "level": 0.9, "speaking_text": "Hello."})
        page.wait_for_function("() => window.__tau.pulse() > 0.5", timeout=20000)
        assert page.evaluate("() => window.__tau.ringScale()") > rest * 1.03
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(SHOTS) / "tau_r4_speaking_pulse.png"))
        rt.display.publish({"state": "idle", "level": 0.0, "speaking_text": None})
        page.wait_for_function("() => window.__tau.pulse() < 0.05", timeout=20000)
        browser.close()
