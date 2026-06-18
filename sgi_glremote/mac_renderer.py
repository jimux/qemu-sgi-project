#!/usr/bin/env python3
"""Native macOS GPU DGL renderer (Milestone 2). Runs on the Mac host (NOT the container).

Same DGL protocol + IRIS-GL->OpenGL translation as the container path (ProxySession), but the GL
backend is MacGLBackend — the Apple GPU (OpenGL-over-Metal). The container_bridge dials in:

  :6053  DGL stream    — single-threaded server; all GL runs on the MAIN thread (macOS/Cocoa
                         requires the glfw context + GL on the thread that created it).
  :6054  frame channel — the bridge connects and drains framechannel PVGL frames, which it pipes
                         into QEMU's pvrex3 gl-listen inside the container.

Run on the Mac host:
  PYTHONPATH=. .venv-glremote/bin/python -m sgi_glremote.mac_renderer
"""
import argparse
import socket
import socketserver
import sys
import threading

try:
    from . import framechannel
    from .macgl_backend import MacGLBackend, ensure_context
    from .proxy_renderer import _Handler
except ImportError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sgi_glremote import framechannel
    from sgi_glremote.macgl_backend import MacGLBackend, ensure_context
    from sgi_glremote.proxy_renderer import _Handler


class _FrameRelay:
    """Holds the current frame-consumer socket (the container bridge) and writes PVGL frames to it.
    Accept runs on a background thread (no GL); the sink is called from the main render thread."""

    def __init__(self, port):
        self.port = port
        self.sock = None
        self.lock = threading.Lock()
        self.sent = 0

    def serve(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", self.port))
        s.listen(1)
        while True:
            c, _ = s.accept()
            with self.lock:
                if self.sock:
                    try:
                        self.sock.close()
                    except OSError:
                        pass
                self.sock = c

    def sink(self, x, y, w, h, rgba):
        with self.lock:
            if self.sock is None:
                return
            try:
                framechannel.send_frame(self.sock, x, y, w, h, rgba)
                self.sent += 1
                if self.sent <= 3 or self.sent % 30 == 0:
                    print("[mac-renderer] frame #%d rect=(%d,%d,%d,%d) -> bridge"
                          % (self.sent, x, y, w, h), flush=True)
            except OSError:
                self.sock = None


class _MacDGLServer(socketserver.TCPServer):
    """Single-threaded so DGL handling + GL run on the main thread (serve_forever's thread)."""
    allow_reuse_address = True

    def __init__(self, addr, frame_sink, qemu_addr=None):
        super().__init__(addr, _Handler)
        # All-local mode (QEMU native on the Mac): qemu_addr set + frame_sink None → ProxySession
        # dials QEMU's pvrex3 gl-listen directly (no container bridge). Bridge mode: frame_sink set.
        self.qemu_addr = qemu_addr
        self.backend_factory = MacGLBackend
        self.frame_sink = frame_sink


def main(argv):
    ap = argparse.ArgumentParser(description="native macOS GPU DGL renderer (Milestone 2)")
    ap.add_argument("--dgl-port", type=int, default=6053)
    ap.add_argument("--frame-port", type=int, default=6054)
    ap.add_argument("--qemu", default="127.0.0.1:5233",
                    help="QEMU pvrex3 gl-listen HOST:PORT to dial directly (all-local Mac); "
                         "set empty/'none' to use the --frame-port bridge relay instead")
    a = ap.parse_args(argv)

    ensure_context()                                  # glfw + GL context on the MAIN thread
    be = MacGLBackend()
    print("[mac-renderer] GL_RENDERER=%s | GL_VERSION=%s" % (be.renderer, be.gl_version), flush=True)

    if a.qemu and a.qemu.lower() != "none":
        host, port = a.qemu.split(":")
        srv = _MacDGLServer(("0.0.0.0", a.dgl_port), None, qemu_addr=(host, int(port)))
        print("[mac-renderer] DGL :%d  -> gl-listen %s (all-local) -> GPU"
              % (a.dgl_port, a.qemu), flush=True)
    else:
        relay = _FrameRelay(a.frame_port)
        threading.Thread(target=relay.serve, daemon=True).start()
        srv = _MacDGLServer(("0.0.0.0", a.dgl_port), relay.sink)
        print("[mac-renderer] DGL :%d  frames :%d (bridge) -> GPU"
              % (a.dgl_port, a.frame_port), flush=True)
    try:
        srv.serve_forever()                           # GL runs here, on the main thread
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
