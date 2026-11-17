"""Round 5: "we give her access slowly and with receipts"."""

from conftest import make_profile
from test_display_browser import running_tau  # noqa: F401  (fixture)


def test_permission_changes_in_settings_leave_receipts(tau_home):
    from tau.actionlog import ActionLog
    from tau import paths
    from tau.settings import Settings

    data = make_profile(tau_home)
    st = Settings(data, paths.profile_path())
    assert st.change("permissions", "organ:services", "suggest")["ok"]
    st.change("watch", "sense:gpu", False)
    got = ActionLog(paths.log_dir(data)).receipts()
    assert any("Suggest" in r["what"] and r["who"] == "you, in Settings" for r in got), got
    assert all(r["when"] and r["machine"] for r in got)
    assert (paths.log_dir(data) / "actions.jsonl").exists()


def test_suggest_and_act_actions_leave_receipts_and_yes_no(tau_home, tmp_path):
    from tau.actionlog import ActionLog
    from tau.organs import Organs

    log = ActionLog(tmp_path)
    seen, shown = [], []
    log.on_write = seen.append
    org = Organs({"organs": {"services": "suggest", "voice": "act"}}, log)
    org.on_pending = shown.append
    out = org.call("services", "start", via="display", name="nginx")
    assert out.status == "needs_yes" and shown[-1].summary.startswith("start a service")
    org.decline(out.pending_id)
    assert shown[-1] is None                                  # the Yes / No card goes away
    out = org.call("services", "start", via="display", name="nginx")
    org.confirm(out.pending_id, via="display")
    whats = [(r["what"], r["who"]) for r in log.receipts()]
    assert ("Proposed: start a service (nginx)", "waiting for your yes") in whats
    assert any(w.startswith("Not done") and who == "you said no" for w, who in whats)
    assert any("start a service" in w and who.startswith("you said yes") for w, who in whats), whats
    n = len(log.receipts())
    org.call("voice", "speak", via="reply", text="hello")     # TAU talking is not a lab action
    assert len(log.receipts()) == n and seen


def test_display_shows_receipts_and_a_yes_no_card(running_tau):
    from test_display_browser import _launch, sync_api

    rt, url = running_tau
    rt._save_display_state(help_seen=True)
    rt.display.publish({"help_seen": True})
    rt.assistant.log.receipt("Services: Look to Suggest", "you, in Settings")
    pend = rt.assistant.organs
    pend.profile.setdefault("organs", {})["services"] = "suggest"
    out = pend.call("services", "start", via="voice", name="nginx")
    with sync_api.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1366, "height": 860})
        page.goto(url)
        page.locator("#proposal").wait_for(state="visible", timeout=30000)
        assert "start a service" in page.locator("#proptext").inner_text()
        page.click("#sidetab")
        alist = page.locator("#alist")
        alist.locator(".rcpt", has_text="Services: Look to Suggest").wait_for(timeout=10000)
        assert "you, in Settings" in alist.inner_text()
        page.click("#propno")
        page.locator("#proposal").wait_for(state="hidden", timeout=10000)
        alist.locator(".rcpt", has_text="Not done").wait_for(timeout=10000)
        assert out.pending_id not in pend.pending
        browser.close()
