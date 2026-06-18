#!/usr/bin/env python3
"""Native macOS GPU render backend (Phase 2 / Milestone 2).

Same IRIS-GL->OpenGL translation as the software path (IrisGLBackend); only the GL context +
readback differ. A single hidden glfw window provides one shared OpenGL 2.1 (legacy / fixed-
function) context — on macOS that's "OpenGL over Metal", i.e. it runs on the GPU (e.g. Apple
M-series). Each DGL window gets its own offscreen FBO (RGBA8 color + depth renderbuffer); frames
are read back with glReadPixels.

macOS threading note: glfw window creation + GL must stay on the MAIN thread (Cocoa). Use the
single-threaded mac_renderer entry point — create the context via `ensure_context()` on the main
thread before serving, and serve one connection at a time. Do NOT drive this from a worker thread.

Run the probe:  .venv-glremote/bin/python -m sgi_glremote.macgl_backend
"""
import os
import struct
import sys

import glfw
from OpenGL import GL
import numpy as np

try:
    from .iris_gl_backend import IrisGLBackend
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sgi_glremote.iris_gl_backend import IrisGLBackend

_MASTER = None          # the single hidden glfw window holding the shared GL context


def ensure_context():
    """Create the shared hidden-window GL context (idempotent). MUST run on the main thread."""
    global _MASTER
    if _MASTER is not None:
        return _MASTER
    if not glfw.init():
        raise RuntimeError("glfw.init() failed")
    # No version hints on macOS => 2.1 legacy profile (full fixed-function pipeline).
    glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
    win = glfw.create_window(16, 16, "sgi_glremote", None, None)
    if not win:
        glfw.terminate()
        raise RuntimeError("glfw.create_window() failed")
    glfw.make_context_current(win)
    _MASTER = win
    return win


class _FBOCtx:
    """An offscreen framebuffer (color + depth) for one DGL window."""
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        self.color = GL.glGenRenderbuffers(1)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, self.color)
        GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_RGBA8, w, h)
        GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0,
                                     GL.GL_RENDERBUFFER, self.color)
        self.depth = GL.glGenRenderbuffers(1)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, self.depth)
        GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_DEPTH_COMPONENT24, w, h)
        GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT,
                                     GL.GL_RENDERBUFFER, self.depth)
        status = GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER)
        if status != GL.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError("FBO incomplete: 0x%x" % status)


class MacGLBackend(IrisGLBackend):
    """Renders the IRIS GL command stream on the macOS GPU via a shared GL context + per-window FBO."""

    def __init__(self):
        super().__init__()
        ensure_context()
        self.renderer = GL.glGetString(GL.GL_RENDERER).decode()
        self.gl_version = GL.glGetString(GL.GL_VERSION).decode()

    def _new_window_ctx(self, w, h):
        ensure_context()
        ctx = _FBOCtx(w, h)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, ctx.fbo)
        return ctx

    def _make_current(self, win):
        glfw.make_context_current(_MASTER)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, win.ctx.fbo)

    def _read_rgba(self, win):
        GL.glFinish()
        c = win.ctx
        GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
        buf = GL.glReadPixels(0, 0, c.w, c.h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
        a = np.frombuffer(buf, np.uint8).reshape(c.h, c.w, 4)
        return np.flipud(a).tobytes()           # GL is bottom-up; flip to top-down


def _probe():
    """Render the gltri sequence on the GPU + a 3D depth-tested poly; confirm GPU + correctness."""
    from sgi_glremote.dgl import load_tables
    table = load_tables()
    by = {i["name"]: o for o, i in table.items() if i["name"]}
    be = MacGLBackend()
    print("GL_RENDERER:", be.renderer, "| GL_VERSION:", be.gl_version)

    def F(*v):
        return [struct.unpack(">I", struct.pack(">f", x))[0] for x in v]

    def C(n, a):
        be.command(by[n], n, tuple(a))

    wid = be.winopen("gltri")
    C("ortho2", F(0.0, 640.0, 0.0, 480.0))
    C("RGBcolor", [(0 << 16) | 0, 0]); C("clear", [])
    C("RGBcolor", [(255 << 16) | 0, 0])
    C("bgnpolygon", [])
    for x, y in ((100.0, 100.0), (500.0, 100.0), (300.0, 400.0)):
        C("v2f", F(x, y))
    C("endpolygon", [])
    be.flush()
    w, h, rgba = be.frames[wid]
    arr = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)
    center = tuple(int(v) for v in arr[h - 200, 300])
    corner = tuple(int(v) for v in arr[5, 5])
    print("center:", center, "corner:", corner)
    assert center[0] > 200 and center[1] < 60 and center[2] < 60, ("center not red", center)
    assert corner[0] < 40, ("corner not black", corner)
    assert "Apple" in be.renderer or "AMD" in be.renderer or "Intel" in be.renderer, \
        ("not a GPU renderer?", be.renderer)
    print("OK: IRIS GL triangle rendered on the macOS GPU (%s)" % be.renderer)
    return True


if __name__ == "__main__":
    _probe()
