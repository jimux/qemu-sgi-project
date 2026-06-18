"""Texture path (Milestone 3): texdef2d upload + texbind + sphere-map texgen + MODULATE env.
A solid-blue texture sphere-mapped onto a white quad must render the TEXTURE color, proving the
IRIS-GL texture opcodes translate correctly. OSMesa (container) only."""
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
pytest.importorskip("sgi_glremote.osmesa_backend", reason="PyOpenGL/OSMesa not available")
from sgi_glremote.osmesa_backend import OSMesaBackend
from sgi_glremote.dgl import load_tables


def _f(x):
    return struct.unpack(">I", struct.pack(">f", x))[0]


def test_texdef2d_texgen_modulate_renders_texture_color():
    import numpy as np
    by = {i["name"]: o for o, i in load_tables().items() if i["name"]}
    be = OSMesaBackend()

    def C(n, a):
        be.command(by[n], n, tuple(a))

    be.winopen("ttex")
    C("ortho2", [_f(0.0), _f(1.0), _f(0.0), _f(1.0)])
    # 4x4 solid-blue texture: IRIS texel is ABGR-packed (A hi, R lo) -> blue = 0x00ff0000
    W = H = 4
    C("texdef2d", [1, 4, W, H, W * H * 4] + [0x00FF0000] * (W * H))
    C("texbind", [0, 1])
    C("texgen", [0, 4, 0])          # TX_S sphere map
    C("texgen", [1, 4, 0])          # TX_T sphere map
    C("RGBcolor", [(255 << 16) | 255, 255])   # white vertex color
    C("bgnpolygon", [])
    for x, y in ((0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)):
        C("n3f", [_f(0), _f(0), _f(1)]); C("v2f", [_f(x), _f(y)])
    C("endpolygon", [])
    be.flush()

    w, h, rgba = be.frames[be._cur.wid]
    a = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)
    r, g, b = (int(v) for v in a[h // 2, w // 2][:3])
    # white * blue-texture (MODULATE) -> blue dominant
    assert b > 150 and r < 100 and g < 100, ("expected blue-textured quad", (r, g, b))


def test_texture_registered_by_index():
    by = {i["name"]: o for o, i in load_tables().items() if i["name"]}
    be = OSMesaBackend()
    be.winopen("t")
    be.command(by["texdef2d"], "texdef2d", (7, 4, 2, 2, 16) + (0,) * 4)
    assert 7 in be.textures            # texdef2d registered a GL texture under its index
