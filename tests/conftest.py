import json
import os
import stat
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(autouse=True)
def tau_home(tmp_path, monkeypatch):
    home = tmp_path / "tauhome"
    home.mkdir()
    monkeypatch.setenv("TAU_HOME", str(home))
    monkeypatch.delenv("TAU_NVIDIA_SMI", raising=False)
    return home


class MockModelServer:
    """OpenAI-compatible and Ollama-style endpoints that record every request."""

    def __init__(self, reply="ready", fail=False, models=("mock-model",)):
        self.reply, self.fail, self.models = reply, fail, list(models)
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.requests.append({"method": "GET", "path": self.path, "body": None,
                                       "headers": dict(self.headers)})
                if self.path.endswith("/v1/models"):
                    return self._send(200, {"data": [{"id": m} for m in outer.models]})
                if self.path.endswith("/api/tags"):
                    return self._send(404, {})
                if self.path.endswith("/health"):
                    return self._send(200, {"ok": True})
                return self._send(404, {})

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n).decode()
                outer.requests.append({"method": "POST", "path": self.path, "body": raw,
                                       "headers": dict(self.headers)})
                if outer.fail:
                    return self._send(500, {"error": "boom"})
                if self.path.endswith("/api/chat"):
                    return self._send(200, {"model": outer.models[0], "done": True,
                                            "message": {"role": "assistant", "content": outer.reply}})
                return self._send(200, {"id": "x", "object": "chat.completion", "model": outer.models[0],
                                        "choices": [{"index": 0, "finish_reason": "stop",
                                                     "message": {"role": "assistant", "content": outer.reply}}],
                                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def chat_bodies(self):
        return [r["body"] for r in self.requests if r["method"] == "POST"]

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def mock_server():
    s = MockModelServer()
    yield s
    s.close()


@pytest.fixture
def fake_smi(tmp_path, monkeypatch):
    """Write a fake nvidia-smi whose output comes from a JSON file the test edits."""
    state = tmp_path / "smi.json"
    state.write_text(json.dumps({"total": 24576, "used": 2000, "procs": []}))
    script = tmp_path / "fake_smi.py"
    script.write_text(textwrap.dedent(f"""
        import json, sys
        s = json.load(open({str(state)!r}))
        args = " ".join(sys.argv[1:])
        if "query-compute-apps" in args:
            for p in s["procs"]:
                print(f"{{p['pid']}}, {{p['name']}}, {{p['mb']}}")
        else:
            print(f"0, Fake GPU, {{s['total']}}, {{s['used']}}, {{s['total'] - s['used']}}")
    """))
    monkeypatch.setenv("TAU_NVIDIA_SMI", str(script))

    def set_state(**kw):
        d = json.loads(state.read_text())
        d.update(kw)
        state.write_text(json.dumps(d))

    return set_state


def make_profile(home: Path, **overrides) -> dict:
    """A filled profile for tests (every placeholder answered)."""
    from tau.profile import fill_template

    values = {
        "ASSISTANT_NAME": "TAU", "WAKE_WORD": "tau", "VOICE_NAME": "af_heart", "ROLE": "hub",
        "BRAIN_DIR": str(home / "tau_brain"), "LOG_DIR": str(home / "log"),
        "MACHINE_NAME": "test-hub", "OS_USER": "tester", "USER_NAME": "Sam",
        "WORKSPACE_ROOTS": [str(home / "ws")], "ALLOWED_PATHS": [str(home / "ws")],
        "ALLOWED_COMMANDS": ["echo *"], "HEAVY_APPS": ["bigapp"], "MIN_FREE_VRAM_GB": 4,
        "SERVICES": [], "APP_PREP": [], "MODEL_CHOICES": [],
    }
    values.update(overrides)
    text = fill_template(values, skip_missing=True)
    data = yaml.safe_load(text)
    (home / "profile.yaml").write_text(text)
    (home / "ws").mkdir(exist_ok=True)
    return data
