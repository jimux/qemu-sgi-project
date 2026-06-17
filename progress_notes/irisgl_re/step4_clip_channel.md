# Step 4 — ValidateClip clip channel (window tracking + occlusion)

VirGL roadmap Step 4: make the host-rendered GL overlay track and clip to its real desktop
window, using the kernel's occlusion-aware clip geometry. Without this the overlay sits at a
fixed rect and ignores window moves/occlusion.

## Mechanism — a private pvrex3 register block

RRM (the rendering resource manager) calls the board driver's `gf_ValidateClip` whenever a GL
window's clip state changes (open / move / resize / restack / occlude), handing it a
`struct RRM_ValidateClip` (`sys/rrm.h`):

```
rnid, clipid, xorg, yorg, xsize, ysize, numpieces,
struct RRM_PieceList *piecelist,   /* visible sub-rects; NULL when numpieces<=1 */
wid, obscured, widcheck, changed, hwi_mode, fboffset
```

We forward the relevant fields to the QEMU compositor through a private register block in the
**REX3-unused high offsets `0x1C00+`** of the existing pvrex3 MMIO window (no new device, no new
PA, no PROM change — reuses the already-mapped 8KB REX3 register space):

| offset | reg | meaning |
|--------|-----|---------|
| 0x1C00 | WID | window ID |
| 0x1C04..0x1C10 | XORG/YORG/XSIZE/YSIZE | window screen rect |
| 0x1C14 | OBSCURED | 1 = fully hidden |
| 0x1C18 | NUMPIECES | visible piece count (clamped to 32) |
| 0x1C20..0x1C2C | PIECE_X/Y/W/H | staged piece rect |
| 0x1C30 | PIECE_PUSH | write index i → store staged piece as piece[i] |
| 0x1C3C | COMMIT | write 1 → latch the clip record (`clip_valid=true`) |

## Two sides

- **kernel** `ip54_tftp_staging/pvfb.c` — `pvfb_gf_ValidateClip` (was a no-op) stages the fields
  via `PVREX3_REG(...)` (KSEG1 uncached) and latches with COMMIT. Logs the first 8 calls.
  **Needs an lboot rebuild to compile/run.**
- **QEMU** `qemu-sgi-repo/hw/display/sgi_pvrex3.c` — the write handler stores the clip record;
  `pvrex3_clip_visible(sx,sy)` gates each composited pixel to the visible pieces (or the window
  rect when numpieces<=1); `pvrex3_composite_gl` skips entirely when `obscured`. All gated by
  `clip_valid`, so **behaviour is identical to pre-Step-4 until the kernel writes the channel**
  (safe: current golden kernel never touches `0x1C00+`).

## Host-side validation (no lboot needed)

`gl-clip-test` QOM property drives the clip record straight from the QEMU monitor — the *same*
record the kernel writes — so the compositor clip logic is testable without the kernel rebuild:

```
qom-set <pvrex3> gl-clip-test off                                  # clip disabled
qom-set <pvrex3> gl-clip-test 1,320,260,640,480,1                  # obscured → nothing drawn
qom-set <pvrex3> gl-clip-test 1,320,260,640,480,0;320,260,320,480  # left-half piece only
```

`run_a_clip_test.py` boots ip54-test, composites the rendered gltri triangle, and diffs each
case's overlay region against the desktop reference (the obscured case = pure desktop, since it
skips compositing). **VERIFIED 2026-06-14:**

```
baseline-vs-desktop diff = 302201/307200 (98%)   -> overlay composited over desktop
half-LEFT  vs desktop diff = 150353               -> left piece visible (overlay present)
half-RIGHT vs desktop diff = 0                    -> right half clipped to EXACTLY the desktop
VERDICT: PASS
```

The right half being **0 pixels different** from the desktop is the clean proof that visible-piece
clipping works; the 98% baseline diff + the obscured case reverting to desktop proves obscured-skip.
NB: the gltri overlay has a black background, so brightness is misleading — the test diffs against
the desktop, it does not count "foreground" pixels.

