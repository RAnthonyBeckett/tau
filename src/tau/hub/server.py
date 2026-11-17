"""The hub side: accepts node pairing and node connections on the LAN.

The hub listens on the LAN address chosen at setup (never ``0.0.0.0`` unless
the user opts in with ``network.lan_opt_in: [hub]``). Only paired nodes get a
session; everything after the first hello is encrypted.
"""

from __future__ import annotations

import hmac
import os
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from tau import paths
from tau.hub import crypto

CODE_TTL = 600


class HubServer:
    def __init__(self, bind: str, port: int, state: Optional[Path] = None,
                 on_snapshot: Optional[Callable[[Dict[str, Any]], None]] = None,
                 lan_opt_in: bool = False):
        if bind in ("0.0.0.0", "::", "") and not lan_opt_in:
            raise PermissionError("the hub won't listen on every interface unless you opt in "
                                  "(network.lan_opt_in: [hub])")
        self.bind, self.port = bind, int(port)
        self.state = Path(state) if state else paths.state_dir()
        self.on_snapshot = on_snapshot
        self.on_paired: Optional[Callable[[str], None]] = None   # the runtime fills in a nickname
        self.nodes: Dict[str, Dict[str, Any]] = {}       # live: name -> {channel, state, lock}
        self._waiting: Dict[str, Dict[str, Any]] = {}
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()

    # -- pairing codes ----------------------------------------------------------------
    @property
    def _pair_file(self) -> Path:
        return self.state / "pairing.json"

    @property
    def _nodes_file(self) -> Path:
        return self.state / "nodes.json"

    def new_pairing_code(self) -> str:
        code = crypto.new_code()
        crypto.save_secret_json(self._pair_file, {"code": code, "expires": time.time() + CODE_TTL})
        return code

    def paired_nodes(self) -> Dict[str, Any]:
        return crypto.load_json(self._nodes_file) or {}

    # -- serving -------------------------------------------------------------------------
    def start(self) -> "HubServer":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":     # on Windows this would let a second hub share a port already in use
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.bind, self.port))
        s.listen(16)
        self.port = s.getsockname()[1]
        self._sock = s
        threading.Thread(target=self._accept_loop, daemon=True, name="tau-hub").start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(30)
            first = crypto.recv_json(conn)
            if first.get("type") == "pair":
                self._pair(conn, first)
            elif first.get("type") == "hello":
                self._session(conn, first)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _pair(self, conn: socket.socket, first: Dict[str, Any]) -> None:
        pending = crypto.load_json(self._pair_file)
        if not pending or pending["expires"] < time.time():
            crypto.send_json(conn, {"error": "no valid pairing code; run `tau pair` on the hub"})
            return
        s = crypto.spake(pending["code"], "hub")
        msg_hub = s.start()
        msg_node = bytes.fromhex(first["spake"])
        self._record(first, {"spake": msg_hub.hex()})
        crypto.send_json(conn, {"spake": msg_hub.hex()})
        try:
            confirm_key, seal_key = crypto.pair_keys(s.finish(msg_node))
        except Exception:
            self._wrong_try(pending, conn)
            return
        msg = crypto.recv_json(conn)
        self._record(msg)
        if not hmac.compare_digest(bytes.fromhex(msg.get("confirm", "")),
                                   crypto.proof(confirm_key, b"node", msg_node, msg_hub)):
            self._wrong_try(pending, conn)
            return
        node_key = os.urandom(32)
        reply = {"sealed": crypto.seal(seal_key, node_key, b"tau-pair").hex(),
                 "confirm": crypto.proof(confirm_key, b"hub", msg_node, msg_hub).hex()}
        # Save the key and spend the code before replying, so the node can never connect
        # (or a reader see the transcript) before the hub knows it.
        nodes = self.paired_nodes()
        nodes[first["node"]] = {"key": node_key.hex(), "paired_at": time.time()}
        crypto.save_secret_json(self._nodes_file, nodes)
        self._pair_file.unlink(missing_ok=True)  # one-time code
        self._record(reply)
        crypto.send_json(conn, reply)
        if self.on_paired is not None:
            try:
                self.on_paired(str(first["node"]))
            except Exception:
                pass

    def _wrong_try(self, pending: Dict[str, Any], conn: socket.socket) -> None:
        pending["tries"] = int(pending.get("tries", 0)) + 1
        if pending["tries"] >= crypto.MAX_TRIES:
            self._pair_file.unlink(missing_ok=True)
            crypto.send_json(conn, {"error": "wrong code; that code is now used up, run `tau pair` for a new one"})
        else:
            crypto.save_secret_json(self._pair_file, pending)
            crypto.send_json(conn, {"error": "wrong code"})

    # Tests set this to a list to see exactly what crossed the wire during pairing.
    transcript: Optional[list] = None

    def _record(self, *msgs: Dict[str, Any]) -> None:
        if self.transcript is not None:
            self.transcript.extend(dict(m) for m in msgs)

    def _session(self, conn: socket.socket, first: Dict[str, Any]) -> None:
        name = first["node"]
        info = self.paired_nodes().get(name)
        if not info:
            crypto.send_json(conn, {"error": "unknown node; pair it first"})
            return
        n_hub = os.urandom(16)
        crypto.send_json(conn, {"n_hub": n_hub.hex()})
        key = crypto.session_key(bytes.fromhex(info["key"]), bytes.fromhex(first["n_node"]), n_hub)
        ch = crypto.Channel(conn, key, is_hub=True)
        auth = ch.recv()  # fails to decrypt if the node doesn't hold the key
        if auth.get("type") != "auth":
            return
        conn.settimeout(None)
        live = {"channel": ch, "state": {}, "lock": threading.Lock(), "seen": time.time()}
        self.nodes[name] = live
        ch.send({"type": "welcome"})
        try:
            while not self._stop.is_set():
                msg = ch.recv()
                live["seen"] = time.time()
                t = msg.get("type")
                if t == "state":
                    live["state"] = msg.get("state", {})
                elif t == "snapshot":
                    if self.on_snapshot:
                        self.on_snapshot(msg["snapshot"])
                    live["snapshot_at"] = time.time()
                elif t == "result":
                    w = self._waiting.get(msg.get("id"))
                    if w:
                        w["result"] = msg
                        w["event"].set()
        finally:
            if self.nodes.get(name) is live:
                del self.nodes[name]

    # -- calling nodes -----------------------------------------------------------------------
    def remote_call(self, machine: str, organ: str, action: str, args: Dict[str, Any],
                    timeout: float = 30) -> Any:
        live = self.nodes.get(machine)
        if not live:
            raise ConnectionError(f"{machine} isn't connected right now")
        cid = uuid.uuid4().hex
        w = {"event": threading.Event(), "result": None}
        self._waiting[cid] = w
        with live["lock"]:
            live["channel"].send({"type": "call", "id": cid, "op": f"{organ}.{action}", "args": args})
        try:
            if not w["event"].wait(timeout):
                raise TimeoutError(f"{machine} didn't answer in {timeout:.0f}s")
        finally:
            self._waiting.pop(cid, None)
        res = w["result"]
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or "the node reported an error")
        return res.get("data")

    def node_states(self) -> Dict[str, Dict[str, Any]]:
        return {n: dict(v["state"]) for n, v in self.nodes.items()}
