"""Round 4 item 1: every setting is readable and changeable through the display's
/settings endpoints, and each change is written to the profile at once."""
import json
import urllib.request

import pytest
import yaml

from conftest import make_profile


def fake_brains(url):
    from tau.detect import FoundBrain

    def find(extra=None, env=None):
        return [FoundBrain("local", "openai_compat", url, ["gemma-4-26B-A4B-it-Q4_K_M", "llama3.1-8b", "Qwen3-30B-A3B"],
                           label=f"llama.cpp at {url}", sizes={"gemma-4-26B-A4B-it-Q4_K_M": 16_800_000_000},
                           meta={"llama3.1-8b": {"llama.expert_count": 0, "gguf_read": True}})]
    return find


# A llama.cpp router (8080) with a child on a temporary port, an Ollama tray and Claude on temporary ports.
SNAP = {"ports": [
    {"port": 8080, "pid": 100, "process": "llama-server"},
    {"port": 58204, "pid": 101, "process": "llama-server"},
    {"port": 60812, "pid": 200, "process": "ollama app"},
    {"port": 11434, "pid": 201, "process": "ollama"},
    {"port": 60371, "pid": 300, "process": "claude"},
    {"port": 5432, "pid": 400, "process": "postgres"}],
    "procs": {"101": {"name": "llama-server", "ppid": 100}, "100": {"name": "llama-server", "ppid": 1},
              "200": {"name": "ollama app", "ppid": 1}, "201": {"name": "ollama", "ppid": 200},
              "300": {"name": "claude", "ppid": 1}}}


@pytest.fixture
def served(tau_home, mock_server):
    from tau.runtime import Runtime

    data = make_profile(tau_home, LOCAL_ENGINE="openai_compat", LOCAL_LLM_URL=mock_server.url + "/v1",
                        LOCAL_MODEL="mock-model", BRAIN_PREFER="local", HUB_PORT=47111, HUB_HOST="127.0.0.1",
                        DISPLAY_PORT=47110)    # no bind address: no hub listening in tests
    rt = Runtime(data).build(want_voice=False)
    st = rt.settings
    st._find_brains = fake_brains(mock_server.url)
    st._discover = lambda: SNAP
    st._audio = lambda: {"mics": ["Desk mic", "Headset"], "speakers": ["Speakers", "Headset"]}
    st._monitors = lambda: [{"index": 0, "w": 1920, "h": 1080}, {"index": 1, "w": 2560, "h": 1440}]
    calls = {"sample": [], "mic": 0}
    st.hooks["play_sample"] = lambda text: calls["sample"].append(text)
    st.hooks["test_mic"] = lambda dev: (calls.__setitem__("mic", calls["mic"] + 1), 0.4)[1]
    st.hooks["test_speaker"] = lambda dev: True
    st.hooks["popout"] = lambda: calls.__setitem__("popout", True)
    port = rt.display.serve(0)
    yield rt, f"http://127.0.0.1:{port}/", calls, tau_home
    rt.stop.set()
    rt.display.stop()


def get(base, path):
    return json.loads(urllib.request.urlopen(base + path, timeout=10).read())


