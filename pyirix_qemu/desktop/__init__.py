"""IRIX desktop "eyes" -- structured UI introspection + reliable cursor
targeting for the running 4Dwm desktop, without framebuffer screenshots.

Quick start (desktop guest at the 4Dwm desktop, gwagent pinned to CPU 0 +
gdbstub on :1234, gwxq deployed to /tmp/gwxq):

    import pyirix_qemu.host_channel as hc
    from pyirix_qemu.desktop import Desktop, ServoDriver, Targeter, describe_screen

    gw = hc.Gateway.attach(port=1234, base=0x10013000, scan=False)
    d  = Desktop(gw)
    print(describe_screen(gw))               # JSON: windows + readiness
    cat = d.find("Icon Catalog")             # -> Window with geometry + grabs

    servo = ServoDriver(mon_sock, qlog)      # virtuix monitor + NP_CURSOR log
    t = Targeter(d, servo)
    t.resize_window("Icon Catalog", 400, 300, anchor="se")   # protocol, exact
    t.click_window("Toolchest")              # servo click, offset-corrected

See progress_notes/ip55/desktop_eyes.md for the validated facts (login X-grab,
the 30px cursor offset, protocol-vs-drag resize).
"""
from .introspect import Desktop, Window, DesktopError, deploy_helpers
from .targeting import ServoDriver, Targeter, CLICK_OFFSET
from .describe import describe_screen

__all__ = [
    "Desktop", "Window", "DesktopError", "deploy_helpers",
    "ServoDriver", "Targeter", "CLICK_OFFSET", "describe_screen",
]
