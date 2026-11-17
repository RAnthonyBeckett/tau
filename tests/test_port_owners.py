"""Round 5: the node names the program on every port when the owner allows one sudo line."""

import types

import importlib

from tau.discovery import portowners

collect = importlib.import_module("tau.discovery.collect")

USER_SS = ("tcp LISTEN 0 4096 0.0.0.0:32400 0.0.0.0:*\n"
           'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=900,fd=3))\n')
ROOT_SS = ('LISTEN 0 4096 0.0.0.0:32400 0.0.0.0:* users:(("Plex Media Serv",pid=1234,fd=40))\n'
           'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=900,fd=3))\n'
           'LISTEN 0 4096 127.0.0.1:32401 0.0.0.0:* users:(("Plex Media Serv",pid=1234,fd=41))\n')


def fake_run(calls):
    def run(cmd):
        calls.append(cmd)
        if cmd[:2] == ["sudo", "-n"]:
            assert cmd[2:] == [portowners.ss_path(), "-tlnpH"]       # exactly the allowed command
            return ROOT_SS
        if cmd[0] == "ss":
            return USER_SS
        return ""
    return run


def test_without_setup_ports_stay_unnamed_and_sudo_is_never_tried(tau_home):
    calls = []
    snap = collect.collect(run=fake_run(calls), system="Linux", machine_name="node1", include_tools=False)
    assert {p["port"]: p["process"] for p in snap["ports"]}[32400] is None
    assert not any(c[0] == "sudo" for c in calls)


def test_with_setup_the_owner_is_filled_in(tau_home):
    portowners._mark()
    calls = []
    snap = collect.collect(run=fake_run(calls), system="Linux", machine_name="node1", include_tools=False)
    owners = {p["port"]: p["process"] for p in snap["ports"]}
    assert owners[32400] == "Plex Media Serv" and owners[22] == "sshd" and owners[32401] == "Plex Media Serv"


def test_setup_prints_one_line_and_never_handles_a_password(tau_home, monkeypatch):
    monkeypatch.setattr(portowners, "_on_windows", lambda: False)
    said, ran = [], []

    def run(cmd, **kw):
        ran.append(cmd)
        return types.SimpleNamespace(returncode=0)

    assert portowners.setup(say=said.append, ask=lambda q: True, run=run) == 0
    text = "\n".join(said)
    assert "NOPASSWD: " in text and text.count("NOPASSWD") == 1 and "-tlnpH" in text
    assert ["sudo", "visudo", "-cf"] == ran[0][:3] and ran[1][-1] == portowners.SUDOERS_FILE
    assert all("password" not in " ".join(c).lower() for c in ran)
    assert portowners.enabled()
    line = portowners.sudoers_line("alice", "/usr/bin/ss")
    assert line == "alice ALL=(root) NOPASSWD: /usr/bin/ss -tlnpH"
