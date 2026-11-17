"""The display's local web page, served on loopback only.

* ``GET /``            the page (docked mode: it *is* the dashboard).
* ``GET /state``       current display state as JSON.
* ``GET /events``      Server-Sent Events: a new state on every change.
* ``POST /action``     clicks: ``{"segment": id}`` or ``{"core": true}``.
* ``POST /say``        typed text from the type box; the reply also lands in the conversation.
* ``GET /work``, ``POST /work``  folders, connections and jobs (see :mod:`tau.work`).
* ``GET /connections``, ``POST /connections``  connections and switches (see :mod:`tau.connect`).
* ``GET /settings?section=brain``  one Settings section as rows (see :mod:`tau.settings`).
* ``POST /settings``   ``{"section", "id", "value", "confirm"}``: one change, written to the
  profile at once; the reply says what changed and the status line repeats it.

No timers push data: the page gets a new state only when something changes.
"""

from __future__ import annotations

import json
import mimetypes
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from tau.display.model import display_state

STATIC = Path(__file__).parent / "static"


class _Server(ThreadingHTTPServer):
    # On Windows, address reuse lets a second program bind a port that is already being served,
    # so a second TAU would share the first one's port instead of noticing it.
    allow_reuse_address = os.name != "nt"


class DisplayHub:
    def __init__(self, profile: Mapping[str, Any],
                 on_action: Callable[[Dict[str, Any]], Dict[str, Any]] = lambda a: {"ok": False},
                 on_text: Callable[[str], Dict[str, Any]] = lambda t: {"ok": False},
                 on_work: Callable[[Dict[str, Any]], Dict[str, Any]] = lambda w: {"ok": False},
                 on_connections: Callable[[Dict[str, Any]], Dict[str, Any]] = lambda c: {"ok": False}):
        self.profile = profile
        self.on_work = on_work
        self.on_connections = on_connections
        self.live: Dict[str, Any] = {"state": "idle"}
        self.on_action, self.on_text = on_action, on_text
        self.settings = None            # a tau.settings.Settings, set by the runtime
        self._subs: List[queue.Queue] = []
        self._lock = threading.Lock()
        self.httpd: Optional[ThreadingHTTPServer] = None

    def snapshot(self) -> Dict[str, Any]:
        return display_state(self.profile, self.live)

    def publish(self, update: Dict[str, Any]) -> None:
        with self._lock:
            self.live.update(update)
            snap = json.dumps(self.snapshot())
            for q in list(self._subs):
                try:
                    q.put_nowait(snap)
                except queue.Full:
                    pass

    def add_message(self, who: str, text: str, kind: str = "say", **extra: Any) -> None:
        """Show a line of conversation on the display ("you" or "tau"), with the time it was said."""
        import time

        if not text:
            return
        with self._lock:
            conv = list(self.live.get("conversation") or [])
        conv.append({"who": who, "text": str(text), "kind": kind, "at": time.time(), **extra})
        self.publish({"conversation": conv[-60:]})

    def note(self, text: str) -> None:
        """One sentence on the status line saying what just changed (it fades after a few seconds)."""
        import time

        self.publish({"note": {"text": str(text), "at": time.time()}})

    def settings_view(self, section: Optional[str]) -> Dict[str, Any]:
        if self.settings is None:
            return {"error": "settings aren't available"}
        return self.settings.view(section)

    def settings_change(self, data: Mapping[str, Any]) -> Dict[str, Any]:
        if self.settings is None:
            return {"ok": False, "message": "Settings aren't available."}
        out = self.settings.change(str(data.get("section", "")), str(data.get("id", "")), data.get("value"),
                                   bool(data.get("confirm")))
        if out.get("message") and not out.get("confirm"):
            self.note(out["message"])
        else:
            self.publish({})
        return out

    def flash(self, text: str, kind: str = "warning") -> None:
        """Flash the frame and show one line that stays until dismissed."""
        import time

        self.publish({"flash": {"text": text, "kind": kind, "at": time.time()}})

    # -- server ---------------------------------------------------------------------------
    def serve(self, port: int = 0, host: str = "127.0.0.1", lan_opt_in: bool = False) -> int:
        if host not in ("127.0.0.1", "localhost", "::1") and not lan_opt_in:
            raise PermissionError("the display only listens on loopback unless you opt in")
        hub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == "/state":
                    return self._json(hub.snapshot())
                if path == "/work":
                    return self._json(hub.on_work({"op": "summary"}))
                if path == "/connections":
                    from urllib.parse import parse_qs

                    q = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                    only_on = (q.get("only_on") or ["0"])[0] == "1"
                    text = (q.get("text") or [""])[0]
                    res = hub.on_connections({"op": "view", "only_on": only_on, "text": text})
                    return self._json(res["view"] if isinstance(res, dict) and "view" in res else res)
                if path == "/settings":
                    from urllib.parse import parse_qs

                    q = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                    return self._json(hub.settings_view((q.get("section") or [None])[0]))
                if path == "/events":
                    q: queue.Queue = queue.Queue(maxsize=64)
                    hub._subs.append(q)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    try:
                        self.wfile.write(f"data: {json.dumps(hub.snapshot())}\n\n".encode())
                        self.wfile.flush()
                        while True:
                            try:
                                msg = q.get(timeout=25)
                                self.wfile.write(f"data: {msg}\n\n".encode())
                            except queue.Empty:
                                self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                    finally:
                        if q in hub._subs:
                            hub._subs.remove(q)
                    return
                rel = "index.html" if path in ("/", "") else path.lstrip("/")
                f = (STATIC / rel).resolve()
                if STATIC.resolve() not in f.parents or not f.is_file():
                    return self._json({"error": "not found"}, 404)
                body = f.read_bytes()
                ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                if f.suffix == ".js":
                    ctype = "text/javascript"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0) or 0)
                try:
                    data = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return self._json({"error": "bad json"}, 400)
                if self.path == "/action":
                    return self._json(hub.on_action(data))
                if self.path == "/say":
                    return self._json(hub.on_text(str(data.get("text", ""))))
                if self.path == "/work":
                    try:
                        return self._json(hub.on_work(data))
                    except (ValueError, LookupError, PermissionError) as exc:
                        return self._json({"ok": False, "say": str(exc)})
                    except Exception as exc:
                        import logging

                        logging.getLogger("tau.work").exception("work request failed")
                        return self._json({"ok": False, "say": f"That didn't work ({type(exc).__name__}). "
                                                               "The details are in TAU's log."})
                if self.path == "/connections":
                    try:
                        return self._json(hub.on_connections(data))
                    except (ValueError, LookupError, PermissionError) as exc:
                        return self._json({"ok": False, "say": str(exc)})
                    except Exception as exc:
                        import logging

                        logging.getLogger("tau.connections").exception("connections request failed")
                        return self._json({"ok": False, "say": f"That didn't work ({type(exc).__name__}). "
                                                               "The details are in TAU's log."})
                if self.path == "/settings":
                    try:
                        return self._json(hub.settings_change(data))
                    except Exception as exc:  # never a blank page: say what happened
                        import logging

                        logging.getLogger("tau.settings").exception("settings change failed")
                        return self._json({"ok": False, "message": f"That didn't work ({type(exc).__name__}). "
                                                                   "The details are in TAU's log."})
                return self._json({"error": "not found"}, 404)

        self.httpd = _Server((host, int(port)), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="tau-display").start()
        return self.httpd.server_address[1]

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
