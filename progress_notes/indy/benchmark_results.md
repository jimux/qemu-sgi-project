# Benchmark Results & Performance Guide

Measured performance data for the SGI Indy QEMU emulation under various
configurations. Use this document to understand the impact of QEMU flags,
hardware configuration, and SCSI topology on boot and emulation performance.

All measurements taken on the project host. Absolute times are
host-dependent; relative comparisons between configurations are meaningful.

---

## Quick Reference

| Configuration Change | PROM Boot Impact | Kernel Boot Impact |
|---------------------|------------------|-------------------|
| `-icount shift=0,sleep=off` | **None** | **Dramatic speedup** |
| Add SCSI disk (target 1) | +60.0s (SCSI probe) | Needed for boot |
| Add SCSI CD-ROM | +30.1s more (probe) | Needed for install |
| 32MB → 64MB RAM | **Required** (32MB hangs) | Untested |
| 64MB → 128MB RAM | +0.05s (memory probe) | Untested |
| 128MB → 256MB RAM | +0.01s (negligible) | Untested |

**Key insight:** PROM boot is dominated by real-time polling delays (escape
countdown, SCSI target probing). `-icount shift=0,sleep=off` has no effect
on PROM timing because the PROM never executes WAIT — it busy-loops. The
flag only helps once the IRIX kernel is running and using WAIT for idle.

---

## 1. PROM Boot Timing

Time from QEMU launch to "System Maintenance Menu" prompt.

### No SCSI Devices

| RAM | Time to Menu | Notes |
|-----|-------------|-------|
| 32 MB | **TIMEOUT** | Never reaches menu |
| 64 MB | 30.50s | Baseline (~30s is escape countdown) |
| 128 MB | 30.55s | +0.05s memory probe overhead |
| 256 MB | 30.56s | +0.06s memory probe overhead |

**Finding:** 32MB RAM is insufficient — the PROM does not reach the menu.
64MB is the practical minimum. Memory probing overhead above 64MB is
negligible (<0.1s). The ~30s baseline is entirely the "Press Escape to
enter System Maintenance Menu" countdown — actual hardware init and
memory probing complete in well under 1 second.

### With SCSI Devices

| Configuration | Time to Menu | Delta from Baseline |
|---------------|-------------|---------------------|
| No devices, 64MB | 30.50s | — (baseline) |
| Disk at target 1 | 90.51s | +60.0s (SCSI probe) |
| Disk + CD-ROM | 120.59s | +90.1s (SCSI probe) |

**Breakdown of PROM boot with disk + CD-ROM (120.6s):**
- <1s: Memory probe, hardware init
- ~30s: "Press Escape to enter System Maintenance Menu" countdown
- ~60s: SCSI bus scan — probes all 8 targets (0-7), timeout on empty ones
- ~30s: Additional CD-ROM identification and volume header reads

### Effect of `-icount shift=0,sleep=off` on PROM

| Config | Default | icount sleep=off | Delta |
|--------|---------|-------------------|-------|
| No devices, 64MB | 30.51s | 30.52s | +0.01s (noise) |
| Disk only | 90.51s | 90.59s | +0.08s (noise) |
| Disk + CD-ROM | 120.59s | 120.61s | +0.02s (noise) |

**Conclusion:** The PROM never executes WAIT. All delays are polling loops
with calibrated iteration counts. icount mode cannot accelerate these.
The sub-0.1s deltas are measurement noise — there is zero meaningful
difference.

---

## 2. PROM SCSI Command Profile

During a 120-second PROM boot with disk (target 1) + CD-ROM (target 4),
the PROM issues the following WD33C93 commands:

### WD33C93 Controller Commands

| Command | Opcode | Count | Purpose |
|---------|--------|-------|---------|
| SELECT_ATN_XFER | 0x08 | 50 | Send SCSI command to target |
| DISCONNECT | 0x04 | 14 | Bus disconnect after completion |
| RESET | 0x00 | 3 | Controller reset (init, bus reset, re-init) |
| **Total** | | **67** | |

