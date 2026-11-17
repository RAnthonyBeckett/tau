"""Bug 13: TAU only saw the host name. Setup offers the senses in plain words
with a recommended starter set, services filled from discovery, and the
display shows them straight away."""
from tau import detect, engines
from tau.display.model import build_segments
from tau.profile import load_profile
from tau.senses.starter import starter_services
from tau.setup import ScriptedAsker, Setup

SNAP = {
    "platform": "Windows", "machine": {"name": "lab-pc", "os": "Windows", "gpus": []},
    "services": [{"name": f"Svc{i}", "state": "running"} for i in range(200)],
    "ports": [
        {"port": 135, "process": "svchost", "address": "0.0.0.0"},
        {"port": 445, "process": "System", "address": "0.0.0.0"},
        {"port": 8080, "process": "llama-server.exe", "address": "127.0.0.1"},
        {"port": 8080, "process": "llama-server.exe", "address": "::1"},
        {"port": 5432, "process": "postgres", "address": "127.0.0.1"},
        {"port": 8096, "process": "jellyfin", "address": "0.0.0.0"},
        {"port": 49664, "process": "lsass", "address": "0.0.0.0"},
    ],
    "drives": [], "containers": [],
}


def fake_discovery(lab_dir, machine_name=None):
    from tau.discovery.labmap import LabMap

    LabMap(lab_dir).write_snapshot(SNAP)
    return SNAP


def test_starter_services_skip_the_operating_system():
    rows = starter_services(SNAP, "lab-pc")
    assert [r["name"] for r in rows] == ["llama-server", "jellyfin", "postgres"]
    assert rows[0]["importance"] == 2 and rows[0]["port"] == 8080 and rows[0]["machine"] == "lab-pc"


def test_accepting_the_starter_set_turns_senses_on_and_fills_the_rings(tau_home, monkeypatch):
    monkeypatch.setattr(detect, "default_engine_hosts", lambda: {})
    monkeypatch.setattr(detect, "agent_clis", lambda: [])
    monkeypatch.setattr(engines, "ollama_running", lambda *a, **k: False)
    monkeypatch.setattr(detect, "machine_basics", lambda: {
        "MACHINE_NAME": "lab-pc", "OS_NAME": "Windows 11", "OS_USER": "sam", "CPU_NAME": "cpu",
        "RAM_GB": 32, "GPU_NAME": "Some GPU", "GPU_VRAM_GB": 12})
    # look yes; brain skip; names; hub yes; corner; hands-free no; neighbours no; starter set: Enter (yes); pacing no
    asker = ScriptedAsker(answers=[True, "skip", "TAU", "Sam", "plainly", True, None, False, False])
    pf = Setup(asker, env={}, find_brains=lambda *a: [], discover=fake_discovery).run()
    starter_q = next(q for q in asker.asked if q.startswith("Shall I watch the starter set"))
    assert "the graphics card" in starter_q and "your drives" in starter_q and "3 services" in starter_q
    assert any("llama-server, jellyfin, postgres" in s for s in asker.said)
    data = load_profile(pf)
    for organ in ("gpu", "drives", "models", "services", "notifications"):
        assert data["organs"][organ] == "sense", organ
    assert [s["name"] for s in data["services"]] == ["llama-server", "jellyfin", "postgres"]
    segs = {s["id"]: s for s in build_segments(data, {"services": {"llama-server": True}, "vram_free_gb": 9.5})}
    assert segs["svc:llama-server"]["status"] == "lit"
    assert segs["gpu"]["label"] == "Graphics card: 9.5 GB free" and segs["gpu"]["status"] == "lit"
    assert "svc:none" not in segs and segs["drives"]["status"] != "empty"


def test_declining_the_starter_set_asks_one_sense_at_a_time(tau_home):
    asker = ScriptedAsker(answers=[False, True, False, False, True, False])
    s = Setup(asker, env={})
    s.values.update({"MACHINE_NAME": "lab-pc", "GPU_NAME": "Some GPU"})
    s._snapshot = SNAP
    s.topic_senses()
    singles = [q for q in asker.asked if q.startswith("Watch ")]
    assert len(singles) == 5 and singles[0].startswith("Watch the graphics card?")
    assert s._organs == {"gpu": "sense", "drives": "off", "models": "off", "services": "sense",
                         "notifications": "off"}
