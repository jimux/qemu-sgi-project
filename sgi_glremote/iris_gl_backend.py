#!/usr/bin/env python3
"""IRIS GL -> OpenGL translation, context-agnostic (the shared core of every render backend).

`IrisGLBackend` decodes the DGL command stream into fixed-function OpenGL calls (immediate-mode
geometry, matrix stack, lighting/materials, depth) against *whatever GL context is current*. It
knows nothing about how that context was created or how frames are read back — those three hooks
(`_new_window_ctx`, `_make_current`, `_read_rgba`) are implemented by a subclass:

  - `OSMesaBackend`  (osmesa_backend.py)  — software GL in the Linux container (Phase 0/1).
  - `MacGLBackend`   (macgl_backend.py)   — native macOS GPU via glfw + FBO (Phase 2 / Milestone 2).

IRIS GL is OpenGL's ancestor, so the mapping is direct (bgnpolygon/v3f/endpolygon -> glBegin/
glVertex/glEnd, lmdef/lmbind -> glMaterial/glLight, perspective -> glFrustum, …). Float args ride
the DGL FIFO as big-endian IEEE-754 bit patterns (u32); reinterpret per arg.
"""
import math
import os
import struct

from OpenGL import GL  # noqa: E402

# IRIS GL mmode() matrix-stack selectors -> OpenGL matrix mode.
#   MSINGLE=0 (one combined stack), MPROJECTION=1, MVIEWING=2, MTEXTURE=3.
_MMODE = {0: GL.GL_MODELVIEW, 1: GL.GL_PROJECTION, 2: GL.GL_MODELVIEW, 3: GL.GL_TEXTURE}

# IRIS GL lmdef() material property tokens (stored AS FLOATS in the props array) ->
# (OpenGL material pname, value count). LMNULL=0 terminates the list.
#   EMISSION=1 AMBIENT=2 DIFFUSE=3 SPECULAR=4 SHININESS=5 COLORINDEXES=6 ALPHA=7
_MAT_PROP = {
    1: (GL.GL_EMISSION, 3),
    2: (GL.GL_AMBIENT, 3),
    3: (GL.GL_DIFFUSE, 3),
    4: (GL.GL_SPECULAR, 3),
    5: (GL.GL_SHININESS, 1),
    6: (None, 3),            # COLORINDEXES (CI mode) — ignored in RGBA
    7: (None, 1),            # ALPHA — folded into the colors' 4th component; skip standalone
}

try:
    from .server import Backend
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sgi_glremote.server import Backend


def _f(word):
    """Reinterpret a u32 (big-endian IEEE-754 bit pattern) as a Python float."""
    return struct.unpack(">f", struct.pack(">I", word & 0xFFFFFFFF))[0]


def _axis(word):
    """IRIS GL rot()/rotate() axis char ('x'/'y'/'z') — packed in some byte of the word."""
    for shift in (24, 16, 8, 0):
        c = (word >> shift) & 0xFF
        if c in (ord("x"), ord("X"), ord("y"), ord("Y"), ord("z"), ord("Z")):
            return chr(c).lower()
    return "z"


class GLWindow:
    """A DGL window: id, size, current color/depth state, and an opaque backend `ctx` handle
    (an OSMesa context+buffer, or a native FBO) that the backend's hooks know how to use."""

    def __init__(self, wid, name, ctx, w=640, h=480):
        self.wid = wid
        self.name = name
        self.w = w
        self.h = h
        self.ctx = ctx                # backend-specific; set by _new_window_ctx
        self.color = (0, 0, 0)
        self.depth_on = False         # zbuffer(TRUE) -> clear depth each frame


