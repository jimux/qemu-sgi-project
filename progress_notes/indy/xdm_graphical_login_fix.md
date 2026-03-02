# xdm Graphical Login Fix

## Problem

When xdm (the X display manager) starts the X server at boot, the xdm child
process blocks and the X server becomes unresponsive to client connections.
The graphical login screen never appears. Clients like `xdpyinfo` hang
indefinitely when trying to connect.

Killing xdm and starting Xsgi manually (`/usr/bin/X11/Xsgi :0 -ac &`) works
perfectly — 4Dwm, xclock, xterm all run.

## Root Cause

The blocking is caused by `DisplayManager*grabServer: True` in
`/var/X11/xdm/xdm-config`.

When `grabServer` is True, xdm calls `XGrabServer()` after connecting to the
X server. The XGrabServer processing inside Xsgi interacts with the IRIX
shmiq (shared memory input queue) subsystem. Without actual keyboard/mouse
hardware generating interrupts, the shmiq path blocks, causing the X server
to hang and preventing all client connections.

### Why manual startup works

When Xsgi is started manually, no client calls XGrabServer. The shmiq system
is initialized but never enters the blocking grab-processing path. Regular X
clients (xclock, xterm, 4Dwm) use the standard X event dispatch mechanism
which doesn't trigger the problematic shmiq interaction.

### What was NOT the cause

- **`-ac` flag**: Access control (`-ac`) is not needed. The fix works without
  it. The xdm-config already has `DisplayManager*authorize: off`.
- **X server flags**: The xdm-specified flags (`-bs -nobitscale -c -pseudomap
  4sight -solidroot sgilightblue -cursorFG red -cursorBG white -gamma 1.7`)
  all work fine when Xsgi is started manually.
- **gfxinit**: The `/usr/gfx/gfxinit` graphics initialization program runs
  correctly and doesn't affect subsequent Xsgi behavior.
- **Input device nodes**: `/hw/input/keyboard`, `/hw/input/mouse`, `/dev/shmiq`,
  and `/dev/gfx` all exist and work correctly. The pckm driver detects both
  keyboard and mouse via the 8042 controller emulation.
- **Xlogin script**: The `/var/X11/xdm/Xlogin` script runs and exits immediately.
  It does gamma correction, screen enumeration, and screensaver setup.
  `/usr/Cadmin/bin/clogin` (visual login) doesn't exist on the base install.

## Fix

One-line change in `/var/X11/xdm/xdm-config`:

```
DisplayManager*grabServer:              False
```

(Changed from `True`)

No other files need modification. The `Xservers` file stays as-is.

## Diagnostic Method

1. Booted IRIX from `irix_disk_fresh.qcow2`, logged in via serial console
2. Confirmed all input/graphics device nodes exist
3. Observed xdm (parent), Xsgi, and xdm (child) all sleeping
4. xdpyinfo hung → X server not processing connections
5. Killed xdm, started Xsgi manually with same flags xdm uses (no `-ac`)
   → xdpyinfo worked immediately
6. Ran gfxinit + Xsgi → still worked (ruled out gfxinit)
7. Changed `grabServer: False`, restarted xdm → xlogin window appeared
8. Tested `grabServer: False` without `-ac` → still works (minimal fix)

## Verification

After the fix, `xlswins` shows the xlogin window:
```
0x2f  ()
  0x80000b  (xlogin)
    0x80000c  ()
```

The xlogin window is 550x384 pixels at position 365,213 — the standard SGI
graphical login dialog.

## Files Modified

| File (on IRIX disk) | Change |
|---------------------|--------|
| `/var/X11/xdm/xdm-config` | `grabServer: True` → `grabServer: False` |

## Relation to Keyboard/Mouse Input

Keyboard and mouse input is now fully functional via PS/2 controller
emulation (8042 in IOC2). Use the `newport_sendkey` and `newport_mouse`
MCP tools to interact with the graphical login. See
[`keyboard_mouse_input.md`](keyboard_mouse_input.md).

## Impact on Security

Setting `grabServer: False` removes a security feature: the server grab
prevents other X clients from snooping on the password entry during login.
This is acceptable in an emulator context where there is no real security
concern. On real SGI hardware with a physical keyboard, `grabServer: True`
would work correctly because the shmiq driver receives real hardware
interrupts.
