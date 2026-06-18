#!/usr/bin/env python3
"""End-to-end host transport test for the dgld-shim path (no guest, no QEMU).

Wires the REAL sockets of the whole host chain:

    mock shim  --(W/G over TCP)-->  ShimServer  --(OSMesa)-->  framechannel.send_frame
                                                                    |
                                              mock QEMU gl-listen <-+  (PVGL frame)

and asserts the mock QEMU receives a single PVGL frame at the window rect carrying the
gltri red triangle. This proves the socket plumbing that the guest dgld-shim will drive:
ShimServer threading, the W/G framing, framechannel, and the pvrex3 PVGL wire contract.

Run in the dev container:  python3 -m sgi_glremote.test_shim_e2e
"""
import socket
import struct
import threading
import time
import sys

from sgi_glremote.shim_renderer import ShimServer
from sgi_glremote.dgl import load_tables


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _build_stream():
    table = load_tables()
    by_name = {i["name"]: o for o, i in table.items() if i["name"]}

    def G(words):
        body = struct.pack(">%dI" % len(words), *words)
        return b"G" + struct.pack(">I", len(body)) + body

    def W(wid, x, y, w, h):
        body = struct.pack(">iiiii", wid, x, y, w, h)
        return b"W" + struct.pack(">I", len(body)) + body

    def fop(name, *fs):
        return [by_name[name]] + [struct.unpack(">I", struct.pack(">f", v))[0] for v in fs]

    s = b""
    s += G([by_name["winopen"], 2, struct.unpack(">I", b"gl\0\0")[0]])
    s += W(1, 320, 260, 640, 480)
    s += G(fop("ortho2", 0.0, 640.0, 0.0, 480.0))
    s += G([by_name["RGBcolor"], (255 << 16), 0])
    s += G([by_name["bgnpolygon"]])
    for x, y in ((100.0, 100.0), (500.0, 100.0), (300.0, 400.0)):
        s += G(fop("v2f", x, y))
    s += G([by_name["endpolygon"]])
    s += G([by_name["gflush"]])
    return s


def main():
    qemu_port = _free_port()
    shim_port = _free_port()

    # --- mock QEMU pvrex3 gl-listen: accept one conn, read one PVGL frame ---
    received = {}

    def mock_qemu():
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", qemu_port))
        srv.listen(1)
        conn, _ = srv.accept()
        buf = b""
        while len(buf) < 4 + 16:
            d = conn.recv(65536)
            if not d:
                break
            buf += d
        assert buf[:4] == b"PVGL", buf[:4]
        x, y, w, h = struct.unpack("<iiii", buf[4:20])
        need = 20 + w * h * 4
        while len(buf) < need:
            d = conn.recv(65536)
            if not d:
                break
            buf += d
        received["rect"] = (x, y, w, h)
        received["rgba"] = buf[20:need]
        conn.close()
        srv.close()

    qt = threading.Thread(target=mock_qemu, daemon=True)
    qt.start()
    time.sleep(0.2)

    # --- ShimServer renders W/G and forwards frames to mock QEMU ---
    srv = ShimServer(("127.0.0.1", shim_port), ("127.0.0.1", qemu_port))
    st = threading.Thread(target=srv.serve_forever, daemon=True)
    st.start()
    time.sleep(0.2)

    # --- mock shim: connect, send the W/G stream ---
    c = socket.create_connection(("127.0.0.1", shim_port), timeout=5)
    c.sendall(_build_stream())
    time.sleep(1.0)            # let render + frame forward complete
    c.close()

    qt.join(timeout=5)
    srv.shutdown()

    assert received.get("rect") == (320, 260, 640, 480), received.get("rect")
    rgba = received["rgba"]
    assert len(rgba) == 640 * 480 * 4, len(rgba)
    import numpy as np
    arr = np.frombuffer(rgba, np.uint8).reshape(480, 640, 4)
    px = tuple(int(v) for v in arr[480 - 200, 300][:3])
    assert px == (255, 0, 0), px
    print("OK: mock-shim -> ShimServer -> framechannel -> mock-QEMU PVGL frame")
    print("    rect=%s, %d bytes, center pixel=%s (red triangle)" % (received["rect"], len(rgba), px))
    return 0


if __name__ == "__main__":
    sys.exit(main())