### SCSI Commands to Disk (Target 1)

| Command | Opcode | Count | Purpose |
|---------|--------|-------|---------|
| INQUIRY | 0x12 | 4 | Device identification |
| TEST_UNIT_READY | 0x00 | 4 | Check device ready |
| READ(10) | 0x28 | 3 | Read volume header, partition table |
| MODE_SENSE | 0x1a | 2 | Query device parameters |
| MODE_SELECT | 0x15 | 1 | Set device parameters |
| **Total** | | **14** | |

### Timeout Probes

| Targets | Count | Purpose |
|---------|-------|---------|
| 0, 2, 3, 4, 5, 6, 7 | 36 | Selection timeout on empty targets |

Each empty target probe results in a SELECTION_TIMEOUT (status 0x42),
which accounts for most of the SCSI probe time. With 7 empty targets
and ~8s per timeout, this accounts for ~56s of the ~60s SCSI probe phase.

---

## 3. Bare-Metal CPU Timing

From `tests/bare_metal/timing_test.S`, run via `tests/test_cpu_timing.py`.
Measures CP0 Count ticks for various operations.

### Default Mode (no icount)

```
TEST COUNT_RATE:      delta=817    iterations=10000   PASS
TEST WAIT_WAKEUP:     delta=16929  expected=10000     tolerance=100000 PASS
TEST PIT_PERIOD:      delta=562612 expected=500000    tolerance=100000 PASS
TEST INST_THROUGHPUT: delta=2460   expected=500       PASS
TEST MEM_THROUGHPUT:  delta=550    iterations=1000    PASS
```

- WAIT wakeup: ~17K ticks (nondeterministic, varies between runs)
- PIT period: ~562K ticks (12% above expected 500K)
- Timing is nondeterministic

### `-icount shift=0,sleep=off`

```
TEST COUNT_RATE:      delta=1500   iterations=10000   PASS
TEST WAIT_WAKEUP:     delta=2      expected=10000     tolerance=100000 PASS
TEST PIT_PERIOD:      delta=500000 expected=500000    tolerance=100000 PASS
TEST INST_THROUGHPUT: delta=50     expected=500       PASS
TEST MEM_THROUGHPUT:  delta=250    iterations=1000    PASS
```

- **WAIT wakeup: 2 ticks** (virtually instant — down from 17K)
- **PIT period: exactly 500,000 ticks** (perfectly deterministic)
- All timing is deterministic and reproducible

### Comparison

| Metric | Default | icount sleep=off | Ratio |
|--------|---------|-------------------|-------|
| WAIT wakeup (ticks) | 16,929 | 2 | 8,465x faster |
| PIT period (ticks) | 562,612 | 500,000 | Exact vs ~12% error |
| Count rate (10K loops) | 817 | 1,500 | 1.8x |
| Inst throughput (ticks) | 2,460 | 50 | 49x |
| Mem throughput (ticks) | 550 | 250 | 2.2x |

**Key insight for kernel boot:** With `sleep=off`, WAIT returns in 2 ticks
instead of 17K. Since the IRIX kernel spends 80%+ of time in WAIT (idle
loop), this eliminates the wall-clock bottleneck and lets the PIT 100 Hz
scheduler run at host speed instead of real-time.

---

## 4. Bare-Metal SCSI Timing

From `tests/bare_metal/scsi_bench.S`, run via `tests/test_scsi_timing.py`.
Direct WD33C93 register access via HPC3 MMIO, measured in CP0 Count ticks.

### Default Mode

```
TEST RESET_TIMING:    delta=1635                     PASS
TEST REG_WRITE_THRUPUT: delta=1866  iterations=100   PASS
TEST ASR_POLL_THRUPUT:  delta=5267  iterations=1000  PASS
TEST SELECT_TIMEOUT:    status=54                    FAIL
```

### `-icount shift=0,sleep=off`

```
TEST RESET_TIMING:    delta=2                        PASS
TEST REG_WRITE_THRUPUT: delta=36   iterations=100    PASS
TEST ASR_POLL_THRUPUT:  delta=250  iterations=1000   PASS
TEST SELECT_TIMEOUT:    status=54                    FAIL
```

