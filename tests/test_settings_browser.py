"""Round 4 item 1 through the real page: a headless browser opens Settings and
drives each control, and each change reaches the profile and the status line.

Set TAU_SCREENSHOTS=<folder> to save a picture of each section (results/ has them)."""
import os
from pathlib import Path

import yaml

from test_display_browser import _launch, sync_api
from test_settings import fake_brains, served  # noqa: F401  (fixture)

SHOTS = os.environ.get("TAU_SCREENSHOTS")


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.wait_for_timeout(300)
        page.screenshot(path=str(Path(SHOTS) / f"tau_r4_{name}.png"))


def on_disk(home):
    return yaml.safe_load((home / "profile.yaml").read_text())


def row(page, rid):
    return page.locator(f'#setrows .row[data-id="{rid}"]')


def says(page, text):
    page.locator("#setmsg", has_text=text).wait_for(timeout=30000)   # Windows runners are slower


def section(page, title):
    page.click(f'#setnav button:text-is("{title}")')
    page.locator("#settitle", has_text=title).wait_for(timeout=30000)


def test_every_setting_from_the_display(served):
    rt, url, calls, home = served
    rt._save_display_state(help_seen=True)
    rt.display.publish({"help_seen": True})
    with sync_api.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.goto(url)
        page.locator("#setbtn").wait_for(state="visible", timeout=15000)
        page.click("#setbtn")
        page.locator("#settings").wait_for(state="visible")
        page.locator("#setnav button").first.wait_for(timeout=30000)
        nav = page.locator("#setnav button").all_inner_texts()
        assert nav == ["Brain", "What I watch", "Permissions", "Voice and hearing", "Name and how I speak to you",
                       "Pacing profile", "Display", "Lab"]

        # Brain: check again, every model with size and MoE/dense, switch model, test it
        page.click('#setrows button:text-is("Check again")')
        says(page, "Found 1 model server with 3 models")
        models = page.locator('#setrows .row.list').first
        text = models.inner_text()
        assert "gemma-4-26B-A4B-it-Q4_K_M" in text and "about 16.8 GB" in text and "MoE" in text and "dense" in text
        shot(page, "settings_brain")
        page.locator("#setrows li", has_text="llama3.1-8b").locator("button", has_text="Use this").click()
        says(page, "Now using llama3.1-8b")
        assert on_disk(home)["brain"]["local"]["model"] == "llama3.1-8b"
        page.locator("#status", has_text="Now using llama3.1-8b").wait_for(timeout=5000)   # said on the status line
        page.click('#setrows button:text-is("Test the brain now")')
        says(page, "answered")

        # What I watch: a switch, look for services, watch one, add by name, remove
        section(page, "What I watch")
        row(page, "sense:gpu").locator("button.toggle").click()
        says(page, "Graphics card: on")
        assert on_disk(home)["organs"]["gpu"] == "sense"
        page.click('#setrows button:text-is("Look for services on this machine")')
        says(page, "I found 3 programs")
        found = row(page, "found").inner_text()
        assert "llama-server" in found and "port 8080" in found and "58204" not in found and "60371" not in found
        row(page, "found").locator("li", has_text="llama-server").locator("button").click()
        says(page, "checking llama-server on port 8080")
        row(page, "add_service").locator("input").fill("ollama")
        row(page, "add_service").locator("button").click()
        says(page, "Added ollama")
        shot(page, "settings_watch")
        row(page, "services").locator("li", has_text="ollama").locator("button", has_text="Remove").click()
        says(page, "stopped checking ollama")
        assert [s["name"] for s in on_disk(home)["services"]] == ["llama-server"]

        # Permissions: four buttons each; Act asks first
        section(page, "Permissions")
        row(page, "organ:drives").locator("button", has_text="Act").click()
        page.locator("#setconfirm", has_text="Is that right?").wait_for(timeout=5000)
        shot(page, "settings_permissions_confirm")
        assert on_disk(home)["organs"]["drives"] == "off"
        page.click("#setconfirm button.yes")
        says(page, "Drives is set to Act")
        assert on_disk(home)["organs"]["drives"] == "act"
        row(page, "organ:drives").locator("button", has_text="Look").click()
        says(page, "Drives is set to Look")

        # Voice and hearing
        section(page, "Voice and hearing")
        row(page, "voice").locator("button", has_text="Emma (British)").click()
        says(page, "Emma")
        page.click('#setrows button:text-is("Play a sample")')
        says(page, "Playing")
        assert calls["sample"]
        row(page, "mic").locator("button", has_text="Headset").click()
        says(page, "Microphone set to Headset")
        page.click('#setrows button:text-is("Test the microphone")')
        says(page, "I heard you")
        assert row(page, "mic_level").is_visible()
        page.click('#setrows button:text-is("Test the speaker")')
        says(page, "Played my chime")
        assert "nothing is saved" in row(page, "hands_free").inner_text()      # the privacy sentence, visible
        row(page, "hands_free").locator("button.toggle").click()
        says(page, "Listen to all speech is on")
        assert "not trained yet" in row(page, "wake").inner_text()
        shot(page, "settings_voice")

        # Names
        section(page, "Name and how I speak to you")
        row(page, "user_name").locator("input").fill("Sam")
        row(page, "user_name").locator("input").press("Enter")
        says(page, "I'll call you Sam")
        assert on_disk(home)["user"]["name"] == "Sam"

        # Pacing
        section(page, "Pacing profile")
        row(page, "pacing:enabled").locator("button.toggle").click()
        says(page, "Pacing profile: on")
        row(page, "pacing:max_paragraphs").locator("button", has_text="2 paragraphs").click()
        says(page, "at most 2 paragraphs")
        row(page, "pacing:hard_day").locator("button.toggle").click()
        says(page, "Hard day: on")
        shot(page, "settings_pacing")
        pc = on_disk(home)["pacing"]
        assert pc["enabled"] and pc["max_paragraphs"] == 2 and pc["hard_day"]

        # Display
        section(page, "Display")
        row(page, "corner").locator("button", has_text="Bottom left").click()
        says(page, "bottom left")
        row(page, "monitor").locator("button", has_text="Screen 2").click()
        says(page, "screen 2")
        row(page, "display_port").locator("input").fill("60000")
        row(page, "display_port").locator("button").click()
        says(page, "Pick a fixed port")
        assert "47110" in str(on_disk(home)["display"]["port"])
        row(page, "display_port").locator("input").fill("47120")
        row(page, "display_port").locator("button").click()
        says(page, "Display port saved as 47120")
        page.click('#setrows button:text-is("Open the pop-out now")')
        says(page, "Opening the pop-out")

        # Lab: role, port, pair a machine (the code is on screen, large)
        section(page, "Lab")
        assert "47111" in row(page, "hub_port").locator("input").input_value()
        page.click('#setrows button:text-is("Pair a machine")')
        says(page, "Pairing code")
        code = row(page, "pair_code")
        assert code.is_visible() and "works once" in code.inner_text()
        shot(page, "settings_lab")

        # Every piece of text in the panel is at least 14 px
        small = page.evaluate("""() => [...document.querySelectorAll('#settings *')].filter(e =>
            e.childNodes.length && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) &&
            parseFloat(getComputedStyle(e).fontSize) < 14).map(e => e.outerHTML.slice(0, 80))""")
        assert small == []
        page.click("#setclose")
        page.locator("#settings").wait_for(state="hidden")
        browser.close()
