import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import MockModelServer, make_profile
from tau import okf
from tau.assistant import Assistant
from tau.discovery import collect, run_discovery
from tau.discovery.labmap import LabMap
from tau.hub.server import HubServer

ROOT = Path(__file__).resolve().parents[1]

LINUX = {
    "systemctl": """ollama.service          loaded active running Ollama Service
ssh.service             loaded active running OpenBSD Secure Shell server
cups.service            loaded inactive dead  CUPS Scheduler
docker.service          loaded active running Docker Application Container Engine
""",
    "ss": """tcp   LISTEN 0      4096       127.0.0.1:11434      0.0.0.0:*    users:(("ollama",pid=812,fd=3))
tcp   LISTEN 0      128          0.0.0.0:22         0.0.0.0:*    users:(("sshd",pid=640,fd=3))
""",
    "docker": '{"Names":"web","Image":"nginx:1","State":"running","Ports":"0.0.0.0:8080->80/tcp","Status":"Up"}\n',
}

WIN_SVC = json.dumps([
    {"Name": "Spooler", "DisplayName": "Print Spooler", "State": "Running", "StartMode": "Auto", "ProcessId": 2200},
    {"Name": "OllamaSvc", "DisplayName": "Ollama", "State": "Running", "StartMode": "Auto", "ProcessId": 4100},
    {"Name": "wuauserv", "DisplayName": "Windows Update", "State": "Stopped", "StartMode": "Manual", "ProcessId": 0},
])
WIN_PORTS = json.dumps([{"LocalAddress": "127.0.0.1", "LocalPort": 11434, "OwningProcess": 4100},
                        {"LocalAddress": "0.0.0.0", "LocalPort": 445, "OwningProcess": 4}])
WIN_PROCS = json.dumps([{"Id": 4100, "ProcessName": "ollama"}, {"Id": 4, "ProcessName": "System"}])
WIN_DRIVES = json.dumps([
    {"DeviceID": "C:", "DriveType": 3, "FileSystem": "NTFS", "Size": 1e12, "FreeSpace": 4e11},
    {"DeviceID": "Z:", "DriveType": 4, "FileSystem": "NTFS", "Size": 4e12, "FreeSpace": 1e12,
     "ProviderName": "\\\\nas\\share"}])


def linux_runner(cmd):
    j = " ".join(cmd)
    if j.startswith("systemctl"):
        return LINUX["systemctl"]
    if j.startswith("ss "):
        return LINUX["ss"]
    if j.startswith("docker"):
        return LINUX["docker"]
    return ""


def windows_runner(cmd):
    j = " ".join(cmd)
    if "Win32_Service" in j:
        return WIN_SVC
    if "Get-NetTCPConnection" in j:
        return WIN_PORTS
    if "Get-Process" in j or "Win32_Process" in j:
        return WIN_PROCS
    if "Win32_LogicalDisk" in j:
        return WIN_DRIVES
    if "Get-PhysicalDisk" in j:
        return json.dumps([{"FriendlyName": "SSD", "MediaType": "SSD"}])
    return ""


@pytest.mark.parametrize("system,runner,name", [("Linux", linux_runner, "linbox"),
                                                ("Windows", windows_runner, "winbox")])
def test_discovery_writes_valid_okf_lab_map(tmp_path, system, runner, name):
    lab = tmp_path / "lab"
    snap = run_discovery(lab, machine_name=name, run=runner, system=system, include_tools=False)
    rep = okf.validate(lab)
    assert rep.ok, rep.errors
    assert (lab / f"{name}.md").exists() and (lab / "index.md").exists() and (lab / "log.md").exists()
    assert list((lab / name / "services").glob("*.md"))
    m = okf.read_concept(lab / f"{name}.md", lab)
    assert m.type == "Machine" and m.tier == "machine-confirmed" and not m.stale
    assert any(p["port"] == 11434 for p in m.meta["listening"])
    if system == "Windows":
        z = okf.read_concept(lab / name / "drives" / "Z.md", lab)
        assert z.meta["network"] and z.meta["never_walk"]
        svc = okf.read_concept(lab / name / "services" / "OllamaSvc.md", lab)
        assert svc.meta["ports"] == [11434]
    else:
        assert (lab / name / "services" / "container-web.md").exists()


