"""Offline smoke test for the dgld-proxy host renderer: drive a ProxySession with the PVSH header,
the live-captured login handshake, and a synthetic framed gltri GL sequence — assert it replies to
the handshake AND emits a red-triangle frame to a mock QEMU. Requires OSMesa (container only)."""
import os
import socket
import struct
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("sgi_glremote.osmesa_backend", reason="PyOpenGL/OSMesa not available")
from sgi_glremote.proxy_renderer import ProxySession
from sgi_glremote.dgl import load_tables


def _frame(words):
    body = struct.pack(">%dI" % len(words), *[w & 0xFFFFFFFF for w in words])
    return struct.pack(">I", 0x10000000 | len(body)) + body


def _f(x):
    return struct.unpack(">I", struct.pack(">f", x))[0]


def test_proxy_handshake_and_triangle():
    by_name = {i["name"]: o for o, i in load_tables().items() if i["name"]}

    # mock QEMU pvrex3 gl-listen: accept one PVGL frame
    got = {}
    port = _free_port()

    def mock_qemu():
        srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port)); srv.listen(1)
        c, _ = srv.accept()
        buf = b""
        while len(buf) < 20:
            d = c.recv(65536)
            if not d:
                break
            buf += d
        x, y, w, h = struct.unpack("<iiii", buf[4:20])
        need = 20 + w * h * 4
        while len(buf) < need:
            d = c.recv(65536)
            if not d:
                break
            buf += d
        got["rect"] = (x, y, w, h)
        got["rgba"] = buf[20:need]
        c.close(); srv.close()

    t = threading.Thread(target=mock_qemu, daemon=True); t.start()

    sess = ProxySession(("127.0.0.1", port))
    # feed everything through the framed connection directly (bypass the socket recv loop)
    out = b""
    out += sess.conn.feed(b"\x00\x00\x12\x34")                 # byte-order probe
    assert out == b"\xff\xff\xed\xcc"
    sess.win = (1, 320, 260, 640, 480)

    # synthetic gltri GL stream, each command framed
    def G(name, *args):
        return _frame([by_name[name]] + list(args))

    def Gq(name, *args):                       # value-returning: frame ends with reply-sync
        return _frame([by_name[name]] + list(args) + [0x10004, 0])
    stream = b""
    stream += Gq("winopen", 2, struct.unpack(">I", b"gl\0\0")[0])
    stream += G("ortho2", _f(0.0), _f(640.0), _f(0.0), _f(480.0))
    stream += G("RGBcolor", (255 << 16), 0)
    stream += G("bgnpolygon")
    for x, y in ((100.0, 100.0), (500.0, 100.0), (300.0, 400.0)):
        stream += G("v2f", _f(x), _f(y))
    stream += G("endpolygon")
    stream += G("gflush")
    reply = sess.conn.feed(stream)
    # winopen should have returned a framed WID
    assert reply.startswith(struct.pack(">I", 0x10000004)), reply.hex()

    t.join(timeout=5)
    assert got.get("rect") == (320, 260, 640, 480), got.get("rect")
    import numpy as np
    arr = np.frombuffer(got["rgba"], np.uint8).reshape(480, 640, 4)
    px = tuple(int(v) for v in arr[480 - 200, 300][:3])
    assert px == (255, 0, 0), px


