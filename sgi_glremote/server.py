#!/usr/bin/env python3
"""DGL host renderer skeleton (Phase 0c front-half) + live-capture tool (#55).

A TCP server that speaks the DGL handshake (dgl_protocol.md), decodes the IRIS GL command
stream (dgl.py), and dispatches commands to a pluggable Backend. Phase 0 ships:
  * CaptureBackend — logs every decoded command (the live dissector for #55), and
  * a place to drop the GL backend (OSMesa now, native macOS GPU in Phase 2).

The renderer is intentionally backend-pluggable so Phase 2 only swaps the Backend.

Run as the live-capture server:
    python3 -m sgi_glremote.server --port 5232 --capture /tmp/dgl_capture.bin
Then point a guest IRIS GL app at this host (DISPLAY=<host>:0.0) via slirp routing (#56).
"""
import argparse
import socket
import socketserver
import struct
import sys
import threading

try:
    from .dgl import DglDecoder, DglSync, load_tables, VALUE_RETURNING, \
        OP_DGLLOGINX, OP_DGLVERSION, OP_DGLXAUTHORITY
except ImportError:  # run as a script
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sgi_glremote.dgl import DglDecoder, DglSync, load_tables, VALUE_RETURNING, \
        OP_DGLLOGINX, OP_DGLVERSION, OP_DGLXAUTHORITY


class Backend:
    """Renderer backend interface. Phase 2 implements this against the host GPU."""

    def winopen(self, name):
        """Open a GL window 'name'; return a WID byte (1..255). We own WID assignment."""
        raise NotImplementedError

    def command(self, op, name, args):
        """A decoded GL command (non-value-returning)."""

    def value(self, op, name, args):
        """A value-returning GL command; return the reply word (int)."""
        fn = VALUE_RETURNING.get(name)
        return fn(args) if fn else 0

    def swapbuffers(self, wid):
        """Frame boundary: return (w, h, rgba_bytes) or None if no frame is ready."""
        return None


class CaptureBackend(Backend):
    """Logs/counts decoded commands; assigns sequential WIDs. The #55 dissector."""

    def __init__(self, log=None):
        self.log = log or (lambda s: print(s))
        self.count = 0
        self.by_name = {}
        self.unknown = []
        self._next_wid = 1
        self.wids = {}

    def winopen(self, name):
        wid = self._next_wid
        self._next_wid += 1
        self.wids[wid] = name
        self.log("winopen(%r) -> WID %d" % (name, wid))
        return wid

    def command(self, op, name, args):
        self.count += 1
        self.by_name[name] = self.by_name.get(name, 0) + 1
        if name is None:
            self.unknown.append(op)
        if self.count <= 200:
            self.log("  cmd 0x%x %-16s %s" % (op, name, args[:6]))

    def summary(self):
        top = sorted(self.by_name.items(), key=lambda kv: -kv[1])[:20]
        return "decoded %d commands; top: %s; unknown opcodes: %s" % (
            self.count, top, sorted(set(self.unknown)))


class DglConnection:
    """Drives one client socket through handshake + command decode."""

    def __init__(self, sock, backend, table, capture_file=None):
        self.sock = sock
        self.backend = backend
        self.dec = DglDecoder(table)
        self.cap = open(capture_file, "wb") if capture_file else None

    def _reply(self, word):
        self.sock.sendall(struct.pack(">I", word & 0xFFFFFFFF))

    def run(self):
        try:
            while True:
                data = self.sock.recv(65536)
                if not data:
                    break
                if self.cap:
                    self.cap.write(data); self.cap.flush()
                try:
                    cmds = self.dec.feed(data)
                except DglSync as e:
                    print("[dgl] lost sync: %s — stopping decode" % e)
                    # keep draining/capturing raw bytes for offline analysis
                    continue
                for op, name, args in cmds:
                    self._dispatch(op, name, args)
        finally:
            if self.cap:
                self.cap.close()

    def _dispatch(self, op, name, args):
        if name in ("winopen", "swinopen"):
            wid = self.backend.winopen(_unpack_name(args))
            self._reply(wid)
            return
        if name in VALUE_RETURNING and VALUE_RETURNING[name] is not None:
            self._reply(self.backend.value(op, name, args))
            return
        if op in (OP_DGLVERSION,):           # login dglversion -> status 0
            self._reply(0)
            return
        if op in (OP_DGLLOGINX, OP_DGLXAUTHORITY):
            self.backend.command(op, name or "dgl-login", args)
            return
        if name == "swapbuffers":
            self.backend.command(op, name, args)
            frame = self.backend.swapbuffers(_current_wid(args))
            return
        self.backend.command(op, name, args)


def _unpack_name(args):
    if not args:
        return ""
    nbytes = args[0]
    raw = struct.pack(">%dI" % (len(args) - 1), *args[1:])
    return raw[:nbytes].split(b"\0", 1)[0].decode("latin1")


def _current_wid(args):
    return args[0] if args else 0


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        print("[dgl] connection from %s" % (self.client_address,))
        DglConnection(self.request, self.server.backend, self.server.table,
                      self.server.capture_file).run()
        if isinstance(self.server.backend, CaptureBackend):
            print("[dgl] " + self.server.backend.summary())


class DglServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, backend, capture_file=None):
        super().__init__(addr, _Handler)
        self.backend = backend
        self.table = load_tables()
        self.capture_file = capture_file


# ---------------------------------------------------------------- self-test
def selftest():
    """Loopback: a fake client sends handshake + commands; assert server replies."""
    table = load_tables()
    by_name = {i["name"]: o for o, i in table.items() if i["name"]}
    srv = DglServer(("127.0.0.1", 0), CaptureBackend(log=lambda s: None))
    host, port = srv.server_address
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()

    c = socket.create_connection((host, port))

    def send(op, *args):
        c.sendall(struct.pack(">%dI" % (1 + len(args)), op, *[a & 0xFFFFFFFF for a in args]))

    def read_word():
        b = b""
        while len(b) < 4:
            b += c.recv(4 - len(b))
        return struct.unpack(">I", b)[0]

    # handshake: dglversion(2) -> expect 0
    send(OP_DGLVERSION, 2)
    assert read_word() == 0, "dglversion reply"
    # gversion -> expect 2
    send(by_name["gversion"], 0, 0)
    assert read_word() == 2, "gversion reply"
    # winopen "gl" -> expect a WID (1)
    c.sendall(struct.pack(">III", by_name["winopen"], 2, struct.unpack(">I", b"gl\0\0")[0]))
    wid = read_word()
    assert wid == 1, ("winopen WID", wid)
    # a normal command (clear)
    if "clear" in by_name:
        cw = table[by_name["clear"]]["cmd_words"] or 1
        send(by_name["clear"], *([0] * (cw - 1)))
    c.close()
    import time; time.sleep(0.1)
    srv.shutdown()
    print("OK: handshake replies correct (dglversion=0, gversion=2, winopen WID=1)")
    return True


def main(argv):
    ap = argparse.ArgumentParser(description="DGL host renderer / live-capture server")
    ap.add_argument("--port", type=int, default=5232)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--capture", default=None, help="write raw client bytes to this file")
    ap.add_argument("--backend", default="capture", choices=["capture", "osmesa"],
                    help="capture=log/dissect (default); osmesa=render via software GL")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    if a.backend == "osmesa":
        from sgi_glremote.osmesa_backend import OSMesaBackend
        backend = OSMesaBackend()
    else:
        backend = CaptureBackend()
    srv = DglServer((a.host, a.port), backend, capture_file=a.capture)
    print("[dgl] listening on %s:%d (capture=%s)" % (a.host, a.port, a.capture))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
