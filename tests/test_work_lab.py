"""Round 6 item 5: machine boundaries. Remote dispatch is NOT built; these tests prove it
is refused plainly and never falls back to running on the hub."""
import sys

from tau.work import CommandWorker, OpenRig, WorkStore

from test_work import FAKE


def store(tmp_path):
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE, encoding="utf-8")
    return WorkStore({"machine": {"name": "hub-pc"}},
                     workers={"fake": CommandWorker([sys.executable, str(fake), "{package_file}"], "Fake")})


def test_remote_project_never_reads_or_runs_a_same_named_hub_path(tmp_path):
    s = store(tmp_path)
    same = tmp_path / "shared-name"
    same.mkdir()
    (same / "AGENTS.md").write_text("HUB COPY - WRONG MACHINE", encoding="utf-8")
    pr = s.add_project(str(same), machine="node-a")       # same path string, but it lives on node-a
    assert pr["remote"] and pr["machine"] == "node-a"
    assert s.rules(pr["id"])["found"] == []
    s.connect("fake")
    c = s.draft("x", project=pr["id"], worker="fake")
    assert "HUB COPY" not in s.package(c["id"])["text"]
    r = s.send(c["id"], c["approval"], accept_unenforced=True)
    assert r["ok"] is False and "isn't built yet" in r["say"] and s.job(c["id"])["state"] == "Draft"
    assert "pid" not in s.job(c["id"])


def test_two_nodes_different_roots_dont_cross(tmp_path):
    s = store(tmp_path)
    a = s.add_project("/srv/a/project", machine="node-a")
    b = s.add_project("D:\\work\\project", machine="node-b")
    assert a["name"] == "project" and b["name"] == "project"
    assert a["id"] != b["id"] and s.project(b["id"])["machine"] == "node-b"


def test_openrig_is_listed_but_never_sent_work(tmp_path):
    o = OpenRig()
    o.exe = None
    assert o.detect()["state"] == "Unavailable" and "needs connection" in o.detect()["next"].lower()
    o.exe = "/usr/bin/rig"
    assert o.detect()["state"] == "Needs connection"


def test_needs_connection_worker_never_becomes_the_default(tmp_path):
    s = store(tmp_path)
    rig = OpenRig()
    rig.exe = "/usr/bin/rig"
    s.workers["openrig"] = rig
    s.connect("openrig")
    assert "worker" not in s.data["prefs"]
    s.connect("fake")
    assert s.data["prefs"]["worker"] == "fake"
