# Desktop "eyes" — RAM/X11 introspection + reliable cursor targeting (2026-06-25)

Tooling that gives an agent structured **eyes** into the running IRIX 4Dwm desktop — the window list (names/geometry/state), readiness signals, and reliable cursor targeting — **without framebuffer screenshots**. Replaces the old hit-or-miss "screenshot → guess a pixel → usually miss" loop. Package: `pyirix_qemu/desktop/`.

## Architecture (what runs where)

```
host Python (pyirix_qemu.desktop)
  └─ Gateway (gwagent over QEMU gdbstub :1234)  ── run X clients / gwxq in-guest
       └─ gwxq.n32   (in-guest libX11 helper)   ── JSON window tree + protocol move/resize
  └─ ServoDriver (monitor mouse_move + NP_CURSOR log) ── closed-loop cursor targeting
```

The winning insight: **X11 introspection from inside the guest beats raw RAM-struct scanning.** Xsgi is a standards X server; `XQueryTree` + properties give everything, and the golden has `DisplayManager._0.authorize:false` (a local root client connects to `:0` with no xauth).

## Validated facts (all proven live, the hard-won ones)

1. **X is GRABBED during the login screen.** `DisplayManager*grabServer:True` matches display `_0` (the `.grabServer:False` is less specific), so xdm/clogin grab the server while the face-picker is up → **every X client hangs** (xdpyinfo/xwininfo/gwxq all block). So **login readiness must use process presence (`ps | grep clogin`), never an X query.** Post-login the grab releases and X introspection works. (Auth is *off*; the hang is the grab, not auth.)

2. **Cursor targeting needs a constant +(30,30) offset.** The `NP_CURSOR` `cursor=(x,y)` trace reports the VC2 register *before hotspot correction*; a click lands at `VC2 − (30,30)` (a Newport hardware offset, measured live by detecting the red cursor in a screendump). So `ServoDriver` targets `VC2 = screen + (30,30)`. The servo (closed-loop, reads VC2 from the log) then lands clicks **exactly** — this is what made the login button and all targeting reliable.

3. **Move works via titlebar drag; resize does NOT via handle drag.** 4Dwm honors a synthetic titlebar press-drag (move) and the maximize button, but **interactive resize-handle drag never engages** (the cursor flies free, no rubber-band) even with the grab verified dead-center on the 32×32 corner gadget windows. The reliable resize is **protocol `XMoveResizeWindow` on the client** (4Dwm honors ConfigureRequests — maximize proves it). Proven **pixel-exact, all 8 ways** (4 corners + 4 sides, each anchor held).

4. **Frame-aware grab points need no constants.** 4Dwm reparents each client into a decoration frame (gadget sub-windows: titlebar, 4×32px corners, 4 edges). The **frame-vs-client geometry delta** yields titlebar height (`client.y−frame.y`, =32) and border (`client.x−frame.x`, =8) exactly — `Window.move_grab()`/`resize_grab()` compute from that.

5. **gwagent on an SMP guest must be pinned to CPU 0** (`runon 0 /tmp/gwagent`). The gdbstub reads CPU 0's TLB; if gwagent floats to another CPU its magic page isn't visible and `Gateway.attach` returns None. (The 1-CPU build host didn't need this.)

6. **Serial console is fragile** (csh vs sh — use `exec /bin/sh`; hung X clients wedge the tty and Ctrl-C in raw mode doesn't recover; bulk-push over the tty drops lines). The **gwagent gdbstub channel is the robust transport**; the serial is a bootstrap/fallback. Always **kill-guard** in-guest X clients (`( cmd ) & p=$!; sleep N; kill $p`) so a login-grab hang can't wedge gwagent's popen.

## Package API

- `Desktop(gw)` — `windows()` / `find(name)` (gwxq JSON → `Window` with client+frame geom + `move_grab`/`resize_grab`); readiness `login_ready()` (clogin), `desktop_ready()` (toolchest), `x_up()`, `wait(pred)`.
- `ServoDriver(mon_sock, qlog)` — `to/click/press/release/dbl/drag/where`, screen coords, (30,30) baked in.
- `Targeter(desktop, servo)` — `click_window`, `move_window(...,method="protocol"|"drag")`, `resize_window(...,anchor="se"|"nw"|...)` (protocol, 8-way).
- `describe_screen(gw, servo)` — the JSON "what's on screen" the agent reads instead of a screenshot.
- `parse_x.py` — zero-deploy fallback parsing `xwininfo -root -tree` / `xprop` (unit-tested host-side against `test_fixtures/`).

## Setup recipe (desktop guest at the 4Dwm desktop)

```
# boot_desktop.py now boots with -nic user,tftp=ip54_tftp_staging
# 1. login: servo-click root face (390,270)+offset then Log In (847,823)+offset
# 2. on the serial root sh: TFTP gwagent + gwxq.bin into /tmp; runon 0 /tmp/gwagent &
# 3. monitor: gdbserver tcp::1234
# 4. host: gw=Gateway.attach(port=1234, base=0x10013000, scan=False); deploy_helpers(gw,"gwxq.n32")
```

## Build / artifacts

- `gwxq.c` → compiled n32 on irix-devel (`cc -n32 -O -o gwxq gwxq.c -lX11`), shipped as `pyirix_qemu/desktop/gwxq.n32` (18216 B) + `ip54_tftp_staging/gwxq.bin`.
- Tests: `tests/test_desktop_parse_x.py` (host-only, against captured fixtures).
- Deferred (P3): widget-tree introspection (editres / the recovered `4Dwm`/`clogin` `CorePart`-offset globals) + the clogin face-picker user list.
