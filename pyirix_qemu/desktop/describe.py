"""describe_screen() -- the structured "what's on screen" report an agent reads
INSTEAD of a framebuffer screenshot.
"""
from __future__ import annotations

from dataclasses import asdict

from .introspect import Desktop


def describe_screen(gw, servo=None, display=":0") -> dict:
    d = Desktop(gw, display=display)
    ready = {
        "login": d.login_ready(),
        "desktop": d.desktop_ready(),
    }
    out = {"ready": ready, "windows": [], "cursor": None, "root": None}
    if not ready["desktop"]:
        # X is grabbed at the login screen -- no window query possible
        out["note"] = ("login screen (X grabbed) -- no window introspection; "
                       "use readiness only" if ready["login"] else "X not ready")
        return out
    out["ready"]["x_up"] = d.x_up()
    out["root"] = dict(zip(("w", "h"), d.root_size()))
    wins = d.windows(managed_only=True)
    out["windows"] = [{
        "id": w.id, "name": w.name, "class": w.wm_class,
        "x": w.x, "y": w.y, "w": w.w, "h": w.h,
        "state": w.state, "mapped": w.mapped,
        "move_grab": w.move_grab(),
    } for w in wins]
    if servo is not None:
        try:
            out["cursor"] = servo.where()
        except Exception:
            pass
    return out