class IrisGLBackend(Backend):
    """Translates the IRIS GL DGL stream to OpenGL. Subclasses provide context create/current/
    readback. All GL calls below run against whatever context `_make_current` selected."""

    def __init__(self):
        self._wins = {}
        self._cur = None
        self._active = None           # window whose context is currently bound (avoid churn)
        self._next_wid = 1
        self.frames = {}              # wid -> (w, h, rgba bytes) at last flush
        self.materials = {}           # lmdef index -> {GL_DIFFUSE: [r,g,b], ...}
        self.textures = {}            # texdef2d index -> GL texture id
        self._lighting_on = False

    # --- context hooks (implemented by subclasses) ------------------------
    def _new_window_ctx(self, w, h):
        """Create + make-current a GL context/framebuffer of size w x h; return an opaque handle."""
        raise NotImplementedError

    def _make_current(self, win):
        """Make `win`'s context current."""
        raise NotImplementedError

    def _read_rgba(self, win):
        """Return win's framebuffer as top-down RGBA bytes (w*h*4)."""
        raise NotImplementedError

    def _activate(self, win):
        """Make `win`'s context current ONLY if it isn't already. Switching context / rebinding
        the FBO between glBegin and glEnd is GL_INVALID_OPERATION on strict drivers (Apple GL), so
        we never re-activate mid-command-stream for the same window."""
        if win is not self._active:
            self._make_current(win)
            self._active = win

    # --- window lifecycle -------------------------------------------------
    def _init_gl_state(self, win):
        """Default 2D ortho projection + identity modelview (matches IRIS GL fresh-window state)."""
        GL.glViewport(0, 0, win.w, win.h)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glLoadIdentity()
        GL.glOrtho(0, win.w, 0, win.h, -1, 1)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glLoadIdentity()

    def winopen(self, name, w=640, h=480):
        wid = self._next_wid
        self._next_wid += 1
        ctx = self._new_window_ctx(w, h)         # leaves the new context current
        win = GLWindow(wid, name, ctx, w, h)
        self._wins[wid] = win
        self._cur = win
        self._active = win
        self._init_gl_state(win)
        return wid

    def _winset(self, wid):
        w = self._wins.get(wid)
        if w:
            self._cur = w
            self._activate(w)

    # --- command dispatch -------------------------------------------------
    def command(self, op, name, args):
        w = self._cur
        if name == "winset" and args:
            self._winset(args[0]); return
        if w is None:
            return
        self._activate(w)
        if name == "RGBcolor":
            # [packed_rg][b] : r=hi16, g=lo16 of word0; b=lo16 of word1
            if len(args) >= 2:
                r = (args[0] >> 16) & 0xFFFF
                g = args[0] & 0xFFFF
                b = args[1] & 0xFFFF
            elif len(args) == 1:
                r, g, b = (args[0] >> 16) & 0xFF, (args[0] >> 8) & 0xFF, args[0] & 0xFF
            else:
                return
            w.color = (r, g, b)
            GL.glColor3ub(r, g, b)
        elif name == "cpack" and args:
            v = args[0]
            r, g, b = v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF
            w.color = (r, g, b)
            GL.glColor4ub(r, g, b, (v >> 24) & 0xFF)
        elif name == "clear":
            r, g, b = w.color
            GL.glClearColor(r / 255.0, g / 255.0, b / 255.0, 1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT | (GL.GL_DEPTH_BUFFER_BIT if w.depth_on else 0))
        elif name == "czclear" and args:
            # czclear(cpack_color, zvalue): clear color + Z in one call.
            v = args[0]
            GL.glClearColor((v & 0xFF) / 255.0, ((v >> 8) & 0xFF) / 255.0,
                            ((v >> 16) & 0xFF) / 255.0, 1.0)
            GL.glClearDepth(1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        elif name == "zclear":
            GL.glClear(GL.GL_DEPTH_BUFFER_BIT)
        elif name == "ortho2" and len(args) >= 4:
            GL.glMatrixMode(GL.GL_PROJECTION); GL.glLoadIdentity()
            GL.glOrtho(_f(args[0]), _f(args[1]), _f(args[2]), _f(args[3]), -1, 1)
            GL.glMatrixMode(GL.GL_MODELVIEW); GL.glLoadIdentity()
        # --- matrix stack / viewing transforms -------------------------------
        elif name == "mmode" and args:
            GL.glMatrixMode(_MMODE.get(args[0] >> 16, GL.GL_MODELVIEW))
        elif name == "perspective" and len(args) >= 4:
            # perspective(fovy_decideg, aspect, near, far): fovy is a deci-degree short (hi 16).
            # Implement gluPerspective via glFrustum (GLU isn't reliably present).
            fovy = (args[0] >> 16) / 10.0
            aspect, near, far = _f(args[1]), _f(args[2]), _f(args[3])
            top = near * math.tan(math.radians(fovy) / 2.0)
            right = top * aspect
            # IRIS GL perspective() REPLACES the current matrix; glFrustum multiplies, so reset.
            GL.glLoadIdentity()
            GL.glFrustum(-right, right, -top, top, near, far)
        elif name in ("loadmatrix", "multmatrix") and len(args) >= 17:
            # array-encoded: args[0]=bytelen(0x40), args[1:17]=16 row-major floats.
            mat = [_f(a) for a in args[1:17]]
            (GL.glLoadMatrixf if name == "loadmatrix" else GL.glMultMatrixf)(mat)
        elif name == "translate" and len(args) >= 3:
            GL.glTranslatef(_f(args[0]), _f(args[1]), _f(args[2]))
        elif name == "scale" and len(args) >= 3:
            GL.glScalef(_f(args[0]), _f(args[1]), _f(args[2]))
        elif name == "rot" and len(args) >= 2:
            # rot(float angle_degrees, char axis)
            a = _axis(args[1])
            GL.glRotatef(_f(args[0]), float(a == "x"), float(a == "y"), float(a == "z"))
        elif name == "rotate" and args:
            # rotate(Angle angle_decideg, char axis): angle in hi 16, axis byte in lo word.
            a = _axis(args[0] if len(args) < 2 else args[1])
            GL.glRotatef((args[0] >> 16) / 10.0, float(a == "x"), float(a == "y"), float(a == "z"))
        elif name == "pushmatrix":
            GL.glPushMatrix()
        elif name == "popmatrix":
            GL.glPopMatrix()
        # --- depth / culling / shading ---------------------------------------
        elif name == "zbuffer" and args:
            w.depth_on = bool(args[0])
            (GL.glEnable if w.depth_on else GL.glDisable)(GL.GL_DEPTH_TEST)
        elif name == "backface" and args:
            if args[0]:
                GL.glEnable(GL.GL_CULL_FACE); GL.glCullFace(GL.GL_BACK)
            else:
                GL.glDisable(GL.GL_CULL_FACE)
        elif name == "frontface" and args:
            if args[0]:
                GL.glEnable(GL.GL_CULL_FACE); GL.glCullFace(GL.GL_FRONT)
            else:
                GL.glDisable(GL.GL_CULL_FACE)
        elif name == "shademodel" and args:
            GL.glShadeModel(GL.GL_FLAT if (args[0] >> 16) == 0 else GL.GL_SMOOTH)
        elif name == "n3f" and len(args) >= 3:
            GL.glNormal3f(_f(args[0]), _f(args[1]), _f(args[2]))
        # --- lighting / materials -------------------------------------------
        elif name == "lmdef":
            self._lmdef(args)
        elif name == "lmbind":
            self._lmbind(args)
        # --- textures -------------------------------------------------------
        elif name == "texdef2d":
            self._texdef2d(args)
        elif name == "texbind":
            self._texbind(args)
        elif name == "texgen":
            self._texgen(args)
        elif name in ("tevdef", "tevbind"):
            # texture-environment: atlantis MODULATEs the env-map over the lit material color.
            GL.glTexEnvi(GL.GL_TEXTURE_ENV, GL.GL_TEXTURE_ENV_MODE, GL.GL_MODULATE)
        elif name == "bgnpolygon":
            GL.glBegin(GL.GL_POLYGON)
        elif name in ("bgntmesh",):
            GL.glBegin(GL.GL_TRIANGLE_STRIP)
        elif name == "bgnline":
            GL.glBegin(GL.GL_LINE_STRIP)
        elif name in ("endpolygon", "endtmesh", "endline"):
            GL.glEnd()
        elif name in ("v2f", "v2i", "v2s"):
            if len(args) >= 2:
                if name == "v2f":
                    GL.glVertex2f(_f(args[0]), _f(args[1]))
                else:
                    GL.glVertex2i(args[0], args[1])
        elif name in ("v3f",):
            if len(args) >= 3:
                GL.glVertex3f(_f(args[0]), _f(args[1]), _f(args[2]))
        # RGBmode/gconfig/foreground/prefsize/winconstraints/etc. -> setup no-ops

    # --- lighting helpers -------------------------------------------------
    def _enable_lighting(self):
        """Turn on a default headlight the first time atlantis binds anything lit. We don't yet
        translate atlantis's own light/lmodel definitions, so a white directional light from the
        upper-front + global ambient gives the creatures' n3f normals something to shade against."""
        if self._lighting_on:
            return
        self._lighting_on = True
        GL.glEnable(GL.GL_LIGHTING)
        GL.glEnable(GL.GL_LIGHT0)
        GL.glEnable(GL.GL_NORMALIZE)
        GL.glLightModeli(GL.GL_LIGHT_MODEL_TWO_SIDE, 1)   # creatures' winding is inconsistent
        GL.glLightfv(GL.GL_LIGHT0, GL.GL_POSITION, [0.4, 0.7, 1.0, 0.0])
        GL.glLightfv(GL.GL_LIGHT0, GL.GL_DIFFUSE, [1.0, 1.0, 1.0, 1.0])
        GL.glLightfv(GL.GL_LIGHT0, GL.GL_AMBIENT, [0.25, 0.25, 0.25, 1.0])

    def _lmdef(self, args):
        """lmdef(deftype, index, np, props[]): args = (w1, w2, bytelen, props...).
        deftype/index pack into w1 (hi/lo 16); we parse DEFMATERIAL(0) into self.materials.
        Properties are a float[] token-value list (AMBIENT=2.0, r,g,b, DIFFUSE=3.0, …)."""
        if len(args) < 4:
            return
        deftype = (args[0] >> 16) & 0xFFFF
        index = args[0] & 0xFFFF
        if deftype != 0:                      # only materials for now (lights use the default)
            return
        props = args[3:]
        mat = {}
        i = 0
        while i < len(props):
            tok = int(round(_f(props[i])))
            i += 1
            if tok == 0:                       # LMNULL terminates
                break
            pn = _MAT_PROP.get(tok)
            if pn is None:
                break                          # unknown token -> stop (avoid mis-parsing)
            gl_pn, n = pn
            if i + n > len(props):
                break
            vals = [_f(props[i + k]) for k in range(n)]
            i += n
            if gl_pn is not None:
                mat[gl_pn] = vals
        if mat:
            self.materials[index] = mat

    def _lmbind(self, args):
        """lmbind(target, index): apply a previously-defined material. The target enum packing is
        ambiguous on the wire, so we bind by INDEX match: if `index` names a material we parsed,
        apply it (and ensure lighting is on). target==0/index==0 is an unbind -> ignore."""
        if not args:
            return
        index = args[0] & 0xFFFF
        mat = self.materials.get(index) or self.materials.get((args[0] >> 16) & 0xFFFF)
        if not mat:
            return
        self._enable_lighting()
        for gl_pn, vals in mat.items():
            if gl_pn == GL.GL_SHININESS:
                GL.glMaterialf(GL.GL_FRONT_AND_BACK, gl_pn, max(0.0, min(128.0, vals[0])))
            else:
                GL.glMaterialfv(GL.GL_FRONT_AND_BACK, gl_pn, vals + [1.0])

    # --- textures ---------------------------------------------------------
    def _texdef2d(self, args):
        """texdef2d(index, nc, w, h, image[], np, props[]) -> glTexImage2D. From dgld_interpret +
        the live capture: args = (index, nc, w, h, image_bytelen, image words..., props...). Each
        image word is one IRIS texel packed ABGR (alpha high, red low); reverse the 4 bytes to get
        GL's RGBA. Texture-environment params (filter/wrap) use sensible GL defaults."""
        if len(args) < 6:
            return
        index, nc, w, h = args[0], args[1], args[2], args[3]
        ntex = w * h
        img = args[5:5 + ntex]
        if w <= 0 or h <= 0 or len(img) < ntex:
            return
        import numpy as np
        raw = struct.pack(">%dI" % ntex, *[t & 0xFFFFFFFF for t in img])   # bytes [A,B,G,R]/texel
        rgba = np.frombuffer(raw, np.uint8).reshape(-1, 4)[:, ::-1].tobytes()  # -> [R,G,B,A]
        tid = self.textures.get(index)
        if tid is None:
            tid = int(GL.glGenTextures(1))
            self.textures[index] = tid
        GL.glBindTexture(GL.GL_TEXTURE_2D, tid)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, w, h, 0,
                        GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, rgba)

    def _texbind(self, args):
        """texbind(target, index): bind + enable the texture, or disable when index 0."""
        index = args[1] if len(args) >= 2 else 0
        tid = self.textures.get(index)
        if index and tid is not None:
            GL.glEnable(GL.GL_TEXTURE_2D)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tid)
            GL.glTexEnvi(GL.GL_TEXTURE_ENV, GL.GL_TEXTURE_ENV_MODE, GL.GL_MODULATE)
        else:
            GL.glDisable(GL.GL_TEXTURE_2D)

    _TEXGEN_COORD = None       # built lazily (PyOpenGL enums) in _texgen

    def _texgen(self, args):
        """texgen(coord, mode, params[]): atlantis sphere-environment-maps the fish. coord
        TX_S=0/TX_T=1 -> GL_S/GL_T; mode TG_OFF=0 disables, else GL_SPHERE_MAP."""
        if self._TEXGEN_COORD is None:
            type(self)._TEXGEN_COORD = {
                0: (GL.GL_S, GL.GL_TEXTURE_GEN_S),
                1: (GL.GL_T, GL.GL_TEXTURE_GEN_T),
                2: (GL.GL_R, GL.GL_TEXTURE_GEN_R),
                3: (GL.GL_Q, GL.GL_TEXTURE_GEN_Q),
            }
        coord = args[0] if args else 0
        mode = args[1] if len(args) > 1 else 0
        pair = self._TEXGEN_COORD.get(coord)
        if pair is None:
            return
        gl_coord, gen_enum = pair
        if mode == 0:                                   # TG_OFF
            GL.glDisable(gen_enum)
        else:                                           # TG_SPHEREMAP / reflection mapping
            GL.glTexGeni(gl_coord, GL.GL_TEXTURE_GEN_MODE, GL.GL_SPHERE_MAP)
            GL.glEnable(gen_enum)

    def value(self, op, name, args):
        return Backend.value(self, op, name, args)

    def swapbuffers(self, wid=None):
        w = self._wins.get(wid) if wid else self._cur
        if w is None:
            return None
        self._activate(w)
        f = (w.w, w.h, self._read_rgba(w))
        self.frames[w.wid] = f
        return f

    def flush(self):
        if self._cur:
            self._activate(self._cur)
            self.frames[self._cur.wid] = (self._cur.w, self._cur.h, self._read_rgba(self._cur))

    def get_matrix(self):
        """Current modelview matrix as 16 big-endian-u32 floats (for IRIS GL getmatrix)."""
        if not self._cur:
            return None
        self._activate(self._cur)
        m = GL.glGetFloatv(GL.GL_MODELVIEW_MATRIX)
        flat = [float(m[i][j]) for i in range(4) for j in range(4)]
        return [struct.unpack(">I", struct.pack(">f", v))[0] for v in flat]
