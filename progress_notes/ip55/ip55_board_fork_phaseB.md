# Phase B — IP55 IRIX kernel-board fork (foundation laid; build-host sweep is next)

Goal (user: "the pure version"): give IP55/Virtuix its OWN IRIX `CPUBOARD` and machine-dependent kernel source so it builds with `-DIP55` from `ml/IP55.c` and never shares IP22's — completing the Indy/Virtuix separation on the IRIX side, mirroring the QEMU-side device split.

## ✅✅ COMPLETE 2026-06-24: IP55-sourced kernel BOOTS on -M virtuix

`ip55_desktop_kernel/unix.ip55` (8,383,504 bytes, built 100% from `ml/IP55.c` + `-DIP55` + the sweep) **boots on `-M virtuix -smp 4 -accel tcg,thread=multi` to `login:`** (verified via `tmp/indy-virtuix-sep/virtuix_gate.py`). The IP55 board fork is proven end-to-end: IP55 has its own CPUBOARD/product/Makefiles/machine-dependent source and builds + boots its own kernel, never IP22's. This is a base (kdebug) kernel with vce_avoidance/biozero shims; the full Indy desktop gfx is the follow-on relink (below).

## UPDATE 2026-06-24: IP55 kernel BUILDS + LINKS from its own -DIP55 source ✅

