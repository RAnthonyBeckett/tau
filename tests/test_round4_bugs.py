"""Round 4 item 4: the bugs found on the owner's Windows PC after 1.0.0."""
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from conftest import ROOT, make_profile


# a. Setup's questions reach stdout even when stderr goes nowhere (quiet.py sends it to the log).
def test_setup_questions_reach_stdout_with_stderr_discarded(tau_home):
    make_profile(tau_home)
    env = dict(os.environ, TAU_HOME=str(tau_home), PYTHONPATH=str(ROOT / "src"))
    r = subprocess.run([sys.executable, "-m", "tau", "setup", "names"], input="Nova\nRob\nplainly\n",
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env, timeout=120)
    assert "What should I be called?" in r.stdout
    assert "What's your name?" in r.stdout and "How would you like me to speak to you?" in r.stdout
    assert yaml.safe_load((tau_home / "profile.yaml").read_text())["tau"]["name"] == "Nova"


# b. No console window flashes: every Popen gets CREATE_NO_WINDOW on Windows.
def test_every_subprocess_gets_no_window_flag_on_windows():
    import tau

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

    assert tau._no_console_windows(FakePopen, nt=True) is True
    assert FakePopen(["anything"]).kwargs["creationflags"] & 0x08000000
    assert FakePopen(["x"], creationflags=0x200).kwargs["creationflags"] == 0x200 | 0x08000000   # kept, not replaced
    assert tau._no_console_windows(FakePopen, nt=True) is False        # patched once only

    class LinuxPopen(FakePopen):
        pass

    LinuxPopen._tau_quiet = False
    assert tau._no_console_windows(LinuxPopen, nt=False) is False


@pytest.mark.skipif(os.name != "nt", reason="the real Popen is patched on Windows only")
def test_real_popen_is_patched_on_windows():
    import tau  # noqa: F401

    assert getattr(subprocess.Popen, "_tau_quiet", False)


# c. The Start-menu shortcut never points into another account's profile.
def test_shortcut_target_must_be_in_the_installing_users_profile(tmp_path, monkeypatch):
    from tau import launcher

    users = tmp_path / "Users"
    me, admin = users / "owner", users / "Administrator"
    me.mkdir(parents=True)
    assert launcher.other_users_profile(admin / "AppData/Local/TAU/venv", home=me) == "Administrator"
    assert launcher.other_users_profile(me / "AppData/Local/TAU/venv", home=me) is None
    assert launcher.other_users_profile(tmp_path / "elsewhere/TAU/venv", home=me) is None   # custom TAU_INSTALL_DIR
    monkeypatch.setattr(launcher, "_windows", lambda: True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: me))
    with pytest.raises(RuntimeError, match="another account"):
        launcher.install(venv=admin / "AppData/Local/TAU/venv")


def test_installer_refuses_another_accounts_window():
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "explorer.exe" in text and "GetOwner" in text and "-AllowElevated" in text
    assert "$deskUser -ne $env:USERNAME" in text


# d. Services are saved by fixed port, never temporary ones.
def test_temporary_ports_map_to_parent_or_are_skipped():
    from tau.senses.starter import starter_services
    from test_settings import SNAP

    rows = {s["name"]: s["port"] for s in starter_services(SNAP)}
    assert rows == {"llama-server": 8080, "ollama": 11434, "postgres": 5432}
    # a router child alone (parent listening on 8080) still maps to 8080
    only_child = {"ports": [{"port": 8080, "pid": 1, "process": "llama-server"},
                            {"port": 58204, "pid": 2, "process": "llama-server-child"}],
                  "procs": {"2": {"name": "llama-server-child", "ppid": 1}, "1": {"name": "llama-server", "ppid": 0}}}
    assert [(s["name"], s["port"]) for s in starter_services(only_child)] == [("llama-server", 8080)]


def test_old_profiles_are_repaired_and_temporary_ports_never_read_down(tau_home):
    from tau.senses.heartbeat import check_all
    from tau.settings import DEFAULT_DISPLAY_PORT, DEFAULT_HUB_PORT, repair_profile

    data = make_profile(tau_home, DISPLAY_PORT=58321, HUB_PORT=60111, SERVICES=[
        {"name": "llama-server", "port": 58204, "found_by": "discovery"},
        {"name": "ollama app", "port": 60812, "found_by": "discovery"},
        {"name": "web", "port": 8080, "found_by": "discovery"}])
    assert check_all(data)["llama-server"] is None             # unknown, not "down"
    said = repair_profile(data)
    assert data["display"]["port"] == DEFAULT_DISPLAY_PORT and data["hub"]["port"] == DEFAULT_HUB_PORT
    assert [s["name"] for s in data["services"]] == ["web"]
    assert any("llama-server, ollama app" in s for s in said)