def test_emit_frame_sends_full_overlay_plus_clip_pieces():
    """#80: when the window registry carries visible clip pieces, _emit_frame sends ONE full-window
    PVGL overlay plus a PVCL clip record carrying the pieces (the compositor clips per-pixel). Drives
    a gltri stream and asserts both messages arrive with the expected geometry."""
    by_name = {i["name"]: o for o, i in load_tables().items() if i["name"]}
    got = {"frames": [], "clips": []}
    port = _free_port()

    def mock_qemu():
        srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port)); srv.listen(1)
        c, _ = srv.accept()
        c.settimeout(3.0)
        buf = b""
        try:
            while True:
                while len(buf) < 12:
                    d = c.recv(65536)
                    if not d:
                        return
                    buf += d
                if buf[:4] == b"PVGL":
                    while len(buf) < 20:
                        buf += c.recv(65536)
                    x, y, w, h = struct.unpack("<iiii", buf[4:20])
                    need = 20 + w * h * 4
                    while len(buf) < need:
                        buf += c.recv(65536)
                    got["frames"].append((x, y, w, h))
                    buf = buf[need:]
                elif buf[:4] == b"PVCL":
                    obsc, npc = struct.unpack("<ii", buf[4:12])
                    need = 12 + npc * 16
                    while len(buf) < need:
                        buf += c.recv(65536)
                    pcs = [struct.unpack("<iiii", buf[12 + i * 16:28 + i * 16]) for i in range(npc)]
                    got["clips"].append((obsc, pcs))
                    buf = buf[need:]
                else:
                    buf = buf[1:]
        except socket.timeout:
            return
        finally:
            c.close(); srv.close()

    t = threading.Thread(target=mock_qemu, daemon=True); t.start()

    sess = ProxySession(("127.0.0.1", port))
    assert sess.conn.feed(b"\x00\x00\x12\x34") == b"\xff\xff\xed\xcc"
    sess.wid = 1
    sess.win = (1, 100, 100, 200, 200)
    pieces = [(100, 100, 40, 200), (140, 260, 160, 40)]
    sess.windows[1] = (100, 100, 200, 200, 0, pieces)

    def G(name, *args):
        return _frame([by_name[name]] + list(args))

    def Gq(name, *args):
        return _frame([by_name[name]] + list(args) + [0x10004, 0])
    stream = b""
    stream += Gq("winopen", 2, struct.unpack(">I", b"gl\0\0")[0])
    stream += G("ortho2", _f(0.0), _f(200.0), _f(0.0), _f(200.0))
    stream += G("RGBcolor", (255 << 16), 0)
    stream += G("clear")
    stream += G("gflush")
    sess.conn.feed(stream)
    t.join(timeout=5)

    # overlay is sent at the window origin (size = backend render size); clip carries the pieces
    assert got["frames"] and got["frames"][0][:2] == (100, 100), got["frames"]
    assert (0, pieces) in got["clips"], got["clips"]


def test_pvwn_control_updates_window_registry():
    """#68 dynamic windowing: a PVWN control connection (sent by dgld_proxy on window move/resize/
    occlude) updates the ProxyServer's shared WID->geometry registry that data sessions read at
    composite time."""
    import time
    from sgi_glremote.proxy_renderer import ProxyServer

    port = _free_port()
    srv = ProxyServer(("127.0.0.1", port), ("127.0.0.1", 1))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def rec(wid, x, y, w, h, obsc, pieces=()):
        b = struct.pack(">7i", wid, x, y, w, h, obsc, len(pieces))
        for p in pieces:
            b += struct.pack(">4i", *p)
        return b

    try:
        c = socket.create_connection(("127.0.0.1", port))
        # PVWN magic + initial geometry (no occluders), a move, partial occlusion, full occlusion
        c.sendall(b"PVWN" + rec(7, 200, 150, 640, 480, 0))
        _wait_for(lambda: srv.windows.get(7) == (200, 150, 640, 480, 0, []))
        c.sendall(rec(7, 620, 430, 640, 480, 0))
        _wait_for(lambda: srv.windows.get(7) == (620, 430, 640, 480, 0, []))
        # partial occlusion: two visible pieces
        c.sendall(rec(7, 620, 430, 640, 480, 0, [(620, 430, 640, 200), (620, 700, 300, 210)]))
        _wait_for(lambda: srv.windows.get(7)
                  == (620, 430, 640, 480, 0, [(620, 430, 640, 200), (620, 700, 300, 210)]))
        c.sendall(rec(7, 620, 430, 640, 480, 1))                   # fully obscured: no pieces
        _wait_for(lambda: srv.windows.get(7) == (620, 430, 640, 480, 1, []))
        c.close()
    finally:
        srv.shutdown()


def _wait_for(pred, timeout=3.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met within %.1fs" % timeout)


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p
