"""Round 6 item 6: Check TAU is read-only, time-limited, and honest about the British voice."""
import time

from tau import work_check
from tau.assistant import Assistant


def test_british_voice_without_pronunciation_model_is_not_ready(monkeypatch):
    import huggingface_hub
    import tau.kokoro_voice as kv

    monkeypatch.setattr(kv, "voice_on_disk", lambda v, repo_id=kv.REPO_ID: True)
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda repo_id, filename: None)
    s = work_check.voice_status({"tau": {"voice_name": "bf_emma"}})
    assert s["state"] == "Unavailable" and "British pronunciation model" in s["say"] and s["next"]
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda repo_id, filename: "/cache/x")
    assert work_check.voice_status({"tau": {"voice_name": "bf_emma"}})["state"] == "Ready"


def test_slow_check_times_out_without_freezing(monkeypatch):
    asst = Assistant({"machine": {"name": "hub"}})
    asst.work.workers = {}
    monkeypatch.setattr(work_check, "CHECKS", [lambda a: time.sleep(5) or [], work_check._projects])
    t = time.time()
    r = work_check.check_all(asst, limit=0.5)
    assert time.time() - t < 3
    assert any(c["state"] == "Status unknown" for c in r["checks"])
    folders = [c for c in r["checks"] if c["what"] == "Folders"][0]
    assert folders["flow"] == "folder"          # missing folder links to its guided step
    assert "microphone" in r["not_checked"]
