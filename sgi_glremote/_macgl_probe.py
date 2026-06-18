#!/usr/bin/env python3
"""Feasibility probe: create a HARDWARE OpenGL context on macOS (via glfw, hidden window),
confirm it's GPU-backed and exposes the fixed-function pipeline IRIS GL needs (2.1 legacy),
then render + read back a triangle through an FBO (the offscreen path the renderer will use)."""
import glfw
from OpenGL import GL
import numpy as np

if not glfw.init():
    raise SystemExit("glfw.init failed")
# macOS: NO version hints => 2.1 legacy profile (full fixed-function: glBegin/glMaterial/glLight).
glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
win = glfw.create_window(64, 64, "probe", None, None)
if not win:
    glfw.terminate(); raise SystemExit("create_window failed")
glfw.make_context_current(win)

print("GL_RENDERER:", GL.glGetString(GL.GL_RENDERER).decode())
print("GL_VENDOR  :", GL.glGetString(GL.GL_VENDOR).decode())
print("GL_VERSION :", GL.glGetString(GL.GL_VERSION).decode())

W = H = 128
# offscreen FBO with a color renderbuffer
fbo = GL.glGenFramebuffers(1)
GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
rb = GL.glGenRenderbuffers(1)
GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, rb)
GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_RGBA8, W, H)
GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, rb)
assert GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) == GL.GL_FRAMEBUFFER_COMPLETE

GL.glViewport(0, 0, W, H)
GL.glClearColor(0, 0, 0, 1)
GL.glClear(GL.GL_COLOR_BUFFER_BIT)
GL.glColor3f(1, 0, 0)                       # fixed-function immediate mode
GL.glBegin(GL.GL_TRIANGLES)
GL.glVertex2f(-0.6, -0.6); GL.glVertex2f(0.6, -0.6); GL.glVertex2f(0.0, 0.7)
GL.glEnd()
GL.glFinish()

buf = GL.glReadPixels(0, 0, W, H, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
a = np.frombuffer(buf, np.uint8).reshape(H, W, 4)
center = tuple(int(v) for v in a[H // 2, W // 2])
corner = tuple(int(v) for v in a[2, 2])
print("center px:", center, "corner px:", corner)
assert center[0] > 200 and center[1] < 60, ("center not red", center)
print("OK: hardware GL context + FBO offscreen render + readback works")
glfw.terminate()