def post(base, body):
    req = urllib.request.Request(base + "settings", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def on_disk(home):
    return yaml.safe_load((home / "profile.yaml").read_text())


def test_every_section_has_rows(served):
    rt, base, _, _ = served
    from tau.settings import SECTIONS

    first = get(base, "settings")
    assert [s["title"] for s in first["sections"]] == [t for _, t in SECTIONS]
    for sid, _ in SECTIONS:
        v = get(base, f"settings?section={sid}")
        assert v["section"] == sid and v["rows"], sid
        for r in v["rows"]:
            assert r["kind"] in ("info", "switch", "choice", "text", "button", "list", "meter")
            assert r["label"], r


def test_brain_lists_models_with_size_and_moe_and_switches(served, mock_server):
    rt, base, _, home = served
    r = post(base, {"section": "brain", "id": "check"})
    assert r["ok"] and "1 model server with 3 models" in r["message"]
    models = next(x for x in r["settings"]["rows"] if x["kind"] == "list")["items"]
    by = {m["id"]: m for m in models}
    assert "about 16.8 GB" in by["gemma-4-26B-A4B-it-Q4_K_M"]["detail"]
    assert "MoE" in by["gemma-4-26B-A4B-it-Q4_K_M"]["detail"] and "MoE" in by["Qwen3-30B-A3B"]["detail"]
    assert "dense" in by["llama3.1-8b"]["detail"]          # GGUF metadata: expert_count 0
    use = by["llama3.1-8b"]["buttons"][0]
    r = post(base, {"section": "brain", "id": use["id"], "value": use["value"]})
    assert r["ok"] and "Now using llama3.1-8b" in r["message"]
    assert on_disk(home)["brain"]["local"]["model"] == "llama3.1-8b"
    assert rt.profile["brain"]["local"]["model"] == "llama3.1-8b"           # the running TAU sees it at once
    assert rt.assistant.lanes.clients["local"].model == "llama3.1-8b"
    assert rt.display.snapshot()["note"]["text"].startswith("Now using")    # and the status line says so
    r = post(base, {"section": "brain", "id": "test"})
    assert r["ok"] and "answered" in r["message"]


def test_a_brain_that_fails_its_test_is_not_saved(served, mock_server):
    rt, base, _, home = served
    post(base, {"section": "brain", "id": "check"})
    mock_server.fail = True
    r = post(base, {"section": "brain", "id": "use", "value": {"engine": "openai_compat", "url": mock_server.url,
                                                                "model": "llama3.1-8b"}})
    assert not r["ok"] and "kept the old one" in r["message"]
    assert on_disk(home)["brain"]["local"]["model"] == "mock-model"


def test_watch_switches_and_services(served):
    rt, base, _, home = served
    r = post(base, {"section": "watch", "id": "sense:gpu", "value": True})
    assert r["ok"] and on_disk(home)["organs"]["gpu"] == "sense"
    r = post(base, {"section": "watch", "id": "look"})
    found = next(x for x in r["settings"]["rows"] if x["id"] == "found")["items"]
    names = {i["id"]: i["detail"] for i in found}
    # temporary ports never offered; the router child maps to its parent's fixed port
    assert names == {"llama-server": "found on this machine, port 8080", "ollama": "found on this machine, port 11434",
                     "postgres": "found on this machine, port 5432"}
    r = post(base, {"section": "watch", "id": "watch_found", "value": "llama-server"})
    assert r["ok"] and {"name": "llama-server", "port": 8080} .items() <= on_disk(home)["services"][0].items()
    r = post(base, {"section": "watch", "id": "add_service", "value": "60371"})
    assert not r["ok"] and "temporary" in r["message"]
    r = post(base, {"section": "watch", "id": "add_service", "value": "ollama"})
    assert r["ok"] and any(s["name"] == "ollama" and s["port"] == 11434 for s in on_disk(home)["services"])
    r = post(base, {"section": "watch", "id": "remove_service", "value": "llama-server"})
    assert [s["name"] for s in on_disk(home)["services"]] == ["ollama"]
    segs = [s["id"] for s in rt.display.snapshot()["segments"]]
    assert "svc:ollama" in segs and "svc:llama-server" not in segs


def test_permissions_act_needs_a_yes(served):
    rt, base, _, home = served
    r = post(base, {"section": "permissions", "id": "organ:drives", "value": "act"})
    assert not r["ok"] and "Is that right?" in r["confirm"]
    assert on_disk(home)["organs"]["drives"] == "off"
    r = post(base, {"section": "permissions", "id": "organ:drives", "value": "act", "confirm": True})
    assert r["ok"] and on_disk(home)["organs"]["drives"] == "act"
    r = post(base, {"section": "permissions", "id": "organ:drives", "value": "suggest"})
    assert r["ok"] and on_disk(home)["organs"]["drives"] == "suggest"


def test_voice_names_pacing_display_lab(served):
    rt, base, calls, home = served
    assert post(base, {"section": "voice", "id": "voice", "value": "bf_emma"})["ok"]
    assert on_disk(home)["tau"]["voice_name"] == "bf_emma"
    post(base, {"section": "voice", "id": "play_sample"})
    assert calls["sample"]
    r = post(base, {"section": "voice", "id": "test_mic"})
    assert r["ok"] and any(x["kind"] == "meter" for x in r["settings"]["rows"])
    assert post(base, {"section": "voice", "id": "mic", "value": "Headset"})["ok"]
    assert on_disk(home)["audio"]["mic"] == "Headset"
    assert post(base, {"section": "voice", "id": "hands_free", "value": True})["ok"]
    assert on_disk(home)["tau"]["listen_without_wake_word"] is True
    wake = next(x for x in get(base, "settings?section=voice")["rows"] if x["id"] == "wake")
    assert wake["value"].startswith("not trained yet") and "needs" in wake["value"]

    assert post(base, {"section": "names", "id": "assistant_name", "value": "Nova"})["ok"]
    assert on_disk(home)["tau"]["name"] == "Nova" and rt.display.snapshot()["name"] == "Nova"
    assert post(base, {"section": "names", "id": "speak_to_me_as", "value": "plainly"})["ok"]

    assert post(base, {"section": "pacing", "id": "pacing:enabled", "value": True})["ok"]
    assert post(base, {"section": "pacing", "id": "pacing:max_paragraphs", "value": "2"})["ok"]
    assert post(base, {"section": "pacing", "id": "pacing:hard_day", "value": True})["ok"]
    pc = on_disk(home)["pacing"]
    assert pc["enabled"] is True and pc["max_paragraphs"] == 2 and pc["hard_day"] is True

    assert post(base, {"section": "display", "id": "corner", "value": "bottom-left"})["ok"]
    assert post(base, {"section": "display", "id": "monitor", "value": "1"})["ok"]
    assert on_disk(home)["display"]["corner"] == "bottom-left" and on_disk(home)["display"]["monitor"] == 1
    bad = post(base, {"section": "display", "id": "display_port", "value": "60000"})
    assert not bad["ok"] and "fixed port" in bad["message"]
    assert post(base, {"section": "display", "id": "display_port", "value": "47120"})["ok"]
    assert on_disk(home)["display"]["port"] == 47120
    assert post(base, {"section": "display", "id": "popout"})["ok"] and calls.get("popout")

    r = post(base, {"section": "lab", "id": "pair"})
    assert r["ok"] and any(x["id"] == "pair_code" for x in r["settings"]["rows"])
    assert post(base, {"section": "lab", "id": "hub_port", "value": "47112"})["ok"]
    assert on_disk(home)["hub"]["port"] == 47112


def test_unknown_setting_says_so(served):
    _, base, _, _ = served
    r = post(base, {"section": "brain", "id": "nonsense"})
    assert r["ok"] is False and "don't know" in r["message"]


def test_architecture_from_name_and_gguf(tmp_path):
    import struct

    from tau import engines

    assert engines.architecture("mixtral-8x7b-instruct") == "MoE"
    assert engines.architecture("gemma-4-26B-A4B-it-Q4_K_M") == "MoE"
    assert engines.architecture("llama3.1:8b") is None          # not stated
    # a minimal GGUF header: version 3, no tensors, two keys
    def kv_str(k, v):
        kb, vb = k.encode(), v.encode()
        return struct.pack("<Q", len(kb)) + kb + struct.pack("<I", 8) + struct.pack("<Q", len(vb)) + vb

    def kv_u32(k, v):
        kb = k.encode()
        return struct.pack("<Q", len(kb)) + kb + struct.pack("<I", 4) + struct.pack("<I", v)

    for experts, want in ((8, "MoE"), (None, "dense")):
        body = kv_str("general.architecture", "llama") + (kv_u32("llama.expert_count", experts) if experts else b"")
        f = tmp_path / f"m{experts}.gguf"
        f.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, 2 if experts else 1) + body)
        meta = engines.gguf_metadata(f)
        assert engines.architecture("some-model", meta) == want, meta
