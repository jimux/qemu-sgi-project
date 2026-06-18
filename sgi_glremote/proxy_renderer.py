#!/usr/bin/env python3
"""Host renderer for the transparent dgld-proxy shim (VirGL Milestone 0, host side).

The in-guest dgld_proxy sends one control header `"PVSH" + wininfo(wid,x,y,w,h)` then pipes libgl's
raw DGL bytes both ways. This renderer:
  - runs DglFramedConnection to speak the real framed DGL protocol (handshake replies validated
    against the live capture) and reply to libgl through the proxy;
  - decodes the GL command payloads (winopen/color/clear/ortho2/polygon/…) into the OSMesa backend;
  - on gflush/swapbuffers, reads back the RGBA frame and streams it to QEMU's pvrex3 gl-listen at
    the window's screen rect (from PVSH) -> composited into the desktop window.

Run:  python3 -m sgi_glremote.proxy_renderer --listen 6053 --qemu 127.0.0.1:5233
"""
import argparse
import os
import socket
import socketserver
import struct
import sys
import threading

try:
    from .dgl import load_tables
    from .dgl_framed import DglFramedConnection
    from . import framechannel
except ImportError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sgi_glremote.dgl import load_tables
    from sgi_glremote.dgl_framed import DglFramedConnection
    from sgi_glremote import framechannel


def _default_backend():
    """OSMesa is the default backend, but it's import-time unavailable on macOS (no OSMesa). Import
    it lazily so the native macOS renderer (which passes MacGLBackend) can import this module."""
    try:
        from .osmesa_backend import OSMesaBackend
    except ImportError:
        from sgi_glremote.osmesa_backend import OSMesaBackend
    return OSMesaBackend


def _unpack_name(args):
    if not args:
        return ""
    nbytes = args[0]
    raw = struct.pack(">%dI" % (len(args) - 1), *args[1:]) if len(args) > 1 else b""
    return raw[:nbytes].split(b"\0", 1)[0].decode("latin1")


