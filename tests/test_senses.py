import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tau.senses import cmdfail
from tau.senses.appbridge import bridges_from_profile
from tau.senses.drives import may_walk
from tau.senses.gpu import gpu_processes, read_gpus, say_vram
from tau.senses.notifications import parse_dbus_monitor


def test_wrap_records_failure_with_stderr_tail(capsys):
    code = cmdfail.wrap([sys.executable, "-c", "import sys; sys.stderr.write('kaboom\\n'); sys.exit(3)"])
    assert code == 3
    f = cmdfail.last_failure()
    assert f["code"] == 3 and "kaboom" in f["stderr"] and f["source"] == "wrap"
    assert cmdfail.wrap([sys.executable, "-c", "pass"]) == 0
    assert cmdfail.last_failure()["code"] == 3  # success isn't recorded


def test_shell_hooks_never_claim_stderr():
    for shell, text in cmdfail.HOOKS.items():
        assert "cannot see stderr" in text, shell


def test_never_walk_rules():
    assert not may_walk("/mnt/nas/movies", ["/mnt/nas"])
    assert not may_walk("Z:\\share\\x", ["Z:"])
    assert may_walk("/home/me/proj", ["/mnt/nas"])


def test_fake_gpu(fake_smi):
    fake_smi(used=6000, procs=[{"pid": 7, "name": "/usr/bin/llama-server", "mb": 5000}])
    g = read_gpus()
    assert g[0]["free_mb"] == 24576 - 6000
    assert gpu_processes()[0]["name"] == "llama-server"
    assert "of 24 gigs" in say_vram(g)


def test_app_bridge_example():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"scene": "intro", "fps": 60}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        prof = {"app_bridges": [{"name": "demo", "kind": "http_json",
                                 "url": f"http://127.0.0.1:{srv.server_address[1]}/status"}]}
        b = bridges_from_profile(prof)["demo"]
        assert b.status()["scene"] == "intro"
        assert "scene intro" in b.summarize(b.status())
    finally:
        srv.shutdown()


def test_dbus_notification_parse():
    lines = """method call time=1 sender=:1.5 -> destination=:1.2 serial=7 path=/org/freedesktop/Notifications; interface=org.freedesktop.Notifications; member=Notify
   string "Backup"
   uint32 0
   string ""
   string "Backup finished"
   string "All files copied"
""".splitlines()
    n = parse_dbus_monitor(lines)
    assert n == [{"app": "Backup", "title": "Backup finished", "body": "All files copied"}]
