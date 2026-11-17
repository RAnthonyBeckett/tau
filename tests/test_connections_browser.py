"""Round 8: connections screen through the real page in a headless browser.

Set TAU_SCREENSHOTS=<folder> to save pictures of the screen.
"""
import os
from pathlib import Path
from typing import Any

from test_display_browser import _launch, sync_api
from test_settings import fake_brains, served  # noqa: F401  (fixture)

SHOTS = os.environ.get("TAU_SCREENSHOTS")


def shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.wait_for_timeout(300)
        page.screenshot(path=str(Path(SHOTS) / f"tau_r8_connections_{name}.png"))


def make_conn(
    id: str,
    machine: str = "box",
    place: str = "",
    kind: str = "Command-line tool",
    name: str = "tool",
    state: str = "Found",
    setting: str = "off",
    evidence: str = "evidence note",
    allows: str = "",
    needs: str = "",
    door: str = "doorway",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "machine": machine,
        "place": place,
        "kind": kind,
        "name": name,
        "state": state,
        "setting": setting,
        "evidence": evidence,
        "allows": allows,
        "needs": needs,
        "door": door,
        "detail": dict(detail) if detail is not None else {},
    }


def test_connections_nothing_looked_at(served):
    rt, url, calls, home = served
    rt._save_display_state(help_seen=True)
    rt.display.publish({"help_seen": True})
    with sync_api.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(url)
        page.locator("#connbtn").wait_for(state="visible", timeout=15000)
        page.click("#connbtn")
        page.locator("#conn").wait_for(state="visible", timeout=10000)

        # "haven't looked" sentence and "Look now" button are visible
        page.locator("#connbody", has_text="haven't looked").wait_for(timeout=10000)
        assert page.locator('#connbody button:text-is("Look now")').is_visible()
        shot(page, "empty")
        browser.close()


def test_connections_typical_machine(served):
    rt, url, calls, home = served
    rt._save_display_state(help_seen=True)
    rt.display.publish({"help_seen": True})

    # About 40 connections across 6 kinds on one machine ("box"):
    # - 5 command-line tools in one folder so a bundle appears
    # - 2 other command-line tools
    # - 1 Model server
    # - 8 installed programs
    # - 8 background services
    # - 8 drives
    # - 8 web services
    conns = []
    for i in range(5):
        conns.append(make_conn(f"cmd_b_{i}", machine="box", kind="Command-line tool", name=f"batch_tool_{i}",
                               detail={"folder": r"C:\Tools"}))
    conns.append(make_conn("cmd_single_1", machine="box", kind="Command-line tool", name="alpha_tool"))
    conns.append(make_conn("cmd_single_2", machine="box", kind="Command-line tool", name="beta_tool"))

    conns.append(make_conn("model_svr_1", machine="box", kind="Model server", name="Local Model Server",
                           evidence="Listening on port 8080", allows="answer your questions"))

    for i in range(8):
        conns.append(make_conn(f"prog_{i}", machine="box", kind="Installed program", name=f"Program {i}"))
    for i in range(8):
        conns.append(make_conn(f"svc_{i}", machine="box", kind="Background service", name=f"Service {i}"))
    for i in range(8):
        conns.append(make_conn(f"drv_{i}", machine="box", kind="Drive", name=f"Drive {chr(67+i)}:"))
    for i in range(8):
        conns.append(make_conn(f"web_{i}", machine="box", kind="Web service that describes itself", name=f"Web Service {i}"))

    rt.look_store().save({
        "machine": "box",
        "at": "2026-11-15T12:00:00",
        "connections": conns,
        "finders": {},
    })

    with sync_api.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(url)
        page.locator("#connbtn").wait_for(state="visible", timeout=15000)
        page.click("#connbtn")
        page.locator("#conn").wait_for(state="visible", timeout=10000)

        # Machine is open
        mach = page.locator('#connbody details.mach:has(summary:has-text("box"))')
        mach.wait_for(state="visible", timeout=10000)
        assert mach.get_attribute("open") is not None

        # Every kind is closed at first
        assert page.locator("#connbody details.kind[open]").count() == 0

        # Opening "Model server" shows its row with four level buttons
        kind_model = mach.locator('details.kind:has(summary:has-text("Model server"))')
        kind_model.locator("summary").first.click()
        row = kind_model.locator('.connrow', has_text="Local Model Server")
        row.wait_for(state="visible", timeout=10000)

        btns = row.locator('.levelbtn')
        assert btns.count() == 4
        for label in ("Off", "Look only", "Ask first", "Act"):
            assert row.locator(f'.levelbtn:text-is("{label}")').is_visible()

        # Clicking "Look only" shows the confirm question
        row.locator('.levelbtn:text-is("Look only")').click()
        confirm_box = page.locator("#connconfirm")
        confirm_box.wait_for(state="visible", timeout=10000)
        assert "?" in confirm_box.inner_text()

        # Clicking "No" leaves the setting off on disk
        confirm_box.locator('button:text-is("No")').click()
        confirm_box.wait_for(state="hidden", timeout=10000)
        disk_conn = next(c for c in rt.look_store().load("box")["connections"] if c["id"] == "model_svr_1")
        assert disk_conn["setting"] == "off"

        # Clicking "Look only" then "Yes, change it" saves "sense" on disk
        row.locator('.levelbtn:text-is("Look only")').click()
        confirm_box.wait_for(state="visible", timeout=10000)
        confirm_box.locator('button:text-is("Yes, change it")').click()
        confirm_box.wait_for(state="hidden", timeout=10000)

        # Verify disk (once the page shows the change, so the request has finished)
        page.locator('#connbody .connrow.ison', has_text="Local Model Server").wait_for(timeout=10000)
        disk_conn_after = next(c for c in rt.look_store().load("box")["connections"] if c["id"] == "model_svr_1")
        assert disk_conn_after["setting"] == "sense"

        # Row gains highlight
        page.locator('#connbody .connrow.ison', has_text="Local Model Server").wait_for(timeout=10000)

        # Kind's summary now ends "1 on"
        kind_sum = kind_model.locator("summary").first
        assert kind_sum.inner_text().strip().endswith("1 on")

        # Kind is still open after the change
        assert kind_model.get_attribute("open") is not None

        # Screenshot "typical" (before the filter)
        shot(page, "typical")

        # "Show only what's on" leaves exactly one row
        page.click("#connonly")
        page.wait_for_function("() => document.querySelectorAll('#connbody .connrow.one').length === 1")
        assert page.locator("#connbody .connrow.one").count() == 1
        assert page.locator("#connbody .connrow.one b").inner_text() == "Local Model Server"

        # Screenshot "typical_on" (after)
        shot(page, "typical_on")
        browser.close()


