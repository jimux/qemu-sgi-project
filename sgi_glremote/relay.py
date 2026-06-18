#!/usr/bin/env python3
"""Bidirectional stdin/stdout <-> TCP relay for slirp `guestfwd=...-cmd:` forwarding.

slirp runs this once PER guest connection with the guest's TCP stream as stdin(0)/stdout(1).
We connect to a host service (the DGL capture server or Xvfb) and shuttle bytes both ways.
Blocking socket + a reader thread (no non-blocking sendall — X11 is bursty/bidirectional and a
non-blocking sendall raises BlockingIOError and kills the relay).

    guestfwd=tcp:10.0.2.100:6000-cmd:<wrapper calling> relay.py 127.0.0.1 6000
"""
import os
import socket
import sys
import threading

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.create_connection((host, port))


def sock_to_stdout():
    try:
        while True:
            d = sock.recv(65536)
            if not d:
                break
            os.write(1, d)
    except OSError:
        pass
    os._exit(0)


threading.Thread(target=sock_to_stdout, daemon=True).start()

try:
    while True:
        d = os.read(0, 65536)
        if not d:
            break
        sock.sendall(d)
except OSError:
    pass
finally:
    try:
        sock.shutdown(socket.SHUT_WR)
    except OSError:
        pass
