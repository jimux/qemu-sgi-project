#!/usr/bin/env python3
"""DGL framed connection protocol (the real wire format, sniffed 2026-06-14).

After the raw byte-order probe, libgl speaks a BUFFERED protocol: every message is
    [0x10000000 | payload_nbytes]  (big-endian u32 header)
    [payload: a big-endian u32 opcode stream]
and each value-returning request ends with a 0x10004 reply-sync marker; the server answers with a
framed reply [0x10000000 | reply_nbytes][reply words].

Phase 1 (raw, no framing): C->S 0x1234, S->C 0xffffedcc (== -0x1234, byte-order confirm).

This module implements the server side of both phases, driven incrementally (feed bytes, get reply
bytes back), so it can sit behind the transparent dgld-shim proxy OR a direct socket. The exact
handshake replies are validated against the live capture in tests/test_dgl_framed.py.

See progress_notes/irisgl_re/dgl_protocol.md + dgl_handshake_capture.txt.
"""
import struct

try:
    from .dgl import load_tables
except ImportError:
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sgi_glremote.dgl import load_tables

FRAME_MARKER = 0x10000000
XDR_PROBE = 0x1234
XDR_REPLY = 0xffffedcc          # -0x1234 & 0xffffffff
OP_REPLYSYNC = 0x10004          # trailing marker on value-returning requests

# Login opcodes (high range) -> number of reply words (from the live capture).
OP_DGLXDRFORMAT = 0x10003
OP_DGLLOGINX = 0x10010
OP_DGLVERSION = 0x10007
OP_DGLXAUTHORITY = 0x10013
OP_GVERSION = 0x143
OP_GVERSION_STR = 0x1     # gversion(buf): returns [scalar][12-byte version-string array]
OP_WINOPEN = 0x132
OP_SWINOPEN = 0x1a3
OP_GETGDESC = 0x1be       # getgdesc(token) -> one graphics-description value (value-returning)
OP_GETGCONFIG = 0x216     # getgconfig(token) -> current GLC_* compat-mode value (value-returning)

# getgdesc(token) values (gl/gl.h GD_* tokens). What a typical 24-bit RGB double-buffered Z
# pipe reports; OSMesa-backed software path advertises a capable-but-simple config. Unknown
# tokens default to 1 (capability present) — apps query these to choose render paths.
GD_VALUES = {
    0: 1280, 1: 1024, 2: 1280, 3: 1024,           # XPMAX YPMAX XMMAX YMMAX
    4: 0, 5: 0x7fffff,                            # ZMIN ZMAX
    6: 8, 7: 8, 8: 8, 9: 8, 10: 8, 11: 8,         # BITS_NORM SNG/DBL R/G/B
    12: 8, 13: 8,                                 # SNG/DBL CMODE
    16: 24,                                       # BITS_NORM_ZBUFFER
    21: 8, 22: 8, 23: 1,                          # ALPHA SNG/DBL, CURSOR
    25: 1, 28: 1,                                 # BLEND, DITHER
    35: 1,                                        # NSCRNS
    39: 256,                                      # NVERTEX_POLY
    50: 0,                                        # STEREO
    55: 1,                                        # WSYS (1 = window system present)
    61: 1,                                        # SCRNTYPE
    66: 60,                                       # TIMERHZ
    70: 0, 71: 0, 72: 0,                          # ACBUF, ACBUF_HW, STENCIL (none)
    73: 6,                                        # CLIPPLANES
    76: 1,                                        # LIGHTING_TWOSIDE
    77: 1,                                        # POLYMODE
    80: 1,                                        # TEXTURE
    84: 0,                                        # MULTISAMPLE (none in OSMesa software path)
    85: 0, 86: 0,                                 # TEXTURE_3D, TEXTURE_LUT
}


def getgdesc_value(token):
    return GD_VALUES.get(token, 1)


# IEEE-754 identity 4x4 matrix as big-endian u32 words (1.0 = 0x3f800000).
_F1 = 0x3f800000
IDENTITY_MATRIX = [_F1, 0, 0, 0,  0, _F1, 0, 0,  0, 0, _F1, 0,  0, 0, 0, _F1]

# Value-returning opcodes -> number of reply words the client expects (so we never desync the
# client's read). State-dependent ones (getmatrix) are filled by a query hook; the rest default
# to zeros (safe: "not pressed", "queue empty", "feature absent"). Extend as demos reveal more.
REPLY_WORDS = {
    0x3f: 16,    # getmatrix -> 4x4 matrix
    0x32: 1,     # getbutton -> 0 (not pressed)
    0x7a: 1,     # qtest -> 0 (event queue empty)
    0x1be: 1,    # getgdesc
    0x216: 1,    # getgconfig
    0x33: 1,     # getvaluator
}

