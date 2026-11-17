"""``tau node``: the small helper on every non-hub machine.

It pairs once with a code from the hub, then keeps one outgoing encrypted
connection to the hub. It sends this machine's discovery snapshot and a light
state report, and carries out the operations the hub forwards (the hub has
already checked the user's permission). It opens no listening port.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from tau import paths
from tau.hub import crypto


def _run_cmd(args: Dict[str, Any]) -> Dict[str, Any]:
    from tau.organs.base import split_command

    proc = subprocess.run(split_command(args["command"]), capture_output=True, text=True, timeout=120)
    return {"ok": proc.returncode == 0, "code": proc.returncode, "stderr": proc.stderr[-2000:]}


def _gpu(_a):
    from tau.senses.gpu import read_gpus
    return read_gpus()


def _focused(_a):
    from tau.senses.window import focused_window
    return focused_window()


def _procs(a):
    from tau.senses.window import top_processes
    return top_processes(int(a.get("top", 5)))


def _kill(a):
    import psutil
    psutil.Process(int(a["pid"])).terminate()
    return {"ok": True}


def _notifs(a):
    from tau.senses.notifications import recent
    return recent(int(a.get("limit", 5)))


def _lasterr(_a):
    from tau.senses.cmdfail import last_failure
    return last_failure()


def _drives(_a):
    from tau.senses.drives import list_drives
    return list_drives()


def _discover(_a):
    from tau.discovery import collect
    return collect()


def _look(_a):
    from tau.connect.lab import look_here
    return look_here()


NODE_OPS: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "discover.run": _discover,
    "look.run": _look,
    "services.start": _run_cmd, "services.stop": _run_cmd, "services.restart": _run_cmd,
    "apps.run": _run_cmd,
    "gpu.vram": _gpu, "window.focused": _focused, "processes.list": _procs,
    "processes.kill": _kill, "notifications.recent": _notifs, "cmdfail.last_error": _lasterr,
    "drives.list": _drives,
}


class NodeClient:
    def __init__(self, name: str, state: Optional[Path] = None):
        self.name = name
        self.state = Path(state) if state else paths.state_dir()
        self._stop = threading.Event()
        self.channel: Optional[crypto.Channel] = None
        self._lock = threading.Lock()

    @property
    def _file(self) -> Path:
        return self.state / "node.json"

    def paired(self) -> bool:
        return self._file.exists()

    # -- pairing ---------------------------------------------------------------------------------
    def pair(self, host: str, port: int, code: str, timeout: float = 30) -> None:
        import hmac

        sp = crypto.spake(code, "node")
        msg_node = sp.start()
        with socket.create_connection((host, int(port)), timeout=timeout) as s:
            crypto.send_json(s, {"type": "pair", "node": self.name, "spake": msg_node.hex()})
            first = crypto.recv_json(s)
            if "error" in first:
                raise PermissionError(first["error"])
            msg_hub = bytes.fromhex(first["spake"])
            try:
                confirm_key, seal_key = crypto.pair_keys(sp.finish(msg_hub))
            except Exception as exc:
                raise PermissionError("the hub's pairing message didn't check out") from exc
            crypto.send_json(s, {"confirm": crypto.proof(confirm_key, b"node", msg_node, msg_hub).hex()})
            reply = crypto.recv_json(s)
            if "error" in reply:
                raise PermissionError(reply["error"])
            if not hmac.compare_digest(bytes.fromhex(reply["confirm"]),
                                       crypto.proof(confirm_key, b"hub", msg_node, msg_hub)):
                raise PermissionError("the hub's answer didn't check out")
            node_key = crypto.open_sealed(seal_key, bytes.fromhex(reply["sealed"]), b"tau-pair")
        crypto.save_secret_json(self._file, {"hub_host": host, "hub_port": int(port),
                                             "name": self.name, "key": node_key.hex()})

    # -- running -------------------------------------------------------------------------------------
    def connect(self, timeout: float = 30) -> crypto.Channel:
        info = crypto.load_json(self._file)
        if not info:
            raise RuntimeError("not paired; run `tau node --pair CODE --hub HOST:PORT`")
        s = socket.create_connection((info["hub_host"], info["hub_port"]), timeout=timeout)
        n_node = os.urandom(16)
        crypto.send_json(s, {"type": "hello", "node": self.name, "n_node": n_node.hex()})
        first = crypto.recv_json(s)
        if "error" in first:
            s.close()
            raise PermissionError(first["error"])
        key = crypto.session_key(bytes.fromhex(info["key"]), n_node, bytes.fromhex(first["n_hub"]))
        ch = crypto.Channel(s, key, is_hub=False)
        ch.send({"type": "auth"})
        if ch.recv().get("type") != "welcome":
            raise PermissionError("the hub didn't accept this node")
        s.settimeout(None)
        self.channel = ch
        return ch

    def send(self, msg: Dict[str, Any]) -> None:
        with self._lock:
            self.channel.send(msg)

    def light_state(self) -> Dict[str, Any]:
        state: Dict[str, Any] = {"at": time.time()}
        try:
            from tau.senses.gpu import read_gpus
            g = read_gpus()
            if g:
                state["vram_free_mb"] = g[0]["free_mb"]
                state["vram_total_mb"] = g[0]["total_mb"]
        except Exception:
            pass
        return state

    def serve(self, snapshot: Optional[Dict[str, Any]] = None, state_every: float = 30,
              once: bool = False) -> None:
        """Send the snapshot, then handle calls until stopped."""
        if self.channel is None:
            self.connect()
        if snapshot is not None:
            self.send({"type": "snapshot", "snapshot": snapshot})
        self.send({"type": "state", "state": self.light_state()})

        def reporter():
            while not self._stop.wait(state_every):
                try:
                    self.send({"type": "state", "state": self.light_state()})
                except Exception:
                    return

        threading.Thread(target=reporter, daemon=True).start()
        while not self._stop.is_set():
            msg = self.channel.recv()
            if msg.get("type") != "call":
                continue
            op = NODE_OPS.get(msg.get("op", ""))
            try:
                if op is None:
                    raise PermissionError(f"this node doesn't do {msg.get('op')}")
                self.send({"type": "result", "id": msg["id"], "ok": True, "data": op(msg.get("args") or {})})
            except Exception as exc:
                self.send({"type": "result", "id": msg["id"], "ok": False, "error": str(exc)})
            if once:
                break

    def stop(self) -> None:
        self._stop.set()
