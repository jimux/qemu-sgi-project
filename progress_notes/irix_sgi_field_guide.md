# What We Learned About IRIX and SGI Hardware

A field guide to the undocumented behavior, hidden invariants, and "black-box" mechanisms we cracked while bringing SGI machines (Indy/IP24, O2/IP32, Origin 200/IP27, Tezro/IP35, and the custom paravirtual IP54/IP30) up to a booting, graphical IRIX 6.5 under QEMU.

This document is **about SGI and IRIX as systems** — the knowledge that generalizes beyond any one emulator. For QEMU-porting gotchas (QOM, GPIO, reset ordering), see [`lessons_learned.md`](lessons_learned.md). For device-level register detail, see the per-platform notes under `indy/`, `o2/`, `origin200/`, `ip35/`, and `ip54/`.

---

## 0. The meta-lesson: SGI hardware lies are internally consistent

Almost every hard-won discovery here has the same shape. A register returns a value that *looks* wrong, or the OS depends on behavior nobody wrote down — but once you find it, the design is self-consistent and deliberate. The "crack" is identifying the invariant the firmware or kernel silently relies on.

A few canonical examples, each detailed below:

- **You cannot lie to IRIX about CPU speed.** The MC's RPSS counter ticks at a fixed 1 MHz and IRIX calibrates CP0 Count against it at boot (§3).
- **PROM checksums are self-zeroing.** Sum all 32-bit words including the stored checksum and the total is exactly zero — patch one instruction and the section is rejected (§1).
- **Status bits self-deassert.** Newport's VRINT is a *timed pulse* the driver never explicitly clears; model it level-held and the framebuffer never opens (§4).
- **Interrupt-line numbering is off by design.** MAME's line 0 is IP2; the PIT timers bypass the interrupt controller entirely and wire straight to IP4/IP5 (§2).

When a value looks wrong, assume the silicon is consistent and you're missing the invariant. Cross-reference **three** sources before trusting any one of them: MAME's device model, the IRIX kernel source headers, and the original ASIC datasheet.

---

## 1. Firmware and boot (ARCS / PROM)

### The PROM is a checksummed, self-describing container

SGI PROMs are not flat blobs. The O2 (IP32) PROM is five named segments (`sloader`, `env`, `post1`, `firmware`, `version`), each behind a 72-byte `SHDR` header; code sections begin with a `j`/`nop` pair that branches over the header. Both header and body use the **same self-zeroing checksum**: sum all big-endian 32-bit words and negate; including the stored checksum word makes the total zero. Consequences:

- **You cannot NOP-patch a PROM in place.** A single changed instruction in the `post1` body invalidates the body checksum, and the sloader drops into firmware-download mode instead of executing. You must recompute and re-store the checksum.
- The `version` segment is simultaneously a valid ELF32 — byte 0x13 of the SHDR (nominally padding) is `0x08` = `EM_MIPS`, so `file` identifies it as a MIPS ELF. SGI compiled standard ELF objects and repacked them into SHDR.

### Disassembly requires knowing the execution VMA

PROM code is copied to RAM and run from a different address than it lives at in flash. MIPS `j`/`jal` keep the top 4 bits of the *current* PC, so the same blob produces correct jumps only when disassembled at its run address. O2 firmware runs at `0x81000000` (`--adjust-vma=0x81000000`); the sloader at `0xbfc00000`; `post1` is copied to `0xA0004000`. Disassemble at the load address and every `jal` target is wrong.

### Two-stage PROMs on big iron

Origin 200 (IP27) boots in two stages: **IP27prom** (in LBOOT flash) does Hub init, memory sizing, and XIO topology discovery, then loads a separate **IO6prom** from the *Bridge ASIC's* flash into DRAM and jumps to it. There is **no serial console output until the second stage initializes IOC3** (~step 14 of boot) — earlier silence is normal, not a hang. Very early diagnostics go out an I2C path (PCF8584) before IOC3 is up; returning I2C-idle on reads is enough to let the PROM move on.

