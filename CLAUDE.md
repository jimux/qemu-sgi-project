# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Goal

Full system emulation of SGI systems, start with Indy (IP24), in QEMU to boot IRIX 6.5 with a functional desktop experience including graphics, sound, and networking. Target Indy first as it's the best documented SGI system. MAME has a full implementation. While Linux and NetBSD have partial ability to run on SGI hardware, and their code may include insights we could learn from. But the MAME implementation will be most instructive, as it has a full system emulated.

Document lessons learned in `progress_notes/`.

## ⭐ Guiding Principle — Virtualization-Native, NOT Hardware-Authentic (frames ALL work)

The real target is **IP55: a fast, clean, virtualization-native IRIX.** Indy/IP22 (the `-M virtuix` base) is **scaffolding** — chosen only for its excellent emulation coverage as a foundation to build on. **Faithfully replicating 1990s SGI silicon is explicitly NOT a goal.**

Hardware authenticity is not a constraint — it is a means (emulation coverage) we use when convenient and discard when it gets in the way. **When a hardware-faithful approach and a virtualization-native approach conflict, choose virtualization-native.** Prefer host-backed real-time sources, paravirtual devices/shortcuts, host GPU/audio/network passthrough, and reverse-engineered-then-rebuilt ("clock-locked") guest binaries over cycle-accurate or bus-accurate hardware modeling.

Concretely: the VM should not know or care about its own clock rate (cosmetic `hinv` display only). Timekeeping should lock directly to the host wall clock, not derive from `CP0 Count × cpufreq` — the modeled CPU "frequency" must become irrelevant to correctness (see `progress_notes/ip55/clock_decoupling.md`). The destination is IRIX running fast and clean by leaning on the host (flat-out execution, real time, host devices), with authenticity to the original hardware never an end in itself.

This principle should color every design decision — when in doubt, take the shortcut that makes IRIX run better on a hypervisor, not the one that makes it behave more like a real Indy.

## Build & Run — Always Use MCP Tools

### ⭐ Driving a running IRIX guest: the gwagent host↔guest channel (USE THIS for the build host)

For running commands and moving files in/out of a **running IRIX guest** (e.g. the `irix-devel` MIPSpro build host), use the **gwagent channel** — `pyirix_qemu/host_channel.py` `Gateway` over QEMU's gdbstub. It writes commands into guest RAM and reads results back, so it has **no serial line-length limit** (the serial console truncates commands at ~256 chars) and does clean **`push_file`/`pull_file`** (no TFTP-get-only constraint). This is the mature, reliable channel — prefer it over driving the serial console for anything non-trivial.

```python
# Guest must be at an idle shell with gwagent running (TFTP-get pyirix_qemu/gwagent.n32 -> /tmp/gwagent; /tmp/gwagent &).
# Enable the gdbstub on a RUNNING VM via the monitor: `gdbserver tcp::1234` (no reboot), OR launch QEMU with -gdb tcp::1234.
import pyirix_qemu.host_channel as hc
gw = hc.Gateway.attach(port=1234, base=0x10013000, scan=False)   # known base = fast; scan=True is the slow fallback
st, out = gw.run("cd /var/tmp/v/irix/kern; smake ...", timeout_s=200)  # long output -> redirect to a file + gw.pull_file
gw.push_file(open("local","rb").read(), "/tmp/guestpath")
data = gw.pull_file("/unix")
```

⚠️ A gdb client connect **halts the guest CPU**, so do NOT attach during boot (it freezes the boot) — attach only once the guest is idle at a shell, and the per-op halt resumes automatically. The slow magic-page **scan** is what timed out before; pass `base=0x10013000, scan=False`. Reusable helpers live in `tmp/ip55-buildfork/` (`gwx.py` = run/push/pull; `bh_session.py` = serial boot driver — note IRIX-devel NVRAM is `console=g`, so serial is silent until poked, autoboots, and you must send Start-System `1` exactly ONCE: resending interrupts the boot). See memory `buildhost_serial_not_gdb_during_boot` and `progress_notes/ip55/ip55_board_fork_phaseB.md`.

### ⭐ Driving the IRIX desktop UI: tool-based "eyes" (USE THIS instead of screenshot-and-guess)

