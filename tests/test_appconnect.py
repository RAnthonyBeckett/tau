"""Round 6: an app connection that reports real outcomes, through a fixture app."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tau import appconnect
from tau.assistant import Assistant
from tau.work import handle_op


class FixtureApp:
    """Describes two actions; 'render' succeeds, 'export' answers 200 without confirming."""

    def __init__(self, describe=True):
        self.calls = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if self.path == "/tau/v1/describe" and describe:
                    return self._send(200, {"name": "Editor", "actions": [
                        {"id": "render", "label": "Render the timeline"}, {"id": "export", "label": "Export"}]})
                if self.path == "/tau/v1/status":
                    return self._send(200, {"project": "demo", "frames": 240})
                return self._send(404, {})

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n))
                outer.calls.append(body)
                if body["action"] == "render":
                    return self._send(200, {"ok": True, "result": "Rendered 240 frames to out.mp4"})
                return self._send(200, {"queued": True})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.address = f"127.0.0.1:{self.srv.server_address[1]}"


@pytest.fixture
def app():
    a = FixtureApp()
    yield a
    a.srv.shutdown()


def test_connect_lists_only_described_actions_and_reports_outcomes(app):
    asst = Assistant({"machine": {"name": "hub"}})
    r = handle_op(asst, {"op": "app_connect", "address": app.address})
    assert r["ok"] and "Render the timeline" in r["say"]
    aid = r["app"]["id"]
    out = handle_op(asst, {"op": "app_do", "id": aid, "action": "render"})
    assert out["ok"] and "Rendered 240 frames" in out["say"]
    out = handle_op(asst, {"op": "app_do", "id": aid, "action": "export"})
    assert out["ok"] is False and "didn't confirm" in out["say"]          # HTTP 200 is not "done"
    out = handle_op(asst, {"op": "app_do", "id": aid, "action": "delete_everything"})
    assert out["ok"] is False and [c["action"] for c in app.calls] == ["render", "export"]
    assert "frames 240" in handle_op(asst, {"op": "app_status", "id": aid})["say"]


def test_spoken_request_goes_through_the_yes_card(app):
    asst = Assistant({"machine": {"name": "hub"}})
    handle_op(asst, {"op": "app_connect", "address": app.address})
    r = asst.handle("ask editor to render the timeline")
    assert r.kind == "needs_yes" and app.calls == []                      # nothing ran before the yes
    r = asst.handle("yes")
    assert "Rendered 240 frames" in r.say and len(app.calls) == 1
    assert "doesn't offer that" in asst.handle("ask editor to delete my files").say


def test_unreachable_non_loopback_and_undescribed():
    with pytest.raises(PermissionError):
        appconnect.describe("http://192.168.1.20:8080")
    assert appconnect.describe("127.0.0.1:1")["state"] == "Unavailable"
    bare = FixtureApp(describe=False)
    try:
        d = appconnect.describe(bare.address)
        assert d["state"] == "Unavailable" and "doesn't offer TAU's connection" in d["why"]
    finally:
        bare.srv.shutdown()
