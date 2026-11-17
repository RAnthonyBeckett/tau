"""Round 6: folders, connections and jobs, end to end with a fake worker.

A fake worker proves the plumbing only; it is not evidence that Claude Code or Codex works."""
import json
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tau.work import ClaudeCode, CommandWorker, WorkStore, open_intent, read_log, work_intent

FAKE = textwrap.dedent('''
    import os, sys, time, pathlib
    pkg = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
    mode = os.environ.get("FAKE_MODE", "ok")
    print("cwd=" + os.getcwd())
    print("rules_seen=" + str("PROJECT RULE 42" in pkg))
    if mode == "slow":
        time.sleep(30)
    if mode == "fail":
        print("something broke"); sys.exit(3)
    if mode == "ask":
        print("TAU-QUESTION: Which version did you mean?")
    if mode == "escape":
        pathlib.Path(os.environ["ESCAPE_TO"]).write_text("escaped", encoding="utf-8")
    if mode != "nooutput":
        pathlib.Path("out").mkdir(exist_ok=True)
        pathlib.Path("out/result.txt").write_text("done", encoding="utf-8")
    print("All done: wrote out/result.txt")
''')


def wait_for(store, jid, states=("Finished", "Failed", "Cancelled"), t=15):
    end = time.time() + t
    while time.time() < end:
        if store.job(jid)["state"] in states:
            return store.job(jid)
        time.sleep(0.05)
    raise AssertionError(store.job(jid)["state"])


@pytest.fixture
def lab(tmp_path):
    proj = tmp_path / "Comic project – ünïcode"
    proj.mkdir()
    (proj / "AGENTS.md").write_text("PROJECT RULE 42: be kind to files.", encoding="utf-8")
    (proj / "STATE.md").write_text("Checkpoint: page 3 inked.", encoding="utf-8")
    (proj / "notes.txt").write_text("hello", encoding="utf-8")
    (proj / ".env").write_text("SECRET=1", encoding="utf-8")
    other = tmp_path / "Other"
    other.mkdir()
    (other / "AGENTS.md").write_text("OTHER RULES", encoding="utf-8")
    fake = tmp_path / "fakeworker.py"
    fake.write_text(FAKE, encoding="utf-8")
    workers = {"fake": CommandWorker([sys.executable, str(fake), "{package_file}"], "Fake worker")}
    profile = {"machine": {"name": "hub-pc"}}
    s = WorkStore(profile, workers=workers)
    return s, proj, other, workers, profile


def test_folder_with_spaces_unicode_survives_restart_and_reads_rules(lab):
    s, proj, other, workers, profile = lab
    pr = s.add_project(str(proj))
    assert pr["access"] == "read" and pr["machine"] == "hub-pc"
    s2 = WorkStore(profile, workers=workers)
    assert s2.data["active"]["project"] == pr["id"]
    r = s2.rules(pr["id"])
    names = [f["name"] for f in r["found"]]
    assert "AGENTS.md" in names and "STATE.md" in names
    assert any("page 3" in f["text"] for f in r["found"])


def test_scope_secrets_and_links(lab, tmp_path):
    s, proj, other, *_ = lab
    pr = s.add_project(str(proj))
    assert s.read_file(pr["id"], "notes.txt")["text"] == "hello"
    with pytest.raises(PermissionError):
        s.read_file(pr["id"], ".env")
    with pytest.raises(PermissionError):
        s.read_file(pr["id"], "../Other/AGENTS.md")
    try:
        os.symlink(other / "AGENTS.md", proj / "link.md")
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    with pytest.raises(PermissionError):
        s.read_file(pr["id"], "link.md")
    b = s.browse(pr["id"])
    assert ".env" not in [i["name"] for i in b["items"]] and b["hidden"] >= 1


def test_whole_drive_and_missing_folder_refused(lab, tmp_path):
    s, *_ = lab
    with pytest.raises(ValueError):
        s.add_project(str(Path(tmp_path.anchor)))
    with pytest.raises(ValueError):
        s.add_project(str(tmp_path / "nope"))
    assert s.data["projects"] == {}


def test_remove_never_deletes_files(lab):
    s, proj, *_ = lab
    pr = s.add_project(str(proj))
    s.remove_project(pr["id"])
    assert (proj / "AGENTS.md").exists() and s.data["projects"] == {}


def test_full_loop_cwd_rules_result_followup(lab):
    s, proj, other, *_ = lab
    s.add_project(str(other))
    pr = s.add_project(str(proj), access="change")
    s.connect("fake")
    card = s.draft("Ink page 4", worker="fake", write=True, expect="out/result.txt")
    assert card["ask"] is None and "Fake worker will work in" in card["summary"]
    assert set(card["fields"]) == {"What you asked", "Project", "Who will do it", "Which machine",
                                   "Files and instructions included", "What may change", "Where the result will go"}
    out = s.send(card["id"], card["approval"], accept_unenforced=True)
    assert out["ok"]
    assert s.send(card["id"], card["approval"], accept_unenforced=True)["ok"] is False      # duplicate send does nothing
    job = wait_for(s, card["id"])
    log = read_log(job["log_file"])
    assert f"cwd={proj.resolve()}" in log and "rules_seen=True" in log
    assert "OTHER RULES" not in Path(job["package"]).read_text(encoding="utf-8")
    assert job["state"] == "Finished" and job["result"]["ok"] and "All done" in job["result"]["summary"]
    fu = s.follow_up("use the other version")
    assert fu["fields"]["Project"] == pr["name"] and "Earlier request: Ink page 4" in fu["request"]