def test_connections_large_lab(served):
    rt, url, calls, home = served
    rt._save_display_state(help_seen=True)
    rt.display.publish({"help_seen": True})

    # Four machines with 100 connections each (400 total)
    # One kind with 70 rows on one machine ("lab-alpha")
    machines = ["lab-alpha", "lab-beta", "lab-gamma", "lab-delta"]
    for m in machines:
        conns = []
        if m == "lab-alpha":
            for i in range(70):
                conns.append(make_conn(f"{m}_inst_{i:02d}", machine=m, kind="Installed program",
                                       name=f"Alpha Tool N{i:02d}"))
            for i in range(15):
                conns.append(make_conn(f"{m}_drv_{i:02d}", machine=m, kind="Drive", name=f"Alpha Drive {i}"))
            for i in range(15):
                conns.append(make_conn(f"{m}_svc_{i:02d}", machine=m, kind="Background service", name=f"Alpha Service {i}"))
        else:
            for i in range(50):
                conns.append(make_conn(f"{m}_inst_{i:02d}", machine=m, kind="Installed program",
                                       name=f"Program {m} N{i:02d}"))
            for i in range(50):
                conns.append(make_conn(f"{m}_svc_{i:02d}", machine=m, kind="Background service",
                                       name=f"Service {m} {i:02d}"))
        rt.look_store().save({
            "machine": m,
            "at": "2026-11-15T12:00:00",
            "connections": conns,
            "finders": {},
        })

    with sync_api.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(url)
        page.locator("#connbtn").wait_for(state="visible", timeout=15000)
        page.click("#connbtn")
        page.locator("#conn").wait_for(state="visible", timeout=10000)

        # All four machines are listed closed with their counts
        page.locator("#connbody details.mach").first.wait_for(timeout=10000)
        assert page.locator("#connbody details.mach").count() == 4
        assert page.locator("#connbody details.mach[open]").count() == 0
        for m in machines:
            mach_summary = page.locator(f'#connbody details.mach:has(summary:has-text("{m}")) > summary')
            assert "100 found, 0 on" in mach_summary.inner_text()

        # Opening one machine ("lab-alpha")
        mach_alpha = page.locator('#connbody details.mach:has(summary:has-text("lab-alpha"))')
        mach_alpha.locator("summary").first.click()

        # Opening its 70-row kind ("Installed program")
        kind_prog = mach_alpha.locator('details.kind:has(summary:has-text("Installed program"))')
        kind_prog.locator("summary").first.click()

        # Shows 60 rows and a "Show 10 more" button
        page.wait_for_function(
            "() => document.querySelectorAll('#connbody details.mach[open] details.kind[open] .connrow.one').length === 60"
        )
        btn_more = kind_prog.locator('button:text-is("Show 10 more")')
        btn_more.wait_for(state="visible", timeout=10000)

        # Clicking "Show 10 more" shows all 70
        btn_more.click()
        page.wait_for_function(
            "() => document.querySelectorAll('#connbody details.mach[open] details.kind[open] .connrow.one').length === 70"
        )
        btn_more.wait_for(state="hidden", timeout=5000)

        # Typing in the filter narrows the list
        page.fill("#connfilter", "Alpha Tool N6")
        page.wait_for_timeout(600)
        page.wait_for_function(
            "() => document.querySelectorAll('#connbody .connrow.one').length === 10"
        )

        # Screenshot "large" with one machine and one kind open
        shot(page, "large")
        browser.close()


def test_connections_small_viewport(served):
    rt, url, calls, home = served
    rt._save_display_state(help_seen=True)
    rt.display.publish({"help_seen": True})

    rt.look_store().save({
        "machine": "box",
        "at": "2026-11-15T12:00:00",
        "connections": [
            make_conn("cmd_1", machine="box", kind="Command-line tool", name="tool_1"),
            make_conn("cmd_2", machine="box", kind="Command-line tool", name="tool_2"),
            make_conn("prog_1", machine="box", kind="Installed program", name="Program 1"),
        ],
        "finders": {},
    })

    with sync_api.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.goto(url)
        page.locator("#connbtn").wait_for(state="visible", timeout=15000)
        page.click("#connbtn")
        page.locator("#conn").wait_for(state="visible", timeout=10000)

        # At 800x600 the panel has no sideways scrolling
        scroll_w = page.evaluate("() => document.documentElement.scrollWidth")
        client_w = page.evaluate("() => document.documentElement.clientWidth")
        assert scroll_w <= client_w

        conn_scroll_w = page.evaluate("() => document.getElementById('conn').scrollWidth")
        conn_client_w = page.evaluate("() => document.getElementById('conn').clientWidth")
        assert conn_scroll_w <= conn_client_w

        shot(page, "small")
        browser.close()
