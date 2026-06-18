#!/usr/bin/env python3
"""DGL wire-protocol core for the accelerated-graphics host renderer (Phase 0).

Decodes the IRIS GL DGL command stream (`[opcode:u32][args:u32…]`, big-endian) that the
guest `libgl` ships over TCP :5232. Driven by the reverse-engineered opcode tables
(progress_notes/irisgl_re/dgl_opcodes*.json) and the protocol notes (dgl_protocol.md).

This module is both:
  * the renderer's front-half (parse the stream → a list of GL commands), and
  * the live-capture / dissector tool (#55): point a guest IRIS GL app at it and log the
    decoded stream to validate the table + nail the variable-length arg layouts.

A DGL command's word-length must be known to walk the stream (there is no generic length
prefix). 539 opcodes are fixed (`cmd_words` in the table); 67 are variable (length embedded,
e.g. winopen/charstr carry a byte-count word; poly* carry a vertex-count word). We encode the
known variable rules; an unknown variable opcode raises DglSync so the capture stops cleanly
at the first gap (which then gets added from the captured bytes).
"""
import json
import os
import struct

_HERE = os.path.dirname(os.path.abspath(__file__))
_RE = os.path.join(os.path.dirname(_HERE), "progress_notes", "irisgl_re")


class DglSync(Exception):
    """Raised when the decoder can't determine an opcode's length (lost stream sync)."""


def load_tables():
    """Return {opcode:int -> {name, cmd_words}} merged from client+server tables."""
    client = json.load(open(os.path.join(_RE, "dgl_opcodes.json")))
    server = json.load(open(os.path.join(_RE, "dgl_opcodes_server.json")))
    table = {}
    for k, v in client.items():
        op = int(k, 16)
        table[op] = {"name": v.get("call"), "cmd_words": v.get("cmd_words"),
                     "sym": v.get("sym")}
    for k, v in server.items():
        op = int(k, 16)
        table.setdefault(op, {"name": None, "cmd_words": None})
        if not table[op].get("name"):
            table[op]["name"] = v.get("fn")
    return table


# Value-returning opcodes: the server must write back a reply word (dgl_protocol.md).
# name -> a function(args)->int producing the reply (overridable by the renderer).
VALUE_RETURNING = {
    "gversion": lambda a: 2,          # server GL version
    "dglversion": lambda a: 0,        # 0 = accept (else 0x78)
    "winopen": None,                  # renderer assigns the WID (see DglServer)
    "swinopen": None,
    "gl_setdisplay": lambda a: 0,
    "getgdesc": lambda a: 0,
}

# Login/handshake opcodes (high 0x10000+ range, per dgl_protocol.md).
OP_DGLLOGINX = 0x10010
OP_DGLVERSION = 0x10007
OP_DGLXAUTHORITY = 0x10013

# Variable-length arg rules for the 67 `cmd_words: null` opcodes. Each rule takes the word
# list starting AT the opcode and returns the total command word count (incl. opcode), or
# raises DglSync if it needs more bytes. Extend from the live capture (#55).
def _len_stringcmd(words):
    # [op][byte_len][ceil(len/4) words] — winopen/charstr/objreplace style
    if len(words) < 2:
        raise DglSync("need length word")
    nbytes = words[1]
    return 2 + (nbytes + 3) // 4


def _len_poly(coords_per_vert):
    # [op][n][n*coords floats]
    def rule(words):
        if len(words) < 2:
            raise DglSync("need vertex count")
        n = words[1]
        return 2 + n * coords_per_vert
    return rule


VAR_RULES = {
    "winopen": _len_stringcmd,
    "charstr": _len_stringcmd,
    "objreplace": _len_stringcmd,
    "poly": _len_poly(3), "polf": _len_poly(3),
    "poly2": _len_poly(2), "polf2": _len_poly(2),
    "poly2i": _len_poly(2), "polf2i": _len_poly(2),
    "polyi": _len_poly(3), "polfi": _len_poly(3),
}


class DglDecoder:
    """Feed big-endian u32 words; yields (opcode, name, args[list[int]]) commands."""

    def __init__(self, table=None):
        self.table = table or load_tables()
        self.buf = b""

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            cmd = self._try_one()
            if cmd is None:
                break
            out.append(cmd)
        return out

    def _words(self):
        n = len(self.buf) // 4
        return list(struct.unpack(">%dI" % n, self.buf[:n * 4])) if n else []

    def _try_one(self):
        words = self._words()
        if not words:
            return None
        op = words[0]
        info = self.table.get(op)
        name = info["name"] if info else None
        cw = info["cmd_words"] if info else None
        if cw is not None:                       # fixed-length
            total = cw
        else:                                    # variable-length
            rule = VAR_RULES.get(name)
            if rule is None:
                raise DglSync("unknown var-length opcode 0x%x (%s)" % (op, name))
            try:
                total = rule(words)
            except DglSync:
                return None                       # wait for more bytes
        if len(words) < total:
            return None                           # incomplete command
        args = words[1:total]
        self.buf = self.buf[total * 4:]
        return (op, name, args)


def selftest():
    """Round-trip a synthetic stream of known commands."""
    table = load_tables()
    # Build a stream: gversion(0x1,3w), RGBcolor, clear, winopen("gl"), poly(3 verts)
    def enc(op, *args):
        return struct.pack(">%dI" % (1 + len(args)), op, *[a & 0xFFFFFFFF for a in args])
    # find opcodes by name
    by_name = {}
    for o, i in table.items():
        if i["name"] and i["name"] not in by_name:
            by_name[i["name"]] = o
    stream = b""
    # gversion (3 words total -> 2 args)
    stream += enc(by_name["gversion"], 0, 0)
    # RGBcolor (cmd_words from table)
    rgb = by_name.get("RGBcolor") or by_name.get("cpack")
    if rgb:
        cw = table[rgb]["cmd_words"] or 2
        stream += enc(rgb, *([0xFF] * (cw - 1)))
    # clear
    if "clear" in by_name:
        cw = table[by_name["clear"]]["cmd_words"] or 1
        stream += enc(by_name["clear"], *([0] * (cw - 1)))
    # winopen "gl" -> [op][2][b"gl\0\0"]
    stream += struct.pack(">III", by_name["winopen"], 2, struct.unpack(">I", b"gl\0\0")[0])
    # poly: [op][3][3*3 floats]
    if "poly" in by_name:
        stream += struct.pack(">II", by_name["poly"], 3) + struct.pack(">9I", *range(9))

    dec = DglDecoder(table)
    cmds = dec.feed(stream)
    names = [c[1] for c in cmds]
    print("decoded:", names)
    assert names[0] == "gversion", names
    assert "clear" in names, names
    assert "winopen" in names, names
    assert "poly" in names, names
    # winopen args = [2, packed("gl")]
    wino = [c for c in cmds if c[1] == "winopen"][0]
    assert wino[2][0] == 2, wino
    assert dec.buf == b"", ("trailing bytes", dec.buf)
    print("OK: round-trip clean, %d commands, no trailing bytes" % len(cmds))
    return True


if __name__ == "__main__":
    selftest()
