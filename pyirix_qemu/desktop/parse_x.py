"""Pure parsers for stock IRIX X-client text output -- the zero-deploy fallback
for window introspection when the gwxq helper isn't on the guest (e.g. on the
Indy machine, or before gwxq is pushed). No I/O here, so it is unit-testable on
the host against captured fixtures (test_fixtures/).

The primary path is gwxq's JSON (introspect.Desktop); this parses
`xwininfo -root -tree` into the same shape.

Real `xwininfo -root -tree` line format (captured live):
    <indent>0x<id> "<name>": ("<inst>" "<class>")  <W>x<H>+<rx>+<ry>  +<ax>+<ay>
  or  <indent>0x<id> (has no name): ()  <W>x<H>+<rx>+<ry>  +<ax>+<ay>
"""
from __future__ import annotations

import re

# tail = geometry + absolute position; head = id + name + class before it
_TAIL = re.compile(
    r"\s+(\d+)x(\d+)([+-]\d+)([+-]\d+)\s+([+-]\d+)([+-]\d+)\s*$")
_HEAD = re.compile(
    r"^(?P<indent>\s*)0x(?P<id>[0-9a-fA-F]+)\s+"
    r'(?:"(?P<name>.*)"|\(has no name\)):\s+'
    r'(?:\("(?P<inst>.*?)"\s+"(?P<cls>.*?)"\)|\(\))\s*$')


def parse_xwininfo_tree(text: str) -> list[dict]:
    """Parse `xwininfo -root -tree` into a list of window dicts with
    id,name,inst,class,w,h,x,y (root-relative absolute),depth,parent.

    depth/parent are reconstructed from indentation (each tree level is more
    indented than its parent), so the 4Dwm frame relationship is available the
    same way as in the gwxq path.
    """
    wins: list[dict] = []
    stack: list[tuple[int, str]] = []  # (indent, id)
    for line in text.splitlines():
        tail = _TAIL.search(line)
        if not tail:
            continue
        head = _HEAD.match(line[:tail.start()] + "\n")
        if not head:
            continue
        w, h, _rx, _ry, ax, ay = tail.groups()
        indent = len(head.group("indent"))
        wid = "0x" + head.group("id")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else "0x0"
        depth = len(stack)
        stack.append((indent, wid))
        wins.append({
            "id": wid,
            "name": head.group("name") or "",
            "inst": head.group("inst") or "",
            "class": head.group("cls") or "",
            "w": int(w), "h": int(h), "x": int(ax), "y": int(ay),
            "depth": depth, "parent": parent,
        })
    return wins


def parse_xprop_blocks(text: str, sep: str = "PROP=") -> dict:
    """Parse a batched `for w in ...; do echo PROP=$w; xprop -id $w ...; done`
    into {id: {state, name, class, managed}}. state: normal/iconic/withdrawn."""
    out: dict[str, dict] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith(sep):
            cur = line[len(sep):].strip()
            out[cur] = {"managed": False}
            continue
        if cur is None:
            continue
        d = out[cur]
        m = re.search(r"window state:\s*(\w+)", line)
        if m:
            d["state"] = m.group(1).lower()
            d["managed"] = True
        m = re.search(r'WM_NAME\(\w+\)\s*=\s*"(.*)"', line)
        if m:
            d["name"] = m.group(1)
        m = re.search(r'WM_CLASS\(\w+\)\s*=\s*"(.*?)",\s*"(.*?)"', line)
        if m:
            d["inst"], d["class"] = m.group(1), m.group(2)
    return out
