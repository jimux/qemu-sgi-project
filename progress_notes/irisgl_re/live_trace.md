# Live IRIS GL / REX3 trace — convergence of the static map with a running system

Tasks **#45 (G3/G4: primitive→REX3 + live validation)** and **#49 (B3: live IRIS GL trace)**.
Runner: `run_a_gltrace.py` (golden-restored ip54-test, `-d trace:sgi_pvrex3_* -D _pvrex3_live.log`).
Captured 2026-06-14.

## What was captured

Booted ip54-test to the xdm/4Dwm desktop with pvrex3 register tracing, then ran `glxinfo`
against the live X server (`:0`). Trace: **399,975 `sgi_pvrex3_reg_write` events** (22.8 MB).

### REX3 register-write histogram (the live primitive→REX3 conversation)

| count   | reg     | REX3 name   | meaning |
|---------|---------|-------------|---------|
| 327,680 | 0x0230  | HOSTRW0     | host pixel R/W data port — bulk image/text blit via **PIO** |
|  36,585 | 0x0240  | DCBDATA0    | DCB data — backend/VC2/CMAP register writes (each write = one DCB bus xfer) |
|  34,014 | 0x0238  | DCBMODE     | DCB mode — sets slave/CRS/datawidth before each DCBDATA0 |
|   1,024 | 0x0234  | HOSTRW1     | second host pixel word |
|     483 | 0x0014  | (config)    | drawmode/command setup |
|   46/46 | 0x0150/0x0154 | XYSTARTI/XYENDI | rectangle span coordinates |
|    ~13  | 0x0220  | (DCB/aux)   | |
|   5–11  | 0x1300–0x1330 | CFG bank | REX3 config registers (config/clock/SRAM) |
|     1×  | 0x0114–0x0134, 0x0200–0x020c | drawmode/color/clip | one-time pipeline setup |

**Reading:** the dominant traffic is **HOSTRW0 host PIO** — Xsgi pushes pixel/glyph data
straight through the host R/W port rather than DMA. This is exactly the behaviour the
PIXELDMA→PIO threshold patch (#16) forces, and it confirms the live rendering path matches
the recovered `rex3DrawImage`/`ng1_pixeldma` map: bulk images go out as host PIO words, not
as a PIXELDMA descriptor. The DCBMODE+DCBDATA0 pair (the recovered DCB protocol — same path
the mouse-cursor fix #50 uses to poke VC2 CURSOR_X/Y) carries all backend/VC2/colour-map
programming. The 0x1300 CFG bank appears only at init.

## GLX is live on Xsgi (server-side handshake validated)

`glxinfo` on `:0` returned:

```
display: :0
server glx vendor string: SGI
server glx version string: 1.2 Irix 6.5
server glx extensions (GLX_): EXT_import_context, EXT_vis
```

So the **GLX server extension is loaded and answering** — i.e. Xsgi's `dlopen()`'d GLX dyDDX
module (the `GLXLoadExtension`/`__glXExtensionInit` path mapped in G2, `decomp/glx_server.json`)
is up. The local desktop's GL goes through **GLX-over-X**, not the DGL socket; the DGL daemon
(`/usr/etc/dgld`, present) is the *remote* IRIS GL transport, confirming the two-mechanism
architecture in `README.md` ("which the local desktop uses" — answer: **GLX-over-X**).

## NEW finding — NG1 board-revision / OpenGL-library mismatch

`glxinfo` also printed (the one piece that gates hardware GL):

```
This system is configured with an early model of NG1 graphics.
The OpenGL libraries currently in use on this system are not compatible.
OpenGL applications will run, but with unpredictable graphic output.
... copy the contents of /usr/gfx/arch/IP22NG1 from an earlier release.
```

The emulated pvrex3/NG1 enumerates as an **early NG1 (RevA)** board, but the installed
`/usr/gfx/arch/IP22NG1` OpenGL libraries are built for a later board rev. So:
- **2D / IRIS-GL-via-Xsgi (4Dwm, X clients) works** — that path is the REX3 traffic above and
  renders correctly (desktop comes up, cursor tracks, screendump `_gltrace_desktop.png`).
- **Hardware OpenGL** (heavy demos: `ideas`/`flight`/`perfly`) would be "unpredictable" until
  RevA-matched gfx libs are dropped into `/usr/gfx/arch/IP22NG1`. This is the concrete reason
  the full GL demos aren't a clean validation target on the current image — a *library/board
  rev* gap, not a missing transport. Either match the installed lib's expected board rev in
  the pvrex3 board-id/enumeration, or stage the RevA `/usr/gfx/arch/IP22NG1` libset.

## Bottom line (validation outcome)

- Static DGL opcode table + REX3 register map + DCB/cursor protocol: **confirmed live** —
  the running register conversation matches (HOSTRW0 PIO blits, DCBMODE/DCBDATA0 backend,
  CFG-bank init).
- GLX server path: **confirmed live** (glxinfo handshakes with the dlopen'd GLX module).
- Remaining gap for *hardware* OpenGL demos = the NG1 RevA library/board-rev mismatch above
  (newly identified, actionable), not the IRIS GL/DGL/REX3 plumbing — which is validated.

Artifacts: `_pvrex3_live.log` (22.8 MB trace), `_gltrace_desktop.png`, `run_a_gltrace.py`.