### Comparison

| Metric | Default | icount sleep=off | Ratio |
|--------|---------|-------------------|-------|
| RESET latency (ticks) | 1,635 | 2 | 818x |
| 100 reg writes (ticks) | 1,866 | 36 | 52x |
| Per reg write (ticks) | 18.7 | 0.36 | 52x |
| 1000 ASR polls (ticks) | 5,267 | 250 | 21x |
| Per ASR poll (ticks) | 5.3 | 0.25 | 21x |

### SELECT_TIMEOUT Anomaly

The SELECT_TIMEOUT test fails in both modes: the WD33C93 returns status
54 (0x36) instead of the expected SELECTION_TIMEOUT status 0x42 (66).
Status 0x36 is not a standard WD33C93 status code.

**Possible causes:**
- The bare-metal test may be reading the wrong register for status
- The WD33C93 implementation may encode status differently than expected
- The command phase state machine may produce a different completion path
  when no device is present at the selected target

This is an informative result — documenting actual emulation behavior vs
expected hardware behavior.

### SCSI Throughput Implications

With `sleep=off`, raw MMIO throughput is ~52x faster for writes and ~21x
faster for reads. However, this doesn't directly translate to SCSI command
throughput because:
1. SCSI commands involve multiple register accesses + DMA transfers
2. The DMA engine adds its own overhead (descriptor fetch, memory copy)
3. The guest disk I/O is limited by host filesystem speed

---

## 5. Kernel Boot Performance

### Why `-icount shift=0,sleep=off` Matters

The IRIX kernel idle loop:
```
loop:
    WAIT            # Halt CPU until interrupt
    # PIT timer fires (IP4) → schedule()
    # If no work: loop back to WAIT
```

Without `sleep=off`: Each WAIT waits for real wall-clock time until the
next PIT interrupt (10ms at 100 Hz). Maximum throughput: 100 scheduling
quanta per second.

With `sleep=off`: WAIT returns in 2 CP0 Count ticks. The PIT fires
immediately in virtual time. Throughput: limited only by host CPU speed,
potentially thousands of quanta per second.

### Miniroot Boot Recipe

```
qemu_serial_interact
  extra_args="-icount shift=0,sleep=off"
  scsi_drives=["/workspace/irix_disk.img",
               "IRIX 6.5 Installation Tools June 1998.img:cdrom"]
  boot_wait=15
  interactions=[
    {"expect": "Option", "send": "2\r", "timeout": 5},
    {"expect": "enter.*to start", "send": "\r", "timeout": 15},
    {"expect": "press.*enter", "send": "\r", "timeout": 10},
    {"expect": "c, f, r, or a", "send": "c\r", "timeout": 30}
  ]
  timeout=600  collect_after=300
```

### Boot Timeline (measured, with `-icount shift=0,sleep=off`)

| Phase | Wall-clock | Bottleneck |
|-------|-----------|------------|
| PROM POST + memory probe | <1s | Polling loops |
| Escape countdown | ~30s | Real-time delay |
| SCSI bus probe (disk) | +60s | Selection timeouts |
| SCSI bus probe (disk+CD) | +90s | Selection timeouts |
| System Maintenance Menu | 30.5-120.6s total | — |
| Load sashARCS from CD | ~5s | DMA transfer |
| Load miniroot kernel | ~10s | DMA transfer |
| Kernel init → banner | ~5s | Fast with icount |
| Miniroot init + devices | ~5-10 min | SCSI probing, MAKEDEV |
| IRIX 6.5 installed disk boot | ~45s | icount kernel boot |

---

## 6. SCSI Device Attachment

### Working Syntax

```
-drive if=scsi,file=disk.img,format=raw
-drive if=scsi,file=cd.img,format=raw,media=cdrom,readonly=on
```

This uses `scsi_bus_legacy_handle_cmdline()` which discovers the SCSI bus
created inside the WD33C93 device.