To **see and interact with the running 4Dwm desktop** — list windows (names/geometry/state), poll readiness, and click/move/**resize** exact targets **without framebuffer screenshots** — use the **`pyirix_qemu/desktop/`** package (built on the gwagent channel above + an in-guest `gwxq` X11 helper + the closed-loop NP_CURSOR servo). It ends the token-heavy "screenshot → guess a pixel → miss" loop. **Read `pyirix_qemu/desktop/README.md`** for the setup recipe + API + recipes; findings in `progress_notes/ip55/desktop_eyes.md`, memory `desktop_eyes_tooling`. Key gotchas baked in there: X is *grabbed* during the login screen (readiness via the clogin process, not X queries); resize works via *protocol* `XMoveResizeWindow` not handle-drag; the cursor needs a +(30,30) offset (handled); pin gwagent to CPU 0 on SMP. Intra-window items (menu entries, list rows) still need one screenshot to read their text — the tooling targets at the window level, the servo then clicks exactly.

**MCP parameter types:** Many tools have `integer`-typed parameters (e.g. `ram_mb`, `timeout`, `disk_size_mb`). These require actual integers, not floats or strings. MCP input validation will reject `256.0` or `"256"` — pass `256`.

```
qemu_build                           # Build QEMU (ninja)
qemu_configure                       # Run ../configure in qemu/build/
qemu_run_sgi                         # Run QEMU with default Indy PROM
qemu_run_sgi machine=indigo2-r10k    # Run IP28 variant
qemu_run_sgi timeout=45 ram_mb=64    # Custom RAM/timeout (64MB min, 30s escape countdown)
qemu_run_sgi debug_flags=unimp,int   # Custom -d flags
qemu_monitor command="info mtree -f" # QEMU monitor command
qemu_registers boot_wait=3           # Dump CPU registers
qemu_guest_disasm address=0xbfc00000 # Disassemble guest code
qemu_guest_memory address=0x1fb80000 # Dump guest physical memory
qemu_boot_milestones                 # Identify where a boot stalls
qemu_create_disk                     # Create SCSI disk image
qemu_disk_convert source=disk.img    # Convert raw → qcow2
qemu_copy_file                       # Copy a file into/out of a running guest
qemu_serial_interact                 # Interactive serial session (expect/send)
qemu_serial_upload_binary            # Upload a binary file via serial
qemu_serial_write_file               # Write a text file into the guest via serial
qemu_snapshot_save                   # Boot + save VM snapshot (needs qcow2)
qemu_snapshot_restore                # Restore a saved VM snapshot
qemu_session_start                   # Start persistent QEMU session (interactive)
qemu_session_send                    # Send text / read output from session
qemu_session_snapshot                # Save snapshot on running session
qemu_session_stop                    # Stop a persistent session
qemu_session_cleanup                 # Kill all QEMU processes + sessions
newport_sendkey / newport_mouse      # Inject keyboard/mouse input
newport_screendump                   # Capture screenshot of the guest display
harness_install                      # Full IRIX install into a VM instance
harness_boot / harness_resume        # Boot or resume an installed instance
irix_quick_inspect                   # Snapshot of running IRIX state (ps, net, uptime)
irix_ps / irix_sysinfo / irix_netstat  # Live IRIX process/system/network info
nvram_dump / nvram_set               # Read or write NVRAM fields
parse_qemu_log                       # Parse -d unimp trace, map to SGI hardware
vm_instance_create/list/info/fork/reset/delete/migrate  # VM instance management
fs_info / fs_ls / fs_cat / fs_extract / fs_inject       # Filesystem tools
xfs_superblock / xfs_inode / xfs_path / xfs_block / xfs_check / xfs_repair_superblock
prom_build / prom_try_compile / prom_disasm / prom_symbols / prom_sections / prom_preprocess
list_proms / disassemble / analyze_function / build_call_graph  # PROM analysis
ghidra_analyze / ghidra_decompile / ghidra_functions / ghidra_xrefs  # Ghidra
library_scan / library_search / library_stage            # External software library
log_grep / log_context / log_uniq / log_range            # Log file tools
```

### Manual Build / Direct Invocation (native, no Docker)

This project now runs **directly on Ubuntu** (no Docker layer — that was a Mac→Linux bridge,
no longer needed on the dedicated workstation). Build deps live on the host: `libslirp-dev
libcap-ng-dev libseccomp-dev libgtk-3-dev libsdl2-dev libpixman-1-dev libfdt-dev libffi-dev
libglib2.0-dev meson(>=1.5; via pip if apt's is too old) ninja-build pkgconf`.

```bash
# Build QEMU once (or after any qemu-sgi-repo/ source change):
cd qemu-sgi-repo && rm -rf build-linux && mkdir build-linux && cd build-linux
../configure --target-list=mips64-softmmu --disable-fuse --disable-fuse-lseek \
             --enable-slirp --enable-gtk
ninja -j$(nproc) qemu-system-mips64

# Run with a real interactive window on the host X server (this machine is dedicated —
# do NOT use -display none or background-hiding tricks).
# DISK SAFETY (see "VM Lifecycle & Disk Safety" below): boot a FRESH OVERLAY of a
# clean golden, use cache=writethrough (kill-safe), and `init 0` to stop — never
# boot a golden/instance disk directly with cache=writeback.
mkdir -p tmp/ip54-run                              # project-local, per-effort (see Scratch & Artifacts)
qemu-img create -f qcow2 -b prebuilt_disks/irix-6.5.5-complete-fixed.qcow2 \
                -F qcow2 tmp/ip54-run/work.qcow2    # disposable overlay
./qemu-sgi-repo/build-linux/qemu-system-mips64 -M sgi-ip54 \
  -bios PROM_library/bins/cpu/ip54/ip54.bin -m 256M \
  -L qemu-sgi-repo/build-linux/pc-bios -display gtk \
  -drive if=mtd,file=tmp/ip54-run/work.qcow2,format=qcow2,cache=writethrough,file.locking=off \
  -nic user,tftp=ip54_tftp_staging,hostfwd=tcp::2324-10.0.2.15:23 \
  -audiodev pa,id=aud0 -global sgi-pvaudio.audiodev=aud0
# To stop: at a guest shell run `sync; sync; init 0` (NOT a raw kill); the overlay
# stays clean and re-usable. If you must kill, treat the overlay as dirty (xfs_scan)
# or just discard it and re-create from the golden.
```

The MCP tools listed above still work — they wrap QEMU invocations and run the same way
natively (no `docker exec` prefix). For interactive feedback prefer the GTK window; for
scripted/CI use the existing `newport_screendump` + telnet workflow with `-display none`.

## Repository Structure

| Directory | Purpose |
|-----------|---------|
| `qemu/` | Upstream QEMU v10.2.0+ - implementation target |
| `qemu-irix/` | QEMU 2.11 fork with IRIX userland (reference only) |
| `mame/source/src/mame/sgi/` | MAME SGI implementation (hardware reference) |
| `PROM_library/bins/cpu/ip24/` | IP24 PROM images |
| `software_library/irix-657m-source/` | IRIX 6.5.7m source (kernel, ARCS, IP32prom, headers) |
| `software_library/irix-655-source/` | IRIX 6.5.5f + 6.5.5m source (kernel, headers) |
| `sgi_mcp/` | MCP server — build, run, install, PROM analysis, Ghidra, filesystem tools |
| `pyirix/` | General SGI/IRIX Python tools: EFS reader, dist package analysis, image catalog |
| `pyirix_qemu/` | QEMU orchestration: QEMUSession serial engine, disk management, install harness |
| `analysis_tools/` | Python toolkit for SGI binary analysis |
| `gathered_documentation/` | Hardware docs, driver analysis, Newport graphics |
| `progress_notes/` | Implementation lessons and discoveries |
| `ip32prom-decompiler/` | IP32 PROM decompiler (Rust) |
| `netbsd_source/` | NetBSD SGI port source (reference) |
| `vm_instances/` | VM instance storage (disk images, NVRAM, manifests per instance) |

## QEMU Implementation Files

**Indy (IP24) machine:** `qemu/hw/mips/sgi_indy.c`
**Indy devices:** `qemu/hw/misc/sgi_mc.c`, `qemu/hw/misc/sgi_hpc3.c`, `qemu/hw/display/sgi_newport.c`
**Indy headers:** `qemu/include/hw/misc/sgi_mc.h`, `qemu/include/hw/misc/sgi_hpc3.h`, `qemu/include/hw/display/sgi_newport.h`
**Build config:** `qemu/hw/mips/Kconfig`, `qemu/hw/mips/meson.build`, `qemu/hw/display/Kconfig`

**O2 (IP32) machine:** `qemu/hw/mips/sgi_o2.c`
**O2 devices:** `qemu/hw/misc/sgi_crime.c`, `qemu/hw/misc/sgi_mace.c`, `qemu/hw/display/sgi_gbe.c`, `qemu/hw/misc/sgi_crime_re.c`

## Testing

```bash
python3 -m pytest tests/ -m "not slow" -v   # Fast tests (~0.2s) — run before committing
python3 -m pytest tests/ -v                 # All tests including slow
python3 -m pytest tests/test_foo.py -v      # Single file
```

Tests serve dual purposes: **regression detection** and **assumption documentation**. Many assertions are best-guesses based on MAME, datasheets, or observed behavior — not ground truth. When a test fails after a code change, ask: **did I break something, or did I fix a wrong assumption?** Update the test if the old assertion was wrong; revert the code if the new behavior is incorrect. Use `[CROSS-REF]`, `[ASSUMPTION]`, and `[INVESTIGATIVE]` tags in test names to document confidence level.

## Scratch & Artifacts — project-local `tmp/`, one dir per effort

Do **not** write scratch artifacts to system `/tmp`. Use the project-local **`tmp/`** directory (gitignored) so logs, screenshots, and disposable disks are easy for the user to find, inspect, and clean up themselves.

- **One subdirectory per distinct effort**, named for the effort, so unrelated work never mixes: e.g. `tmp/ip55-smp-boot/`, `tmp/disk-safety/`, `tmp/newport-colormap/`. Put that effort's boot/serial logs, screendumps, scratch scripts, and disposable overlay disks under it (`tmp/<effort>/boot.log`, `tmp/<effort>/work.qcow2`, `tmp/<effort>/desktop.png`).
- `tmp/` and everything under it is ignored by git; delete a subdir freely when its effort is done. The user may also clear it themselves.
- Reserve system `/tmp` only for truly ephemeral, non-user-facing internals (e.g. MCP unix sockets). Keep durable/inspectable artifacts in `tmp/<effort>/`; promote anything worth keeping (key screenshots, final notes) into `progress_notes/`.

## VM Lifecycle & Disk Safety (READ — corruption is the #1 time-sink)

Disk corruption from force-killing QEMU mid-write has cost more time than any other class of bug (e.g. the IP55 "won't boot at ≥4 CPUs / 15-of-15 panics" hunt was a poisoned XFS journal replaying into `EFSCORRUPTED` on every boot — the SMP kernel was fine; days were lost on a corrupted disk). Two facts drive the rules: corruption is **broken write-ordering**, not a "place" (a SIGKILL under `cache=writeback` loses/reorders the journal writes XFS relies on; `cache=writethrough` makes a kill *crash-consistent*, the case journaling survives); and **"repaired" ≠ "correct"** — `xfs_repair`/log-zeroing make a filesystem *consistent* by **discarding in-flight data** (files come back missing/truncated). So for a dev disk holding an OS + kernel-under-test, the trustworthy fix is **roll back to a golden, not repair**.

The defense is layered — strongest lever first:

1. **Prevent.** Use `cache=writethrough` on every writable disk (kill-safe; the MCP SCSI path already does). Shut down **gracefully**: at a guest shell run `sync; sync; init 0` (reboot: `init 6`, never raw `reboot`), wait for the PROM/halt prompt, *then* stop QEMU. `qemu_session_stop` does this by default (`graceful=true`); when there's no shell it uses monitor `quit` (flushes). **Killing is a last resort** (`qemu_session_cleanup`/SIGKILL/`kill -9`) — only when the guest is genuinely wedged (unresponsive to `init 0` and monitor `quit`).
2. **Contain.** Do ALL work on a **fresh disposable overlay** of an immutable golden (`golden_fork` / `vm_instance_fork` / `qemu-img create -b <golden>`). Never boot a golden/backing disk directly. A crash then only poisons a throwaway. Goldens are `chmod 444`; `qemu_session_start` refuses to write-open one.
3. **Detect + gate (don't trust-repair).** Any disk whose VM was force-killed is **dirty** — the kill paths drop a `<disk>.dirty` marker and `qemu_session_start` refuses to boot it. Before reuse run **`disk_verify`** (`qemu-img check` container + `xfs_scan` guest FS). Default remediation is **discard + re-fork from a golden** (`vm_instance_reset` clears the marker). `xfs_scan(clean_log=true)` is a labeled salvage-only escape hatch — a structural pass alone does NOT certify a force-killed disk (a poisoned journal replay can't be ruled out by inspection).
4. **Golden catalog + promotion.** Milestone snapshots live in the **golden catalog** (`golden_list` / `golden_snapshot` / `golden_register` / `golden_fork`) — immutable, checksummed, external qcow2 files with provenance (NOT qcow2 internal snapshots). Promote only a **clean, verified** state: source not dirty, passes `qemu-img check`, cleanly shut down (`init 0`), and ideally verified to boot from a fresh overlay (record how in `verified`). The clean baseline is `prebuilt_disks/irix-6.5.5-complete-fixed.qcow2` (registered as golden `irix655-complete-fixed`).

MCP tool guards now enforce much of this: dirty-disk boots refused, immutable-golden write-opens refused, `vm_instance_delete`/`reset` refused while a VM holds the disk, SIGKILL paths mark disks dirty.

## Snapshots

Avoid qcow2 internal snapshots — they are incompatible across QEMU builds and have caused data loss. Prefer the **golden catalog** (`golden_snapshot`/`golden_fork`) and `vm_instance_fork` + `vm_instance_reset` for all test workflows. See **VM Lifecycle & Disk Safety** above.

## Token Conservation

Binary analysis tools produce large output. Key patterns to keep context small:

- **`grep_filter`** on `qemu_run_sgi` / `qemu_serial_interact` — return only matching lines
- **`save_log` + `log_uniq`/`log_grep`** — write full output to file, query it; `log_uniq`
  collapses MMIO polling loops (thousands of identical lines → handful of unique ones)
- **Explore subagent** for broad analysis — isolates large outputs from main context window
- **Prefer summary tools**: `qemu_boot_milestones`, `xfs_check`, `irix_quick_inspect`,
  `parse_qemu_log` over raw logs or individual low-level tools
- **Ghidra**: one function at a time with `ghidra_decompile`; results cached after
  first `ghidra_analyze` so all queries in a session are fast
- **Limit disassembly ranges**: pass explicit byte counts to `disassemble` /
  `ghidra_disassemble`

## IP54 — Common Misunderstandings (read before debugging)

IP54 development repeatedly went in circles chasing wrong theories — most of them rooted in assuming Linux-style behavior where IRIX does something specific and different. A full retrospective is in `IP54-dev-lessons/` (start with `00-README.md`). The recurring traps, and where the details live:

- **An unexpectedly-zero page means "read the `reg_t`", not "something corrupted it".** mmap of a device (VCHR/VBLK → `pas_addmmapdevice`, `r_maxfsize = len`) differs from mmap of a file (`pas_addmmap`/`pas_addexec`, `r_maxfsize = off + len`); `fault.c:2732` uses that to choose demand-zero vs vnode-read. → `IP54-dev-lessons/03-irix-particulars-that-bit-us.md` §1, `progress_notes/ip54/ROOT_CAUSE_FOUND_2026-06-20.md`.
- **`t9 = 0` / jump-to-zero is a zeroed GOT entry** (rld maps sub-page data segments from `/dev/zero`), not a wild jump. → `03-...md` §2.
- **Desktop input is shmiq, not idev; the cursor is the gfx board's `gf_PositionCursor`.** Don't instrument the idev event path to "prove" input is broken. → `02-dead-ends-and-false-theories.md` #1–#2, `03-...md` §3.
- **There are no CPU caches in QEMU TCG** — `dki_dcache_wbinval` is a no-op; "cache-alias / pvdisk reads zeros" cannot be a cause. → `02-...md` #3, memory `ip54_libpthread_got_zero_root_cause`.
- **A SIGBUS is an exception, not corruption** — IRIX userland's unaligned `lw` needs QEMU `MO_UNALN`, not a cache fix. → `02-...md` #5, `03-...md` §8, memory `ip54_mo_unaln_fix`.
- **os.a can't be relinked** (GNU armap + CFLAGS mismatch) → kernel `os.a` fixes ship as narrowly-scoped PROM binary trampolines. → `03-...md` §6, `05-infrastructure-and-toolchain.md`, `progress_notes/irix_archive_armap.md`.
- **IRIX XFS V1 has no inline regular files and corrupts on offline edits** — apply config LIVE, `init 6` not `reboot`. → `03-...md` §5, `05-...md` §2d.
- **"signal 11 held, epc 0x0" is a held-signal-across-exec / bsh-arena issue, not an IP54 kernel bug** (use `#!/bin/sh`). → `03-...md` §7.
- **Reproduce on the EXACT crashing config first** — the biggest single time-sink was debugging the working golden `/unix` for a crash that only happens on `/unix.new`; gate every gdb session on kernel md5 + symbol-drift. → `04-debugging-methodology.md`, `05-...md` §2a–§2b.

## Documentation

Hardware details, memory maps, boot recipes, timing analysis, installation guides, and key technical findings all live in `progress_notes/`. Start there for context on any subsystem before modifying it. IP54 retrospective/lessons (dead-ends, IRIX particulars, debugging methodology) are in `IP54-dev-lessons/`.

**Markdown style:** When writing markdown artifacts (progress notes, docs, READMEs), write prose as one line per paragraph — don't insert hard newlines where the viewer can wordwrap. Let the renderer wrap. (Hard-wrapping is fine only inside code blocks/tables.)
