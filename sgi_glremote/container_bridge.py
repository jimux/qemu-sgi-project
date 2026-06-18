#!/usr/bin/env python3
"""Container-side bridge for Milestone 2 (real macOS GPU).

QEMU runs in the container; the renderer runs natively on the Mac (for the GPU). The Mac is only
reachable from the container via host.docker.internal, so EVERY cross-boundary connection is dialed
container->Mac. Two relays:

  DGL    : listen :6053  (slirp delivers the guest dgld_proxy's connection to 10.0.2.2:6053 here)
           <-> host.docker.internal:6053  (Mac renderer DGL server)        — bidirectional
  frames : host.docker.internal:6054 (Mac renderer frame source)
           -> 127.0.0.1:<glport>      (QEMU pvrex3 gl-listen, in the container) — one-way

Use as a library (start_bridge) from the M2 harness, or run standalone.
"""
import argparse
import socket
import threading
import time


def _pipe(src, dst):
    try:
        while True:
            d = src.recv(65536)
            if not d:
                break
            dst.sendall(d)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.close()
            except OSError:
                pass


def _dgl_relay(listen_port, mac_host, mac_dgl_port, log):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", listen_port))
    s.listen(1)
    log("DGL relay listening :%d -> %s:%d" % (listen_port, mac_host, mac_dgl_port))
    while True:
        proxy, _ = s.accept()
        try:
            mac = socket.create_connection((mac_host, mac_dgl_port), timeout=10)
        except OSError as e:
            log("DGL relay: cannot reach Mac renderer: %s" % e)
            proxy.close()
            continue
        log("DGL relay: guest <-> Mac connected")
        threading.Thread(target=_pipe, args=(proxy, mac), daemon=True).start()
        threading.Thread(target=_pipe, args=(mac, proxy), daemon=True).start()


def _frame_relay(mac_host, mac_frame_port, glport, log):
    while True:
        try:
            mac = socket.create_connection((mac_host, mac_frame_port), timeout=10)
        except OSError:
            time.sleep(1)
            continue
        try:
            qemu = socket.create_connection(("127.0.0.1", glport), timeout=10)
        except OSError:
            mac.close()
            time.sleep(1)
            continue
        log("frame relay: Mac frames -> QEMU gl-listen :%d connected" % glport)
        _pipe(mac, qemu)                  # blocks until either side closes
        log("frame relay: disconnected; retrying")
        time.sleep(1)


def start_bridge(mac_host="host.docker.internal", dgl_port=6053, mac_dgl_port=6053,
                 mac_frame_port=6054, glport=5233, log=None):
    """Start both relays on daemon threads. Returns immediately."""
    log = log or (lambda m: print("[bridge] " + m, flush=True))
    threading.Thread(target=_dgl_relay, args=(dgl_port, mac_host, mac_dgl_port, log),
                     daemon=True).start()
    threading.Thread(target=_frame_relay, args=(mac_host, mac_frame_port, glport, log),
                     daemon=True).start()


def main(argv):
    ap = argparse.ArgumentParser(description="container<->Mac DGL/frame bridge (Milestone 2)")
    ap.add_argument("--mac-host", default="host.docker.internal")
    ap.add_argument("--dgl-port", type=int, default=6053)
    ap.add_argument("--mac-dgl-port", type=int, default=6053)
    ap.add_argument("--mac-frame-port", type=int, default=6054)
    ap.add_argument("--glport", type=int, default=5233)
    a = ap.parse_args(argv)
    start_bridge(a.mac_host, a.dgl_port, a.mac_dgl_port, a.mac_frame_port, a.glport)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