def test_service_and_notification_queries_answer_from_map_with_zero_model_calls(tau_home, monkeypatch):
    server = MockModelServer()
    try:
        data = make_profile(tau_home, LOCAL_ENGINE="openai_compat", LOCAL_LLM_URL=server.url, LOCAL_MODEL="m",
                            MACHINE_NAME="linbox")
        a = Assistant(data, profile_file=tau_home / "profile.yaml")
        a.brain.ensure(data)
        run_discovery(a.lab_map.root, machine_name="linbox", run=linux_runner, system="Linux",
                      include_tools=False)
        run_discovery(a.lab_map.root, machine_name="winbox", run=windows_runner, system="Windows",
                      include_tools=False)
        r = a.handle("Tau, what services does the lab have?")
        assert "ollama" in r.say and "Spooler" in r.say and "cups" not in r.say
        r = a.handle("what's running on winbox?")
        assert "OllamaSvc" in r.say and "ssh" not in r.say
        r = a.handle("what's on port 11434?")
        assert "ollama on linbox" in r.say and "ollama on winbox" in r.say
        import tau.senses.notifications as notes
        monkeypatch.setattr(notes, "recent", lambda limit=5: [{"app": "Backup", "title": "Backup finished"}])
        r = a.handle("any notifications?")
        assert "Backup finished" in r.say
        assert server.chat_bodies() == [] and a.lanes.total_calls() == 0
    finally:
        server.close()


NODE_SCRIPT = r"""
import sys, json
sys.path.insert(0, {src!r})
from tau.hub.node import NodeClient
n = NodeClient("node-a", state={state!r})
if not n.paired():
    n.pair("127.0.0.1", {port}, {code!r})
n.connect()
snap = {{"platform": "Linux", "machine": {{"name": "node-a", "os": "Linux", "gpus": []}},
        "services": [{{"name": "nodesvc", "state": "running", "pid": 9}}], "ports": [], "drives": [],
        "containers": []}}
n.serve(snapshot=snap, state_every=0.2, once=True)
"""


def test_hub_and_node_pair_and_exchange_state(tmp_path, fake_smi):
    lab = LabMap(tmp_path / "lab")
    hub = HubServer("127.0.0.1", 0, state=tmp_path / "hub_state", on_snapshot=lab.write_snapshot).start()
    try:
        code = hub.new_pairing_code()
        script = NODE_SCRIPT.format(src=str(ROOT / "src"), state=str(tmp_path / "node_state"),
                                    port=hub.port, code=code)
        env = dict(**__import__("os").environ)
        proc = subprocess.Popen([sys.executable, "-c", script], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 30
        while "node-a" not in hub.nodes and time.time() < deadline:
            time.sleep(0.05)
        assert "node-a" in hub.nodes, proc.stderr.read() if proc.poll() is not None else "node did not connect"
        # node's snapshot landed in the lab map
        while not (tmp_path / "lab" / "node-a.md").exists() and time.time() < deadline:
            time.sleep(0.05)
        assert okf.validate(tmp_path / "lab").ok
        # the node reports its light state
        while not hub.node_states().get("node-a", {}).get("vram_total_mb") and time.time() < deadline:
            time.sleep(0.05)
        assert hub.node_states()["node-a"]["vram_total_mb"] == 24576
        # the hub calls an organ action on the node, over the encrypted channel
        gpus = hub.remote_call("node-a", "gpu", "vram", {})
        assert gpus[0]["name"] == "Fake GPU"
        proc.wait(10)
        # the pairing code is single-use and the node key is stored owner-only
        assert not (tmp_path / "hub_state" / "pairing.json").exists()
        assert "node-a" in hub.paired_nodes()
        node_file = tmp_path / "node_state" / "node.json"
        assert node_file.exists()
        if os.name != "nt":                              # POSIX permissions only
            assert (node_file.stat().st_mode & 0o077) == 0
    finally:
        hub.stop()


def test_wrong_code_is_refused(tmp_path):
    from tau.hub.node import NodeClient

    hub = HubServer("127.0.0.1", 0, state=tmp_path / "hs").start()
    try:
        hub.new_pairing_code()
        with pytest.raises(PermissionError):
            NodeClient("evil", state=tmp_path / "ns").pair("127.0.0.1", hub.port, "AAAA-BBBB-CCCC")
        assert "evil" not in hub.paired_nodes()
    finally:
        hub.stop()


def test_hub_refuses_all_interfaces_without_opt_in(tmp_path):
    with pytest.raises(PermissionError):
        HubServer("0.0.0.0", 0, state=tmp_path)