### ARCS reservations are advisory; IRIX probes the hardware itself

A critical and costly discovery: **IRIX does not build its free-memory list from the ARCS `FirmwarePermanent`/`FirmwareTemporary` descriptors.** It probes the memory controller directly. So if firmware advertises a span as free but the emulator has overlaid an MMIO region there, IRIX *will* eventually allocate into it. The only real fix is to punch the MMIO pages out of the PROM's `FreeMemory` descriptors — telling ARCS isn't enough (§7, IP54 HEART overlap).

### The PROM `tolower` double-evaluation bug

`getenv()`/`nvmatch()` in the PROM source uses a `tolower` macro that double-evaluates `*s2++`, silently corrupting NVRAM environment lookups. Always `#undef tolower` in `getenv.c`. (Also: `PROM_STACK` must sit above `_end` in `prom.ld` — if it equals `_fdata`, BSS zeroing wraps to ~4 GB.)

---

## 2. Interrupt architecture across the SGI line

### The numbering trap: MAME line 0 == IP2

MAME's `set_input_line(N)` is 0-indexed from IP2, so "line 2" is IP4, not IP2. Getting this wrong routes timer interrupts to the wrong CP0 handler and the kernel hangs *in its idle loop* — long after the misroute, because POST and init poll timers directly and only the idle path depends on delivery. Test interrupt delivery independently of polling.

### Timers usually bypass the interrupt controller

On IOC2-class boards the 8254 PIT outputs wire **directly** to CPU pins — Timer 0 → IP4 (100 Hz scheduling `clock()`), Timer 1 → IP5 (profiling `ackkgclock()`). They do **not** pass through the INT3 cascade. Worse, IRIX on IOC2 actually uses the **R4000 CP0 Count/Compare (IP7)** for scheduling and uses the PIT only for PROM delay calibration. So observing PIT/IP4 traffic tells you about the profiling clock, not the scheduler.

### One pin, many sources: the demux pattern

The high-integration ASICs OR many sources onto a single CPU pin and demux in software:

| Platform | Aggregator | CPU pin | Kernel handler |
|---|---|---|---|
| O2 (IP32) | CRIME (32 sources) | IP2 | `crime_intr()` → `crimevec_tbl[]` |
| Origin 200 (IP27) | Hub PI | IP8 (RT counter) | scheduling clock |
| IP54 (custom) | HEART (64-bit ISR) | IP3–IP7 | `c0vec_tbl[]` |

Two recurring rules fell out of this:

- **A shared line must be edge-detected and re-OR'd.** QEMU GPIO is last-write-wins and does not auto-OR; clearing one source drops the line even if another still asserts. Track per-source state and recompute the OR.
- **Clearing the ISR must re-assert still-pending sources.** After the kernel writes `CLR_ISR`, any device still holding its line must see its bit come back, or the next interrupt is lost.

### Spurious-interrupt storms from unimplemented hardware

Leave an unmasked source for hardware you didn't emulate (e.g. INT3 `LIO_CENTR`, the Centronics port) and the kernel's stray handler runs on every interrupt, never clears the source, and re-fires — 500+ times/second, looking exactly like "stuck in idle." Mask the interrupt map down to only the bits of hardware you actually model.

---

## 3. Timekeeping: you cannot lie to IRIX about time

This is one of the most useful insights in the whole project.

