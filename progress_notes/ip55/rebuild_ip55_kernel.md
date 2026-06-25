# Rebuilding the IP55 desktop kernel (`unix.ip55.g`) from scratch

One end-to-end recipe to regenerate `ip55_desktop_kernel/unix.ip55.g` — the IP55-native
SMP desktop kernel that boots the 4Dwm desktop on `-M virtuix`. It is built 100% from
`ml/IP55.c` with `-DIP55` (a real IRIX CPUBOARD, not IP22-in-disguise) and relinked with
the genuine Indy gfx/input/textport drivers.

This consolidates work spread across `ip54_tftp_staging/` (build scripts staged into the
guest) and `tmp/ip55-buildfork/` (host driver helpers). See also
[`ip55_board_fork_phaseB.md`](ip55_board_fork_phaseB.md) (the board fork) and
[`smp_desktop_kernel.md`](smp_desktop_kernel.md) (the original IP22-virtuix desktop relink
this mirrors).

## Prerequisites

- **Build host:** the `irix-devel` VM instance — IRIX 6.5.5 + MIPSpro (`-M indy`), with the
  kernel source tree at `/var/tmp/v/irix/kern` (persists across reboots on its disk; the
  per-build `IP55bootarea/` is regenerated). Memory: [[irix_devel_build_image]].
- **Host↔guest channel:** the **gwagent** gdbstub channel — `pyirix_qemu/host_channel.py`
  `Gateway` + the helpers in `tmp/ip55-buildfork/` (`gwx.py` run/push/pull, `bh_session.py`
  serial boot driver, `bhx.py` serial command client). The 64 KB-buffer `gwagent.n32` is in
  `pyirix_qemu/`. ⚠️ The build host NVRAM is `console=g` (no serial shell); drive boot over
  serial, run commands + move files over gwagent. Do NOT attach gwagent during boot (the gdb
  halt freezes it). See [[buildhost_serial_not_gdb_during_boot]].
- **Staged scripts** (TFTP'd or pushed into the guest's `/tmp`, run with `gwx run sh /tmp/x.sh`):
  all live in `ip54_tftp_staging/`. ⚠️ Never inline multi-line `awk` through `gwx run` — it
  hangs the agent's `popen`; push a script file instead.

## Step 1 — Boot the build host

```
python3 tmp/ip55-buildfork/bh_session.py        # boots irix-devel to a root shell (serial)
# then start gwagent + the gdbstub for the fast channel:
#   guest: tftp -g gwagent /tmp/gwagent (from ip54_tftp_staging); chmod +x; /tmp/gwagent &
#   monitor: gdbserver tcp::1234   (or launch QEMU with -gdb tcp::1234, but only attach idle)
```

## Step 2 — Apply the IP55 board definitions

`apply_ip55.sh` installs the board into the shared tree (idempotent):
- `IP55defs` — product file: `CPUBOARD=IP55`, `COMPLEX=MP`.
- `kcommondefs` — IP55 `OBJECT_STYLE` + `PRODCDEFS` (→ `-DIP55`, the R4600/R5000 WAR set;
  NO `_SYSTEM_SIMULATION`) + `CONPOLL` blocks → `IP55bootarea`, `IP55boot`.
- `ml/Makefile` + `bsd/mips/Makefile` — IP55 object entries.
- `ml/IP55.c` + `ml/IP55asm.s` — forked from the SMP-patched `IP22.c` (so the SMP/dopatch
  changes carry in: IPI vector slot, per-CPU r4000 clock, the `virtuix_xicache_local` §5o
  skip in `os/machdep.c`, etc. — see [[ip55_smp_ipi_fix_and_scsi_frontier]]).

## Step 3 — The `#if IP22 → IP22||IP55` sweep

`sweep_ip55.pl` (perl, preprocessor-conditions only, skips comment-only IP22, idempotent):
turns every `#if/#elif/#ifdef/#ifndef` that tests `IP22` into one that also accepts `IP55`.
**82 files / 270 sites → the kernel compiles with ZERO cc errors under `-DIP55`.** Without it,
`immu.h` `pte_t`/`TLBLO` are undefined under `-DIP55`.

```
gwx run "cd /var/tmp/v/irix/kern && perl /tmp/sweep_ip55.pl"
```

## Step 4 — Build all archives `-DIP55`

```
gwx run "cd /var/tmp/v/irix/kern && /usr/sbin/smake PRODUCT=IP55 COMPILATION_MODEL=N32 ROOT=/usr/tmp/v/root headers"
gwx run "cd /var/tmp/v/irix/kern && /usr/sbin/smake -k PRODUCT=IP55 COMPILATION_MODEL=N32 ROOT=/usr/tmp/v/root"
```