The build-host work is done: the IP55 board compiles the **entire kernel with `-DIP55`** and links a complete `/unix.ip55` (8.38 MB ELF N32 MSB mips-3). Key results:
- **Conditional sweep**: a perl pass (`ip54_tftp_staging/sweep_ip55.pl`) added `IP22 → IP22||IP55` to preprocessor conditions across **82 files / 270 sites** — the kernel then compiled with **zero cc errors** under `-DIP55`. (This was the feared "~104-file sweep"; done in one bulk pass + the build confirmed it.)
- **Board applied on the build host** (`/var/tmp/v/irix/kern`): `apply_ip55.sh` inserted the IP55 blocks into kcommondefs/ml-Makefile/bsd-Makefile in place; `IP55defs` (COMPLEX=MP) installed; `ml/IP22.c`→`ml/IP55.c` (patched, with virtuix SMP) + `IP22asm.s`→`IP55asm.s`; `IP55bootarea` seeded from `IP22bootarea`.
- **Full `smake PRODUCT=IP55`**: all archives built `-DIP55` (`kernel.o`, `os.a` 2.7M, `ml.a`, `io.a`, `xfs.a`, …).
- **Link**: `lboot -b IP55bootarea` (plain IP22 kdebug spec) → only `vce_avoidance` + `biozero` undefined (provided by the gfx/desktop driver layer, i.e. the full desktop relink). Shimmed (`do_shim_link_ip55.sh`: `int vce_avoidance=0; void biozero(){}` ar'd into gfxstubs.a — vce=0 is correct for cache-less QEMU; biozero stub is a boot-test caveat) → **`LBOOT_RC=0`, /unix.ip55 linked, no undefined.**
- Build pipeline scripts (in `ip54_tftp_staging/`, pushed via gwagent): `apply_ip55.sh`, `sweep_ip55.pl`, `dolboot_ip55.sh`, `do_relink_ip55.sh` (full desktop-gfx relink — needs gfx/input master.d + objects, the smp_desktop_kernel pipeline), `do_shim_link_ip55.sh`. Host helpers in `tmp/ip55-buildfork/`.

Remaining for a *desktop* IP55 kernel (follow-on): replace the vce_avoidance/biozero shims with the real Indy gfx/desktop drivers via the relink pipeline (master.d descriptors + ng1/gfx/tport/shmiq objects) — same mechanism as the working IP22-virtuix `unix.g.smp-desktop`. The board fork itself (IP55 builds its own -DIP55 kernel, never IP22's) is COMPLETE.

## (original) Status: foundation DONE (on disk), build-host iterative sweep REMAINING

### Done — IP55 board definition (in the source mirror `software_library/irix-655-source/f/irix/kern`, which is **gitignored** so NOT git-tracked; persists on disk):
- **`include/make/IP55defs`** (+ `f/root/usr/include/make/IP55defs`): new product def, `CPUBOARD=IP55`, R4000/Express/N32 — fork of `4DACE1defs` (IP22).
- **`kern/kcommondefs`**: added `#elif $(CPUBOARD)=="IP55"` OBJECT_STYLE block, `#if $(CPUBOARD)=="IP55"` PRODCDEFS (the R4x00/R5000 WAR set, same as IP22), and IP55 in the CONPOLL gate. `GKDEFS`'s `-D$(CPUBOARD)` then emits **`-DIP55`**; `$(CPUBOARD)bootarea`/`boot` become **`IP55bootarea`/`IP55boot`** automatically.
- **`kern/ml/Makefile`**: `#if $(CPUBOARD)=="IP55"` selects `IP55.c` + `IP55asm.s` plus the same shared Indy support files as IP22 (`arcs delay error timer timer_r4000 timer_8254 pio mcparity upgraph`, `tlb.s mcparasm.s spl.s arcsasm.s`).
- **`kern/bsd/mips/Makefile`**: IP55 builds the Indy-class NIC drivers (EC2/Seeq etc.), same as IP22.
- **`kern/ml/IP55.c`** (5479 lines) + **`kern/ml/IP55asm.s`** (109 lines): forked from `IP22.c`/`IP22asm.s` (pristine mirror copies; the virtuix SMP code is layered at build time by `dopatch.sh`, as for the current IP22-built kernel).

All additive (`#elif`/`#if IP55`): **IP22/Indy builds are byte-unaffected.**

### Remaining — build-host iterative work (next session):
1. **Apply to the build host** (`irix-devel`, `/var/tmp/v/irix/kern`): push the IP55 board files (IP55defs, the modified kcommondefs/ml-Makefile/bsd-Makefile) via TFTP; `cp` the build host's **patched** `ml/IP22.c`→`ml/IP55.c` and `IP22asm.s`→`IP55asm.s` (so IP55.c carries the virtuix SMP patches, not the pristine fork).
2. **`smake PRODUCT=IP55`** → collect compile errors. The **`#if IP22 → #if IP22 || IP55` sweep** is driven by these errors (~104 files reference IP22; only the compiled desktop-kernel subset matters — let the build tell you which). Also sweep `ml/IP55.c` itself (its internal `#ifdef IP22`).
3. **master.d/IP55** entries + the `IP55boot` system spec (sysgen-generated per CPUBOARD during the build).
4. **`lboot`** → `/unix` (IP55-sourced); **boot on `-M virtuix -kernel`**, confirm SMP desktop (vs the current IP22-sourced `ip55_desktop_kernel/unix.g.smp-desktop`, kept as fallback until proven).

## Build-host channel: use SERIAL + TFTP, NOT the gwagent gdb channel
The gwagent (gdbstub) channel **halts the guest CPU when a gdb client connects** — attaching during/after boot froze the build host mid-boot (left it at the PROM menu; `cont` via monitor resumed it). For the build-host shell work, drive the **direct serial console** (`tmp/ip55-buildfork/bh_serial.py`) and push files via **TFTP** (drop in `ip54_tftp_staging/`, `tftp get` on the guest). Verified: serial reaches a root shell, build tree intact (`ml/IP22.c` 147 KB patched, `IP22bootarea` populated). Boot driver `tmp/ip55-buildfork/boot_bh.py` (note: its gwagent-attach step is what halted boot — skip it / use serial).

## Reference
Build chain: `ip54_tftp_staging/{dostage,dobuild,mpbuild,dolboot,dopatch}.sh` (retarget IP22→IP55 paths/`-DIP55`/`PRODUCT=IP55` for the IP55 build). The current working virtuix kernel is built IP22 + dopatch SMP patches; this fork makes that source IP55-native.