def test_question_comes_back(lab, monkeypatch):
    s, proj, *_ = lab
    s.add_project(str(proj))
    s.connect("fake")
    monkeypatch.setenv("FAKE_MODE", "ask")
    c = s.draft("do it", worker="fake")
    s.send(c["id"], c["approval"], accept_unenforced=True)
    j = wait_for(s, c["id"])
    assert j["result"]["question"] == "Which version did you mean?"
    fu = s.follow_up("the second one")
    assert "Its question: Which version" in fu["request"] and "can't continue" in fu["say"]


def test_nonzero_exit_and_exit_zero_without_output(lab, monkeypatch):
    s, proj, *_ = lab
    s.add_project(str(proj), access="change")
    s.connect("fake")
    monkeypatch.setenv("FAKE_MODE", "fail")
    c = s.draft("x", worker="fake")
    s.send(c["id"], c["approval"], accept_unenforced=True)
    j = wait_for(s, c["id"])
    assert j["state"] == "Failed" and "exit 3" in j["result"]["issue"]
    monkeypatch.setenv("FAKE_MODE", "nooutput")
    c = s.draft("x", worker="fake", expect="out/missing.txt")
    s.send(c["id"], c["approval"], accept_unenforced=True)
    j = wait_for(s, c["id"])
    assert j["state"] == "Finished" and j["result"]["ok"] is False and "wasn't made" in j["result"]["issue"]


def test_cancel_and_restart_mid_job(lab, monkeypatch):
    s, proj, other, workers, profile = lab
    s.add_project(str(proj))
    s.connect("fake")
    monkeypatch.setenv("FAKE_MODE", "slow")
    c = s.draft("x", worker="fake")
    s.send(c["id"], c["approval"], accept_unenforced=True)
    assert s.job(c["id"])["state"] == "Running"
    s2 = WorkStore(profile, workers=workers)          # TAU restarted while it ran
    assert s2.job(c["id"])["state"] == "Status unknown"
    assert s.cancel(c["id"])["ok"]
    j = wait_for(s, c["id"])
    assert j["state"] == "Cancelled"


def test_changed_draft_invalidates_approval(lab):
    s, proj, other, *_ = lab
    s.add_project(str(proj))
    s.connect("fake")
    c = s.draft("x", worker="fake")
    old = c["approval"]
    s.amend(c["id"], files=["notes.txt"])
    assert s.send(c["id"], old)["ok"] is False
    assert s.job(c["id"])["state"] == "Draft"


def test_ambiguous_project_asks_once(lab):
    s, proj, other, *_ = lab
    s.add_project(str(proj))
    s.add_project(str(other))
    s.data["active"]["project"] = None
    c = s.draft("x", worker="fake")
    assert c["ask"] == "project" and "approval" not in c
    assert s.send(c["id"], "")["ok"] is False


def test_omitted_and_secret_files_listed_not_sent(lab):
    s, proj, *_ = lab
    s.add_project(str(proj))
    s.connect("fake")
    c = s.draft("x", worker="fake", files=["notes.txt", ".env", "../Other/AGENTS.md"])
    pk = s.package(c["id"])
    assert pk["included"] == ["notes.txt"] and "SECRET=1" not in pk["text"]
    assert {o["rel"] for o in pk["omitted"]} == {".env", "../Other/AGENTS.md"}


def test_read_folder_means_read_only_even_if_write_asked(lab):
    s, proj, *_ = lab
    s.add_project(str(proj))       # read only
    s.connect("fake")
    c = s.draft("x", worker="fake", write=True)
    assert c["fields"]["What may change"] == "nothing (read only)" and "warning" in c


def test_claude_adapter_never_bypasses_permissions(tmp_path):
    c = ClaudeCode(exe="claude")
    ro = c.argv({}, "p", "do it", {"path": str(tmp_path)}, write=False)
    rw = c.argv({}, "p", "do it", {"path": str(tmp_path)}, write=True)
    assert "plan" in ro and "acceptEdits" in rw
    for a in (ro, rw):
        assert "bypassPermissions" not in a and "--dangerously-skip-permissions" not in a


def test_claude_detection_states(tmp_path):
    assert ClaudeCode(exe=None).__class__ and ClaudeCode.__new__(ClaudeCode)
    c = ClaudeCode()
    c.exe = None
    assert c.detect()["state"] == "Unavailable"
    old = tmp_path / "claude"
    old.write_text("#!/bin/sh\necho 'old 0.1'\n")
    old.chmod(0o755)
    if os.name != "nt":
        c.exe = str(old)
        d = c.detect()
        assert d["state"] == "Unavailable" and "doesn't offer" in d["why"]
        good = tmp_path / "claude2"
        good.write_text("#!/bin/sh\necho '--print --permission-mode --output-format --add-dir --resume'\n")
        good.chmod(0o755)
        c.exe = str(good)
        assert c.detect()["state"] == "Found"          # never "Ready" from detection alone


