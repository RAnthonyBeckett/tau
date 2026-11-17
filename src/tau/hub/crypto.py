"""Pairing and the encrypted hub<->node channel.

Pairing (once per node) is a PAKE: SPAKE2 (the ``spake2`` package, MIT).
  1. The hub shows a one-time code: 8 characters, valid 10 minutes, at most
     3 wrong tries before it is burned.
  2. Node (side A) and hub (side B) run SPAKE2 with that code. Each ends with
     the same key only if both used the same code.
  3. Each side proves it holds the key (HMAC key confirmation), and the hub
     sends a fresh random 32-byte node key sealed with AES-GCM under it.
  The node key is stored on both sides (owner-only file).

Because it's a PAKE, a recording of the pairing exchange can't be used to
test guesses offline: an attacker can only guess online, once per try, and
the hub burns the code after three wrong tries.

Every later connection derives a session key with HKDF from the node key and
both sides' fresh nonces. Frames are AES-GCM with a random nonce, direction
bound into the associated data, and a sequence number that must go up.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import struct
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
MAX_FRAME = 8 * 1024 * 1024


CODE_LEN = 8          # 40 bits; enough for a PAKE with three online tries
MAX_TRIES = 3
ID_NODE, ID_HUB = b"tau-node", b"tau-hub"


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch in ALPHABET)


def spake(code: str, side: str):
    """A SPAKE2 state for ``side`` "node" (A) or "hub" (B)."""
    from spake2 import SPAKE2_A, SPAKE2_B

    cls = SPAKE2_A if side == "node" else SPAKE2_B
    return cls(normalize_code(code).encode(), idA=ID_NODE, idB=ID_HUB)


def pair_keys(shared: bytes) -> tuple:
    """(confirmation key, sealing key) from the SPAKE2 result."""
    okm = HKDF(algorithm=hashes.SHA256(), length=64, salt=None, info=b"tau-pair-v2").derive(shared)
    return okm[:32], okm[32:]


def proof(key: bytes, label: bytes, *parts: bytes) -> bytes:
    return hmac.new(key, label + b"|" + b"|".join(parts), hashlib.sha256).digest()


def session_key(node_key: bytes, n_node: bytes, n_hub: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=n_node + n_hub,
                info=b"tau-session-v1").derive(node_key)


def seal(key: bytes, data: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, data, aad)


def open_sealed(key: bytes, blob: bytes, aad: bytes) -> bytes:
    return AESGCM(key).decrypt(blob[:12], blob[12:], aad)


# -- framing ---------------------------------------------------------------------------
def send_frame(sock: socket.socket, payload: bytes) -> None:
    sock.sendall(struct.pack(">I", len(payload)) + payload)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return buf


def recv_frame(sock: socket.socket) -> bytes:
    (n,) = struct.unpack(">I", recv_exact(sock, 4))
    if n > MAX_FRAME:
        raise ConnectionError("frame too large")
    return recv_exact(sock, n)


def send_json(sock: socket.socket, obj: Dict[str, Any]) -> None:
    send_frame(sock, json.dumps(obj).encode())


def recv_json(sock: socket.socket) -> Dict[str, Any]:
    return json.loads(recv_frame(sock))


class Channel:
    """An encrypted, ordered message channel over one socket."""

    def __init__(self, sock: socket.socket, key: bytes, is_hub: bool):
        self.sock = sock
        self.key = key
        self.tx_label = b"tau1:hub->node" if is_hub else b"tau1:node->hub"
        self.rx_label = b"tau1:node->hub" if is_hub else b"tau1:hub->node"
        self.tx_seq = 0
        self.rx_seq = -1

    def send(self, obj: Dict[str, Any]) -> None:
        self.tx_seq += 1
        body = json.dumps({"seq": self.tx_seq, "msg": obj}, default=str).encode()
        send_frame(self.sock, seal(self.key, body, self.tx_label))

    def recv(self) -> Dict[str, Any]:
        blob = recv_frame(self.sock)
        data = json.loads(open_sealed(self.key, blob, self.rx_label))
        if data["seq"] <= self.rx_seq:
            raise ConnectionError("replayed or out-of-order frame")
        self.rx_seq = data["seq"]
        return data["msg"]


def save_secret_json(path, data: Dict[str, Any]) -> None:
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def load_json(path) -> Optional[Dict[str, Any]]:
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