- **The MC RPSS register ticks at a fixed 1 MHz regardless of CPU speed.** At boot IRIX measures CP0 Count rate *against RPSS* to compute true CPU frequency. Spoofing a CPU clock in NVRAM does nothing — IRIX uses the measured ratio. (Under icount with `sleep=off`, both CP0 Count and RPSS derive from `QEMU_CLOCK_VIRTUAL`, so the ratio stays correct and IRIX measures the virtual CPU honestly.)
- **`lbolt` is the heartbeat of the entire kernel.** It increments once per 100 Hz scheduling tick and is referenced 448 times across 113 files in 6.5.5. Nearly every timing path derives elapsed time from it.
- **The icount/sleep tradeoff is real and has opposite failure modes:**
- `shift=0,sleep=off` makes virtual time fly during the WAIT idle path (`qemu_icount_bias` advanced directly), so SCSI commands needing scheduling quanta complete thousands/sec instead of ~100/sec. Boot drops from minutes to seconds. **But it breaks networking** — IRIX `select()` timeouts expire in virtual microseconds while SLIRP replies arrive in real milliseconds, so ping reports 100% loss even though `netstat` shows the replies. Use `sleep=off` for install/boot only.
- `sleep=on` (default) warps virtual time to wall-clock during idle — realistic speed, networking works.
- **Overshoot rule for delay loops:** under `sleep=off` one virtual 10 ms tick costs ~85 ms of host time. Never sleep `remaining_time` as a virtual delay — sleep exactly *one* tick, re-check the real-time counter on wake, repeat. Applies to `nano_delay()`, `nanosleep_common()`, and `dopoll()` — three *separate* kernel paths that must each be patched.
- **The real-time-clock kernel patch:** expose a host-clock microsecond counter at an MC offset, then hook `nanotime_syscall()` (the `gettimeofday()` backend) to read it instead of interpolating from `lbolt`-derived `hrestime`, and `settime()` to record the epoch. This decouples wall-clock from `lbolt` without disturbing the scheduler. (See `blog/kernel_realtime_patch.md`.)

---

## 4. Graphics: the SGI family and the one insight that unlocks it

### Every board hangs off a common kernel vtable

All SGI graphics share `struct gfx_fncs` (27 functions, `sys/gfx.h`), register themselves via `GfxRegisterBoard()`, use generic ioctls 100–113 and board-private ioctls from 10000 up. Inventory type constants: `INV_NEWPORT=14`, `INV_MGRAS=15` (IMPACT), `INV_CRIME=17` (O2), `INV_RE=12` (RealityEngine).

### The libGLcore.so interception insight (highest leverage)

**Every board-specific register write — REX3 MMIO, IMPACT HQ3 FIFO tokens, Buzz packets, IR GFIFO words, CRM registers — happens exclusively inside `libGLcore.so`.** All application code above that layer speaks the standard OpenGL C API. Replacing/intercepting `libGLcore.so` captures ~100% of GL calls *while they are still in a universal format*, estimated to cover ~95% of graphical IRIX apps. This reframes graphics emulation from "model every ASIC's command stream" to "intercept one library."

### How the architectures differ (and why some are blockers)

| Family | Geometry | Command path | Emulation note |
|---|---|---|---|
| **Newport (NG1)** | CPU (`libGLcore`) | REX3 MMIO | 2D only; fully modeled in MAME |
| **GR2 / Express** | GE7 engines | HQ2 host queue | **GE7 microcode ISA undocumented** — major blocker |
| **IMPACT (MGRAS)** | hardware | user-space HQ3 FIFO @ 0x070000 | heavy context switch (drain+save/restore) |
| **O2 (CRM)** | CPU (software) | CRIME/MRE | **only true UMA SGI** — textures/FB/Z in system RAM |
| **Odyssey / VPro** | Buzz ASIC | `__BUZZpackets` write region | 127 hw contexts (7-bit ID) |
| **InfiniteReality** | 4 GE, sort-middle | 16 KB GFIFO @ 0x0000 | up to 16 pipes, 151 ASICs max |

Note: SGI's later "VPro" V3/V7 boards on x86 Visual Workstations are Nvidia Quadros and share nothing with Odyssey. IrisGL (not OpenGL) is compiled out for IP27/IP30/IP32 (`#ifndef SUPPORT_NATIVE_IRISGL`) — only IP19/20/22/24 run native IrisGL.

