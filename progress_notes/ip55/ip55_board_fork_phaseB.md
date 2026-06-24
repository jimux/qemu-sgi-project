# Phase B — IP55 IRIX kernel-board fork (foundation laid; build-host sweep is next)

Goal (user: "the pure version"): give IP55/Virtuix its OWN IRIX `CPUBOARD` and machine-dependent kernel source so it builds with `-DIP55` from `ml/IP55.c` and never shares IP22's — completing the Indy/Virtuix separation on the IRIX side, mirroring the QEMU-side device split.

## Status: foundation DONE (on disk), build-host iterative sweep REMAINING

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
