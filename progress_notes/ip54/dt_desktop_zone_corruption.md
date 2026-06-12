# .dt desktop kernel zone corruption — investigation (task 15)

Date: 2026-06-12. The last blocker to a STABLE full Indigo Magic
desktop. The visual milestone is already achieved (granite weave + gray
Toolchest with items, framebuffers/a_dtv2_session.png) — this is purely
about the session surviving past ~30 s.

## Symptom

The full `.dt` session (desktop=on) panics ~30 s after the graphical
login, in the kernel zone allocator. Signatures vary by RAM size and
timing but are all the same underlying bug:
- `zone_shake+0x64` / `+0x148`, EXC code 128 "Software detected SEGV",
  Bad addr 0x0 / 0x4
- `mrlock_resort_queue+0x278`, badvaddr 0xff800000
- `bcopy+0xb0`, Data Bus Error, badvaddr 0xc04c8000 (at 120 MB)
- bare `PC=0` (corrupt function pointer, at 256 MB)

`EXC code 128 = Software detected SEGV` is key: this is the kernel's
OWN sanity check firing on a bogus freelist/struct pointer, not a
hardware TLB miss. So a kernel zone freelist gets corrupted (a node's
next-pointer becomes garbage); the next allocation/reclaim that walks
it trips the check and panics. The corruption is upstream; zone_shake
merely detects it.

## What it is NOT (all ruled out empirically)

- **HEART/RAM overlap**: a 120 MB boot (RAM ends 0x0F800000, below the
  HEART shim at 0x0FF00000 — zero overlap) STILL panics. And the PROM
  descriptor hole (ip54_stubs.c, pages 0xFF00-0xFF70) had no effect on
  available memory — the kernel probes RAM directly, ignores ARCS
  FirmwarePermanent descriptors. (That PROM change is a harmless no-op.)
- **Xsgi PIXELDMA paths**: forcing PIO on both the write thresholds
  (rex3DrawImage/12/24, sltiu 0x4000) AND the read thresholds
  (rex3ReadImage/12/24, sltiu 0x1b58) — neither stopped the panic.