### The Newport pipeline, cracked

Newport's path is **REX3 (raster) → VC2 (DID tables + cursor) → XMAP9 (×5, pixel→display format) → CMAP → Bt445 RAMDAC**. The non-obvious bits:

- **VRINT is a timed pulse, not a level.** Real hardware asserts the GIO line during ~500 µs of vertical blank and self-deasserts. The `ng1` driver never reads REX3 STATUS to clear it — it only toggles the INT3 mask and expects self-deassert. Model it level-held and `local1_stat` bit 7 sticks, blocking `open("/dev/graphics")` forever. Correct model: assert on a 60 Hz timer, schedule deassert 500 µs later.
- **The XMAP9 mode table is an invariant X *assumes* but doesn't set.** The PROM/`ng1` driver fills all 32 mode-table entries with `(PIXSIZE_8, cmap_page)` before X starts; Xsgi itself does only ~2 XMAP writes per boot. Skip that initialization and entries 1–31 are 0 (CI8, CMAP page 0, all black) — so correctly-painted VRAM renders black because it's read through a null colormap. The root window being black was a *colormap* bug, not a drawing bug.
- **VRAM is stored BGR.** Irrelevant for color-index modes, but RGB pixel modes must swap R/B on readout; 8bpp RGB uses 3-3-2 BGR packing.
- **RAMDAC LUTs must reset to identity** (`LUT[i]=i`), not zero, or the display is black until the PROM runs `SetGammaIdentity()`. `Bt445SetRGB()` packs as `(r<<24)|(g<<16)|(b<<8)` — R in the MSB.
- **REX3 slope registers accept two's complement but read back sign-magnitude**; the PROM POST verifies the conversion.
- **DCB bus mode register** encodes slave address [10:7], CRS [6:4], width [1:0]. Apply data-width masking *selectively* — CMAP yes, XMAP/VC2 no (they pack the register index in the upper bits).

### REX3 octant encoding lives in the errata