def test_quotes_and_shell_characters_stay_data(lab, monkeypatch):
    s, proj, *_ = lab
    s.add_project(str(proj))
    s.connect("fake")
    c = s.draft('fix "this"; rm -rf / && echo $HOME `id`', worker="fake")
    s.send(c["id"], c["approval"], accept_unenforced=True)
    j = wait_for(s, c["id"])
    assert j["state"] == "Finished"
    assert 'fix "this"; rm -rf /' in Path(j["package"]).read_text(encoding="utf-8")
    assert "rm -rf" not in " ".join(j.get("program", "")) and "rm -rf" not in j.get("receipt", "")


def test_intents():
    assert work_intent("Have Claude work on this chapter outline")["worker"] == "claude_code"
    assert work_intent("Could we have Claude fix the parser?") is None
    assert work_intent("I'm thinking we get codex to refactor it") is None
    assert work_intent("I don't know, maybe ask codex to do it") is None
    assert work_intent("write a python function to sort lists") is None
    assert open_intent("add a folder") == "folder" and open_intent("connect Claude") == "app"
    assert open_intent("how is it going") == "mywork"


def test_unapproved_write_by_unenforcing_worker_never_launches(lab, monkeypatch, tmp_path):
    """A worker that would write outside a read-only project: TAU can't sandbox a custom
    command, so the job stays a draft and the write never happens."""
    s, proj, *_ = lab
    s.add_project(str(proj))                       # read only
    s.connect("fake")
    target = tmp_path / "outside.txt"
    monkeypatch.setenv("FAKE_MODE", "escape")
    monkeypatch.setenv("ESCAPE_TO", str(target))
    c = s.draft("x", worker="fake")
    assert c["unenforced"] is True
    r = s.send(c["id"], c["approval"])
    assert r["ok"] is False and r["unenforced"] and s.job(c["id"])["state"] == "Draft"
    time.sleep(0.3)
    assert not target.exists()


@pytest.mark.skipif(os.name == "nt", reason="uses a shebang script as a stand-in claude")
def test_claude_adapter_json_result_and_resume_same_session(lab, tmp_path):
    """A stand-in `claude` that prints Claude Code's --output-format json shape and records its argv.
    This checks TAU's argv and parsing only; the real CLI's behaviour still needs a local trial."""
    from tau.work import ClaudeCode
    s, proj, other, workers, profile = lab
    rec = tmp_path / "argv.jsonl"
    exe = tmp_path / "claude"
    exe.write_text("#!" + sys.executable + "\nimport json,sys\n"
                   "if '--help' in sys.argv or '--version' in sys.argv:\n"
                   "    print('--print --permission-mode --output-format --add-dir --resume'); sys.exit(0)\n"
                   f"open({str(rec)!r},'a').write(json.dumps(sys.argv[1:])+'\\n')\n"
                   "print(json.dumps({'type':'result','is_error':False,'result':'Which page did you mean?',"
                   "'session_id':'sess-123'}))\n")
    exe.chmod(0o755)
    s.workers["claude_code"] = ClaudeCode(exe=str(exe))
    s.add_project(str(proj))
    s.connect("claude_code")
    c = s.draft("ink it", worker="claude_code")
    assert s.send(c["id"], c["approval"])["ok"]
    j = wait_for(s, c["id"])
    assert j["session"] == "sess-123" and j["result"]["summary"] == "Which page did you mean?"
    fu = s.follow_up("page 4")
    assert "same Claude Code conversation" in fu["say"] and fu["request"] == "page 4"
    s.send(fu["id"], fu["approval"])
    wait_for(s, fu["id"])
    calls = [json.loads(x) for x in rec.read_text().splitlines()]
    assert "--resume" not in calls[0] and calls[1][calls[1].index("--resume") + 1] == "sess-123"
    assert all("plan" in c for c in calls) and all("--output-format" in c for c in calls)
    assert not any("ink it" in " ".join(c) or "page 4" in " ".join(c) for c in calls)   # words stay in the file


def test_log_in_local_encoding_is_read(tmp_path, monkeypatch):
    import locale
    from tau.work import read_log
    f = tmp_path / "out.log"
    f.write_bytes("Comic – ü".encode("cp1252"))
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_set=False: "cp1252")
    assert read_log(str(f)) == "Comic – ü"


def test_card_names_the_external_destination(lab):
    from tau.work import ClaudeCode
    s, proj, *_ = lab
    s.workers["claude_code"] = ClaudeCode(exe="claude")
    s.add_project(str(proj))
    c = s.draft("x", worker="claude_code")
    assert "Anthropic's servers" in c["fields"]["Files and instructions included"]
    c = s.draft("x", worker="fake")
    assert "can't tell where" in c["fields"]["Files and instructions included"]