# Opcodes the table marks variable (cmd_words=None/0) that are actually FIXED length here.
FIXED_LEN = {}

# Array-header opcodes: a run of `base` header words (incl. the opcode) whose LAST word is an
# explicit byte-length, followed by ceil(bytelen/4) data words. Total = base + ceil(p[base-1]/4).
# These are read verbatim from dgld_interpret's per-case pointer advance (advance = p[base-1] bytes
# + base*4), so the sizing is authoritative — not inferred. Critical: if NOT sized, they consume
# the rest of the frame and swallow the trailing swapbuffers that presents it.
#   0x4f loadmatrix / 0x5c multmatrix : [op][0x40][16 floats]            base 2
#   0x132 winopen / 0x1a3 swinopen    : [op][namelen][name…]            base 2
#   0x1e5 scrsubdivide                : [op][arg][bytelen][data]         base 3
#   0x1e6 tevdef / 0x1e8 texgen / 0x16b lmdef : [op][a][b][bytelen][data] base 4
ARRAY_HEADER = {
    0x4f: 2, 0x5c: 2,
    0x1e5: 3,
    0x1e6: 4, 0x1e8: 4, 0x16b: 4,
}
# winopen/swinopen are in ARRAY_HEADER conceptually but handled explicitly in _cmdlen for the WID
# reply path; keep this set for that branch.
ARRAY_OPS = set(ARRAY_HEADER)

# IRIS GL version string returned by gversion(); dglopen only reads it for capability flags
# (it does not fail on the value), so a plausible "GL4.0" satisfies it. 12 bytes (the client
# requested 0xc), packed as 3 big-endian words after the scalar return.
GVERSION_STRING = b"GL4.0\0\0\0\0\0\0\0"

# opcode -> reply words the server sends (framed). winopen handled specially (WID).
HANDSHAKE_REPLY = {
    OP_DGLXDRFORMAT:  [0],
    OP_DGLLOGINX:     [0, 0, 0],
    OP_DGLVERSION:    [0],
    OP_DGLXAUTHORITY: [0],
    OP_GVERSION:      [1],
}


def frame(words):
    """Wrap reply words in the [0x10000000|nbytes] buffer framing."""
    body = struct.pack(">%dI" % len(words), *[w & 0xFFFFFFFF for w in words])
    return struct.pack(">I", FRAME_MARKER | len(body)) + body


