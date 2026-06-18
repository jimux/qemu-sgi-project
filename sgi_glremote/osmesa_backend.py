#!/usr/bin/env python3
"""OSMesa render backend (Phase-0/1 software GL). Thin context layer over IrisGLBackend:
OSMesa provides the offscreen GL context + an RGBA buffer; all IRIS-GL->OpenGL translation lives
in iris_gl_backend.py and is shared with the native macOS GPU backend (macgl_backend.py).
"""
import os
import struct

os.environ.setdefault("PYOPENGL_PLATFORM", "osmesa")
from OpenGL import GL  # noqa: E402
from OpenGL import osmesa  # noqa: E402
from OpenGL import arrays  # noqa: E402

try:
    from .iris_gl_backend import IrisGLBackend
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sgi_glremote.iris_gl_backend import IrisGLBackend


class _OSMesaCtx:
    """An OSMesa context paired with its host-memory RGBA render buffer."""
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.ctx = osmesa.OSMesaCreateContext(GL.GL_RGBA, None)
        self.buf = arrays.GLubyteArray.zeros((h, w, 4))


class OSMesaBackend(IrisGLBackend):
    """Renders the IRIS GL command stream offscreen via software OSMesa."""

    def _new_window_ctx(self, w, h):
        ctx = _OSMesaCtx(w, h)
        osmesa.OSMesaMakeCurrent(ctx.ctx, ctx.buf, GL.GL_UNSIGNED_BYTE, w, h)
        return ctx

    def _make_current(self, win):
        c = win.ctx
        osmesa.OSMesaMakeCurrent(c.ctx, c.buf, GL.GL_UNSIGNED_BYTE, c.w, c.h)

    def _read_rgba(self, win):
        GL.glFinish()
        import numpy as np
        c = win.ctx
        a = np.frombuffer(bytes(c.buf), dtype=np.uint8).reshape(c.h, c.w, 4)
        return np.flipud(a).tobytes()           # OSMesa is bottom-up; flip to top-down


# --------------------------------------------------------------- self-test
def _write_ppm(path, w, h, rgba):
    with open(path, "wb") as fp:
        fp.write(b"P6\n%d %d\n255\n" % (w, h))
        for i in range(0, len(rgba), 4):
            fp.write(rgba[i:i + 3])


def selftest(out="/workspace/_osmesa_tri.ppm"):
    """Render the gltri command sequence and verify a red triangle on black."""
    from sgi_glremote.dgl import load_tables
    table = load_tables()
    by_name = {i["name"]: o for o, i in table.items() if i["name"]}
    be = OSMesaBackend()

    def enc_floats(name, *vals):
        op = by_name[name]
        words = [struct.unpack(">I", struct.pack(">f", v))[0] for v in vals]
        return (op, name, words)

    wid = be.winopen("gltri")
    be.command(*enc_floats("ortho2", 0.0, 640.0, 0.0, 480.0))
    be.command(by_name["RGBcolor"], "RGBcolor", [(0 << 16) | 0, 0])
    be.command(by_name["clear"], "clear", [])
    be.command(by_name["RGBcolor"], "RGBcolor", [(255 << 16) | 0, 0])
    be.command(by_name["bgnpolygon"], "bgnpolygon", [])
    for (x, y) in ((100.0, 100.0), (500.0, 100.0), (300.0, 400.0)):
        be.command(*enc_floats("v2f", x, y))
    be.command(by_name["endpolygon"], "endpolygon", [])
    be.flush()

    w, h, rgba = be.frames[wid]
    _write_ppm(out, w, h, rgba)

    import numpy as np
    arr = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)
    center = arr[h - 200, 300]
    corner = arr[5, 5]
    print("center pixel(RGBA):", tuple(int(v) for v in center))
    print("corner pixel(RGBA):", tuple(int(v) for v in corner))
    assert center[0] > 200 and center[1] < 60 and center[2] < 60, ("center not red", center)
    assert corner[0] < 40 and corner[1] < 40 and corner[2] < 40, ("corner not black", corner)
    print("OK: red triangle on black rendered via OSMesa ->", out)
    return True


if __name__ == "__main__":
    selftest()
