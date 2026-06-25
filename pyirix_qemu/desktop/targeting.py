"""Reliable cursor targeting + window manipulation for the IRIX desktop.

ServoDriver: closed-loop cursor positioning (refactored from servo.py), immune
to X11 pointer acceleration -- it reads the guest hardware-cursor position from
the NP_CURSOR `cursor=(x,y)` trace in the QEMU log and issues proportional
relative mouse_move steps over the monitor socket until on target. The constant
CLICK_OFFSET=(30,30) is the Newport VC2 cursor hardware offset measured live
(the trace value is BEFORE hotspot correction; a click lands at VC2-(30,30)),
so callers pass SCREEN coordinates and the driver targets VC2 = screen+offset.
virtuix-only (NP_CURSOR trace not emitted by the Indy machine).

Targeter: binds introspected geometry to actions. Move via real titlebar drag
OR protocol; RESIZE via protocol only (interactive handle-drag does NOT engage
4Dwm via synthetic Newport input -- protocol XMoveResizeWindow does, exactly,
proven 8-way). Each action re-introspects to confirm the new geometry.
"""
from __future__ import annotations

import re
import socket
import time

CLICK_OFFSET = (30, 30)
_CUR = re.compile(r"cursor=\((-?\d+),(-?\d+)\)")


class ServoDriver:
    def __init__(self, mon_sock: str, qlog: str, tol: int = 3, maxit: int = 80,
                 offset: tuple = CLICK_OFFSET):
        self.mon_sock = mon_sock
        self.qlog = qlog
        self.tol = tol
        self.maxit = maxit
        self.ox, self.oy = offset

    def mon(self, c: str, wait: float = 0.12, read: bool = False) -> str:
        """Send a monitor command. read=False (default) is fire-and-forget --
        right for mouse_move/mouse_button, where the reply is unneeded and the
        servo reads the result from the NP_CURSOR log instead. Reading the full
        reply means waiting out the recv timeout (~slow), so only do it when
        the caller actually wants the text."""
        s = socket.socket(socket.AF_UNIX)
        s.connect(self.mon_sock)
        s.settimeout(0.5)
        time.sleep(0.02)
        try:
            s.recv(65536)
        except Exception:
            pass
        s.sendall((c + "\n").encode())
        time.sleep(wait)
        o = b""
        if read:
            try:
                for _ in range(4):
                    o += s.recv(65536)
            except Exception:
                pass
        s.close()
        return o.decode("latin1", "replace")

    def _vc2(self):
        """Current hardware cursor (VC2) position from the NP_CURSOR log tail."""
        try:
            with open(self.qlog, "rb") as f:
                f.seek(0, 2)
                sz = f.tell()
                f.seek(max(0, sz - 16384))
                tail = f.read().decode("latin1", "replace")
        except FileNotFoundError:
            return None
        ms = _CUR.findall(tail)
        return (int(ms[-1][0]), int(ms[-1][1])) if ms else None

    def where(self):
        """Current CLICK point (screen coords) = VC2 - offset."""
        v = self._vc2()
        return (v[0] - self.ox, v[1] - self.oy) if v else None

    def _read_after(self, prev, timeout: float = 0.5):
        """After a move, wait for the NP_CURSOR log to reflect a NEW position
        (the move registered) before trusting the reading -- this is what keeps
        the closed loop precise without paying the slow monitor recv timeout."""
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.04)
            cur = self._vc2()
            if cur and cur != prev:
                time.sleep(0.04)            # let it settle one more tick
                return self._vc2() or cur
        return self._vc2() or prev

    def _servo_vc2(self, tx, ty):
        self.mon("mouse_move 1 0")
        c = self._read_after(None)
        for _ in range(self.maxit):
            if c is None:
                self.mon("mouse_move 2 0"); c = self._read_after(None); continue
            ex, ey = tx - c[0], ty - c[1]
            if abs(ex) <= self.tol and abs(ey) <= self.tol:
                return c
            dx = max(-12, min(12, ex)); dy = max(-12, min(12, ey))
            prev = c
            self.mon(f"mouse_move {dx} {dy}")
            c = self._read_after(prev)
        return c

    def to(self, sx, sy):
        """Position the CLICK point at screen (sx, sy)."""
        return self._servo_vc2(sx + self.ox, sy + self.oy)

    def click(self, sx, sy):
        self.to(sx, sy)
        self.mon("mouse_button 1"); time.sleep(0.18); self.mon("mouse_button 0")

    def press(self, sx, sy):
        self.to(sx, sy); self.mon("mouse_button 1")

    def release(self):
        self.mon("mouse_button 0")

    def dbl(self, sx, sy):
        self.to(sx, sy)
        for _ in range(2):
            self.mon("mouse_button 1"); time.sleep(0.08)
            self.mon("mouse_button 0"); time.sleep(0.1)

    def drag(self, sx0, sy0, sx1, sy1):
        self.to(sx0, sy0); self.mon("mouse_button 1"); time.sleep(0.3)
        self.to(sx1, sy1); time.sleep(0.2); self.mon("mouse_button 0")


class Targeter:
    """High-level: act on windows by name, confirming via re-introspection."""

    def __init__(self, desktop, servo: ServoDriver):
        self.d = desktop
        self.servo = servo

    # --- pointer actions ---
    def click_window(self, needle, where="center"):
        w = self.d.find(needle)
        if not w:
            raise ValueError(f"no window matching {needle!r}")
        pt = w.center if where == "center" else w.move_grab()
        self.servo.click(*pt)
        return w

    # --- move ---
    def move_window(self, needle, x, y, method="protocol"):
        w = self.d.find(needle)
        if not w:
            raise ValueError(f"no window matching {needle!r}")
        if method == "drag":
            gx, gy = w.move_grab()
            self.servo.drag(gx, gy, x + (gx - w.x), y + (gy - w.y))
        else:
            self.d._x(f"{self.d.gwxq} move {w.id} {x} {y}")
        time.sleep(0.6)
        return self.d.find(needle)

    # --- resize (protocol, 8-way: keep the chosen anchor fixed) ---
    def resize_window(self, needle, new_w, new_h, anchor="se"):
        """anchor in {nw,ne,sw,se,n,s,e,w}: the edge/corner held fixed while the
        opposite side moves to reach (new_w,new_h)."""
        w = self.d.find(needle)
        if not w:
            raise ValueError(f"no window matching {needle!r}")
        x, y = w.x, w.y
        # keep right edge fixed for west moves; bottom edge for north moves
        if "w" in anchor:
            x = w.x + w.w - new_w
        if "n" in anchor:
            y = w.y + w.h - new_h
        self.d._x(f"{self.d.gwxq} moveresize {w.id} {x} {y} {new_w} {new_h}")
        time.sleep(0.6)
        return self.d.find(needle)
