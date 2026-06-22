# IP55 SMP + genuine Indy desktop graphics — graphical xdm login achieved (2026-06-22)

This note records how the IP55/Virtuix SMP kernel got its real Indy desktop drivers back (graphics, textport, input, audio) and reached a graphical **xdm login dialog rendering on the emulated Newport, on 2 CPUs**. Artifacts live in `ip55_desktop_kernel/` (kernel `unix.g.smp-desktop`, the three library-sourced objects, the relink scripts, and the `xdm-login-smp2.png` screenshot).

## The core misunderstanding we corrected

SMP development did **not** remove any desktop functionality, and it never swapped Indy hardware for paravirtual devices. The `virtuix` machine (`qemu-sgi-repo/hw/mips/sgi_indy.c:762`) is the real IP24 init path: real HPC3 + real WD33C93 SCSI + real Seeq ethernet + **real Newport graphics**. The only paravirtual thing in the whole machine is `TYPE_SGI_SMP` (`sgi_indy.c:393`) — an IPI/CPU-ID/secondary-start register block gated on `ncpus>1` (uniprocessor boots stay byte-identical). The SCSI-under-SMP fix was a kernel `master.d` **tunable** (`dopatch.sh §5n`: `wd93_syncenable`/`wd93_enable_disconnect` off on bus 0), not a device swap.

What actually "lost" the desktop was the **kernel sysgen spec**: it was inherited from the abandoned IP54 project's hand-assembled `IP54.sm`, whose own header admits it is "all modules from irix.sm + gfx.sm + audio.sm" with the **Indy graphics deliberately stubbed** (IP54 used a paravirtual framebuffer). So the relink had to rebuild from the **genuine IP22 specs**, not IP54.sm.

## How IRIX decides what gets linked (the mechanics that bit us)

- `lboot` links the objects of the modules **named in the spec**, mapped to object files via `master.d/<module>` descriptors. Staging `gfx.o` into the boot area does nothing unless the spec says `INCLUDE/USE: gfx` **and** `master.d/gfx` exists. The build tree's `master.d` was a subset missing every graphics descriptor — that produced `cannot open master file .../gfx` and undefined `gfx_*` symbols even with the objects present.
- `USE:` (loadable) vs `INCLUDE:` (static): `cn.o` references `gfx_earlyinit` at **static-link** time, so the gfx modules must be `INCLUDE:` (static), not `USE:`. Flipping `USE→INCLUDE` resolved the `gfx_*`/`rrm*`/`shmiq_sproc` cluster.
- A stub archive can shadow real drivers: IP54.sm's stubs line added `gfxstubs, ng1stubs, gr2stubs` as `USE:` modules; `gfxstubs.a` is a **catch-all** that also defines `gfx_earlyinit`, `tp_init`, `shmiq_sproc`, `kbdstate`… so its presence prevented the real drivers from linking. Removing the `gfxstubs`/`ng1stubs` **tokens** (keeping `gr2stubs`/`mgrasstubs`/`crimestubs` — non-Indy boards we don't have objects for) was required. `gfx_earlyinit` actually lives in the **textport** object `tport.a`, not `gfx.o`.

## The recipe (relink5 + relink6, run on the irix-devel build host)

Base spec = the SMP `system.kdebug/irix.sm` (keeps the proven SMP config: `hardlocks`, `-DMP`, the per-CPU/IPI machinery). On top of it:

1. Remove the `gfxstubs`/`ng1stubs` tokens from the stubs `USE:` line.
2. Append the genuine Indy desktop block (all `INCLUDE:`, i.e. static):
   `INCLUDE: shmiq idev` / `mouse keyboard` / `tport tportpckbd` / `htport` / `ng1` / `gfxs rrm xconn` / `gfx` / `kdsp`.
