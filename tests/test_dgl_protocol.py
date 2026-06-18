"""Regression + assumption tests for the DGL host-renderer protocol core (sgi_glremote).

Locks in the Phase-0a reverse-engineering (progress_notes/irisgl_re/dgl_protocol.md):
the opcode-table-driven decoder and the handshake server's value-return replies.

[ASSUMPTION] tags mark behaviour derived from static RE that the live capture (#55) will
confirm with ground-truth bytes.
"""
import os
import socket
import struct
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sgi_glremote.dgl import DglDecoder, DglSync, load_tables, OP_DGLVERSION  # noqa: E402
from sgi_glremote import server as glserver  # noqa: E402


@pytest.fixture(scope="module")
def table():
    return load_tables()


@pytest.fixture(scope="module")
def by_name(table):
    return {i["name"]: o for o, i in table.items() if i["name"]}


def _enc(op, *args):
    return struct.pack(">%dI" % (1 + len(args)), op, *[a & 0xFFFFFFFF for a in args])


def test_tables_load_with_known_opcodes(table):
    # The handshake/winopen opcodes from dgl_protocol.md must be present.
    names = {i["name"] for i in table.values() if i["name"]}
    assert {"gversion", "winopen", "clear"} <= names
    assert table[0x1]["name"] == "gversion"
    assert table[0x132]["name"] == "winopen"


def test_fixed_length_command_decodes(table, by_name):
    dec = DglDecoder(table)
    op = by_name["gversion"]                # cmd_words = 3
    cmds = dec.feed(_enc(op, 11, 22))
    assert cmds == [(op, "gversion", [11, 22])]
    assert dec.buf == b""


def test_variable_winopen_string_length(table, by_name):
    # [winopen][nbytes=2]["gl\0\0"] — _len_stringcmd consumes 2 + ceil(2/4)=1 word
    dec = DglDecoder(table)
    blob = struct.pack(">III", by_name["winopen"], 2, struct.unpack(">I", b"gl\0\0")[0])
    cmds = dec.feed(blob)
    assert len(cmds) == 1 and cmds[0][1] == "winopen"
    assert cmds[0][2][0] == 2
    assert dec.buf == b""


def test_partial_command_waits_for_more_bytes(table, by_name):
    dec = DglDecoder(table)
    full = _enc(by_name["gversion"], 1, 2)
    assert dec.feed(full[:4]) == []          # only the opcode word so far
    assert dec.feed(full[4:]) == [(by_name["gversion"], "gversion", [1, 2])]


def test_unknown_variable_opcode_raises_sync(table):
    # Find a variable opcode with no VAR_RULE (e.g. defrasterfont) -> DglSync.
    from sgi_glremote.dgl import VAR_RULES
    target = None
    for op, info in table.items():
        if info["cmd_words"] is None and info["name"] not in VAR_RULES:
            target = op
            break
    assert target is not None
    dec = DglDecoder(table)
    with pytest.raises(DglSync):
        dec.feed(struct.pack(">I", target))


def test_handshake_server_replies(by_name):
    """[ASSUMPTION] value-returning replies: dglversion->0, gversion->2, winopen->WID."""
    srv = glserver.DglServer(("127.0.0.1", 0), glserver.CaptureBackend(log=lambda s: None))
    host, port = srv.server_address
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = socket.create_connection((host, port))

        def read_word():
            b = b""
            while len(b) < 4:
                b += c.recv(4 - len(b))
            return struct.unpack(">I", b)[0]

        c.sendall(_enc(OP_DGLVERSION, 2))
        assert read_word() == 0
        c.sendall(_enc(by_name["gversion"], 0, 0))
        assert read_word() == 2
        c.sendall(struct.pack(">III", by_name["winopen"], 2,
                              struct.unpack(">I", b"gl\0\0")[0]))
        assert read_word() == 1            # first WID
        c.close()
        time.sleep(0.05)
    finally:
        srv.shutdown()