### Non-Working Syntax

```
-device scsi-hd,drive=disk0,scsi-id=1    # FAILS: "No 'SCSI' bus found"
```

The `-device` syntax cannot find the SCSI bus because the WD33C93's bus
(created via `scsi_bus_init()` as a child of an HPC3 sub-device) is not
discoverable by QOM device path. The legacy `-drive if=scsi` syntax uses
a different lookup mechanism that succeeds.

**Impact:** Test files using `-device scsi-hd` syntax will fail at
runtime. Use `-drive if=scsi` syntax instead.

---

## 7. Development Recommendations

### For Fast Iteration (code changes, register debugging)

```
qemu_run_sgi timeout=5 ram_mb=64
```
No SCSI devices, no icount. PROM reaches menu in ~30.5s (dominated by
escape countdown). Good for testing register access, memory probing,
graphics, serial output.

### For PROM-Level SCSI Testing

```
qemu_run_sgi timeout=120 scsi_drives=["disk.img"]
```
Allow 90-120s for full PROM SCSI probe. No benefit from icount here.

### For Kernel-Level Testing

```
qemu_serial_interact
  extra_args="-icount shift=0,sleep=off"
  timeout=600
```
Always use `sleep=off` for kernel testing. Without it, each scheduling
quantum takes 10ms of real time, making kernel boot painfully slow.

### For Debug Tracing

```
qemu_run_sgi debug_flags=unimp timeout=120
```
`-d unimp` adds ~zero overhead (guarded by `qemu_log_mask`). Avoid
file-based `fopen/fprintf` tracing — this was measured to add minutes
of overhead during kernel boot (80,000+ log entries from timer fires).

---

## 8. Known Measurement Caveats

1. **PROM timing is wall-clock bound:** The PROM uses calibrated polling
   loops, not WAIT. No QEMU flag can accelerate these delays.

2. **Selection timeout duration is hardware-defined:** The WD33C93
   TIMEOUT_PERIOD register controls how long the controller waits for a
   target to respond. Each empty target adds ~8 seconds of real-time
   delay during PROM SCSI probe.

3. **32MB RAM is insufficient:** The PROM either enters an infinite loop
   or takes an extremely long time with only 32MB. Use 64MB minimum.

4. **icount affects determinism:** With `sleep=off`, bare-metal timing
   results are perfectly reproducible. Without it, results vary by
   10-15% between runs due to host scheduling effects.

5. **Bare-metal SCSI bench vs real SCSI commands:** The bench measures
   raw register access throughput. Real SCSI commands involve DMA
   descriptor processing, disk I/O, and interrupt handling — they are
   orders of magnitude slower than raw register access.

6. **SELECT_TIMEOUT status mismatch:** The bare-metal SCSI bench
   reports status 54 (0x36) instead of expected 0x42 for selection
   timeout. This needs investigation — it may indicate a bug in the
   WD33C93 status reporting or in the test's register read sequence.

---

## Appendix: Test Coverage

| Test File | Count | Type | What It Measures |
|-----------|-------|------|-----------------|
| `test_cpu_timing.py` | 19 | Slow | CP0 Count, WAIT, PIT period, instruction throughput |
| `test_scsi_timing.py` | 13 | Slow | WD33C93 reset, register throughput, ASR polling |
| `test_scsi_benchmarks.py` | 13 | Slow | PROM boot timing, SCSI probe timing, RAM impact |
| `test_virtual_time.py` | 11 | Fast | Source analysis of icount/WAIT/PIT code paths |
| `test_prom_boot.py` | — | Slow | PROM POST to menu (regression) |
| `test_scsi_prom_irix.py` | — | Slow | PROM SCSI probe + CD boot (regression) |

Benchmark tests emit structured JSON via `helpers/benchmark_reporter.py`:
```
BENCHMARK: {"name": "prom_boot_default", "metrics": {"elapsed_seconds": 1.5, ...}, "timestamp": "..."}
```

Parse with `parse_benchmarks(output)` for automated comparison.