3. Copy the missing `master.d` descriptors from stock `/var/sysgen/master.d` into the build tree: `gfx gfxs ng1 rrm xconn shmiq idev mouse keyboard htport kdsp a2_dd tport tportpckbd` **and `qcntl`**.
4. Stage the real objects into `IP22bootarea`. Most were already in the build host's `/var/sysgen/boot`; the **three this kernel-dev host lacked** were pulled from the software library (a full-install gold disk, `/usr/cpu/sysgen/IP22boot`): **`tport.a`, `tportpckbd.a`, `qcntl.o`** (saved under `ip55_desktop_kernel/objects/`).
5. Shim `vce_avoidance`: the SP-built gfx/audio objects reference this VCE-avoidance global, which the MP build dropped with `-D_VCE_AVOIDANCE`. QEMU TCG models no caches, so `int vce_avoidance = 0;` (appended to the `master.d/gfx` C-section, compiled into `master.c`) is the correct shim, not a hack.
6. `lboot … -u /unix.g` → clean link (`LBOOT_RC=0`), zero undefined.

## The shmiq blocker and its fix (qcntl)

After relink5, `/unix.g` booted SMP=2 and `gfxinfo` fully enumerated the Newport (`"NG1" graphics, 1280x1024, 8 bitplanes, NG1 rev 2, REX3 rev D, VC2 rev A…`), but **Xsgi died**: `Failed to open shmiq control device.: No such device or address (ENXIO)` → `Error Starting SHMIQ I/O!`.

Root cause: the `shmiq` descriptor declares `DEPENDENCIES: clone, qcntl`, and its own comment notes `shmiq_lock` is *"initialized by the qcntl driver's qcntlinit()"*. The build tree's `master.d` was **missing the `qcntl` descriptor** (every relink logged `qcntl: cannot open master file`), so `qcntlinit()` never ran and `shmiqopen()` returned ENXIO. `shmiq.o` itself was always linked; the open just failed at runtime. Adding the `qcntl` descriptor + `qcntl.o` (relink6) fixed it.

(`shmiqDestroy` is 108 bytes at *offset* 0x2c0 — earlier mis-read of size-vs-offset wasted time; `shmiq.o` was never the problem.)

## Result (verified)

`/unix.g` (8,793,428 bytes, cksum 1946012386) booted on `-M virtuix -smp 2`:
- `NOTICE: Virtuix: SMP up, CPU_COUNT=2 numcpus=2`; `hinv` → `2 66 MHZ IP22 Processors`.
- Newport detected/enumerated by `gfxinfo`; real `gfx_earlyinit` = 68 bytes (matches stock `/unix`, vs the 8-byte stub in `/unix.mp`); 69 gfx/ng1/rrm/xconn/shmiq symbols statically linked.
- **Xsgi runs** (no shmiq error); `gfxinfo` then reports "Operation not permitted" because X has grabbed the board (Managed).
- **xdm "X Window System" login dialog renders on the Newport** → `ip55_desktop_kernel/xdm-login-smp2.png`.

SP-built Indy graphics objects linking into the MP kernel was the standing risk; it works in practice (QEMU has no caches; `vce_avoidance` was the only SP/MP global that mattered).

## Host↔guest transfer notes (build host has no `nm`, serial corrupts long lines)

- Map symbols→objects on the **host** with cross-`nm` after `fs_extract`-ing `/usr/cpu/sysgen/IP22boot` from a gold disk. Guest has no `nm`.
- Binary push: `uuencode` on host → throttled serial heredoc (~1.2 ms/char) → `/usr/bsd/uudecode` on guest → verify with `cksum` (matched exactly for `tport.a`/`tportpckbd.a`).
- Long inline serial commands get char-corrupted; push a script file and run it instead. IRIX `sed` does not expand `\n` in the replacement — build multi-line spec blocks with a `cat <<EOF` heredoc, not `sed s/x/a\nb/`.

## Next

- Drive the xdm login (PS/2 `sendkey` via QEMU monitor) to reach the actual 4Dwm desktop; capture it.
- Fold this into the kernel build pipeline (`dopatch.sh`/`dolboot.sh`) so the desktop drivers + `qcntl` + the `vce_avoidance` shim are produced by the normal SMP build, and install the result **as `/unix`** (namelist match — `configmon`/`ml` need it) instead of booting `/unix.g` by hand.
- The build host (`irix-devel`) is a kernel-dev install missing `tport.a`/`tportpckbd.a`/`qcntl.o`; carry the three saved objects with the build.