- **A single .dt app's normal operation**: launched from a serial root
  shell on a stable classic session, makeDotDesktop, bgicons, fm, and
  iconcatalog EACH survived 45 s — and all four survived running
  concurrently (the bisect doesn't kill them between stages). So it is
  neither one app nor raw concurrency.
- **The Xsgi binary patch / my changes generally**: the classic session
  (desktop=off, 4Dwm + toolchest, SAME patched2 Xsgi) is rock stable
  through the full 150 s settle.

## What it IS — UNIFIED FINDING (2026-06-12)

The corruptor is **process creation (fork/exec) in the post-X kernel
state**, NOT any specific app.

Decisive evidence from the `nodesktop` bisect (b9xs8tcxx): a full `.dt`
session running env-setup + makeIconVisuals + preallocColors + 4Dwm
with the SG_UseBackgrounds granite weave + toolchest, but with the
fm/bgicons/iconcatalog block skipped (built-in `$HOME/.desktop-*/
nodesktop` flag), rendered the FULL granite weave and stayed up for the
ENTIRE 150 s idle settle with ZERO panics. It then panicked the instant
the test script did a **serial console login** afterward — i.e. when
getty→login→sh forked new processes into the post-X environment
(PC=0, EXC 128, the same zone corruption).

So:
- idle post-X session (no new forks) = stable;
- forking/exec'ing new processes after X has run = corrupts a kernel
  zone → next allocation/reclaim panics.

This unifies every observation: the full `.dt` session crashes within
~30 s because fm/bgicons/iconcatalog fork and allocate heavily; the
classic session is stable because 4Dwm+toolchest reach steady state and
stop forking; the nodesktop session is stable until an external login
forks; and apps survive *solo* serial launch because that is a single
fork on an otherwise-quiet system (low collision odds), whereas the
desktop's burst of concurrent forks reliably hits it.

NOT the held-signal mechanism: no `ALERT: Process [X] ... signal N held`
line ever appeared in any boot — these are direct `PANIC: KERNEL FAULT`
(software-detected SEGV), so the bsh-style held-signal-kill is ruled out.


## UPDATE 2026-06-12 (debug-toolkit Layer 1 findings)

QEMU MMU trace events (`mips_mmu_fault`/`wildfault`) now exist. Captured fault
landscape on a non-crashing fm-only boot (168,884 faults): 153k user faults +
15k kseg2/3 sign-extended faults; **ZERO truly-wild (mid-range) faults**. Crucially,
faults at `0xffffffffff800000` are **ROUTINE** (326+/boot) — they're normal
linear-page-table self-map refills during user-page TLB walks. So the bug is NOT a
simple wild-pointer dereference; `ff800000` is the page-table region, faulted on
constantly without issue. The CRASH is a *nested* fault at ff800000 taken **inside
`emulate_branch`** (the delay-slot/branch re-execution path during fault fixup,
EPC 0x881bd700) — a context where the kernel can't service another TLB miss → panic.
Trace events alone won't isolate it (the triggering fault looks routine). Need
**guest GDB** (Layer 4): breakpoint at `panic`, freeze on the fm crash, inspect the
full call stack + faulting instruction + register state + curproc. The fm-only crash
is ~50%/boot (full .dt crashes more reliably — use it with the breakpoint).

## LOCALIZED 2026-06-12: the trigger is `fm`, the fault is a KSEG3 page-table access

Two clean bisects nailed it:
- **NOT the pre-rundesktop gfx steps**: a localization boot (run_a_localize.py)
  ran, on a stable classic session, fork-stress (60×/bin/true) after EACH of
  xsetroot-granite-weave, preallocColors, makeIconVisuals — ALL survived (240
  forks, 0 panics). So the weave render and colormap/icon-visual setup do NOT
  poison the kernel.
- **The culprit app is `fm`**: a full xdm `.dt` session with the rundesktop
  block running ONLY `/usr/sbin/fm -b` (bgicons/iconcatalog/sabgicons disabled
  via Xsession.dt.fmonly) crashes ~30 s post-login, exactly like the full
  session. fm survives *solo serial launch* on a classic session (earlier
  bisect) but crashes when xdm spawns it into the full `.dt`-context session.

### The fault, precisely (fm-only crash, reboot register dump)
- EPC 0x881bd700 — an unlisted static in the **TLB/fault-handler region**
  (between emulate_branch and sizememaccess; it's the fault dispatcher: checks
  `BadVAddr < 0x80000000` (user?) then calls a resolver returning code 0x80 =
  software SEGV).
- **BadVAddr = 0xFFFFFFFFFF800000**, Context = 0xFFFFFFFFFF7FC000,
  EntryHi = ...FF80008A. The Context matches BadVAddr via PTEBase.
- PTEBase = 0xFFFFFFFFFF000000 (set by PROM Patch 10, the "Context PTEBase:
  lui v0,0xFF00 (QEMU mtc0 fix)" — kernel's addiu+dsll didn't sign-extend
  under QEMU; PROM rewrites it to `lui v0,0xFF00`). So BadVAddr 0xFF800000 is
  inside the **KSEG3 linear-page-table self-map**.
- With 16 KB pages, a PTE at self-map offset 0x800000 is the PTE for user VA
  `(0x800000/8) << 14` ≈ **16 GB** — i.e. a process dereferenced a wild high
  pointer; the kernel's attempt to read its PTE in the self-map hit an absent
  page-table page → **nested TLB miss** → general fault → panic instead of a
  clean SIGSEGV to the process.
- The address **0xFF800000 recurs** (also the mrlock_resort_queue panic) — a
  specific computed/stored value, not random corruption. Strong clue.

### Why fm and not bgicons/iconcatalog / not solo-launch
fm (+ its fmserv/fserv daemons) is the heaviest desktop process: it mmaps the
icon/desktop databases, uses shared memory for icon transport, and does
hwgraph icon rendering. Something in fm's address-space / pointer handling
produces the wild ~16 GB access **only** in the full xdm `.dt` context (likely
a value read from a gfx ioctl to our custom pvfb/pvrex3 driver, or a
sign-extension / mmap-placement issue specific to the 0xFF000000 PTEBase +
16 KB-page MMU layout). Solo serial launch on a classic session doesn't set up
the state that yields the bad pointer.

## Recommended next step (focused MMU session)

1. Confirm the faulting *process* and its bad access: instrument the kernel
   fault dispatcher (PROM patch near 0x881bd700, like the existing null-guards)
   to log `curproc->p_pid / comm` + the original user BadVAddr (not the PTE
   address) → names fm vs fmserv/fserv and the exact wild VA.
2. Trace where fm gets the ~16 GB pointer: `par`/truss fm under the `.dt`
   session, or check the return values of its gfx ioctls to pvfb/pvrex3
   (gf_Info / gf_Private / the CMAP/XMAP DCB reads) — a 0xFFFFFFFF or
   garbage return used as a pointer/offset is the prime suspect.
3. Decide fix: if our pvfb/pvrex3 driver returns garbage from an ioctl fm
   trusts → fix the driver (kernel rebuild). If the kernel mishandles the
   nested page-table-region TLB miss (should signal the process, not panic) →
   that's a kernel fault-path / QEMU-TLB-delivery issue; review how QEMU
   vectors a nested TLB miss in KSEG3 and the kernel's tlbmiss-on-page-table
   handler. The recurring 0xFF800000 suggests a single deterministic source.

The headline visual goal (full granite weave + gray Toolchest) is reached and
the session is stable while idle; this is the interaction-stability bug, now
localized to `fm` and a KSEG3 page-table nested-fault at the recurring address
0xFF800000.

## State

- Visual milestone: framebuffers/a_dtv2_session.png (full desktop).
- Canonical Xsgi: /workspace/xsgi.patched2 (read+write PIO; pristine =
  xsgi.bin). Injected at /usr/gfx/arch/IP22NG1/Xsgi in disk.qcow2.allfix.
- Classic session (desktop=off) is a stable fallback that shows the full
  Toolchest with correct gray colors today.

---

## 2026-06-12 — Guest GDB online + reframed as PV-device DMA corruption

### Toolkit: guest GDB now works (the Layer-4 blocker is solved)
The QEMU gdbstub was connecting but **all breakpoints silently failed** and every
KSEG0 memory access returned "Cannot access memory at address 0x88...". Root cause:
**gdb's MIPS pointer width follows the ABI, not the ISA.** `set architecture
mips:isa64` only selects the *decoder*; the default o32/n32 ABI keeps addresses
32-bit, so gdb zero-extended KSEG0 VAs (`0x881a34b4` → `0x00000000881a34b4`, which
is unmapped xkphys, not `0xFFFFFFFF881a34b4`). Planting a software breakpoint is a
memory *write*, which failed the same way → no breakpoint ever existed to hit.

Fix (in `pyirix_qemu/guest_gdb.py`): add **`set mips abi n64`** to every gdb
command sequence, and sign-extend kernel VAs in `_resolve()`/`_sx()`
(`0x881a34b4` → `0xffffffff881a34b4`). Validated: a hardware breakpoint at a hot
PC fires within ~1 s with a full register dump + symbolized stack scan
(`resumeidle+0x60`, etc.) + `x/i $pc` disassembly. Conditional bps work too
(`hbreak *ADDR if $v0 == 0`).

### Exact faulting code (read from the *live* kernel via gdb)
The on-disk symbol JSON addresses match the running kernel, but the staging
`unix.new` *bytes* at this offset do NOT (different rev) — trust the live read:
```
0x8819c1ac:  ld   v0, 8(sp)     ; v0 = a local chunk pointer (stack slot sp+8)
0x8819c1b0:  lw   v0, 0(v0)     ; v0 = *v0      <-- FAULTS when the local is NULL → Bad addr 0x0
0x8819c1b4:  ld   a0, 32(sp)
0x8819c1b8:  sw   zero, 4(v0)   ; v0->zone_free_prev = NULL   (reported EPC; SEGV path rounds up)
```
This is the inner freelist-unlink loop of `zone_shake` (kmem_zone.c:1633-1654):
`next_chunk->zone_free_prev = NULL` etc. A chunk's freelist link
(`struct zone_freelist {next; prev;}`, links live *inside the freed chunk's own
memory*) is NULL/garbage when the code requires a valid node. **`s0` = the zone
pointer** (confirmed: disasm `lh a3,20(s0)` = `zone_units_pp`, offset 20). N32
layout puts `zone->zone_name` at **s0+44**.

### Decisive: more RAM does NOT fix it — it MOVES the crash
- **256 MB**: `PANIC: KERNEL FAULT`, `PC zone_shake+0x148`, `Software detected
  SEGV`, `Bad addr 0x0` (NULL freelist node).
- **512 MB**: a *different* panic — `Kernel/Interrupt Stack Overflow @0x0
  sp:0x0 k1:0xffffca20 ra:0x0`, `PANIC: stack underflow/overflow`, preceded by a
  userspace `Segmentation fault (core dumped)`.
- **either**: sometimes just userspace SIGSEGV → `X connection broken` /
  `4Dwm: I/O error`, machine stays up (no kernel panic).

Three unrelated subsystems (zone allocator, kernel stack, userspace) failing from
one workload, with the failure point **moving when RAM layout changes**, is the
textbook signature of a **wild writer scribbling guest-physical memory** — NOT
memory pressure and NOT a localized `fm` user pointer. Crucially, the 512 MB
**kernel-stack** clobber (`sp:0x0`, `ra:0x0`) cannot be produced by a userspace
fm pointer — userspace cannot write the kernel interrupt stack. So the corruptor
has **kernel write access**.

### Reframed hypothesis: a paravirtual device DMAs to the wrong guest-physical addr
The desktop is the first workload that drives the PV devices hard:
- **fm scans the filesystem** → heavy `pvdisk` reads (DMA into the buffer cache).
- **fm/4Dwm render icons** → `pvfb` / `pvrex3` writes/command processing.
- boot already shows `routed: ... No buffer space available` (network buffer
  stress) — `pvnet` RX DMA is another candidate.
Plain multi-user (login/shell/ps/df/hinv — no disk scan, no rendering) is stable.
A PV device computing a wrong guest-physical DMA target (truncated/un-sign-
extended address, wrong base, off-by-page, or stale descriptor) would scribble
kernel memory and produce exactly this moving, multi-mode corruption.

### Recommended next step (supersedes the earlier MMU-centric plan)
Audit PV-device DMA *guest-physical address computation* in QEMU, prime suspects
in order: `pvdisk` (sgi_ip54pv / pvdisk DMA), then `pvfb`/`pvrex3`, then `pvnet`
RX. Look for: 32-bit address truncation, missing KSEG0→phys mask, descriptor
ring base errors, byte-vs-word length, writes past the intended buffer.
Concrete catches available now that gdb works:
- A **hardware watchpoint** on a known-stable kernel global (or the zone freelist
  head) to trap the wild write red-handed: `watch *(int*)ADDR` then `continue` →
  freezes at the instruction that corrupts it (could be inside a device-MMIO
  helper if QEMU does the bad write via cpu_physical_memory_write — in that case
  the guest PC is the driver code that *programmed* the bad DMA).
- Cross-check by **disabling each PV device's DMA** (force PIO / smaller xfers)
  one at a time and seeing which one makes the desktop stable.

The earlier "fm wild ~16 GB pointer / 0xFF800000 KSEG3 self-map" reading was one
*symptom* (a userspace fault mode); the kernel-stack clobber at 512 MB shows the
true fault is kernel-memory corruption from a device.

---

## 2026-06-12 — ROOT CAUSE FOUND & FIXED: stale pvclock address (wild 100 Hz write)

**The corruptor was the paravirtual clock writing to a stale hardcoded kernel
address.** In `qemu-sgi-repo/hw/mips/sgi_ip54pv.c`, `pvclock_raise_work()` writes
`cause_ip5_count = 1` into guest RAM every 10 ms (100 Hz) at a HARDCODED physical
address:
```c
#define IP54PV_CAUSE_IP5_COUNT_PA  0x0829ED00ULL   /* was: VA 0x8829ED00 */
cpu_physical_memory_write(IP54PV_CAUSE_IP5_COUNT_PA, &one, sizeof(one));
```
That address is `cause_ip5_count`'s KSEG0 VA masked to physical. But the symbol
MOVED across kernel rebuilds (lboot) and the constant rotted:
- `ip54_kernel_symbols.json`      → 0x8829F150
- `ip54_kernel_symbols_local.json`→ 0x8829ED00   (the stale hardcoded value)
- **`ip54_kernel_symbols_disk.json` (the RUNNING /unix.new) → 0x8829EDC0** ✓
So QEMU was writing `0x00000001` 100×/second into VA **0x8829ED00** — 0xC0 bytes
*below* the real `cause_ip5_count` — relentlessly clobbering whatever unrelated
kernel variable the disk build placed there.

### Why this matched every symptom
- **Active from boot, but latent**: writing `1` to a low-traffic variable is
  harmless until a workload (the desktop's allocation churn / fm disk-scan +
  icon render) makes that memory live. Plain multi-user never stressed it →
  stable. Only the `.dt` desktop tripped it → "post-X" crash.
- **Crash signature MOVES with RAM** (256MB zone_shake NULL deref vs 512MB
  kernel-stack `sp:0x0` vs userspace SIGSEGV): the fixed wild-write victim is a
  different live structure under each RAM layout.
- **Timing-sensitive / intermittent** (~50% Mode 1 vs Mode 2; gdb attach shifted
  the odds): a 100 Hz async write from the iothread racing guest execution.
- **`Software detected SEGV` / corrupt freelist**: classic downstream symptom of
  a stray write into kernel data.

The earlier "fm wild pointer / 0xFF800000 KSEG3 self-map" reading was a *symptom*
(one userspace fault mode); the true cause is this device-side wild write, which
also explains the kernel-stack and zone-allocator corruption that no userspace
pointer bug could cause.

### The fix
`IP54PV_CAUSE_IP5_COUNT_PA` corrected to **0x0829EDC0** (VA 0x8829EDC0, the disk
kernel's `cause_ip5_count`), with a prominent warning comment that this is a
guest-symbol address that MUST be re-derived from ip54_kernel_symbols_disk.json
on every kernel rebuild. Rebuilt qemu-system-mips64 (build-linux).

VERIFIED: full `.dt` desktop boots, renders the complete Indigo Magic ambiance
(granite weave to all edges + gray Toolchest with all items + UnixRoot/Register
icons — framebuffers/pvclockfix2_desktop.png), and a confirmed-started session
(serial 'Soundscheme') SURVIVED 238s where it previously crashed within ~100s.
(Test harness lesson: graphical login via newport_sendkey is unreliable; confirm
the session actually started by watching serial for 'Soundscheme' before judging
stability — otherwise a failed login looks like a "survival" sitting at xlogin.)

### Robustness follow-up (recommended)
Hardcoding a guest kernel symbol in the device model is the root fragility — it
broke silently on a kernel rebuild and cost a long investigation. Proper fixes,
in order of preference:
1. Have the kernel publish the address: pvclock/pvtimer init writes
   `&cause_ip5_count` (phys) to a dedicated MMIO register; QEMU uses that.
2. Drop the guest-memory write entirely if the SW2 assertion + CP0 Compare path
   alone drive clock() (test: remove the write, confirm timekeeping) — removes
   the hardcode forever.

### Timekeeping note (post-fix)
After correcting the pvclock address, measured guest:host clock ratio = **0.473**
(guest advances ~45s per 95s wall). This is **TCG emulation speed**, not a tick-
accounting bug: the emulated R10000 runs at ~0.47x real-time on this host and
QEMU's virtual clock tracks guest execution, so the guest sees ~47 of every 100
host-seconds' worth of 100Hz ticks. The guest's internal time is consistent
(100 ticks = 1 guest-second). Confirmed not tick-loss: switching the
cause_ip5_count write from `=1` to accumulating `+=` (capped) made NO difference
to the ratio, and the early-boot backlog it allowed drained in a burst →
stack-overflow panic. So `=1` is retained. (A faster host or a JIT/KVM target
would raise the ratio; the slip is acceptable for the desktop milestone.)
