# Accelerated paravirtual graphics — Phase 0 progress

Plan: `~/.claude/plans/it-s-been-a-while-federated-phoenix.md` (IRIS GL → host GPU, Option A:
composite host-rendered frames into the live Indigo Magic Desktop; phased software-GL-first;
IRIS GL via DGL now, OpenGL later).

## Done + verified (the protocol / render front-half — all host-side, no guest needed)

- **Phase 0a RE complete** — `dgl_protocol.md`. Decompiled `dgld.elf` (DgldProj, `$gp=0x1006c9e4`):
  the two-interpreter login state machine, login opcodes (dglloginX=0x10010, dglversion=0x10007
  accept-v1/2→0 else 0x78, dglxauthority=0x10013→XSetAuthorization), the handshake is plain DGL
  opcodes on the u32 FIFO (some value-returning), and **the winopen WID byte is ours to assign**
  (real window-create is in libgl; dgld's `tgl_*`/`gen_cmd` cluster is a test harness).
- **DGL decoder** — `sgi_glremote/dgl.py`. Table-driven (`dgl_opcodes*.json`): 539 fixed
  (`cmd_words`) + variable-length rules (winopen/charstr string-style, poly* vertex-count).
  Raises `DglSync` at the first unknown var-opcode so a live capture stops cleanly at the gap.
- **DGL renderer skeleton + handshake server + capture tool** — `sgi_glremote/server.py`.
  Backend-pluggable (`Backend`/`CaptureBackend`); completes the handshake (dglversion→0,
  gversion→2, winopen→WID), decodes the stream, `--capture` dumps raw bytes. Loopback-validated.
- **Regression tests** — `tests/test_dgl_protocol.py` (6 passing): table load, fixed + variable
  decode, partial-buffer wait, unknown-var DglSync, and the live handshake replies over a socket.

## Remaining Phase 0 (the integration block — cycle-heavy, needs guest + QEMU rebuild)

1. **Transport (#56) + windowing — RESOLVED architecture: an in-guest `dgld`-shim.**
   Key fact: IRIS GL apps use **DGL even locally** — `libgl` connects to `localhost:5232` where
   inetd spawns `dgld`, which creates the X window on Xsgi `:0` and renders via `/dev/gfx`→REX3.
   (Only *OpenGL* apps use GLX-over-X; that's why the glxinfo live trace showed GLX, not DGL.)
   So for Option A we **replace guest `/usr/etc/dgld` with our shim** (inetd keeps spawning it on
   :5232). The shim:
   - completes the DGL handshake locally;
   - on `winopen`, creates a **real X window on guest Xsgi `:0`** via libX11 (desktop-integrated;
     gives the WID + drives `RRM_ValidateClip` for the compositor) — keeps window mgmt in-guest;
   - **forwards the GL command stream** (tagged with WID) to the host renderer via pvnet/slirp to
     `10.0.2.2:<port>` — i.e. NOT executed on the guest's REX3;
   - handles X expose/resize/move so the window behaves normally.
   This keeps the window local (no host X server, no connection split inside libgl) and sends
   only geometry to the host. The shim is a small N32 in-guest binary (libX11 + a socket
   forwarder; no libgl-rendering), built on the Indy toolchain, reusing the inetd/sgi-dgl
   plumbing. Compositor then injects host frames into the window's `ValidateClip(WID)` region.
   *(Alternative — a libgl LD_PRELOAD wrapper splitting X-local/DGL-host — is messier because the
   host renderer would have to be an X client of the guest `:0`; prefer the shim.)*
2. **Live capture (#55)** — point a guest `_igl` app (e.g. `perfly_igl`) at `server.py --capture`;
   decode → nail `get_bufsize` + the 67 variable-opcode arg layouts empirically.
3. **GL backend (#57 back-half)** — OSMesa/llvmpipe offscreen behind `Backend` (add Mesa to the
   `Dockerfile`); readback RGBA on `swapbuffers`. Phase 2 swaps this for native macOS GPU.
4. **IRIS GL→GL translation (#58)** — opcode→GL semantics for the demo's subset.
5. **Compositor (#59)** — new `qemu-sgi-repo/hw/display/sgi_pvgl.c`: receive `(WID,w,h,RGBA)`
   frames + light up `pvfb_gf_ValidateClip` (kernel) → blit into `pvrex3 vram_rgbci` clipped to
   the visible pieces, `display_dirty`. **Format decision: run Xsgi at 24bpp** (drawdepth=3) to
   avoid the 8bpp CI palette-reverse-lookup — the live display pipeline
   (`pvrex3_update_display`, sgi_pvrex3.c:2895+) is a per-scanline DID/CI/palette path; writing
   24bpp RGB into `vram_rgbci` is direct, 8bpp needs nearest-palette. Add a `composite-test` QOM
   hook (model: the existing `fb-dump` property) to verify placement/clip/dirty via screendump
   independently of the renderer.
6. **Milestone 0 (#60)** — minimal `_igl` app (built on the Indy toolchain) renders a polygon
   inside its desktop window; `newport_screendump` confirms.

## Transport — PROVEN (2026-06-14)

Guest IRIS GL DGL traffic reaches the container-side host renderer via **slirp guestfwd**:
- Manifest/cmdline (capture-time only — see safety note): add to the `-nic user` device
  `guestfwd=tcp:10.0.2.100:5232-tcp:127.0.0.1:5232` (DGL) and
  `guestfwd=tcp:10.0.2.100:6000-tcp:127.0.0.1:6000` (X). App env: `DISPLAY=10.0.2.100:0.0`.
- **GOTCHA:** the guestfwd virtual IP must be an *unused* slirp address (e.g. `10.0.2.100`),
  **NOT** the `10.0.2.2` gateway — slirp rejects that with "Conflicting/invalid host:port".
- **SAFETY:** the `tcp:127.0.0.1:PORT` chardev connects at **QEMU startup**, so the target
  servers (DGL capture on 5232, Xvfb on 6000) must be running *before* boot or QEMU aborts
  ("Connection refused"). Therefore **do NOT leave guestfwd in the committed manifest** — add it
  only for capture runs (the manifest has been reverted to the safe no-guestfwd form).
- **Verified:** guest `telnet 10.0.2.100 5232` delivered "ROUTINGTEST" to the container DGL
  server (`_dgl_capture.bin`). Routing works end-to-end.

## Host infra installed (Dockerfile + running container)

`xvfb xauth libosmesa6 libosmesa6-dev libgl1-mesa-dri mesa-utils` (arm64 — the dev container is
Apple Silicon). Xvfb serves the remote X side: `Xvfb :0 -screen 0 1280x1024x24 -ac -listen tcp`.
OSMesa (`libOSMesa.so.8`) is the Phase-0 software GL backend for the renderer.

## Capture-source finding + the reliable path

`gr_osview` (the only installed IRIS GL app, links `libgl.so`+`libfm.so`) is **unsuitable** as a
capture source: it depends on `/dev/kmem` system stats ("error reading from memory") and font
services, and silently fails at window-open under remote X — it emitted no DGL even with Xvfb up.
**Reliable path:** build the **minimal IRIS GL test program** `sgi_glremote/test_iris/gltri.c`
(`winopen`/`ortho2`/`RGBcolor`/`clear`/`bgnpolygon`/`v2f`/`endpolygon`/`gflush` — a red triangle).
All instances (incl. `ip54-test`) have the full IRIS GL dev env (`gl.h`, `crt1.o`, `libgl.so`,
`cc`). This program is **dual-purpose**: the live-capture source (#55) AND the Milestone-0 app (#60).

**BUILD-PATH FINDINGS (2026-06-14):**
- **`cc` on the `sgi-ip54` machine SEGFAULTS** in the MIPSpro Back End Driver (`/usr/lib32/cmplrs/be
  died, signal 4`, CCRC=32) — the known sgi-ip54 toolchain fragility. **Build `gltri` on the
  `machine="indy"` session** (reliable cc, as with the audio gate), persist the binary to the disk
  (`init 0`), then reboot as `sgi-ip54` to run + capture.
- The serial-heredoc `tftp 10.0.2.2 <<E ... E` fetched a **0-byte** file — needs fixing (use
  `tftp -g`? or serial-write the source directly via `qemu_serial_write_file`). gltri.c is staged
  at `ip54_tftp_staging/gltri.c`.
- **Note:** `gltri`'s stream is almost all *fixed*-length opcodes (immediate-mode
  `bgnpolygon`/`v2f`/`endpolygon`, not the `poly()` array form), so the existing decoder already
  handles it — the live capture is NOT strictly blocking the renderer for Milestone 0; the render
  backend can be built against the static model and the capture folded into the integration run.

## Status
The **render/protocol half is built and verified end-to-end on the host** (RE → decode →
handshake → reply, regression-tested), the **transport is proven** (guestfwd, telnet capture),
and the **host GL infra is installed** (Xvfb + OSMesa). Remaining Phase-0 = the **minimal IRIS GL
test app** (capture + M0 source) → OSMesa render backend wired behind `Backend` → IRIS-GL→GL
translation → the QEMU compositor (`sgi_pvgl.c` + kernel `pvfb_gf_ValidateClip` + pvrex3 RGBA
inject, run at 24bpp) → Milestone 0. The windowing approach is the in-guest `dgld`-shim (above).
