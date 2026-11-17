import json

from conftest import MockModelServer, make_profile
from tau.actionlog import ActionLog
from tau.lanes import Lanes, state_header
from tau.organs import Organs


def _profile(home, local, burst):
    return make_profile(home, LOCAL_ENGINE="openai_compat", LOCAL_LLM_URL=local.url, LOCAL_MODEL="loc",
                        BURST_LLM_URL=burst.url, BURST_MODEL="cloud", BURST_API_KEY_ENV="NOPE",
                        BRAIN_PREFER="local")


def test_gpu_rule_switches_lanes_on_faked_nvidia_smi(tau_home, fake_smi):
    local, burst = MockModelServer(reply="from local"), MockModelServer(reply="from burst")
    try:
        data = _profile(tau_home, local, burst)
        data["organs"]["gpu"] = "sense"
        o = Organs(data, ActionLog(tau_home / "log"))
        lanes = Lanes(data, o)
        msgs = [{"role": "user", "content": "hi"}]
        # card mostly free, no heavy app -> local
        fake_smi(used=2000, procs=[])
        assert lanes.chat(msgs) == ("from local", "local")
        # a heavy app named in the profile takes the card -> burst
        fake_smi(used=20000, procs=[{"pid": 42, "name": "C:\\Games\\BigApp.exe", "mb": 18000}])
        reply, lane = lanes.chat(msgs)
        assert lane == "burst" and reply == "from burst"
        assert "BigApp" in lanes.last_choice.reason
        # app gone but VRAM under the floor (4 GB) and nothing resident... local server claims a model,
        # so TAU stays local rather than loading a second model elsewhere
        fake_smi(used=23000, procs=[])
        assert lanes.choose().lane == "local"
        # gpu organ off -> TAU doesn't look, uses the preferred lane
        data["organs"]["gpu"] = "off"
        fake_smi(used=20000, procs=[{"pid": 42, "name": "bigapp", "mb": 18000}])
        assert lanes.choose().lane == "local"
    finally:
        local.close()
        burst.close()


def test_privacy_local_only_never_uses_burst(tau_home, fake_smi):
    local, burst = MockModelServer(reply="L"), MockModelServer(reply="B")
    try:
        data = _profile(tau_home, local, burst)
        data["organs"]["gpu"] = "sense"
        lanes = Lanes(data, Organs(data, ActionLog(tau_home / "log")))
        fake_smi(used=20000, procs=[{"pid": 1, "name": "bigapp", "mb": 18000}])
        assert lanes.choose(privacy_local_only=True).lane == "local"
        assert lanes.chat([{"role": "user", "content": "x"}], privacy_local_only=True)[1] == "local"
        assert not burst.chat_bodies()
    finally:
        local.close()
        burst.close()


def test_state_header_is_small_and_only_from_allowed_organs(tau_home, fake_smi):
    data = make_profile(tau_home)
    o = Organs(data, ActionLog(tau_home / "log"))
    h = json.loads(state_header(data, o, {"ws": "proj"}))
    assert "vram" not in h and h["ws"] == "proj"
    data["organs"]["gpu"] = "sense"
    s = state_header(data, o, {"ws": "proj"})
    h = json.loads(s)
    assert h["vram"].endswith("G free")
    assert len(s) < 300  # roughly 60 tokens
