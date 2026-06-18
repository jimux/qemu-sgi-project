#!/usr/bin/env python3
"""Frame-channel client: stream PVGL frames to the pvrex3 live GL overlay socket.

Wire format (matches sgi_pvrex3.c pvrex3_gl_parse):
    "PVGL" (4) + int32 x,y,w,h (little-endian) ; then w*h*4 RGBA bytes.
    w == 0 clears the overlay.

The renderer (OSMesaBackend) calls send_frame() on each flush/swapbuffers once the
compositor (QEMU) is listening (qom-set <pvrex3> gl-listen <port>).
"""
import socket
import struct


def connect(host="127.0.0.1", port=5233, timeout=5):
    return socket.create_connection((host, port), timeout=timeout)


def send_frame(sock, x, y, w, h, rgba):
    """rgba = bytes of length w*h*4 (R,G,B,A order)."""
    assert len(rgba) == w * h * 4, (len(rgba), w, h)
    sock.sendall(b"PVGL" + struct.pack("<iiii", x, y, w, h) + rgba)


def clear(sock):
    sock.sendall(b"PVGL" + struct.pack("<iiii", 0, 0, 0, 0))


def send_clip(sock, obscured, pieces):
    """Drive the compositor's per-pixel visible-region clip (#80). The overlay is sent whole via
    send_frame(); this restricts which of its pixels are drawn to the visible sub-rects.

    Wire format (matches sgi_pvrex3.c pvrex3_gl_parse "PVCL"):
        "PVCL" (4) + int32 obscured + int32 numpieces ; then numpieces * int32 px,py,pw,ph  (LE).
    obscured!=0 => draw nothing; numpieces==0 => unclipped (whole overlay)."""
    msg = b"PVCL" + struct.pack("<ii", 1 if obscured else 0, len(pieces))
    for (px, py, pw, ph) in pieces:
        msg += struct.pack("<iiii", px, py, pw, ph)
    sock.sendall(msg)