The correct hardware encoding is `[XMAJOR][XDEC][YDEC]`, documented only in the "Revision History" of the REX3 manual, not the main text. IRIX computes the octant in software; the hardware only recalculates it when DoSetup is set in DRAWMODE0 or on a write to SETUP. (Linux's `newport_con` exploits this by reusing a prior quadrant.)

---

## 5. Storage: SCSI, the WD33C93, XFS and EFS

### The WD33C93 reports status in surprising places

- The SCSI device status byte is **not** in SCSI_STATUS (which always reports `SELECT_TRANSFER_SUCCESS`) — it's in `TARGET_LUN` bits [4:0].
- Status code names are **chip-centric**: `UNEX_RDATA`/`UNEX_SDATA` "receiving" means the *chip* receives from the target — i.e. a host **write** (DATA OUT).
- **Multi-pass DMA via "unexpected phase":** IRIX allocates exactly 64 HPC3 DMA descriptors per channel (`NSCSI_DMA_PGS=64`, ~256 KB cap). For larger transfers the chip raises "unexpected phase" at TC=0 and the driver re-maps and resumes with `SELECT_ATN_XFER`, *not* `TRANSFER_INFO`. Three register values must be exact or the driver won't recognize the event: SCSI_STATUS = `UNEX_RDATA/SDATA`, both CIP and BSY cleared together, COMMAND_PHASE = 0x46.
- After the transfer loop, you must still **drain zero-count EOX descriptors** or the kernel panics: "SCSI DMA in progress bit never cleared."
- HPC3 DMA descriptors carry **KSEG0 virtual addresses** (`0x8xxxxxxx`); mask with `& 0x1fffffff` before touching physical memory.

### SCSI target numbering is off by one from your array index

`scsihostid` defaults to 0, so the adapter is ID 0 and the *first drive* is target **1**: `scsi(0)disk(1).../unix` → `/dev/dsk/dks0d1s*`. Don't confuse the QEMU drive array index (0 = first drive) with the bus target ID (1). And CD-ROM: the PROM issues MODE SELECT to switch block size 2048→512, but stock QEMU `scsi-disk.c` doesn't recompute `max_lba` — reads near end-of-disc then fail.

### XFS V1 (the IRIX-era format) is its own dialect

Modern `xfsprogs` rejects it ("V1 inodes unsupported"). The format quirks that matter:

- **Leaf directory magic `0xfeeb` lives at byte 8** (inside `xfs_da_blkinfo_t`), not a 4-byte magic at offset 0. Check offset 0 and you miss every directory.
- **Shortform directories have a 9-byte header**: parent inode (uint64) at [0..7], entry count at [8]; entries are `ino(8)+namelen(1)+name[]`. This is incompatible with modern dir2 shortform (uint32 parent at [2..5]).
- **Inline (LOCAL) format is illegal for regular files** — only for shortform directories and symlinks. Any tool writing files into an XFS image must allocate extents even for a 1-byte file, or the kernel reports "corrupt inode (local format for regular file)."
- **The PROM/SASH version gate accepts only versions 1–4**, and version 4 only if feature bits stay within `0x3FFF`. Linux `mkfs.xfs` defaults to version 5 → always rejected. Patch 2 bytes at superblock offset 100.
- **`xfs_sb_t` is ABI-sensitive.** Compile PROM XFS code O32, or 64-bit memory types shift struct offsets and the on-disk layout no longer matches (we saw a 4-byte slip read `sb_inodesize` from the wrong offset).

### EFS vs XFS, and install-media facts

- IRIX 5.3 = EFS only; 6.2 = choice; **6.5 = XFS only**.
- Raw EFS images need an **SGI volume header prepended** before IRIX will mount them. Large EFS images (>700 MB) must be attached as **read-only SCSI disks, not CD-ROMs** — `:cdrom` triggers an oversized READ(10) and a bus reset.
- Software is shipped as `pd001`-magic spec files plus `.sw/.idb/.man`, across six distinct CD directory layouts; a recursive scanner (check `pd001` up to 6 levels deep) finds ~15% more products than a flat `/dist/` scan.
- IRIX 6.5 full install = **8 CDs**; **3+ simultaneous CD-ROMs hang the 6.5 miniroot** (max 2). Install on Indy (IP24), not Indigo2 (IP22) — the 6.5 miniroot stalls probing GIO Fiber Ethernet on IP22.
- Always use `cache=writethrough` on `-drive` — default `writeback` can lose the volume header (e.g. the `sash` binary) on an unclean exit.

---

## 6. Networking, serial, and the input stack

### Seeq 80C03 Ethernet: the MAC that "disappeared"

The Seeq register file is **banked via bits [6:5] of the TX command register**: bank 0x00 = station address, 0x20 = multicast filter low, 0x40 = filter high + control/config. IRIX writes the MAC in bank 0, then writes the multicast hash in banks 0x20/0x40. Miss the bank switch and the hash writes land on the station address bytes — silently rewriting the MAC so every *unicast* packet (ARP replies, ICMP echo replies) is dropped by the address filter while broadcasts still work. Two more Seeq traps:

- **RX descriptor `r_rown` (bit 14) has inverted ownership:** 1 = hardware owns, 0 = software owns (data ready). The ISR loops `while (!r_rown)`.
- **`ENET_MISC` reset is rising-edge**, not level — IRIX writes 0x03 then 0x00 on every interrupt ack; a level-triggered reset wipes Seeq state every IRQ.

### Z85C30 serial: a 3-bit pointer and two write paths

- **WR0 register pointer is bits [2:0], not [0:3].** Masking `val & 0x0f` lets command bits leak into the pointer and silently scatters WR5/WR11/WR14 writes, breaking STREAMS TX setup. Use `& 0x07`.
- IRIX uses the **polled `du_putchar` path for all early-boot output**; the STREAMS TX path (which sets WR1 TX-int-enable) activates only after the full STREAMS stack is pushed. So silence after "Creating miniroot devices..." means device enumeration is running without console output — not that serial broke.

### The IRIX input stack is STREAMS modules you have to assemble

Keyboard/mouse flows: `8042 → pckm (raw PS/2 set-3 bytes) → idev → pckbd (PS/2 decode) → shmiq (shared-memory event queue) → Xsgi`. **All three STREAMS modules must be in the stack.** The most common failure: `pckbd` not pushed (missing `autopush`/`strconf` config), so raw bytes reach X undecoded and `idevGenPtrEvent` never fires — the pointer is dead even though "input" arrives.

Other input-stack facts:

- The 8042 status register distinguishes source: `0x21` (MSFULL|OBF) = mouse, `0x01` (OBF) = keyboard; both share one IRQ.
- **`shmiq` ships as a closed binary** (`shmiq.o`); only `sys/shmiq.h` is in the source tree. The X-wakeup mechanism is genuinely opaque.
- **`XGrabServer` hangs without real interrupts.** With `grabServer: True`, xdm grabs and shmiq blocks waiting for keyboard/mouse interrupt activity that never comes if no 8042 IRQ is wired. Set `DisplayManager.grabServer: false`.

### Indigo Magic graphical login facts

- xdm ignores the wildcard `DisplayManager*loginProgram`; you need the display-specific `DisplayManager._0.loginProgram`. And the xauth cookie path in `Xlogin` (`/usr/lib/X11/xdm/xdm-auth-$dpy`) mismatches where xdm writes it (`/var/X11/xdm/authdir/`) on cold boot — set `DisplayManager._0.authorize: false`.
- **`/var/adm/SYSLOG` is the authoritative X-auth diagnostic** — "AUDIT: client N rejected from local host" appears nowhere else by default.

---

## 7. The IRIX build system and MIPSpro toolchain

### Source trees and what's missing

- Releases come in parallel **"m" (maintenance) and "f" (feature)** streams, 6.5.1–6.5.22; identify via `RELEASE_NAME` in `eoe/include/makerules/releasedefs`.
- Only the **6.5.7m** tree ships `stand/arcs/` (PROM/ARCS source). 6.5.5 has no `stand/`.
- The source product is **intentionally incomplete**: 815 pre-built kernel objects (IP19–IP32) live in `irix-bld.cpio` with no source; graphics, ARCS/PROM, and parts of the C++ runtime are binary-only. `ksys/*.h` kernel headers are build-time only and **not installed** on a running system.

### `smake` is the real blocker

The build uses SGI's proprietary **`smake`** (never open-sourced). `commondefs` relies on `smake`-only features — `:S///` and `:M` modifiers, `!=` command substitution, the `!` forced-rebuild operator, 76+ nested `#if` blocks — that GNU and BSD make can't parse. The toolchain pipeline is `driver → cpp → fec/fecc (→ WHIRL IR .B) → be → asm → ld32/ld64`, and **`be` (the optimizer backend) crashes under qemu-irix userland** in `libC.so.2`/`libCsup.so` static constructors because IRIX C++ runtime needs `usynccntl`/`prctl`/shared-arena primitives that emulation returns `ENOSYS` for.

### Relinking the kernel with lboot

- The merge trick: `ld -n32 -r -o merged.o new_module.o kernel.o` — **symbols listed first win**; "Multiply defined (2nd definition ignored)" is expected.
- Kernel module compile flags that matter: `-n32 -mips3 -O2 -G 8 -non_shared -TENV:kernel -D_KERNEL -D_PAGESZ=<pagesize>`. **`-G 8`** puts globals ≤8 bytes in `.sdata`/`.sbss` addressed via `$gp`; it must match the kernel's threshold. `-non_shared` avoids PIC relocations that cause ELF `e_flags` mismatch. `-D_PAGESZ=16384` is required on R10000 (16 KB pages) or buffers size wrong silently.
- **`lboot` builds in the current directory** — `cd /` first.
- **Any change to a `.o`'s size shifts the whole kernel binary layout.** If the PROM applies runtime patches at hardcoded offsets into the kernel, even a one-byte-larger stub makes an unrelated device init crash. Timestamp `/unix.new` into the future to stop lboot rebuilding it.
- **`S23autoconfig` silently rebuilds the kernel on every multi-user boot** from the stock `.sm` (which references stock drivers), overwriting a custom kernel. Move it to `disabled/`.

### MIPSpro / install logistics

- All MIPSpro binaries are **N32 big-endian ELF**; the `cc`/`as`/`ld` in `usr/bin` are symlinks to `driverwrap`, the real driver is `usr/lib32/cmplrs/driver`.
- The FLEXlm license check warns (server long dead) but compiles anyway.
- Version skew: the dev-libraries CD packages (foundation era) conflict with 6.5.5 `eoe.sw.base`; pull the version-matched overlays from **Overlays CD 2**, not CD 1.
- `csh` expands backticks inside quoted heredocs — `exec sh` first. IRIX `sh` is Bourne (no `$()`). Use `init 6`, never raw `reboot` (XFS panic risk).

---

## 8. Memory and address-map facts

- **MIPS kseg0 (0x80000000) and kseg1 (0xA0000000) both alias physical 0.** Keep all pre-kernel data (SPB, FirmwareVector, stubs) below physical 0x2000; anything higher can be overwritten when the kernel loads.
- **Per-platform memory probing differs even within a family:** IP22 probes by write-read-compare at 4 MB granularity (SEG0 = `0x08000000`); IP28 configures progressively larger sizes and checks for address wrap at 16 MB granularity (SEG0 = `0x20000000`). MEMCFG bank registers encode base (phys>>22), size code `(MB/4)-1`, valid bit, subbank bit — `sys/IP22.h` is authoritative.
- **Big iron reports memory in a single register the OS trusts:** Hub/Bedrock `MD_MEMORY_CONFIG` packs 3 bits/bank (size code, `0x400000 << code` bytes); IRIX `szmem()` reads it and does **not** re-probe. O2 sizes via TLB-mapped probing of SEG1 at `0x40000000` (each CRIME bank = a 128 MB window, smaller SIMMs mirror within it).
- **MMIO overlaid on RAM is transparent only for the addresses you forward.** Every register offset inside the overlay is an invisible hole in RAM: a kernel pointer written there is lost and reads back as whatever the register returns. This produces **load-dependent, RAM-size-dependent random crashes** (one config never touches the hole; a heavier workload allocates into it). The fix is to remove the hole from the free list, not to hope nothing lands there (IP54 HEART shim, §1).
- **The PRDA (process data area) is mapped at virtual `0x200000` for every N32 binary** — the basis of IRIX's userland threading.

---

## 9. Debugging IRIX under emulation

These techniques were essential and non-obvious:

- **GDB against the live MIPS64 kernel needs `set mips abi n64`.** Without it, gdb zero-extends KSEG0 addresses (`0x881a34b4` → `0x00000000881a34b4`, unmapped xkphys) instead of sign-extending them. Planting a software breakpoint is a memory write that fails the same way — so breakpoints silently never exist. Also: in n64, `$10` is `a6` (not `t2`); register-name confusion corrupts hand-assembled patches.
- **Kernel symbol tables drift on every lboot relink.** Stale symbol JSON points breakpoints at the wrong functions silently (we saw 99% of addresses wrong after one rebuild). Always regenerate symbols from the *running* kernel's ELF and diff before a session.
- **Deterministic record/replay works for a full IRIX boot** (single vCPU, `icount shift=7,sleep=off`) — transcripts are SHA1-identical. `reverse-stepi` walks PC backward correctly. Gotchas: set `rrsnapshot=rrstart` explicitly (the auto path aborts replay with a `bdrv_snapshot_delete` assertion); `reverse-continue` is unreliable in practice.
- **Hardware watchpoints never fire for KSEG0/KSEG1 under TCG** — the watchpoint check doesn't cover direct-mapped regions. Use a TCG memory plugin (`contrib/plugins/memwatch.c`) and remember it needs `-d plugin` or output is silently discarded. It sees guest-CPU accesses but **not** device-issued `cpu_physical_memory_write()`.
- **Diagnosing a "wild write":** multiple unrelated crash signatures (zone freelist NULL deref, kernel-stack `sp:0x0`, userspace SIGSEGV) from a single workload, where the crash point *moves when RAM size changes*, is the textbook signature of one fixed bad physical-address write — not a pointer bug in any one process. Userspace can't corrupt the kernel interrupt stack; only a device DMA or a host-side write with a wrong address can.
- **The CP0_Cause RMW race** is a latent upstream `target/mips` correctness bug: the iothread (`cpu_mips_irq_request`) and the vCPU thread (`helper_mtc0_cause/compare`) both read-modify-write `CP0_Cause` unlocked, so a guest `mtc0 cause` between them loses an interrupt bit or resurrects a cleared one. It's statistically invisible until a guest hammers Cause (IP54's pvclock wrote SW2 200×/sec and exposed it). Fix needs both: move device raises to `async_run_on_cpu()` *and* take the BQL around the CP0 helpers.
- **Beware scaled-timer overflow:** `timer_new_ms` fed a `qemu_clock_get_ns()` absolute expiry overflows int64 once host uptime passes ~2.56 h, flipping sign every ~2.5 h — either the timer never fires or it busy-loops and starves all other REALTIME timers. Match clock granularity to timer scale (`timer_new_ns`). This looked exactly like "the desktop crashes QEMU after a few minutes" and had nothing to do with the desktop.
- **Stub registers that return 0 cause faults far from the bug.** O2's GBE stub returning 0 → kernel computes `nscreens=1531` → huge `kmem_alloc` → fault in unrelated code. Seed stubs with MAME's reset/default values and trace every unimplemented access with `qemu_log_mask(LOG_UNIMP)` (zero cost when `-d unimp` is off; never use `fopen/fprintf` tracing — it added minutes to boot).

