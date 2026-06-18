#!/usr/bin/env python3
"""Verify the 3D + lighting path on the macOS GPU backend (atlantis's actual feature set)."""
import struct
import numpy as np
from sgi_glremote.macgl_backend import MacGLBackend
from sgi_glremote.dgl import load_tables

t = load_tables()
by = {i["name"]: o for o, i in t.items() if i["name"]}
be = MacGLBackend()
print("GL_RENDERER:", be.renderer)


def F(*v):
    return [struct.unpack(">I", struct.pack(">f", x))[0] for x in v]


def C(n, a):
    be.command(by[n], n, tuple(a))


wid = be.winopen("t3d")
C("mmode", [1 << 16])                                 # MPROJECTION
C("perspective", [(450 << 16)] + F(1.0, 1.0, 100.0))  # 45 deg
C("mmode", [2 << 16])                                 # MVIEWING
C("zbuffer", [1 << 16])
# define + bind a blue material (AMBIENT, DIFFUSE, SHININESS, LMNULL) at index 1
props = F(2.0, 0.0, 0.1, 0.2, 3.0, 0.2, 0.4, 0.9, 5.0, 20.0, 0.0)
C("lmdef", [(0 << 16) | 1, (len(props) << 16), len(props) * 4] + props)
C("lmbind", [(1 << 16) | 1])
C("czclear", [0x00553311, 0x7fffff])
C("translate", F(0.0, 0.0, -5.0))
C("rotate", [(300 << 16) | ord("y")])
C("bgnpolygon", [])
for x, y, z in ((-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (0.0, 1.0, 0.0)):
    C("n3f", F(0, 0, 1)); C("v3f", F(x, y, z))
C("endpolygon", [])
be.flush()
w, h, rgba = be.frames[wid]
a = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)
lit = int(((a[:, :, 2] > 80) & (a[:, :, 0] < 120) & (a[:, :, 1] > 30)).sum())
print("lit blue-ish triangle px:", lit, "| lighting_on:", be._lighting_on)
assert lit > 500, "no lit triangle"
assert be._lighting_on
print("OK: 3D perspective + depth + lit material renders on the GPU (%s)" % be.renderer)
