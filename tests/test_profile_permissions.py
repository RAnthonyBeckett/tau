import yaml
import pytest

from conftest import make_profile
from tau import profile as prof
from tau.actionlog import ActionLog
from tau.organs import ALLOWED, DENIED, NEEDS_YES, OFF, Organs, decide
from tau.organs.grants import apply_level, grant


def test_unfilled_placeholders_fail_profile_check():
    data = yaml.safe_load(prof.template_text())
    problems = prof.check_profile(data)
    assert any("unfilled placeholder" in p for p in problems)
    with pytest.raises(prof.ProfileError):
        p = prof.paths.tau_home() / "profile.yaml"
        p.write_text(prof.template_text())
        prof.load_profile(p)


def test_filled_profile_passes(tau_home):
    data = make_profile(tau_home)
    assert prof.check_profile(data) == []
    assert prof.load_profile()["tau"]["name"] == "TAU"
    # comments survive filling
    assert "# TAU profile template" in (tau_home / "profile.yaml").read_text()


def test_one_missed_placeholder_fails(tau_home):
    text = prof.fill_template({"ASSISTANT_NAME": "TAU"})  # most left unfilled
    assert prof.placeholders_in_text(text)
    assert prof.check_profile(yaml.safe_load(text))


def test_permission_rule_table():
    assert decide("off", "read") == OFF
    assert decide("sense", "read") == ALLOWED
    assert decide("sense", "change") == DENIED
    assert decide("suggest", "change") == NEEDS_YES
    assert decide("act", "change") == ALLOWED
    assert decide("act", "change", destructive=True) == NEEDS_YES


def test_levels_block_and_allow(tau_home):
    data = make_profile(tau_home)
    log = ActionLog(tau_home / "log")
    o = Organs(data, log)
    # fresh profile: files is off
    assert o.call("files", "read", path=str(tau_home / "ws" / "a.txt")).status == OFF
    data["organs"]["files"] = "sense"
    (tau_home / "ws" / "a.txt").write_text("hello")
    assert o.call("files", "read", path=str(tau_home / "ws" / "a.txt")).status == ALLOWED
    assert o.call("files", "write", path=str(tau_home / "ws" / "b.txt"), text="x").status == DENIED
    data["organs"]["files"] = "suggest"
    out = o.call("files", "write", path=str(tau_home / "ws" / "b.txt"), text="x")
    assert out.status == NEEDS_YES and not (tau_home / "ws" / "b.txt").exists()
    assert o.confirm(out.pending_id).status == ALLOWED
    assert (tau_home / "ws" / "b.txt").read_text() == "x"
    data["organs"]["files"] = "act"
    assert o.call("files", "write", path=str(tau_home / "ws" / "c.txt"), text="y").status == ALLOWED
    # destructive still needs a yes at act
    d = o.call("files", "delete", path=str(tau_home / "ws" / "c.txt"))
    assert d.status == NEEDS_YES and (tau_home / "ws" / "c.txt").exists()
    # allow-list: outside the workspace is refused even at act
    assert o.call("files", "read", path=str(tau_home / "profile.yaml")).status == DENIED
    rows = log.tail(50)
    assert {r["decision"] for r in rows} >= {"off", "allowed", "denied", "needs_yes"}


def test_fresh_profile_defaults(tau_home):
    data = make_profile(tau_home)
    o = Organs(data, ActionLog(tau_home / "log"))
    for organ in ("voice", "display", "chat"):
        assert o.level(organ) == "act"
    for organ in ("services", "notifications"):
        assert o.level(organ) == "sense"
    assert o.level("gpu") == "off" and o.level("handoff") == "off"


def test_command_allow_list(tau_home):
    data = make_profile(tau_home)
    o = Organs(data, ActionLog(tau_home / "log"))
    o.check_command("echo hi")
    with pytest.raises(PermissionError):
        o.check_command("rm -rf something")
    o.check_command("anything the user wrote", trusted=True)


def test_grant_repeats_and_asks(tau_home):
    make_profile(tau_home)
    asked = []
    msg = grant("gpu", "sense", lambda q: asked.append(q) or False)
    assert "nothing changed" in msg and asked and "gpu" in asked[0]
    assert prof.load_profile()["organs"]["gpu"] == "off"
    grant("gpu", "sense", lambda q: True)
    assert prof.load_profile()["organs"]["gpu"] == "sense"
    assert "# TAU profile template" in (tau_home / "profile.yaml").read_text()
    apply_level("services", "act", machine="node-a")
    lv = prof.load_profile()["organs"]["services"]
    assert lv == {"default": "sense", "machines": {"node-a": "act"}}


def test_organs_register_as_openjarvis_tools(tau_home):
    pytest.importorskip("openjarvis")
    from openjarvis.core.registry import ToolRegistry

    from tau import oj_bridge

    names = oj_bridge.register_with_openjarvis()
    assert "tau_services_list" in names and ToolRegistry.contains("tau_gpu_vram")
    data = make_profile(tau_home)
    o = Organs(data, ActionLog(tau_home / "log"))
    oj_bridge.bind(o)
    tool = ToolRegistry.get("tau_gpu_vram")()
    res = tool.execute()
    assert res.success is False and res.metadata["status"] == "off"