class DglFramedConnection:
    """Incremental server: feed raw client bytes, get raw reply bytes. Tracks the handshake,
    extracts GL payloads for the renderer, and assigns WIDs on winopen."""

    def __init__(self, backend=None, on_payload=None, query=None):
        self.buf = b""
        self.got_probe = False
        self.backend = backend          # optional: .winopen(name)->wid, .command(op,name,args)
        self.on_payload = on_payload    # optional callback(list_of_words) for ONE GL command
        self.query = query              # optional fn(op, cmd_words)->list[words] for GL-state queries
        self.next_wid = 1
        self.wids = []
        self.unhandled_queries = []     # value-returning opcodes answered by the default fallback
        self.table = load_tables()      # opcode -> {name, cmd_words}

    def _texdef2d_len(self, words):
        """texdef2d (0x1e7) carries TWO arrays. From dgld_interpret:
            advance_bytes = p[5] + *(int*)((char*)p + p[5] + 0x1c) + 0x20
        i.e. a fixed 0x20-byte (8-word) header, a first array of p[5] bytes, then a second array
        whose byte-length lives at byte offset (p[5] + 0x1c). Returns total words, 0 if incomplete."""
        if len(words) < 8:
            return 0
        len1 = words[5]                          # first array byte length
        idx2 = (len1 + 0x1c) // 4                # word index holding the 2nd array's byte length
        if idx2 >= len(words):
            return 0
        len2 = words[idx2]
        return (len1 + len2 + 0x20 + 3) // 4

    def _cmdlen(self, words):
        """Word count (incl. opcode) of the command starting at words[0]; 0 if incomplete."""
        op = words[0]
        if op in (OP_WINOPEN, OP_SWINOPEN):
            if len(words) < 2:
                return 0
            return 2 + (words[1] + 3) // 4         # [op][namelen][ceil(len/4) name words]
        if op in FIXED_LEN:
            return FIXED_LEN[op]
        if op == 0x1e7:                            # texdef2d: two arrays (see dgld_interpret)
            return self._texdef2d_len(words)
        base = ARRAY_HEADER.get(op)
        if base is not None:                       # [op][..][bytelen][data] header of `base` words
            if len(words) < base:
                return 0
            return base + (words[base - 1] + 3) // 4
        info = self.table.get(op)
        cw = info["cmd_words"] if info else None
        return cw if cw else len(words)            # fixed length, or consume the rest

    def _reply_for(self, op, cmd):
        """Build the framed reply for a value-returning opcode `op` (last in a reply-sync frame)."""
        if op == OP_GVERSION_STR:
            # gversion(buf) can be called mid-stream (not just in the handshake). It returns an
            # ARRAY (scalar + 12-byte version string); a scalar [0] makes libgl mis-unpack the
            # array length and corrupt its read buffer (delayed crash). Always reply the string.
            return self._gversion_reply()
        if op in (OP_WINOPEN, OP_SWINOPEN):
            wid = self.next_wid
            self.next_wid += 1
            self.wids.append(wid)
            return frame([wid])
        if op == OP_GETGDESC:
            return frame([getgdesc_value(cmd[1] if len(cmd) > 1 else 0)])
        if op == OP_GETGCONFIG:
            return frame([0])
        if op == 0x3f:
            # getmatrix returns ONE ARRAY (dgld_interpret: mem_narrays=1, mem_nscalars=0), so the
            # reply is [array_bytelen][16 floats] — NOT 16 bare scalars. Without the length word
            # libgl reads the first float (0x3f800000) as the array length and crashes. Use the
            # renderer's live matrix if available, else identity.
            mat = self.query(op, cmd) if self.query is not None else None
            if not mat:
                mat = IDENTITY_MATRIX
            return frame([len(mat) * 4] + list(mat))
        # other state-dependent queries -> ask the renderer; else a safe default
        if self.query is not None:
            words = self.query(op, cmd)
            if words is not None:
                return frame(words)
        nwords = REPLY_WORDS.get(op)
        if nwords is None:
            self.unhandled_queries.append(op)
            nwords = 1
        return frame([0] * nwords)

    def feed(self, data):
        """Return reply bytes to send back to libgl (may be b'')."""
        self.buf += data
        out = b""
        # Phase 1: the raw 0x1234 probe (no framing) comes first.
        if not self.got_probe:
            if len(self.buf) < 4:
                return out
            probe = struct.unpack(">I", self.buf[:4])[0]
            self.buf = self.buf[4:]
            self.got_probe = True
            if probe == XDR_PROBE:
                out += struct.pack(">I", XDR_REPLY)
            else:
                # not the expected probe; reply the byte-order confirm anyway (best effort)
                out += struct.pack(">I", XDR_REPLY)
        # Phase 2: framed messages.
        while len(self.buf) >= 4:
            hdr = struct.unpack(">I", self.buf[:4])[0]
            if (hdr & 0xFF000000) != FRAME_MARKER:
                # desync / unframed trailing data — stop, keep bytes for next feed
                break
            nbytes = hdr & 0x00FFFFFF
            if len(self.buf) < 4 + nbytes:
                break
            payload = self.buf[4:4 + nbytes]
            self.buf = self.buf[4 + nbytes:]
            out += self._handle_payload(payload)
        return out

    def _gversion_reply(self):
        # reply = [scalar@+4][array_length@+8][array data@+12]. An array on the wire is
        # [length_word][data] (gl_d_charstr: gl_mem_pack_array packs only the data, with the byte
        # length written as a separate preceding word). gl_d_gversion reads scalar@+4 and
        # gl_mem_unpack_array at +8 (so +8=length, +12=data).
        vw = list(struct.unpack(">3I", GVERSION_STRING))       # 12 bytes = 3 words
        return frame([1, len(GVERSION_STRING)] + vw)

    def _handle_payload(self, payload):
        words = list(struct.unpack(">%dI" % (len(payload) // 4), payload)) if len(payload) >= 4 else []
        # a trailing reply-sync marker (0x10004 + 0) means the frame's LAST opcode is
        # value-returning and the app is blocked reading a reply.
        had_replysync = len(words) >= 2 and words[-2] == OP_REPLYSYNC
        if had_replysync:
            words = words[:-2]
        if not words:
            return frame([0]) if had_replysync else b""
        # Login/handshake frames carry a single high-range opcode.
        op0 = words[0]
        if op0 == OP_GVERSION_STR:
            if self.on_payload:
                self.on_payload(words)
            return self._gversion_reply()
        if op0 in HANDSHAKE_REPLY:
            if self.on_payload:
                self.on_payload(words)
            return frame(HANDSHAKE_REPLY[op0])
        # GL command frame: MULTIPLE opcodes may be batched. Process each via on_payload (for
        # rendering); the value-returning opcode is the LAST one (the reply-sync forces a flush).
        i = 0
        last_cmd = None
        while i < len(words):
            n = self._cmdlen(words[i:])
            if n <= 0 or i + n > len(words):
                break
            cmd = words[i:i + n]
            i += n
            if self.on_payload:
                self.on_payload(cmd)
            last_cmd = cmd
        if had_replysync and last_cmd:
            return self._reply_for(last_cmd[0], last_cmd)
        return b""
