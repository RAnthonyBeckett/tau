"""Round 6: the work path in a real browser, against the real runtime, with a fake worker.

Screenshots go to $TAU_SHOTS when set (the build notes use them). A fake worker proves the
plumbing only."""
import os
import sys
import time
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")

from test_display_browser import _launch, running_tau  # noqa: F401  (fixture)
from test_work import FAKE, wait_for
from tau.work import CommandWorker

SHOTS = os.environ.get("TAU_SHOTS")


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def test_folder_app_work_result_in_browser(running_tau, tmp_path):
    rt, url = running_tau
    proj = tmp_path / "Comic project ü"
    proj.mkdir()
    (proj / "AGENTS.md").write_text("PROJECT RULE 42: keep page numbers.", encoding="utf-8")
    (proj / "STATE.md").write_text("Checkpoint: page 3 inked.", encoding="utf-8")
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE, encoding="utf-8")
    store = rt.assistant.work
    store.workers = {"fake": CommandWorker([sys.executable, str(fake), "{package_file}"], "Fake worker"),
                     **{k: v for k, v in store.workers.items()}}
    with pw.sync_playwright() as p:
        b = _launch(p)
        page = b.new_page(viewport={"width": 1280, "height": 860})
        page.goto(url)
        page.wait_for_function("window.__tau && window.__tau.ready")
        page.evaluate("document.getElementById('help') && (document.getElementById('help').style.display='none')")
        shot(page, "r6_01_fresh")
        page.click("#workbar [data-flow=folder]")
        page.wait_for_selector("#fpick")
        page.click("text=Or paste the folder's path")
        page.fill("#fpath", str(proj))
        shot(page, "r6_02_add_folder")
        page.click("#fpaste")
        page.wait_for_selector("[data-a=read]")
        page.click("[data-a=read]")
        page.wait_for_selector("#rset")
        assert "page 3 inked" in page.inner_text("#workbody")
        shot(page, "r6_03_folder_rules")
        page.click("#workbar [data-flow=app]")
        page.wait_for_selector("[data-c=fake]")
        page.click("[data-c=fake]")
        page.wait_for_function("document.querySelector('#workmsg').textContent.includes('Connected')")
        shot(page, "r6_04_add_app")
        page.click("#workbar [data-flow=setup]")
        page.wait_for_selector("#wreq")
        page.fill("#wreq", "Ink page 4 and list what's missing")
        page.click("#wnext")
        page.wait_for_selector("#wsend")
        assert "Fake worker will work in Comic project" in page.inner_text("#workbody")
        page.click("#workbody details summary")
        page.check("#wack")
        shot(page, "r6_05_task_card")
        page.click("#wsend")
        page.wait_for_function("document.querySelector('#worktitle').textContent === 'My work'")
        jid = store.data["active"]["job"]
        wait_for(store, jid)
        time.sleep(0.3)
        page.click("#workbar [data-flow=mywork]")
        page.wait_for_function("document.querySelector('#workbody').textContent.includes('Finished')")
        assert "All done" in page.inner_text("#workbody")
        shot(page, "r6_06_result")
        log = Path(store.job(jid)["log_file"]).read_text(encoding="utf-8")
        assert f"cwd={proj.resolve()}" in log and "rules_seen=True" in log
        # typed request from the type box, same path as speech
        page.fill("#askin", "add a folder")
        page.press("#askin", "Enter")
        page.wait_for_function("document.querySelector('#worktitle').textContent === 'Add a folder'")
        page.set_viewport_size({"width": 420, "height": 700})
        page.click("#workbar [data-flow=mywork]")
        shot(page, "r6_07_small_window")
        b.close()
