# Desktop "eyes" — driving the IRIX 4Dwm desktop without screenshots

`pyirix_qemu/desktop/` gives an agent **structured, tool-based eyes** into the running
IRIX Indigo Magic (4Dwm) desktop: the live window list (names, geometry, state), poll-able
readiness signals, and **reliable cursor targeting** — so you can click/move/resize exact
targets and poll for "ready" instead of taking a screenshot, guessing a pixel, and missing.

This is the **agent how-to**. The findings/rationale are in
`progress_notes/ip55/desktop_eyes.md`; the memory pointer is `desktop_eyes_tooling`.

## What it does / doesn't (read first)

- ✅ **Window-level**: enumerate every X window with name/class/root-relative geometry/state;
  find by name; compute 4Dwm frame + titlebar/border grab points; move (drag or protocol);
  **resize 8-way (protocol, pixel-exact)**; readiness predicates.
- ⚠️ **Intra-window items** (menu entries, list rows, small toggles) are **not** separate X
  windows, so the tooling can't give their per-item geometry. You still need **one
  screenshot** (or a known layout) to read menu text / find a small toggle — then the servo
  clicks it exactly. (Widget-tree introspection via editres is deferred P3.)
- Targeting (servo) is **virtuix-only** (needs the `NP_CURSOR` trace). Introspection works on
  Indy too (pure X11).

## One-time setup (get the channel up)

The package talks to the guest over the **gwagent gdbstub channel** + the in-guest **gwxq**
helper, and drives the cursor over the QEMU **monitor** using the **NP_CURSOR** log.

1. **Boot the desktop** (sets `NP_CURSOR=1`, a tftp dir, monitor+serial sockets):
   `setsid python3 tmp/indy-virtuix-sep/boot_desktop.py virtuix 4 &` — wait for serial `login:`.
2. **Log in** (X is grabbed here, so drive by pixel): servo-click the **root face (390,270)**
   then **Log In (847,823)** — coords are screen; the servo adds the +30 offset:
   `python3 tmp/indy-virtuix-sep/servo.py click 420 300; ... click 877 853` (or via ServoDriver).
   Wait for 4Dwm/toolchest.
3. **Bring up the channel** on the guest's serial root `sh` (`exec /bin/sh` — login shell is csh):
   ```
   cd /tmp; (echo binary; echo "get gwagent /tmp/gwagent"; echo quit) | tftp 10.0.2.2; chmod +x /tmp/gwagent
   (echo binary; echo "get gwxq.bin /tmp/gwxq"; echo quit) | tftp 10.0.2.2; chmod +x /tmp/gwxq
   runon 0 /tmp/gwagent &           # MUST pin to CPU 0 on an SMP guest
   ```
   then on the QEMU monitor: `gdbserver tcp::1234`.
4. **Attach from the host**:
   ```python
   import pyirix_qemu.host_channel as hc
   from pyirix_qemu.desktop import Desktop, ServoDriver, Targeter, describe_screen
   gw = hc.Gateway.attach(port=1234, base=0x10013000, scan=False)   # None if not pinned to CPU0
   ```

## Quick start

```python
d = Desktop(gw)
print(describe_screen(gw))            # JSON: readiness + every managed window w/ grab points

cat = d.find("Icon Catalog")          # -> Window(id, name, class, x,y,w,h, frame, state, ...)
print(cat.w, cat.h, cat.move_grab())  # frame-aware titlebar grab point

servo = ServoDriver("tmp/indy-virtuix-sep/virtuix_mon.sock",
                    "tmp/indy-virtuix-sep/virtuix_q.log")
t = Targeter(d, servo)
```

## Recipes

**Readiness (kill the fixed sleeps):**
```python
d.wait(d.x_up); d.wait(d.desktop_ready)          # post-login
d.login_ready()   # True when the clogin face-picker is up (X is grabbed -> uses ps, not X)
```

**Click / move / resize a window:**
```python
t.click_window("Toolchest")                       # servo click, lands exact
t.move_window("Icon Catalog", 200, 150)           # protocol (default) -- exact
t.move_window("Icon Catalog", 200, 150, method="drag")   # real titlebar drag
t.resize_window("Icon Catalog", 400, 300, anchor="se")   # 8-way: nw/ne/sw/se/n/s/e/w, all exact
```

**Raw introspection / run an in-guest command:**
```python
import json
wins = json.loads(d._x("/tmp/gwxq tree"))         # full tree (all windows incl. gadgets)
d._x("/usr/bin/X11/xwininfo -id 0x.. -children")  # any X client, kill-guarded
```

**Drive a menu (the screenshot-for-text pattern):**
1. Find the menu button (often a separate window) or click its known position with the servo.
2. The menu posts as an override-redirect window — `d._x("/tmp/gwxq tree")` gives its *window*
   geometry, but **take one screendump to read the item labels** (`monitor screendump`).
3. Servo-click the item; re-introspect to confirm the result (e.g. the new app window appears).
   (Validated end-to-end: Toolchest → System → Software Manager opened `swmgr`, confirmed by
   `d.find("Software")` — no screenshot in the decision loop except reading the menu text.)

## Gotchas

- **X is GRABBED during the login screen** → every X client (xdpyinfo/xwininfo/gwxq) hangs.
  Use `login_ready()` (clogin process), never an X query, until `desktop_ready()`.
- **Resize-handle DRAG does not engage 4Dwm** via synthetic input. **Use protocol resize**
  (`Targeter.resize_window`, exact, 8-way). Move-drag and the maximize button do work.
- **gwagent must be `runon 0`-pinned on SMP** or `Gateway.attach` returns None.
- **Always kill-guard in-guest X clients** (the package's `_x` does) so a login-grab hang can't
  wedge gwagent's popen.
- **A halted CPU freezes the desktop**: a Gateway/gdb client left attached mid-op (e.g. a
  killed script) halts the guest. Recover with monitor `cont`, or kill the stray gdb client.
  A still-posted menu or an app's pointer-grab (e.g. swmgr) can also freeze input — dismiss it
  (click elsewhere / Esc) before continuing.
- **The cursor offset is +(30,30)** (baked into ServoDriver) — callers pass *screen* coords.

## Build / artifacts

- `gwxq.c` → built n32 on the `irix-devel` host (`cc -n32 -O -o gwxq gwxq.c -lX11`), shipped as
  `gwxq.n32` (here) + `ip54_tftp_staging/gwxq.bin`. `gwagent.n32` is in `pyirix_qemu/`.
- Host-only tests: `tests/test_desktop_parse_x.py`.