## ⚠️ Open question — coordinate convention (the live Step-4 gate)

Pieces are currently treated as **top-left screen rects** (matching the verified frame-channel
window position from the dgld-shim's `XTranslateCoordinates`). RRM/IRIS GL natively use
**bottom-left** screen coordinates (Y up). If the live gate (drag/occlude a real window and read
the `cmn_err` ValidateClip log) shows the kernel hands us bottom-left coords, flip Y in
`pvrex3_clip_visible()`: `sy' = (PVREX3_SCREEN_H - 1) - sy` for both the window rect and pieces.
This is the one thing that genuinely needs a guest+lboot to pin down — everything else is proven.

## Kernel lboot + boot verification (2026-06-14)

The kernel side is no longer just "written" — it's **built, linked, and booting**:

- `run_step4_lboot.py` (pvfb-only, minimal-risk): restores golden, boots ip54-test on Indy,
  recompiles **only** `pvfb.o` from staging (`CC_pvfb_RC=0` — the ValidateClip change compiles
  with the kernel flags), installs it, and relinks `/unix.new` (`LBRC=0`). Leaves the other 4 PV
  driver objects golden, so the change is isolated.
- `run_step4_verify.py`: boots the new `/unix.new` on sgi-ip54 → **multi-user login, NO PANIC**
  (`IRIX ... IP54`). The Step-4 change is regression-safe.

**ValidateClip not yet observed firing** (`grep -c ValidateClip /var/adm/SYSLOG` = 0 at
boot-to-login). `gf_ValidateClip` is the RRM graphics-pipe clip path: it fires for a **GL window**
create/move, which needs a logged-in desktop session + a GL app — i.e. Milestone 0, not the xdm
login screen. The `cmn_err` logging (first 8 calls) is in place to read the real
`xorg/yorg/xsize/ysize` and settle the coordinate convention the instant a GL window drives it.

## Status

- QEMU side: **built + host-verified** via `gl-clip-test` (half-RIGHT 0-diff, obscured→desktop).
- kernel side: **built, lboot'd, boots multi-user no-panic**; live firing + coordinate-convention
  gate folds into Milestone 0 (needs a GL window). Reproducer: `run_step4_lboot.py`.

## ⚠️ PIVOT (2026-06-15, #68) — the kernel ValidateClip path does NOT fire for our renderer

When #68 (dynamic windowing) was actually built, the premise above proved wrong for THIS
architecture: **`gf_ValidateClip` never fires for a host-rendered proxy window.** RRM calls
`gf_ValidateClip` only for a window bound to a *kernel GL-pipe context*. Our GL window is a plain
X window created by the `dgld_proxy` shim, with all geometry/rendering on the **host** — there is
no kernel GL context, so RRM never invokes the board driver's clip hook (exactly why this note
recorded "ValidateClip not yet observed firing" even at a logged-in desktop). The kernel
`pvfb_gf_ValidateClip` writer + the pvrex3 `0x1C00` register block remain in place and harmless,
but they are **not the live clip source**.