---

## 10. Where the knowledge came from (reference points)

- **MAME** (`src/mame/sgi/`) — the only working full-system SGI emulator and the primary behavioral reference. Newport is complete; CRIME is memory-controller only; IMPACT/Odyssey/IR are absent. Use it for *behavior*, validate register *semantics* against IRIX kernel headers.
- **Original ASIC specs** — REX3, VC2, XMAP9, MC, HPC3, IOC, GIO64 etc., originally from the linux-mips FTP, mirrored at bukosek.si (which also has O2 CRIME/MACE/GBE, Octane HEART, Origin specs).
- **NetBSD `sgimips`** — clean ARCS firmware interaction (`arcemu.c`, `arcbios_calls.S`); the ARC Spec v1.2 PDF defines the callback table / SPB / memory descriptors IRIX expects.
- **qemu-irix** (n64decomp / irixxxx) — QEMU 2.11 fork for IRIX userland; born from the N64 decompilation community needing IDO compilers on Linux.
- **ip32prom-decompiler** (mattst88) — proves SGI PROMs disassemble to bit-identical reassembly. **m2c** with `--target mips-ido-c` decompiles IDO 5.3 output, useful for reading IRIX disassembly.
- **Communities/archives** — irix7.com (TechPubs), sgi.neocities.org (MAME+6.5.22 walkthrough), forums.irixnet.org, the IRIX-32 reverse-engineering project.

---

*This guide consolidates discoveries documented in detail across `progress_notes/` and the auto-memory. When a finding here conflicts with a more recent per-platform note, trust the per-platform note and update this one.*
