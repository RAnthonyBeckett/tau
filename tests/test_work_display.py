"""Round 6: the UI event route (/work) and the typed/spoken text route reach the same job path."""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tau.assistant import Assistant
from tau.display.server import DisplayHub
from tau.work import CommandWorker, handle_op

from test_work import FAKE, wait_for


def post(port, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


@pytest.fixture
def rig(tmp_path):
    proj = tmp_path / "My Project"
    proj.mkdir()
    (proj / "AGENTS.md").write_text("PROJECT RULE 42", encoding="utf-8")
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE, encoding="utf-8")
    profile = {"machine": {"name": "hub-pc"}, "organs": {"chat": "act"}}
    asst = Assistant(profile)
    asst.work.workers = {"fake": CommandWorker([sys.executable, str(fake), "{package_file}"], "Fake worker")}
    done = []
    asst.work.on_done = done.append
    hub = DisplayHub(profile, on_text=lambda t: {"say": asst.handle(t).say},
                     on_work=lambda d: handle_op(asst, d))
    asst.display = hub
    port = hub.serve(0)
    yield asst, hub, port, proj, done
    hub.stop()


def test_ui_route_add_connect_draft_send_result(rig):
    asst, hub, port, proj, done = rig
    r = post(port, "/work", {"op": "add_folder", "path": str(proj)})
    assert r["ok"] and "AGENTS.md" in r["say"] and "read only" in r["say"]
    assert post(port, "/work", {"op": "connect", "id": "fake"})["ok"]
    card = post(port, "/work", {"op": "draft", "request": "summarise the rules"})["card"]
    assert card["fields"]["Project"] == "My Project"
    sent = post(port, "/work", {"op": "send", "id": card["id"], "approval": card["approval"],
                                      "accept_unenforced": True})
    assert sent["ok"], sent
    j = wait_for(asst.work, card["id"])
    for _ in range(40):                 # the "finished" notice follows the state change by a moment
        if done:
            break
        time.sleep(0.05)
    assert j["state"] == "Finished" and done and done[0]["id"] == card["id"]
    summ = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/work", timeout=5).read())
    assert summ["jobs"][0]["state"] == "Finished"


def test_spoken_text_route_drafts_then_send_it(rig):
    asst, hub, port, proj, done = rig
    handle_op(asst, {"op": "add_folder", "path": str(proj)})
    handle_op(asst, {"op": "connect", "id": "fake"})
    # the microphone's on_text and the type box both end in Assistant.handle
    r = asst.handle("Have the worker work on this: tidy the notes")
    assert r.kind == "work_card" and "Press Send" in r.say
    assert not asst.work.unfinished()          # nothing launched from talking
    r = asst.handle("send it")
    assert "can't stop" in r.say and not asst.work.unfinished()     # a custom worker isn't sandboxed
    asst.work.workers["fake"].enforces = True      # routing test only: pretend this adapter enforces limits
    asst.handle("send it")
    jid = asst.work.data["active"]["job"]
    assert wait_for(asst.work, jid)["state"] == "Finished"
    assert "finished" in asst.handle("how is it going").say.lower()


def test_discussion_launches_nothing(rig):
    asst, hub, port, proj, done = rig
    handle_op(asst, {"op": "add_folder", "path": str(proj)})
    handle_op(asst, {"op": "connect", "id": "fake"})
    asst.lanes.chat = lambda *a, **k: ("Sure, we could.", "local")
    for t in ("Could we have Claude refactor the parser?", "I'm thinking we write a python script",
              "I don't know, maybe codex should fix the bug"):
        asst.handle(t)
    assert asst.work.data["jobs"] == {}


def test_open_flows_by_voice(rig):
    asst, hub, port, proj, done = rig
    assert asst.handle("add a folder").say == "Opening Add a folder."
    assert hub.live["open_work"]["flow"] == "folder"
    asst.handle("connect Claude")
    assert hub.live["open_work"]["flow"] == "app"


def test_work_off_refuses_send(rig, tmp_path):
    asst, hub, port, proj, done = rig
    asst.profile.setdefault("organs", {})["work"] = "off"
    handle_op(asst, {"op": "add_folder", "path": str(proj)})
    handle_op(asst, {"op": "connect", "id": "fake"})
    card = handle_op(asst, {"op": "draft", "request": "x"})["card"]
    r = handle_op(asst, {"op": "send", "id": card["id"], "approval": card["approval"]})
    assert r["ok"] is False and asst.work.job(card["id"])["state"] == "Draft"


def test_browse_roots_refuse_outside(tmp_path):
    from tau.work import list_dir
    with pytest.raises(PermissionError):
        list_dir("/proc" if Path("/proc").exists() else "C:\\Windows\\System32\\..\\..\\nowhere")
    assert list_dir("")["dirs"]


@pytest.mark.skipif(os.name != "nt", reason="Windows drive test")
def test_browse_roots_skip_a_drive_that_refuses_access(monkeypatch):
    import pathlib
    from tau.work import _nav_roots

    denied = "Z:\\"
    real_exists = pathlib.Path.exists

    def fake_exists(self):
        if str(self) == denied:
            raise PermissionError("Access denied")
        return real_exists(self)

    monkeypatch.setattr(pathlib.Path, "exists", fake_exists)
    roots = _nav_roots()
    assert pathlib.Path(denied) not in roots


def test_restart_reminder_and_simultaneous_sends(rig):
    import threading
    asst, hub, port, proj, done = rig
    assert asst.handle("what's unfinished").say == "Nothing's unfinished."
    handle_op(asst, {"op": "add_folder", "path": str(proj)})
    handle_op(asst, {"op": "connect", "id": "fake"})
    card = handle_op(asst, {"op": "draft", "request": "tidy"})["card"]
    assert "a draft waiting to send" in asst.handle("where were we").say
    results = []
    body = {"op": "send", "id": card["id"], "approval": card["approval"], "accept_unenforced": True}
    ts = [threading.Thread(target=lambda: results.append(post(port, "/work", body))) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for r in results if r["ok"]) == 1          # a click and a voice "send it" can't start two
    wait_for(asst.work, card["id"])


def test_heavy_request_without_folder_makes_a_draft_not_a_launch(rig):
    asst, hub, port, proj, done = rig
    asst.profile.setdefault("organs", {})["handoff"] = "act"
    r = asst.handle("refactor the renaming code into several files")
    assert r.kind == "work_card" and "Which folder" in r.say
    assert not asst.work.unfinished()
