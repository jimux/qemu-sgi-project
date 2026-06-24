# BUG: window-move leaves stale repaint artifacts (Newport expose/damage)

Status: OPEN, noted 2026-06-24, NOT yet investigated (parked deliberately). Cosmetic, not a blocker — the desktop is fully functional (login, open windows, click-drag window move all work).

## Symptom
Click-drag-moving a 4Dwm window leaves **ghost copies of the window (or its right/lower portion) at the intermediate drag positions**; the regions the window vacated are not repainted, so stale window pixels persist on the desktop.

Screenshot: `virtuix_window_move_artifacts.png` (this dir). The *Icon Catalog: Applications* window was dragged left; two partial ghost copies of its right edge ("(Page 1 of 11) / View / NetscapeMail / Register…") remain at ~x=840 and ~x=1095. The teal weave root background also shows through only where nothing was ever drawn — the vacated window area keeps the old window image instead of the root weave.

## Reproduction
- `-M virtuix` (also expected on `-M indy` — the Newport draw path is shared logic, copied verbatim into `sgi_newport_virtuix.c`), `unix.g.smp-desktop`, golden `irix-6.5.5-complete-fixed.qcow2`.
- Boot to 4Dwm desktop, open a window (e.g. Icon Catalog), click-drag its title bar across the screen, release. Ghost artifacts remain where the window passed/was.

## Likely cause (hypothesis, unverified)
A damage/expose-repaint gap in the Newport display path: when X moves a window, the newly-exposed region should be repainted (root weave / underlying windows) and the framebuffer damage flushed. Either (a) the guest's expose events repaint to VRAM but QEMU's `sgi_newport` dirty-region tracking misses those writes so the display surface isn't updated, or (b) the move is a VRAM→VRAM blit (REX3 scr2scr) whose source/old region isn't invalidated. NOT believed related to the indy/virtuix device separation — `sgi_newport_virtuix.c` is a verbatim copy of `sgi_newport.c`, so the behavior should be identical on both machines (confirm on indy when convenient). Prior sessions also noted "artifacting" on window move.

## Where to look when picked up
- `qemu-sgi-repo/hw/display/sgi_newport.c` / `sgi_newport_virtuix.c`: the REX3 draw ops (scr2scr blit, HOSTRW/pixel writes) and the display dirty/refresh path (`memory_region_set_dirty` / `dpy_gfx_update` / the per-frame `newport_update_display`). Check whether all VRAM writes that result from a window move mark the right dirty rectangles, and whether scr2scr blits invalidate both source and dest.
- Compare against MAME newport for the expose/blit damage semantics.

## Decision
Parked. Revisit after the indy/virtuix separation effort + Indy desktop validation. Do NOT fix inline now.
