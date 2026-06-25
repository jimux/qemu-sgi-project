"""Structured introspection of the running IRIX 4Dwm desktop -- the agent's
"eyes". Reports the window list (names, geometry, state) and readiness signals
WITHOUT a framebuffer screenshot, by driving the in-guest `gwxq` helper (and a
few stock X clients) over the gwagent channel.

All facts here were validated live (see progress_notes/ip55/desktop_eyes.md):
  * X clients HANG during the login screen (xdm grabs the server) -> login
    readiness uses process presence (clogin), never an X query.
  * Post-login the grab releases and X introspection works.
  * X11 root-window pixel coords == VC2 cursor coords == servo targets (1:1).
  * 4Dwm reparents each managed window into a decoration frame; the
    frame-vs-client delta yields the titlebar height + borders exactly.

Transport: a `gw` is a pyirix_qemu.host_channel.Gateway attached to the guest
gdbstub with the gwagent running (pin it to CPU 0 via `runon 0 /tmp/gwagent`
on an SMP guest, else the gdbstub can't see its magic page). gwxq must be
deployed to /tmp/gwxq (see deploy_helpers()).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

GWXQ = "/tmp/gwxq"
DISPLAY = ":0"


@dataclass
class Window:
    id: str
    name: str
    wm_class: str
    x: int          # root-relative client geometry
    y: int
    w: int
    h: int
    mapped: bool
    wm_state: int   # -1 none, 0 Withdrawn, 1 Normal, 3 Iconic
    managed: bool
    depth: int
    parent: str
    # 4Dwm decoration frame (filled in by Desktop.windows for managed windows)
    frame: tuple | None = None   # (fx, fy, fw, fh)

    @property
    def state(self) -> str:
        return {0: "withdrawn", 1: "normal", 3: "iconic"}.get(self.wm_state, "unknown")

    @property
    def center(self) -> tuple:
        return (self.x + self.w // 2, self.y + self.h // 2)

    # decoration metrics derived from the frame-vs-client delta (no constants)
    def _titlebar_h(self) -> int:
        return (self.y - self.frame[1]) if self.frame else 0

    def _border(self) -> int:
        return (self.x - self.frame[0]) if self.frame else 0

    def move_grab(self) -> tuple:
        """Screen point on the titlebar to drag for a move."""
        f = self.frame or (self.x, self.y, self.w, self.h)
        return (f[0] + f[2] // 2, f[1] + max(2, self._titlebar_h() // 2))

    def resize_grab(self, corner: str = "se") -> tuple:
        """Screen point on a frame resize handle (for drag-resize, if used)."""
        f = self.frame or (self.x, self.y, self.w, self.h)
        b = max(3, self._border())
        fx, fy, fw, fh = f
        xs = {"w": fx + b // 2, "e": fx + fw - b // 2}
        ys = {"n": fy + b // 2, "s": fy + fh - b // 2}
        cx = xs["e"] if "e" in corner else xs["w"] if "w" in corner else fx + fw // 2
        cy = ys["s"] if "s" in corner else ys["n"] if "n" in corner else fy + fh // 2
        return (cx, cy)


class DesktopError(Exception):
    pass


class Desktop:
    def __init__(self, gw, display: str = DISPLAY, gwxq: str = GWXQ):
        self.gw = gw
        self.display = display
        self.gwxq = gwxq

    # --- low-level: run a guest command, kill-guarded so a hung X client
    #     (e.g. during the login grab) can't wedge the gwagent popen ---
    def _run(self, cmd: str, timeout_s: int = 20, guard: int = 8) -> str:
        wrapped = (f"( {cmd} ) >/tmp/.gwx.out 2>&1 & p=$!; "
                   f"i=0; while [ $i -lt {guard} ]; do kill -0 $p 2>/dev/null || break; "
                   f"sleep 1; i=`expr $i + 1`; done; kill $p 2>/dev/null; cat /tmp/.gwx.out")
        st, out = self.gw.run(wrapped, timeout_s=timeout_s)
        if st != 1:
            raise DesktopError(f"gateway run failed (status {st}) for: {cmd}")
        return out.decode("latin-1", "replace")

    def _x(self, cmd: str, **kw) -> str:
        return self._run(f"DISPLAY={self.display} {cmd}", **kw)

    # --- window introspection (via gwxq tree JSON) ---
    def windows(self, managed_only: bool = False) -> list[Window]:
        out = self._x(f"{self.gwxq} tree", timeout_s=25)
        raw = json.loads(out)
        wins = [Window(
            id=w["id"], name=w["name"], wm_class=w.get("class") or "",
            x=w["x"], y=w["y"], w=w["w"], h=w["h"], mapped=w["mapped"],
            wm_state=w["wm_state"], managed=w["managed"],
            depth=w["depth"], parent=w["parent"],
        ) for w in raw]
        by_id = {w.id: w for w in wins}
        # attach the 4Dwm frame to each managed window: walk up to the
        # ancestor that is a direct child of the root window.
        roots = {w.id for w in wins if w.depth == 1}
        for w in wins:
            if not w.managed:
                continue
            cur = w
            while cur.parent in by_id and cur.id not in roots:
                cur = by_id[cur.parent]
            if cur is not w:
                w.frame = (cur.x, cur.y, cur.w, cur.h)
        return [w for w in wins if (w.managed or not managed_only)]

    def find(self, needle: str, managed_only: bool = True) -> Window | None:
        n = needle.lower()
        for w in self.windows(managed_only=managed_only):
            if n in w.name.lower() or n in w.wm_class.lower():
                return w
        return None

    def root_size(self) -> tuple:
        out = self._x("/usr/bin/X11/xdpyinfo")
        import re
        m = re.search(r"dimensions:\s*(\d+)x(\d+)", out)
        return (int(m.group(1)), int(m.group(2))) if m else (1280, 1024)

    # --- readiness predicates (poll-able; replace fixed sleeps) ---
    def _proc(self, pat: str) -> bool:
        out = self._run(f"ps -ef | grep '[{pat[0]}]{pat[1:]}'")
        return bool(out.strip())

    def x_up(self) -> bool:
        """X server accepting connections (post-grab). Best-effort: xdpyinfo,
        falling back to gwxq tree returning JSON."""
        try:
            out = self._x("/usr/bin/X11/xdpyinfo")
            if "name of display" in out:
                return True
        except DesktopError:
            pass
        try:
            return self._x(f"{self.gwxq} tree").lstrip().startswith("[")
        except DesktopError:
            return False

    def login_ready(self) -> bool:
        """The clogin face-picker is up. NOTE: X is GRABBED during login, so
        this MUST use process presence, not an X query."""
        return self._proc("clogin")

    def desktop_ready(self) -> bool:
        """Full 4Dwm session up (toolchest running)."""
        return self._proc("toolchest")

    def wait(self, pred, timeout_s: int = 180, interval: float = 2.0) -> bool:
        end = time.time() + timeout_s
        while time.time() < end:
            try:
                if pred():
                    return True
            except DesktopError:
                pass
            time.sleep(interval)
        return False


def deploy_helpers(gw, gwxq_local: str, gwagent_pinned: bool = True) -> None:
    """Push gwxq into the guest /tmp and chmod it. (gwagent itself is assumed
    already running -- pinned to CPU 0 on SMP.)"""
    gw.push_file(open(gwxq_local, "rb").read(), GWXQ)
    gw.run(f"chmod +x {GWXQ}", timeout_s=10)
