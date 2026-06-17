# VirGL-for-IRIX — high-level ordered roadmap

## 🎉 MILESTONE 3 ACHIEVED (2026-06-15) — FULLY TEXTURED atlantis
The fish carry their signature **sphere-environment-mapped reflective skin** (the real atlantis
look), on the Apple M4 Max GPU, composited into the desktop. Artifact `m3_atlantis_textured.png`.
Texture opcodes applied in `iris_gl_backend.py` (texdef2d→glTexImage2D, texbind, texgen→
GL_SPHERE_MAP, tevbind→MODULATE); IRIS texels are ABGR-packed → reversed to GL RGBA. 20 host tests
green. See `phase1_atlantis.md`. Remaining polish: multi-window by WID, dynamic windowing (#68),
dirty-region readback / frame pacing, the minor whale dark-triangle winding artifact.

## 🎉 MILESTONE 2 ACHIEVED (2026-06-15) — REAL macOS GPU
`atlantis` renders on the **Apple M4 Max GPU** (native macOS, OpenGL-over-Metal = `GL_VERSION
"2.1 Metal"`) and composites into the Indigo Magic Desktop — the dolphin/whale/shark school,
shaded, identical to the Milestone-1 software render but executed on the real GPU. Artifact:
`m2_atlantis_macgpu.png`. Live path:
`atlantis → libgl(DGL) → dgld_proxy → slirp 10.0.2.2:6053 → container_bridge → host.docker.internal
→ mac_renderer (MacGLBackend, Apple GPU) → frame → container_bridge → QEMU pvrex3 gl-listen →
composite`. Architecture: the ~200-line IRIS-GL→OpenGL translation was refactored into a context-
agnostic `IrisGLBackend` (`sgi_glremote/iris_gl_backend.py`); `OSMesaBackend` (container, software)
and `MacGLBackend` (`macgl_backend.py`, glfw hidden-window + per-window FBO, GPU) are thin context
layers. QEMU stays in the container; all cross-boundary dials originate container→Mac via
`host.docker.internal` (`container_bridge.py`). Native renderer = `mac_renderer.py` (single-threaded,
GL on the main thread per Cocoa). Key fix: never rebind the FBO / make-context-current mid
glBegin/glEnd (Apple GL is strict → GL_INVALID_OPERATION); `_activate()` switches context only on
window change. 18 host tests green. Run: start `python -m sgi_glremote.mac_renderer` on the Mac,
then `run_m2_atlantis.py` in the container. Remaining (Phase 3 polish): dirty-region readback /
frame pacing, multi-window, textures, dynamic windowing (#68).

## 🎉 MILESTONE 1 ACHIEVED (2026-06-15) — real demo, software GL
`atlantis` renders correctly (lit/shaded creatures, perspective, depth-occlusion) in the desktop
via the container OSMesa path. Artifact `m1_atlantis_lit.png`. See `phase1_atlantis.md`.

## 🎉 MILESTONE 0 ACHIEVED (2026-06-14) — END OF PHASE 0
A real IRIS GL app (`gltri`) renders a **filled red triangle inside its window on the Indigo Magic
Desktop**, with the geometry executed on the **host** (OSMesa) and composited into the guest
framebuffer. Artifact: `_m0_MILESTONE0_triangle.png` / `m0_triangle.ppm` (59,940 red px, geometry
exact). Live pipeline: `gltri → libgl (DGL, DISPLAY=dglhost:0.0) → inetd → dgld_proxy shim → host
proxy_renderer (framed DGL handshake + OSMesa) → pvrex3 gl-listen → composite`. The whole login
handshake + winopen + GL stream flow & render; 7 host tests validate the protocol offline. Detail:
`step6_integration.md`, `dgl_protocol.md`, `dgl_handshake_capture.txt`; driver `run_m0_final.py`.

---

Goal: host-GPU acceleration of IRIS GL apps **inside** the Indigo Magic Desktop (Option A).
Approach: intercept DGL → render on host → readback → composite into the app's window region.
Detail lives in `dgl_protocol.md`, `accel_phase0_progress.md`, and the approved plan
(`~/.claude/plans/it-s-been-a-while-federated-phoenix.md`). This file is just the ordered map.

Legend: **[host]** = host-only, no guest. **[guest]** = needs a guest session.
**[qemu]** = needs a QEMU rebuild. **[kern]** = needs a kernel lboot rebuild.

---

## DONE — foundation (verified this session)
- F1. DGL protocol RE (handshake, login state machine, WID model) — `dgl_protocol.md`. **[host]**
- F2. DGL decoder + handshake server + 6 passing tests — `sgi_glremote/`. **[host]**
- F3. Transport proven (slirp `guestfwd`, telnet capture). **[guest]**
- F4. Host GL infra (Xvfb + OSMesa) installed + Dockerfile. **[host]**
- F5. Architecture decided: in-guest **`dgld`-shim** (window-local + GL-forward).
- F6. `gltri.c` minimal IRIS GL test app written; build-path finding (cc fails on sgi-ip54 →
      build on Indy).

---

## Progress (2026-06-14)
- **Step 1 — gltri built ✅** (on the new `irix-devel` dev image; `/gltri`). Live DGL capture
  **deferred** (transport+relay proven, but the app's `winopen` needs remote-X which hangs over
  slirp; not on the critical path — see #55).
- **Step 2 — render half DONE ✅** — `sgi_glremote/osmesa_backend.py`: DGL decode → IRIS-GL→GL
  translation → OSMesa render → RGBA readback. Verified: renders gltri to a red triangle
  (`_osmesa_tri.png`); pytest `test_osmesa_render.py`. **The entire render half is host-proven.**
- **Bonus:** built `irix-devel`, the canonical comprehensive dev image (progress_notes/
  irix_devel_image.md) — no more hunting for gl.h/crt.
- **Step 3 — compositor CORE DONE ✅** — pvrex3 `gl_overlay` + `pvrex3_composite_gl()` blits a
  host RGBA frame into the DisplaySurface (over desktop, under cursor), in both update_display
  and fb-dump. Verified live: `gl-overlay-test` QOM hook -> a box composites onto the IRIX
  X-login at the right rect (`_composite_test.png`). **Both halves of the pipeline now proven:
  render (triangle) + composite (box).**
- **Frame channel (file) + INTEGRATION DONE ✅** — `gl-overlay-file` QOM hook loads a host RGBA
  frame; the actual OSMesa-rendered `gltri` triangle composites into the IRIX X-login desktop
  (`_integration_tri.png`). **The full render→composite pipeline runs end-to-end with a real
  frame** — a static Milestone 0 (rendered IRIS GL frame inside the IRIX display).
- **Live socket frame channel DONE ✅** — pvrex3 `gl-listen <port>` listens; the renderer streams
  `PVGL` frames (`framechannel.py`) reassembled in the QEMU main loop + composited live. Verified:
  the triangle streams over the socket into the desktop (`_socket_test.png`). The compositor +
  frame channel are real (no per-frame qom-set).
- **Step 5 — dgld-shim HOST SIDE DONE ✅** — both ends of the shim protocol are written + tested:
  - guest shim skeleton `sgi_glremote/dgld_shim/dgld_shim.c` (+ `dgl_oplen.h`, 606 opcodes):
    inetd-spawned on the DGL socket, `XOpenDisplay(":0")` window-local, returns WID + reports
    screen rect, forwards the GL stream as `[type W/G][len BE][payload]`.
  - renderer side `sgi_glremote/shim_renderer.py`: parses W/G (`W`→frame position, `G`→DGL decoder
    →OSMesa→frame), streams `PVGL` frames to the pvrex3 `gl-listen` socket.
  - **Verified host-only over REAL sockets** (`sgi_glremote/test_shim_e2e.py`): mock-shim →
    `ShimServer` → `framechannel` → mock-QEMU receives one PVGL frame at rect (320,260,640,480)
    carrying the red triangle. Plus `tests/test_shim_renderer.py` (synthetic W/G). **9 host tests
    green.** The entire host chain a guest app will drive is now built and proven.
  - **shim COMPILES CLEAN on irix-devel ✅** (2026-06-14): `cc -n32 -O -o dgld_shim dgld_shim.c
    -lX11 -lc` → rc=0, `ELF N32 MSB mips-3 dynamic executable` linking libX11. The skeleton is
    structurally validated against real IRIX X11/socket headers. Built over the **new telnet
    channel** (`pyirix_qemu/irix_telnet.py`), not the flaky serial console — see
    [[telnet_preferred_over_serial]]. REMAINING: tune `TODO(capture)` handshake/var-len bits +
    install over `/usr/etc/dgld` + drive a real IRIS GL app.
- **Step 4 — ValidateClip clip channel BUILT ✅ (QEMU side verified, kernel side pending lboot)** —
  a private pvrex3 register block (REX3-unused offsets `0x1C00+`) carries the GL window's screen
  geometry + occlusion-aware visible clip pieces:
  - **QEMU** (`sgi_pvrex3.{c,h}`): clip registers (WID/XORG/YORG/XSIZE/YSIZE/OBSCURED/NUMPIECES +
    PIECE_X/Y/W/H/PUSH + COMMIT); `pvrex3_clip_visible()` gates each composited pixel to the
    visible pieces; obscured windows skip compositing. Gated by `clip_valid` so pre-Step-4
    behaviour is unchanged until the kernel writes the channel. Builds clean.
  - **kernel** (`ip54_tftp_staging/pvfb.c`): `pvfb_gf_ValidateClip` (was a no-op) now stages
    `vclip->{wid,xorg,yorg,xsize,ysize,obscured,numpieces,piecelist[]}` into the clip registers
    and latches with COMMIT. **BUILT + LBOOT'd + BOOTS ✅** (`run_step4_lboot.py`: `CC_pvfb_RC=0`,
    `LBRC=0`; `run_step4_verify.py`: multi-user, no panic). Live firing not yet seen (count=0 at
    boot-to-login) — `gf_ValidateClip` only fires for a GL-window create/move = Milestone 0.
  - **host-side test hook**: `gl-clip-test` QOM property drives the clip record from the monitor
    (same record the kernel writes), so the compositor clip logic is validated WITHOUT the lboot.
    **VERIFIED ✅** (`run_a_clip_test.py`, 2026-06-14): baseline-vs-desktop diff 98%, half-LEFT diff
    150353, **half-RIGHT diff 0** (right half clipped to exactly the desktop), obscured→desktop.
  - ⚠️ **coordinate convention unconfirmed**: pieces are treated as top-left screen rects (matches
    the verified frame-channel position); if the live gate shows RRM uses bottom-left GL coords,
    flip Y in `pvrex3_clip_visible()` (noted in-code).
- **Step 6 — integration IN PROGRESS** (`step6_integration.md`): pipeline being lit link-by-link.
  CONFIRMED: install path (replace `/usr/etc/dgld`, keep the inetd `sgi-dgl` line; orig backed up);
  Xsgi+xdm live at `:0`; inetd spawns dgld on `:5232`; **renderer transport guest→`10.0.2.2:6053`
  works**; gltri + dgld_shim build + deploy (telnet `get_file`). Shim fixed for inetd (fixed
  renderer addr + file logging). REMAINING: trigger the installed shim (`127.0.0.1:5232` direct) +
  read its log; settle the libgl DGL `DISPLAY`; wire the real renderer + tune `TODO(capture)`
  handshake → triangle composited = Milestone 0.
- **Reliable guest I/O** built this session: `pyirix_qemu/irix_telnet.py` (telnet, exec sh, run()
  sentinel + drain/resync, get_file uuencode pull-out) — see [[telnet_preferred_over_serial]].

## Ordered remaining steps

### Phase 0 — prove the full pipeline end-to-end (software GL)

**Step 1 — Build `gltri` + live DGL capture.** **[guest]**
Build `gltri.c` on the `machine="indy"` session (cc works there), persist to the ip54-test disk,
then boot `sgi-ip54` with the capture `guestfwd` + Xvfb and run it (`DISPLAY=10.0.2.100:0.0`).
*Gate:* the DGL server captures a stream that decodes cleanly; any unknown opcodes get added.
Also fixes the in-guest source transfer (0-byte tftp → use serial-write).

**Step 2 — Render half: OSMesa backend + IRIS-GL→GL translation.** **[host]**
Implement the `Backend` against OSMesa (offscreen ctx, render decoded commands, `glReadPixels`
RGBA on `gflush`/`swapbuffers`) + the opcode→GL semantic map (clear/color/ortho2/polygon/…).
*Gate:* feeding the captured/synthetic `gltri` stream produces a red-triangle RGBA buffer
(PPM compare). No guest needed.

**Step 3 — Composite half: `sgi_pvgl` device + pvrex3 RGBA-inject + 24bpp.** **[qemu]**
New `qemu-sgi-repo/hw/display/sgi_pvgl.c` + a clipped "composite RGBA rect → `vram_rgbci` +
dirty" entry in `sgi_pvrex3.c`; run Xsgi at 24bpp. Add a `composite-test` QOM hook.
*Gate:* a test RGBA pattern injected at a rect appears in `newport_screendump`. Independent of
Steps 1–2.

**Step 4 — Kernel `ValidateClip` hook.** **[kern]**
Light up `pvfb_gf_ValidateClip` (currently a no-op) to forward `xorg/yorg/xsize/ysize/piecelist/
wid/obscured` to `sgi_pvgl` via MMIO.
*Gate:* moving/occluding a desktop window logs correct geometry + visible clip pieces in QEMU.

**Step 5 — In-guest `dgld`-shim.** **[guest]**
Replace guest `/usr/etc/dgld` with a shim (libX11 window-mgmt on `:0` → desktop-integrated
window + WID; forward the GL stream tagged by WID to the host renderer via slirp). Built on Indy.
*Gate:* running `gltri` locally (`DISPLAY=:0`) opens a real desktop window and the shim forwards
its DGL bytes to the host renderer.

**Step 6 — Integrate → Milestone 0.** **[guest][qemu]**
Wire shim → host renderer (OSMesa) → frame channel → `sgi_pvgl` → inject into the
`ValidateClip(WID)` region.
*Gate:* `gltri`'s red triangle renders **inside its window on the Indigo Magic Desktop**
(`newport_screendump`); the 2D desktop is unaffected when no GL app runs. **End of Phase 0.**

### Phase 1 — coverage + correctness (still software GL) **[host+guest]**
**Step 7.** Full opcode translation for a richer app; double-buffering (`swapbuffers`); dynamic
windowing (re-fetch `ValidateClip` on move/resize/restack; honor `obscured`); multiple
simultaneous GL windows (route by WID). *Gate:* a real interactive IRIS GL app renders correctly,
clips/occludes correctly, survives window drags.

### Phase 2 — real host GPU **[host]**
**Step 8.** Swap the OSMesa backend for a **native macOS GPU** backend (Metal / MoltenVK-Vulkan);
move the renderer to a native macOS process; cross-process frame channel (container QEMU ↔ host
renderer). Same decoder + translation. *Gate:* the same app renders on the Mac GPU.

### Phase 3 — polish **[host+guest]**
**Step 9.** Dirty-region readback (avoid full-frame), frame pacing, connection teardown on window
close, multi-app robustness.

### Future — separate, much larger effort
**Step 10 — OpenGL/GLX path.** RE the dyDDX module + the `libGL` client + fix the NG1 RevA
lib/board-rev mismatch, so `libGL`/GLX apps (not just IRIS GL) are accelerated too.

---

## Critical path & parallelism
- **Critical path to Milestone 0:** Steps 1 → 2 → (3 ∥ 4) → 5 → 6.
- Steps **2** (render half, host) and **3+4** (composite half, qemu/kern) are **independent** and
  can be built/verified in parallel; Step 5 (shim) is the glue that joins them at Step 6.
- Step 1 (gltri + capture) unblocks the *testing* of Step 2 but Step 2 can begin against the
  static model immediately.
