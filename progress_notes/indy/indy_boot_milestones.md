# Indy Boot Milestones

Phase-by-phase timeline of the SGI Indy (IP24) QEMU emulation project,
from first register access to full IRIX 6.5 desktop.

---

## Timeline

### Phase 1-3: Foundation (2026-02-03)
Implemented MC register access with BE/LE address normalization, memory
probing with MEMCFG per-bank mapping, RPSS timer, and 8254 PIT timer in
HPC3. Created unimplemented device regions for memory probing. CPU boots
and executes PROM code through memory detection.

### Phase 4: PROM POST (2026-02-05)
WD33C93 SCSI controller fixes (auto-increment, phantom CD-ROM, stale
status). Newport graphics stub responds to REX3 registers. Z85C30 serial
port RR0/RR1 pointer fix. GIO empty slot returns 0xFFFFFFFF. **Milestone:
PROM reaches System Maintenance Menu** with both IP24 PROMs.

### Phase 5: DCB + CMAP (2026-02-06)
Fixed DCB mode register bit layout, sub-word MMIO reads, CMAP status
register. PROM completes graphics POST (with expected VRAM diagnostic
failure).

### Phase 6: Graphics Diagnostic Pass (2026-02-06)
Fixed 20 register write masks and implemented slope sign-magnitude
conversion. **Milestone: PROM completes full POST with no errors.** Screen
rendering works (startup text, boot menu).

### Phase 7: SCSI CD-ROM Boot (2026-02-06)
HPC3 DMA EOX drain fix, SCSI MODE SELECT max_lba fix (upstream QEMU bug).
**Milestone: PROM reads CD volume header, loads sashARCS (~316KB), executes
it.** MCP tools gain CD-ROM support.

### Phase 8: Timer IRQ + Miniroot (2026-02-08)
Fixed PIT timer interrupt routing (Timer0→IP4, Timer1→IP5 instead of
INT3 Local0/Local1). Fixed serial interaction MCP tool buffering. WD33C93
kernel-level SCSI: TARGET_LUN status, COMMAND_PHASE, TRANSFER_INFO path,
DMA XIE interrupt routing. **Milestone: IRIX 6.5 miniroot kernel boots,
prints banner, reaches "audio: AES receiver not responding."** Kernel still
hung in idle loop — timer routing alone insufficient.

### Phase 9: INT3 Cascade Fix (2026-02-09)
Fixed three bugs in INT3 mapped interrupt cascade: PIT timers incorrectly
setting map_status, no centralized cascade function, MAP_MASK writes not
re-evaluating cascade. **Milestone: Kernel boots past idle loop, runs init,
reaches "Creating miniroot devices, please wait..."** (14,890+ syscalls,
257 exec() calls observed).

### Phase 10: Performance (2026-02-09)
Removed ~260 lines of file-based debug tracing (`fopen/fprintf` on every
interrupt/timer). Added SCSI error logging via `qemu_log_mask`. Identified
MODE_SENSE(0x1a) returning CHECK_CONDITION (sense 5/36/0) as the
enumeration blocker.

### Phase 11: Virtual Time (2026-02-09)
Investigated WAIT instruction timing. Discovered `-icount shift=0,sleep=off`
makes PIT fire at host speed instead of wall-clock 10ms periods. Created
bare-metal timing benchmark confirming deterministic PIT periods and instant
WAIT wakeup. See [`virtual_time_and_timing.md`](virtual_time_and_timing.md).

### Phase 12: IRIX Installation (2026-02-10)
Full IRIX 6.5 installation from 8 CDs. Multi-pass DMA fix for large SCSI
transfers (>256KB). HPC3 DMA descriptor chaining with WD33C93B unexpected-
phase interrupts for multi-pass. mkfs_xfs, package installation, kernel
autoconfig all working. See [`irix_installation_guide.md`](irix_installation_guide.md)
and [`multipass_dma_fix.md`](multipass_dma_fix.md).

### Phase 13: Seeq Ethernet (2026-02-11)
Implemented Seeq 80C03 EDLC in HPC3 with bank-selected register writes,
TX/RX DMA descriptor chains, and SLIRP user-mode networking. Ping and
telnet working. See [`seeq_ethernet_implementation.md`](seeq_ethernet_implementation.md).

### Phase 14: Newport Graphics + Xsgi (2026-02-11)
Eight drawing engine fixes (scr2scr direction, DOSETUP, pixel word read,
LENGTH32, LR_ABORT, SKIPFIRST/SKIPLAST, shade mode, color accumulators).
Critical VRINT timed-pulse fix (real hardware deasserts after VBLANK ends,
not read-to-clear like MAME). **Milestone: Xsgi X server, 4Dwm, xclock,
xterm all run.** See [`newport_xsgi_milestone.md`](newport_xsgi_milestone.md).

### Phase 15: xdm Graphical Login (2026-02-13)
Fixed xdm blocking on XGrabServer by setting `grabServer: False` in
`/var/X11/xdm/xdm-config`. Fixed Newport display pipeline bugs: overlay
compositing cidaux bit extraction, block fill Y advance, RAMDAC gamma LUT
init, RGB mode BGR unpacking. **Milestone: xdm login screen renders
correctly.** See [`xdm_graphical_login_fix.md`](xdm_graphical_login_fix.md)
and [`newport_display_pipeline_debug.md`](newport_display_pipeline_debug.md).

### Phase 16: Keyboard/Mouse Input (2026-02-13)
PS/2 keyboard and mouse via 8042 controller in IOC2, routed through INT3
map bit 0x10 to CPU IP2. IRIX pckm driver detects both devices. MCP tools
`newport_sendkey` and `newport_mouse` provide host-to-guest input. **Milestone:
Interactive login at xdm graphical screen.** See
[`keyboard_mouse_input.md`](keyboard_mouse_input.md).

### Phase 17: Full Desktop (2026-02-14)
All subsystems operational: Newport graphics, serial console, SCSI disk,
Seeq ethernet, HAL2 audio stub, keyboard/mouse input. IRIX 6.5 boots from
disk to 4Dwm desktop with xdm graphical login. **Milestone: Functional
IRIX desktop experience.**

---

## PIT Timer IRQ Routing (Definitive)

| Source | CPU IRQ | CP0 Cause Bit | IRIX Handler |
|--------|---------|---------------|--------------|
| INT3 Local0 | IP2 | bit 10 | `lcl0_intr` |
| INT3 Local1 | IP3 | bit 11 | `lcl1_intr` |
| PIT Timer 0 | IP4 | bit 12 | `clock()` |
| PIT Timer 1 | IP5 | bit 13 | `ackkgclock()` |
| Bus error | IP6 | bit 14 | `buserror_intr` |
| CP0 Count/Compare | IP7 | bit 15 | `r4kcount_intr` |

PIT timers bypass INT3 entirely (direct to CPU lines). This is verified
against MAME `ioc2.cpp:210-226` and confirmed by bare-metal benchmarks.

---

## Verified Assumptions

| Assumption | Status |
|---|---|
| PIT Timer 0 fires at 100 Hz | **VERIFIED** (bare-metal benchmark) |
| INT3 cascade per MAME ioc2.cpp:268-284 | **VERIFIED** (kernel boots past idle) |
| PIT timers bypass INT3 entirely | **VERIFIED** (MAME cross-ref) |
| 64MB RAM sufficient for PROM boot | **VERIFIED** (32MB hangs; 64/128/256MB all work) |
| R10000 MRU cache not simulated | **KNOWN-LIMITATION** (non-blocking) |
| VRINT is timed pulse, not level-held | **VERIFIED** (IRIX ng1 driver expects hardware deassert) |