def test_runtime_repairs_and_says_so(tau_home):
    from tau.runtime import Runtime

    data = make_profile(tau_home, DISPLAY_PORT=58321, SERVICES=[{"name": "claude", "port": 60371, "found_by": "discovery"}])
    rt = Runtime(data).build(want_voice=False)
    on_disk = yaml.safe_load((tau_home / "profile.yaml").read_text())
    assert on_disk["display"]["port"] == 47110 and on_disk["services"] == []
    assert any("claude" in line for line in rt.repairs)


# e. The hub and display use fixed ports: a default the owner can change, never 0 or temporary.
def test_setup_offers_fixed_ports(tau_home):
    from tau.setup import ScriptedAsker, Setup

    make_profile(tau_home, DISPLAY_PORT=47110, HUB_PORT=47111, ROLE="hub")
    asker = ScriptedAsker(["60000", "47200", None])     # a temporary port is refused, then 47200, then Enter
    Setup(asker, profile_file=tau_home / "profile.yaml").run("ports")
    d = yaml.safe_load((tau_home / "profile.yaml").read_text())
    assert d["display"]["port"] == 47200 and d["hub"]["port"] == 47111
    assert any("1024 to 49151" in s for s in asker.said)


def test_first_run_ports_are_fixed_defaults(tau_home, monkeypatch):
    from tau import detect
    from tau.settings import EPHEMERAL_LOW
    from tau.setup import Setup, ScriptedAsker

    s = Setup(ScriptedAsker([]), profile_file=tau_home / "profile.yaml")
    s.values["ROLE"] = "hub"
    s.topic_display()
    port = s.values["DISPLAY_PORT"]
    assert port == detect.fixed_port(47110) and 1024 <= port < EPHEMERAL_LOW
    s.topic_ports()
    assert s.values["DISPLAY_PORT"] == port


def _free_port_pair(start: int = 47300) -> int:
    # Some machines refuse whole blocks of ports (seen: several thousand in a row), so look
    # above the usual spot first and then below it, staying under the temporary-port range.
    from tau.settings import EPHEMERAL_LOW

    for base in list(range(start, EPHEMERAL_LOW - 2, 2)) + list(range(start - 2, 20000, -2)):
        s1, s2 = socket.socket(), socket.socket()
        try:
            s1.bind(("127.0.0.1", base))
            s2.bind(("127.0.0.1", base + 1))
            return base
        except OSError:
            continue
        finally:
            s1.close()
            s2.close()
    raise RuntimeError(f"no free port pair found from {start}")


def test_display_keeps_its_port_and_finds_a_running_tau(tau_home):
    from tau.runtime import Runtime

    base = _free_port_pair()
    data = make_profile(tau_home, DISPLAY_PORT=base)
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", base))
    blocker.listen(1)
    try:
        rt = Runtime(data).build(want_voice=False)
        got = rt.serve_display()
        assert got == base + 1                                      # next fixed port, not a random one
        assert yaml.safe_load((tau_home / "profile.yaml").read_text())["display"]["port"] == base + 1
        rt2 = Runtime(yaml.safe_load((tau_home / "profile.yaml").read_text())).build(want_voice=False)
        assert rt2.serve_display() is None                          # TAU already answers there
        rt.display.stop()
    finally:
        blocker.close()


# f. The model list says MoE or dense.
def test_setup_model_labels_say_moe_or_dense():
    from tau import engines
    from tau.setup import _model_label

    ranked, _ = engines.rank_models(["Qwen3-30B-A3B", "llama3.1-8b"], 24, 64,
                                    meta={"llama3.1-8b": {"llama.expert_count": 0}})
    labels = [_model_label(r) for r in ranked]
    assert any("Qwen3-30B-A3B" in l and "MoE" in l for l in labels)
    assert any("llama3.1-8b" in l and "dense" in l for l in labels)
