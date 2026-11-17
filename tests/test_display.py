import json
import urllib.error
import urllib.request

import pytest

from conftest import make_profile
from tau.display.model import build_segments
from tau.display.popout import corner_position
from tau.display.server import DisplayHub


def test_segments_come_from_profile_and_rank_by_importance(tau_home):
    data = make_profile(tau_home, LOCAL_LLM_URL="http://x", LOCAL_MODEL="m",
                        SERVICES=[{"name": "alpha", "importance": 2}, {"name": "beta"}])
    data["organs"]["gpu"] = "sense"
    segs = build_segments(data, {"lane": "burst", "pins": 2, "services": {"alpha": True, "beta": False},
                                 "vram_free_gb": 1.0})
    by = {s["id"]: s for s in segs}
    assert by["brain"]["ring"] == 0 and by["brain"]["accent"] == "cyan"
    assert by["svc:alpha"]["ring"] < by["svc:beta"]["ring"]
    assert by["svc:beta"]["status"] == "attention" and by["pins"]["status"] == "attention"
    assert by["gpu"]["status"] == "attention"  # under the profile's VRAM floor
    assert [s["ring"] for s in segs] == sorted(s["ring"] for s in segs)


def test_server_loopback_state_events_and_actions(tau_home):
    with pytest.raises(PermissionError):
        DisplayHub({}).serve(0, host="0.0.0.0")
    got = []
    hub = DisplayHub(make_profile(tau_home), on_action=lambda a: got.append(a) or {"ok": True})
    port = hub.serve(0)
    base = f"http://127.0.0.1:{port}"
    try:
        assert b"<title>TAU</title>" in urllib.request.urlopen(base + "/").read()
        assert b"three.js" in urllib.request.urlopen(base + "/vendor/three.module.min.js").read(400).lower()
        hub.publish({"state": "listening"})
        assert json.loads(urllib.request.urlopen(base + "/state").read())["state"] == "listening"
        with urllib.request.urlopen(base + "/events", timeout=5) as r:
            first = r.readline().decode()
        assert first.startswith("data: ") and json.loads(first[6:])["state"] == "listening"
        req = urllib.request.Request(base + "/action", data=b'{"core": true}', method="POST",
                                     headers={"Content-Type": "application/json"})
        assert json.loads(urllib.request.urlopen(req).read())["ok"] and got == [{"core": True}]
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/vendor/../../server.py")   # nothing outside static/
        assert e.value.code == 404
    finally:
        hub.stop()


def test_popout_parks_in_the_chosen_corner():
    mon = {"x": 1920, "y": 0, "w": 2560, "h": 1440}
    assert corner_position("top-right", mon, 320) == (1920 + 2560 - 320 - 16, 16)
    assert corner_position("bottom-left", mon, 320) == (1920 + 16, 1440 - 320 - 16)
