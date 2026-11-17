"""SPAKE2 pairing over a real socket: right code, wrong code, burned code,
and a recorded transcript that can't be used to check guesses offline."""
import pytest

from tau.hub import crypto
from tau.hub.node import NodeClient
from tau.hub.server import HubServer


@pytest.fixture
def hub(tmp_path):
    h = HubServer("127.0.0.1", 0, state=tmp_path / "hub").start()
    h.transcript = []
    yield h
    h.stop()


def test_code_is_short_enough_to_type():
    code = crypto.new_code()
    assert len(code) == 9 and code[4] == "-" and len(crypto.normalize_code(code)) == 8
    assert crypto.normalize_code(code.lower().replace("-", " ")) == crypto.normalize_code(code)


def test_pair_then_session_works(hub, tmp_path):
    code = hub.new_pairing_code()
    n = NodeClient("node-a", state=tmp_path / "node")
    n.pair("127.0.0.1", hub.port, code.lower())          # typed in lower case, still fine
    assert "node-a" in hub.paired_nodes()
    assert crypto.load_json(tmp_path / "node" / "node.json")["key"] == hub.paired_nodes()["node-a"]["key"]
    n.connect()                                          # the stored key opens an encrypted session
    with pytest.raises(PermissionError):                 # the code is single-use
        NodeClient("node-b", state=tmp_path / "nb").pair("127.0.0.1", hub.port, code)


def test_wrong_code_fails_cleanly_and_burns_after_three(hub, tmp_path):
    code = hub.new_pairing_code()
    wrong = "ABCD-EFGH" if crypto.normalize_code(code) != "ABCDEFGH" else "HGFE-DCBA"
    for i in range(2):
        with pytest.raises(PermissionError, match="wrong code"):
            NodeClient("evil", state=tmp_path / f"e{i}").pair("127.0.0.1", hub.port, wrong)
    assert "evil" not in hub.paired_nodes() and not (tmp_path / "e0" / "node.json").exists()
    with pytest.raises(PermissionError, match="used up"):
        NodeClient("evil", state=tmp_path / "e3").pair("127.0.0.1", hub.port, wrong)
    with pytest.raises(PermissionError):                 # even the right code is dead now
        NodeClient("node-a", state=tmp_path / "ok").pair("127.0.0.1", hub.port, code)


def test_recorded_transcript_does_not_verify_a_guessed_code(hub, tmp_path):
    code = hub.new_pairing_code()
    NodeClient("node-a", state=tmp_path / "node").pair("127.0.0.1", hub.port, code)
    first, hub_msg, node_confirm, hub_reply = hub.transcript
    msg_node = bytes.fromhex(first["spake"])
    msg_hub = bytes.fromhex(hub_msg["spake"])
    recorded = bytes.fromhex(node_confirm["confirm"])
    # An eavesdropper tries guesses against the recording, including the real code.
    for guess in ("ZZZZ-ZZZZ", "AB23-CD45", code):
        attacker = crypto.spake(guess, "node")
        attacker.start()
        ck, sk = crypto.pair_keys(attacker.finish(msg_hub))
        assert crypto.proof(ck, b"node", msg_node, msg_hub) != recorded, guess
        with pytest.raises(Exception):
            crypto.open_sealed(sk, bytes.fromhex(hub_reply["sealed"]), b"tau-pair")
    # nothing in the transcript is a hash or salt of the code
    flat = str(hub.transcript).upper()
    assert crypto.normalize_code(code) not in flat and "SALT" not in flat