**What actually works (#68):** the clip/geometry truth lives in the proxy, which *owns* the X
window. `dgld_proxy` now (a) polls `XTranslateCoordinates(win→root)` on a ~200ms `gettimeofday`
throttle (decoupled from `select()` so it works during continuous GL streaming), and (b) pushes a
`(wid,x,y,w,h,obscured)` record to the host renderer over a **2nd "PVWN" control connection** (kept
separate from the raw DGL byte pipe). The renderer keeps a shared `ProxyServer.windows` WID→geom
registry; `ProxySession._emit_frame` composites at the live position and **skips when obscured**.
Polling (not `StructureNotify`) is required because a reparenting WM moves the *frame* on a
title-bar drag without `ConfigureNotify`-ing the client. **VERIFIED live** (`run_glscene_move.py`):
glscene's cube window dragged from (200,150)→(620,430) and the host overlay followed exactly
(PVWN timeline `(200,150)`→`(620,430)`; artifacts `m68_window_at_origin.png`,
`m68_window_moved.png`). Regression: `tests/test_proxy_renderer.py::test_pvwn_control_updates_window_registry`.
Code: `ip54_tftp_staging/{dgld_proxy.c,winmove.c}`, `sgi_glremote/proxy_renderer.py`.

## Piece-level partial clipping + full occlusion — DONE (2026-06-15, #80) + multi-window (#81)

Both follow-ups are implemented and verified.

**Occlusion source = proxy X stacking (not the kernel).** `compute_pieces()` in `dgld_proxy.c`
computes the GL window's visible region: start from its screen rect, find its top-level ancestor
(child of root), then subtract every root child stacked **above** it (XQueryTree is bottom-to-top)
that is mapped and overlaps — `rect_subtract()` splits into up-to-4 bands per occluder, capped at
`MAXP=16`. Result = visible pieces (0 pieces ⇒ fully obscured). Sent to the renderer over the PVWN
control channel (record extended to `wid,x,y,w,h,obscured,numpieces,pieces…`). **Proven live:**
lowering the cube window beneath the xdm greeter (371,237 540×316) yielded exactly the 3 expected
bands `[(200,150,640,87),(200,555,640,75),(200,237,171,318)]`.

**Compositor draws one full overlay, clipped per-pixel.** The renderer sends the whole window
overlay (PVGL) **plus a new `PVCL` clip record** (`framechannel.send_clip`); QEMU
`pvrex3_gl_apply_clip` loads it into the same `clip_*` state the Step-4 `0x1C00`/`gl-clip-test`
path uses, and `pvrex3_composite_gl` gates every overlay pixel through `pvrex3_clip_visible()`. One
overlay + a clip record matches the single-overlay device design and handles partial **and** full
occlusion uniformly (obscured ⇒ draw nothing). This is the correct fix — sending N separate
sub-rects fought the single-`gl_overlay` device (only the last survived). **Proven live**
(`run_glscene_clipdemo.py`, artifacts `m80_overlay_unclipped.png` / `m80_overlay_clipped_to_pieces.png`):
the cube overlay composites only in the 3 visible bands, the central occluded region is punched out.
Host regression `tests/test_proxy_renderer.py::test_emit_frame_sends_full_overlay_plus_clip_pieces`.

**Multi-window by WID (#81):** each `dgld_proxy` uses `wid = getpid()` (verified `wid=298` live)
in its PVSH + PVWN, and the renderer's `ProxyServer.windows` registry + `_emit_frame` key generically
by wid — so two concurrent GL apps no longer collide on the old hardcoded `wid=1`.

**Note on the live test environment:** at the xdm greeter (no full 4Dwm), guest window stacking /
reparenting varies boot-to-boot, which made staging a *live* occluder flaky (`winmove lower`/`raise`
sometimes can't restack as expected). The deterministic `gl-clip-test` injection (Step-4 hook, with
`DGL_NO_CLIP=1` suppressing the renderer's own PVCL) drives the clip with the exact pieces
`compute_pieces` produces, giving a reliable end-to-end demonstration. Inside a fully-logged-in
4Dwm desktop (reparented app frames as root children) the stacking-based `compute_pieces` path is
straightforward; that's the production case.

Files: `ip54_tftp_staging/{dgld_proxy.c,winmove.c,coverwin.c}`, `sgi_glremote/{proxy_renderer.py,framechannel.py}`,
`qemu-sgi-repo/hw/display/sgi_pvrex3.c` (`pvrex3_gl_apply_clip` + `PVCL` in `pvrex3_gl_parse`).
Harnesses: `run_glscene_occlude.py`, `run_glscene_clipdemo.py`, `run_buildwin.py`.