This compiles each subsystem's objects + archives **directly into `IP55bootarea/`** (e.g.
`os.a`, `io.a`, `xfs.a`, `kernel.o`). (The §5o `machdep.c` patch must be present *before* this
so the `virtuix_xicache_local` symbol lands in `os.a`; `apply_5o_ip55.sh` stages it.)

## Step 5 — Base kernel link (sanity)

`do_shim_link_ip55.sh` links the plain `system.kdebug` spec + two shims
(`int vce_avoidance=0; void biozero(){}` ar'd into `gfxstubs.a`) → `LBOOT_RC=0` → `/unix.ip55`
(no graphics). Proves the `-DIP55` objects link + boot. Optional but a good gate.

## Step 6 — Desktop relink (the real output)

`do_desktop_relink_ip55.sh` produces `/unix.ip55.g` — the same `IP55bootarea` objects relinked
with the REAL Indy gfx stack instead of gfxstubs:
1. Restore the pristine kdebug spec, sed out `gfxstubs`/`ng1stubs`, append the INCLUDE block:
   `shmiq idev` / `mouse keyboard` / `tport tportpckbd` / `htport` / `ng1` / `gfxs rrm xconn`
   / `gfx` / `kdsp`.
2. Stage 15 `master.d` descriptors (stock `/var/sysgen/master.d` → build tree).
3. Append the **`vce_avoidance` + `biozero` shims** to the `master.d/gfx` C-section (lboot
   compiles descriptor C-sections into `master.c`). `biozero` is referenced by `xfs_rw.o`
   under `#ifdef _VCE_AVOIDANCE` but only *called* when `vce_avoidance != 0`; with the global
   shimmed to 0 the runtime takes the `bp_mapin/bzero` else-path, so the no-op `biozero` is
   linked-but-never-called (safe).
4. Stage the stock gfx objects (`ng1.a gfx.o gfxs.a rrm.o xconn.o kdsp.a a2_dd.o shmiq.o
   idev.o mouse.a keyboard.a htport.o`) into `IP55bootarea`; remove `gfxstubs.a`/`ng1stubs.a`.
5. The 3 objects NOT on the host's `/var/sysgen/boot` are pre-staged from the repo:
   `ip55_desktop_kernel/objects/{tport.a, tportpckbd.a, qcntl.o}` → push via `gwx push` and
   **cksum-verify** (a silently-truncated `qcntl.o` from a flaky push gives the misleading
   `ld FATAL 2: relc_val_size table inconsistent`).
6. `lboot` → `LBOOT_RC=0`, zero undefined symbols → `/unix.ip55.g` (~8.81 MB, ELF N32 MIPS-III).
   Benign warnings: multiply-defined `da_flush_tlb`/`check_delay_tlbflush` (kernel.o vs
   tlbmgr.o, 2nd ignored) — the SMP overrides, same as the IP22-virtuix kernel.

## Step 7 — Transfer the kernel out (cksum-verified)

The gdb-memory pull is flaky at the margins on multi-MB files. Use the gzip + split +
cksum-verified chunked pull (each 60 KB piece = one 64 KB-buffer chunk):

```
gwx run "gzip -c /unix.ip55.g > /tmp/ug.gz; split -b 60000 /tmp/ug.gz ugp."
# pull each ugp.* with a size-retry loop (tmp/ip55-buildfork/pull_pieces.sh pattern),
# reassemble, gunzip, then assert the cksum matches the guest's `cksum /unix.ip55.g`.
```

Verified-clean output → `ip55_desktop_kernel/unix.ip55.g`.

## Step 8 — Verify

```
python3 -m pytest tests/test_virtuix_boot.py -v     # boots it on -M virtuix -smp 4, checks SMP + net
# or visually: qemu_run_sgi machine=virtuix  (the MCP virtuix defaults auto -kernel it)
```

Boots to the graphical IRIS xdm login on Newport; log in → 4Dwm desktop with the dithered
root weave + Toolchest.

## Output artifacts (committed)

- `ip55_desktop_kernel/unix.ip55.g` — the desktop kernel (canonical virtuix boot kernel).
- `ip55_desktop_kernel/unix.ip55` — the base (no-graphics) kernel.
- `ip55_desktop_kernel/objects/{tport.a,tportpckbd.a,qcntl.o}` — the 3 host-staged gfx objects.
- `ip54_tftp_staging/{apply_ip55.sh,sweep_ip55.pl,apply_5o_ip55.sh,do_shim_link_ip55.sh,do_desktop_relink_ip55.sh}` — the build scripts.

⚠️ The `IP55bootarea/` build tree inside `irix-devel` is ephemeral (regenerated by Step 4); the
durable inputs are the committed scripts + objects above plus the board defs in the gitignored
`software_library/irix-655-source` mirror (applied by `apply_ip55.sh`).
