#!/usr/bin/env python3
"""Renderer side of the dgld-shim protocol (VirGL roadmap Step 5/6, host side).

The in-guest dgld-shim forwards, over one socket, a framed control stream:
    [type:1][len:u32 BE][payload]
      'W' wininfo : wid,x,y,w,h (5 x int32 BE)  -- window screen rect
      'G' gldata  : raw DGL u32 command-stream bytes

This module decodes 'G' via the DGL decoder, renders through the OSMesa backend, and on each
gflush/swapbuffers streams the resulting RGBA frame to QEMU's pvrex3 gl-listen socket at the
window's screen rect (from the most recent 'W'). That closes the loop:

    guest app -> libgl -> dgld-shim -> (W/G) -> THIS -> OSMesa -> PVGL frame -> pvrex3 compositor

Run:  python3 -m sgi_glremote.shim_renderer --listen 6053 --qemu 127.0.0.1:5233
"""
import argparse
import socket
import socketserver
import struct
import sys

try:
    from .dgl import DglDecoder, DglSync
    from .osmesa_backend import OSMesaBackend
    from . import framechannel
except ImportError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sgi_glremote.dgl import DglDecoder, DglSync
    from sgi_glremote.osmesa_backend import OSMesaBackend
    from sgi_glremote import framechannel


def _unpack_name(args):
    if not args:
        return ""
    nbytes = args[0]
    raw = struct.pack(">%dI" % (len(args) - 1), *args[1:]) if len(args) > 1 else b""
    return raw[:nbytes].split(b"\0", 1)[0].decode("latin1")


class ShimSession:
    """One dgld-shim connection: decode W/G, render, stream frames to QEMU."""

    def __init__(self, qemu_addr):
        self.dec = DglDecoder()
        self.be = OSMesaBackend()
        self.win = {}          # wid -> (x, y, w, h) screen rect
        self.cur_wid = None
        self.qemu_addr = qemu_addr
        self.qemu = None
        self.buf = b""

    def _qemu_sock(self):
        if self.qemu is None and self.qemu_addr:
            host, port = self.qemu_addr
            try:
                self.qemu = framechannel.connect(host, port)
            except OSError:
                self.qemu = None
        return self.qemu

    def feed(self, data):
        self.buf += data
        while len(self.buf) >= 5:
            typ = self.buf[0:1]
            ln = struct.unpack(">I", self.buf[1:5])[0]
            if len(self.buf) < 5 + ln:
                break
            payload = self.buf[5:5 + ln]
            self.buf = self.buf[5 + ln:]
            if typ == b"W":
                wid, x, y, w, h = struct.unpack(">iiiii", payload)
                self.win[wid] = (x, y, w, h)
                self.cur_wid = wid
            elif typ == b"G":
                self._gldata(payload)

    def _gldata(self, data):
        try:
            cmds = self.dec.feed(data)
        except DglSync:
            return
        for op, name, args in cmds:
            if name in ("winopen", "swinopen"):
                wid = self.be.winopen(_unpack_name(args))
                self.cur_wid = wid
            else:
                self.be.command(op, name, args)
                if name in ("gflush", "swapbuffers"):
                    self._emit_frame()

    def _emit_frame(self):
        self.be.flush()
        cur = self.be._cur
        if cur is None:
            return
        w, h, rgba = self.be.frames.get(cur.wid, (None, None, None))
        if rgba is None:
            return
        rect = self.win.get(self.cur_wid, (0, 0, w, h))
        x, y = rect[0], rect[1]
        sock = self._qemu_sock()
        if sock:
            try:
                framechannel.send_frame(sock, x, y, w, h, rgba)
            except OSError:
                self.qemu = None


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        sess = ShimSession(self.server.qemu_addr)
        while True:
            data = self.request.recv(65536)
            if not data:
                break
            sess.feed(data)


class ShimServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, qemu_addr):
        super().__init__(addr, _Handler)
        self.qemu_addr = qemu_addr


# ----------------------------------------------------------- self-test
def selftest():
    """Feed synthetic W/G shim messages; assert a frame is produced for the gltri triangle."""
    from sgi_glremote.dgl import load_tables
    table = load_tables()
    by_name = {i["name"]: o for o, i in table.items() if i["name"]}
    sess = ShimSession(qemu_addr=None)        # no QEMU -> just render

    def G(words):
        body = struct.pack(">%dI" % len(words), *words)
        return b"G" + struct.pack(">I", len(body)) + body

    def W(wid, x, y, w, h):
        body = struct.pack(">iiiii", wid, x, y, w, h)
        return b"W" + struct.pack(">I", len(body)) + body

    def fop(name, *fs):
        return [by_name[name]] + [struct.unpack(">I", struct.pack(">f", v))[0] for v in fs]

    stream = b""
    stream += G([by_name["winopen"], 2, struct.unpack(">I", b"gl\0\0")[0]])
    stream += W(1, 320, 260, 640, 480)
    stream += G(fop("ortho2", 0.0, 640.0, 0.0, 480.0))
    stream += G([by_name["RGBcolor"], (255 << 16), 0])
    stream += G([by_name["bgnpolygon"]])
    for x, y in ((100.0, 100.0), (500.0, 100.0), (300.0, 400.0)):
        stream += G(fop("v2f", x, y))
    stream += G([by_name["endpolygon"]])
    stream += G([by_name["gflush"]])
    sess.feed(stream)

    assert 1 in sess.win and sess.win[1] == (320, 260, 640, 480), sess.win
    assert sess.be.frames, "no frame rendered"
    w, h, rgba = list(sess.be.frames.values())[0]
    import numpy as np
    arr = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)
    assert tuple(int(v) for v in arr[h - 200, 300][:3]) == (255, 0, 0), "triangle not red"
    print("OK: shim W/G stream -> window rect %s + red triangle rendered" % (sess.win[1],))
    return True


def main(argv):
    ap = argparse.ArgumentParser(description="dgld-shim renderer (W/G -> OSMesa -> QEMU)")
    ap.add_argument("--listen", type=int, default=6053, help="port the shim connects to")
    ap.add_argument("--qemu", default="127.0.0.1:5233", help="QEMU pvrex3 gl-listen host:port")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    host, port = a.qemu.split(":")
    srv = ShimServer(("0.0.0.0", a.listen), (host, int(port)))
    print("[shim-renderer] listening on :%d, frames -> %s" % (a.listen, a.qemu))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
