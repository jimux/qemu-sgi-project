"""Validate the framed DGL handshake server against the LIVE capture (dgl_handshake_capture.txt,
sniffed from the real /usr/etc/dgld on 2026-06-14). Feeding the exact client bytes must reproduce
the exact server replies the real dgld sent. Pure host-side, no guest."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sgi_glremote.dgl_framed import DglFramedConnection


def H(s):
    return bytes.fromhex(s.replace(" ", ""))


# (client -> server, expected server -> client) from the live capture.
EXCHANGES = [
    ("00 00 12 34", "ff ff ed cc"),                                              # byte-order probe
    ("10 00 00 10 00 01 00 03 02 01 00 00 00 01 00 04 00 00 00 00",
     "10 00 00 04 00 00 00 00"),                                                  # dglxdrformat -> 0
    ("10 00 00 3c 00 01 00 10 00 00 00 08 49 52 49 53 00 00 00 00 00 00 00 08 "
     "72 6f 6f 74 00 00 00 00 00 00 00 08 72 6f 6f 74 00 00 00 00 00 00 00 80 "
     "ff ff ff ff 00 00 00 80 00 01 00 04 00 00 00 00",
     "10 00 00 0c 00 00 00 00 00 00 00 00 00 00 00 00"),                          # dglloginX -> 0,0,0
    ("10 00 00 10 00 01 00 07 00 00 00 02 00 01 00 04 00 00 00 00",
     "10 00 00 04 00 00 00 00"),                                                  # dglversion -> 0
    ("10 00 00 2c 00 01 00 13 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
     "00 00 01 ca 00 00 00 08 3a 30 2e 30 00 00 00 00 00 01 00 04 00 00 00 00",
     "10 00 00 04 00 00 00 00"),                                                  # dglxauthority -> 0
    ("10 00 00 0c 00 00 01 43 00 01 00 04 00 00 00 00",
     "10 00 00 04 00 00 00 01"),                                                  # gversion -> 1
]
# op 0x1 (gversion-string) follows in the live stream but the real dgld crashed (NG1-RevA) before
# replying, so we synthesize that reply (GVERSION_STRING) — tested separately for shape, below.
GVERSION_REQ = "10 00 00 14 00 00 00 01 00 00 00 0c 00 00 00 0c 00 01 00 04 00 00 00 00"


def test_handshake_matches_live_capture():
    conn = DglFramedConnection()
    for client_hex, expect_hex in EXCHANGES:
        out = conn.feed(H(client_hex))
        assert out == H(expect_hex), (
            "client=%s\n got=%s\nwant=%s" % (client_hex, out.hex(), H(expect_hex).hex()))


def test_feed_byte_at_a_time():
    """The incremental parser must handle arbitrary chunking (TCP can split anywhere)."""
    conn = DglFramedConnection()
    full_in = b"".join(H(c) for c, _ in EXCHANGES)
    full_expect = b"".join(H(e) for _, e in EXCHANGES)
    out = b""
    for i in range(len(full_in)):
        out += conn.feed(full_in[i:i + 1])
    assert out == full_expect


def test_gversion_string_reply_shape():
    """op 0x1 gversion(buf) -> framed [scalar@+4][array_len@+8][12-byte array@+12]."""
    import struct
    conn = DglFramedConnection()
    conn.feed(H("00 00 12 34"))
    out = conn.feed(H(GVERSION_REQ))
    assert out[:4] == bytes.fromhex("10000014"), out.hex()      # 0x10000000 | 20
    assert len(out) == 24                                       # header + scalar + arraylen + 3 words
    assert struct.unpack(">I", out[4:8])[0] == 1                # scalar
    assert struct.unpack(">I", out[8:12])[0] == 0xc            # array byte length
    assert out[12:24].rstrip(b"\0") == b"GL4.0"                 # array data


def test_winopen_assigns_wid():
    """A winopen frame [0x132][namelen][name] returns a framed WID."""
    import struct
    conn = DglFramedConnection()
    conn.feed(H("00 00 12 34"))                       # probe first
    # winopen "gltri" (namelen 8 padded) + reply-sync, as seen live
    body = struct.pack(">IIIIII", 0x132, 8, 0x676c7472, 0x69000000, 0x10004, 0)
    msg = struct.pack(">I", 0x10000000 | len(body)) + body
    out = conn.feed(msg)
    assert out == struct.pack(">I", 0x10000004) + struct.pack(">I", 1), out.hex()


def _ops_seen(body_words):
    """Feed one GL frame (list of words) and return the opcodes on_payload received, in order.
    This exercises _cmdlen sizing: a mis-sized variable-length op consumes the rest of the frame
    and the later opcodes never reach on_payload."""
    import struct
    seen = []
    conn = DglFramedConnection(on_payload=lambda w: seen.append(w[0]))
    conn.feed(H("00 00 12 34"))
    body = struct.pack(">%dI" % len(body_words), *body_words)
    conn.feed(struct.pack(">I", 0x10000000 | len(body)) + body)
    return seen


def test_texgen_does_not_swallow_swapbuffers():
    """texgen is [op][coord][mode][bytelen][params] = 4 + (mode//2)*4 words. With mode=2 that's
    8 words; a following swapbuffers (0x91) must still be seen. (Regression: consume-rest bug.)"""
    # texgen(coord=0, mode=2, bytelen=16, 4 param floats) then swapbuffers
    body = [0x1e8, 0, 2, 16, 0, 0, 0, 0, 0x91]
    assert _ops_seen(body) == [0x1e8, 0x91]


def test_texgen_spheremap_zero_array():
    """atlantis sphere-maps the fish: texgen(coord, TG_SPHEREMAP, NULL) carries a ZERO-length
    param array. The bytelen word (index 3) is the source of truth, NOT the mode value — so this
    is exactly 4 words and the following swapbuffers must survive. (A mode-derived formula would
    over-consume.)"""
    body = [0x1e8, 1, 4, 0, 0x91]          # texgen coord=1 mode=TG_SPHEREMAP bytelen=0 ; swapbuffers
    assert _ops_seen(body) == [0x1e8, 0x91]


def test_texdef2d_two_arrays():
    """texdef2d (0x1e7) has an 8-word header, a first array of p[5] bytes, then a second array
    whose length lives at byte offset (p[5]+0x1c). Verify a following op survives."""
    # header w0..w7 ; p[5]=len1=8 bytes (2 words of array1) ; 2nd len at word (8+0x1c)/4 = 9.
    # words: 0..7 header, 8..9 array1, then word index 9 holds len2 -> but 9 overlaps array1; use
    # len1=0 so 2nd len is at word 7. len1=0, w[7]=len2=4 (1 word array2): total=8+0+1=... compute.
    w = [0x1e7, 0, 0, 0, 0, 0, 0, 4, 0, 0x91]   # len1=w[5]=0, len2 at word (0+0x1c)/4=7 -> w[7]=4
    # total bytes = 0 + 4 + 0x20 = 36 -> 9 words; word 9 = 0x91 must be seen next
    assert _ops_seen(w) == [0x1e7, 0x91]


def test_multmatrix_array_sizing():
    """multmatrix is opcode 0x5c, array-encoded [op][0x40][16 floats] = 18 words; a trailing
    swapbuffers must survive. (Regression: opcode was wrongly 0x5e in ARRAY_OPS.)"""
    body = [0x5c, 0x40] + [0] * 16 + [0x91]
    assert _ops_seen(body) == [0x5c, 0x91]


def test_getmatrix_replies_as_array():
    """getmatrix is a value-returning ARRAY op (dgld_interpret: narrays=1): the reply must be
    [array_bytelen=64][16 floats] = 17 words, NOT 16 bare floats. (Regression: a length-less
    reply makes libgl read the first float as the array length and crash.)"""
    import struct
    from sgi_glremote.dgl_framed import IDENTITY_MATRIX
    conn = DglFramedConnection()                  # no query hook -> identity fallback
    conn.feed(H("00 00 12 34"))
    body = struct.pack(">III II", 0x3f, 0x40, 0x40, 0x10004, 0)   # getmatrix + reply-sync
    out = conn.feed(struct.pack(">I", 0x10000000 | len(body)) + body)
    words = struct.unpack(">%dI" % ((len(out) - 4) // 4), out[4:])
    assert len(words) == 17, words                # bytelen + 16 floats
    assert words[0] == 64                          # array byte length
    assert list(words[1:]) == IDENTITY_MATRIX


def test_batched_prefsize_winopen():
    """The live case: prefsize + winopen batched in one frame -> only the WID reply (prefsize is
    fire-and-forget). Regression for the multi-opcode walk."""
    import struct
    conn = DglFramedConnection()
    conn.feed(H("00 00 12 34"))
    # prefsize(640,480) then winopen("gltri") then reply-sync  (exact live bytes)
    body = struct.pack(">III IIII II",
                       0x128, 0x280, 0x1e0,            # prefsize 640x480
                       0x132, 8, 0x676c7472, 0x69000000,  # winopen "gltri"
                       0x10004, 0)                     # reply-sync
    msg = struct.pack(">I", 0x10000000 | len(body)) + body
    out = conn.feed(msg)
    assert out == struct.pack(">II", 0x10000004, 1), out.hex()
