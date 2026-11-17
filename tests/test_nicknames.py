"""Round 5: machine names and nicknames resolve the same way for every lab question."""

from tau.discovery.labmap import LabMap


def snap(name, services=(), ports=()):
    return {"machine": {"name": name, "os": "Linux"}, "platform": "Linux",
            "services": [{"name": s, "state": "running"} for s in services],
            "ports": [{"port": p, "address": "0.0.0.0", "process": "plex"} for p in ports]}


def lab(tmp_path, nicks=None):
    m = LabMap(tmp_path / "lab", nicknames=lambda: nicks or {})
    m.write_snapshot(snap("Halcyon", ["plexmediaserver", "sshd"], [32400]))
    m.write_snapshot(snap("Bertha", ["nginx"]))
    return m


def test_case_prefix_and_full_name(tmp_path):
    m = lab(tmp_path)
    for spoken in ("Hal", "hal", "HALCYON", "Halcyon", "halcyon?"):
        r = m.answer_services(machine=spoken)
        assert "plexmediaserver" in r["say"] and "on Halcyon" in r["say"], spoken
    assert "plex" in m.answer_port(32400, machine="hal")["say"]
    assert "sshd" in m.answer_machine("HAL")["say"]


def test_nickname_from_settings(tmp_path):
    m = lab(tmp_path, {"Bertha": ["the big box", "bb"]})
    assert "nginx" in m.answer_services(machine="BB")["say"]
    assert "nginx" in m.answer_machine("the big box")["say"]


def test_unknown_name_lists_the_known_machines(tmp_path):
    m = lab(tmp_path, {"Bertha": ["bb"]})
    r = m.answer_services(machine="Zeus")
    assert "don't know a machine called Zeus" in r["say"]
    assert "Halcyon" in r["say"] and "Bertha (bb)" in r["say"]
    assert "Zeus" in m.answer_port(22, machine="Zeus")["say"]


def test_ambiguous_prefix_does_not_guess(tmp_path):
    m = LabMap(tmp_path / "lab")
    m.write_snapshot(snap("alpha1"))
    m.write_snapshot(snap("alpha2"))
    assert m.resolve("alp") is None and m.resolve("alpha2") == "alpha2"


def test_settings_nickname_row_and_prompt_lines(tmp_path, monkeypatch):
    from tau.settings import Settings

    st = Settings({"tau": {"role": "hub"}}, tmp_path / "p.yaml")
    monkeypatch.setattr(st, "_paired", lambda: ["halcyon.lan"])
    rows = {r["id"]: r for r in st.view("lab")["rows"]}
    assert rows["nickname:halcyon.lan"]["value"] == "halcyon"        # the short hostname
    out = st.change("lab", "nickname:halcyon.lan", "Hal, the library")
    assert out["ok"] and st.profile["lab"]["nicknames"]["halcyon.lan"] == ["Hal", "the library"]