class ProxySession:
    # Default to the container path; override via DGL_TRACE for the native macOS renderer.
    TRACE = os.environ.get("DGL_TRACE", "/workspace/_proxy_trace.log")
    # When set, suppress the PVCL clip record so an externally-injected clip (e.g. the gl-clip-test
    # monitor hook) is not overwritten each frame — used for deterministic clip demos. #80.
    NO_CLIP = bool(os.environ.get("DGL_NO_CLIP"))

    def __init__(self, qemu_addr, backend_factory=None, frame_sink=None, windows=None):
        # backend_factory lets the native macOS renderer plug in MacGLBackend (GPU) behind the same
        # protocol/translation glue; frame_sink overrides the default framechannel->QEMU path (the
        # Mac renderer hands frames to a relay instead of dialing QEMU directly).
        self.be = (backend_factory or _default_backend())()
        self.frame_sink = frame_sink
        self.table = load_tables()
        self.win = (1, 0, 0, 640, 480)
        self.wid = 1
        # Live per-WID geometry registry (x, y, w, h, obscured), shared with the PVWN control
        # connection so window drags/occlusion update where (and whether) we composite. #68.
        self.windows = windows if windows is not None else {}
        self.qemu_addr = qemu_addr
        self.qemu = None
        self.conn = DglFramedConnection(on_payload=self._gl, query=self._query)
        self._seen = set()
        self._swaps = 0
        try:
            self._trace = open(self.TRACE, "a")
        except OSError:
            self._trace = None

    def _log(self, msg):
        if self._trace:
            self._trace.write(msg + "\n")
            self._trace.flush()

    def _qemu_sock(self):
        if self.qemu is None and self.qemu_addr:
            try:
                self.qemu = framechannel.connect(*self.qemu_addr)
            except OSError:
                self.qemu = None
        return self.qemu

    def _query(self, op, cmd):
        """GL-state value-returning queries that need OSMesa state. Returns reply words or None."""
        if op == 0x3f:                       # getmatrix -> current modelview matrix (16 floats)
            try:
                return self.be.get_matrix()
            except Exception:
                return None
        return None

    def _gl(self, words):
        """One DGL payload = [opcode, args...]. Decode + drive OSMesa."""
        op = words[0]
        info = self.table.get(op)
        name = info["name"] if info else None
        if op not in self._seen:                       # log each opcode once (the feature list)
            self._seen.add(op)
            self._log("NEW op=0x%x name=%s nargs=%d args=%s"
                      % (op, name, len(words) - 1,
                         " ".join("%x" % w for w in words[1:9])))
        if name in ("swapbuffers", "mswapbuffers", "gflush"):
            self._swaps += 1
            if self._swaps <= 3 or self._swaps % 25 == 0:
                self._log("  frame #%d (%s)" % (self._swaps, name))
        if name in ("winopen", "swinopen"):
            self.be.winopen(_unpack_name(tuple(words[1:])) or "gl")
        elif name:
            self.be.command(op, name, tuple(words[1:]))
            if name in ("gflush", "swapbuffers", "mswapbuffers"):
                self._emit_frame()

    def _emit_frame(self):
        self.be.flush()
        cur = self.be._cur
        if cur is None:
            return
        w, h, rgba = self.be.frames.get(cur.wid, (None, None, None))
        if rgba is None:
            return
        # Live window position + visible clip pieces from the PVWN control channel (falls back to the
        # PVSH startup geometry). When fully obscured we skip compositing so the covering window —
        # already drawn into pvrex3 vram by Xsgi — shows through. When partially occluded we composite
        # only the visible pieces. #68/#80.
        geom = self.windows.get(self.wid)
        if geom is not None:
            x, y, _gw, _gh, obscured, pieces = geom
        else:
            _, x, y, _, _ = self.win
            obscured, pieces = 0, []
        # Send the whole window overlay once; the compositor draws it clipped per-pixel to the
        # visible pieces (pvrex3_clip_visible). One overlay + a clip record matches the single-
        # overlay device design and handles partial occlusion + full occlusion uniformly. #80.
        if self.frame_sink is not None:
            if obscured:
                return
            self.frame_sink(x, y, w, h, rgba)   # Mac path: clip not yet relayed
            return
        sock = self._qemu_sock()
        if not sock:
            return
        try:
            framechannel.send_frame(sock, x, y, w, h, rgba)
            if not ProxySession.NO_CLIP:
                framechannel.send_clip(sock, obscured, pieces)
        except OSError:
            self.qemu = None

    def handle(self, sock, magic=b""):
        # magic may have been consumed by the dispatcher (which demuxes PVSH data sessions from
        # PVWN control connections); read whatever's left of the 24-byte PVSH header.
        hdr = magic
        while len(hdr) < 24:
            d = sock.recv(24 - len(hdr))
            if not d:
                return
            hdr += d
        if hdr[:4] != b"PVSH":
            return
        self.win = struct.unpack(">iiiii", hdr[4:24])
        self.wid = self.win[0]
        # Seed the live registry from PVSH so the first frames composite at the startup position
        # even before any move event arrives (empty pieces => composite the full window rect).
        self.windows.setdefault(
            self.wid, (self.win[1], self.win[2], self.win[3], self.win[4], 0, []))
        self._log("=== session PVSH win=%s ===" % (self.win,))
        while True:
            data = sock.recv(65536)
            if not data:
                self._log("client EOF")
                break
            nq = len(self.conn.unhandled_queries)
            reply = self.conn.feed(data)
            for op in self.conn.unhandled_queries[nq:]:
                info = self.table.get(op)
                self._log("UNHANDLED-QUERY op=0x%x name=%s (default 0)"
                          % (op, info["name"] if info else None))
            if reply:
                sock.sendall(reply)


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        d = sock.recv(n - len(buf))
        if not d:
            return None
        buf += d
    return buf


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        magic = _recv_exact(sock, 4)
        if magic is None:
            return
        if magic == b"PVWN":
            # Control connection: a stream of variable records pushed by the proxy each ~200ms poll —
            #   [wid][x][y][w][h][obscured][numpieces]  then numpieces * [px][py][pw][ph]
            # Update the shared registry (wid -> (x, y, w, h, obscured, pieces)) that live data
            # sessions read at composite time. Pieces are the window's visible region (screen coords)
            # with windows stacked above it subtracted. #68/#80.
            try:
                ctl_log = open(ProxySession.TRACE, "a")
                ctl_log.write("=== PVWN control connection opened ===\n")
                ctl_log.flush()
            except OSError:
                ctl_log = None
            last = None
            while True:
                hdr = _recv_exact(sock, 28)
                if hdr is None:
                    return
                wid, x, y, w, h, obsc, npieces = struct.unpack(">7i", hdr)
                pieces = []
                if npieces > 0:
                    pbuf = _recv_exact(sock, npieces * 16)
                    if pbuf is None:
                        return
                    for i in range(npieces):
                        pieces.append(struct.unpack(">4i", pbuf[i * 16:i * 16 + 16]))
                self.server.windows[wid] = (x, y, w, h, obsc, pieces)
                # The proxy sends every poll; log only when the geometry/clip actually changes.
                cur = (wid, x, y, w, h, obsc, tuple(pieces))
                if ctl_log and cur != last:
                    ctl_log.write("PVWN wid=%d (%d,%d) %dx%d obsc=%d pieces=%s\n"
                                  % (wid, x, y, w, h, obsc, pieces))
                    ctl_log.flush()
                    last = cur
            return
        ProxySession(self.server.qemu_addr,
                     backend_factory=self.server.backend_factory,
                     frame_sink=self.server.frame_sink,
                     windows=self.server.windows).handle(sock, magic=magic)


class ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, qemu_addr, backend_factory=None, frame_sink=None):
        super().__init__(addr, _Handler)
        self.qemu_addr = qemu_addr
        self.backend_factory = backend_factory
        self.frame_sink = frame_sink
        # Shared WID -> (x, y, w, h, obscured) registry; dict writes are atomic under the GIL so a
        # control thread updating it while a session thread reads it needs no explicit lock.
        self.windows = {}


def main(argv):
    ap = argparse.ArgumentParser(description="dgld-proxy host renderer (DGL framed -> OSMesa -> QEMU)")
    ap.add_argument("--listen", type=int, default=6053)
    ap.add_argument("--qemu", default="127.0.0.1:5233")
    a = ap.parse_args(argv)
    host, port = a.qemu.split(":")
    srv = ProxyServer(("0.0.0.0", a.listen), (host, int(port)))
    print("[proxy-renderer] listening :%d, frames -> %s" % (a.listen, a.qemu))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
