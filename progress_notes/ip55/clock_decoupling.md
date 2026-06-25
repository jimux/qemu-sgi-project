# IP55 clock decoupling — root cause FOUND + FIXED, and the virtualization-native end-state (2026-06-23)

The guest wall clock raced under emulation (double-click rejected, networking RTTs huge, animations too fast). This note records the **resolved** root cause + the pragmatic fix, and frames the real direction: uncoupling IRIX timekeeping from the CPU cycle counter entirely so the modeled frequency becomes a cosmetic label. This is a concrete instance of the CLAUDE.md **virtualization-native, not hardware-authentic** principle.

## ⭐ Phase 1 IMPLEMENTED + VERIFIED (2026-06-24) — wall clock host-sourced, cpufreq cosmetic

The virtualization-native uncoupling is **done and proven** for the wall clock (gettimeofday/`date`/networking/timestamps). It no longer derives from `CP0 Count × cpufreq` — it reads the host real-time clock directly, so the modeled CPU frequency is now cosmetic.

- **QEMU:** added a wrap-free 64-bit host real-time µs counter `MC_REALTIME_CTR64` (`hw/misc/sgi_mc.c`/`.h`, KSEG1 `0xbfa00054` LO / `0xbfa00058` HI, live low/high of `qemu_clock_get_us(QEMU_CLOCK_REALTIME)`; SMP-safe via guest HI/LO/HI re-read). Smoke-tested ~1.0 µs/µs.
- **Kernel:** `ip54_tftp_staging/time_override.c` overrides `nanotime_syscall()` (the function behind gettimeofday/microtime): anchors once to the boot-RTC `time` global, then computes `anchor + (host_us_now − host_us_at_anchor)` from the 64-bit counter. Minimal (only `sys/{types,time,systm}.h`). Linked ahead of `kernel.o` (`ld -n32 -r` first-wins) → `/unix.mp`.
- **DECISIVE ACCEPTANCE TEST PASSED:** at a modeled `IP55_CPU_HZ=800000000` (which without the override races **~11.6×**), the override kernel's `date` advances at **0.988× ≈ 1.0×** real time. The frequency is now irrelevant to the wall clock — exactly the goal.
- Build: produced on the irix-devel MIPSpro host, driven over the **gwagent gdb channel** (reliable; `pyirix_qemu/host_channel.py` `Gateway.run/pull_file`). The override kernel is `ip55_desktop_kernel/unix.mp.rt`; verifier `tmp/ip55-clockdecouple/verify_decouple.py`.

### Side-finding (IMPORTANT): MC GIO-DMA regression broke `machine=indy` PROM boot
Diagnosing why the build host "wouldn't boot" exposed a real QEMU regression: the GIO-DMA `perform_dma()` (from the menu/weave fix) completed synchronously and **cleared RUNNING (0x40) before the guest could observe it**, so the indy PROM's "VDMA Clear" never saw RUNNING → "VDMA Clear failed to start" → UTLB crash before login. `virtuix` was unaffected only because it `-kernel`-boots **past** the PROM. **Fixed in `sgi_mc.c`:** `perform_dma` now keeps RUNNING set (`0x48`), and the `MC_DMA_RUN` *read* clears RUNNING only (COMPLETE persists) — so a consumer observes RUNNING once then COMPLETE, satisfying both the PROM and IRIX `vdma_wait()`. (Re-verify the virtuix desktop weave/menu still render.)

### Still open (Phase 2/3)
The 100 Hz scheduler tick / `sleep`/`select` still derive from the (66 MHz-matched) CP0 timer, so at 800 MHz those still scale (Phase 2 = real-time-driven tick). Userspace `CLOCK_SGI_CYCLE`/UST (RPSS-mmap) is Phase 3. The 66 MHz modeled-clock default remains as belt-and-suspenders for the tick.

## Root cause (RESOLVED)

QEMU modeled the CPU at **100 MHz** (`hw/mips/sgi_indy.c`, `clock_set_hz(cpuclk, 100000000)`), but IRIX's `hinv` **fixed-believes 66 MHz IP22**. CP0 Count advances at `cpu_clock / CCRes` (CCRes=2 → Count rate = cpu_clock/2) in real time, and IRIX derives time-of-day as `Count × NSEC_PER_CYCLE` using its *believed* frequency. So Count advanced at 50 MHz while IRIX expected 33 MHz → time-of-day ran **100/66 ≈ 1.5× fast**. It is purely a modeled-frequency mismatch — not the clock *source*, not boot calibration.

Proof it's the guest's Count→time math, not QEMU's clocks: instrumenting the PS/2 button path showed `QEMU_CLOCK_REALTIME` and `QEMU_CLOCK_VIRTUAL` advance together (244 ms vs 243 ms across a real double-click), so QEMU's clocks track real time; the racing is entirely in IRIX's cycle→seconds conversion.

## The fix (shipped)

`hw/mips/sgi_indy.c`: default `cpuclk` **100 MHz → 66.666 MHz** (matching IRIX's belief), with an `IP55_CPU_HZ` env override for tuning. Measured at `-smp 16` on the **default clock** (no env var, no kernel patch): **1.02×** (was 1.45–2.6×); `hinv` still reports 66 MHz → the belief is *fixed*, not self-calibrating, so the match is stable. The CPU still executes flat-out (TCG ignores the modeled clock); only timekeeping is corrected — exactly "fast CPU, real clock." Confirmed live: double-click launches apps at normal human speed.

## How we got here (superseded approaches, kept as flag-gated foundations)

- `QEMU_MIPS_COUNT_REALTIME=1` (env-gated, `target/mips/system/cp0_timer.c`) routes CP0 Count off `QEMU_CLOCK_REALTIME`. Alone it took racing 2.6×→~1.45×, i.e. it removed a minor virtual-clock component but left the dominant ~1.5× — which we now know was the frequency mismatch. Still in the tree, still useful (immune to pause/resume/migration), but **not needed** for the basic fix.
- RPSS-coherence (`mc_timebase_clock()` in `hw/misc/sgi_mc.c`, routing the RPSS counter onto the same flag-selected clock) — implemented on the hypothesis that boot cpufreq calibration was incoherent. Re-measured: **still 1.45×, no change → calibration was NOT the cause.** Kept (correct-in-principle, flag-gated, default unchanged) as a foundation, but it was a red herring for this symptom. The actual lever was the modeled frequency.

## The real direction — virtualization-native timekeeping ("clock-locked binaries")

The 66 MHz match is the **zero-RE pragmatic win**: it makes today's cycle-derived time math come out right by matching the modeled rate to the believed rate. It does not *remove* the dependency — it satisfies it. The end-state (per the CLAUDE.md guiding principle) is to **uncouple IRIX timekeeping from `Count × cpufreq` entirely**, so the CPU "frequency" is a pure cosmetic `hinv` label with no effect on correctness — at which point you could set `hinv` to 800 MHz / 2 GHz / anything and the wall clock stays locked to the host.

RE/rebuild targets (the "clock-locked binaries", via the override-object + `binary_re` pipelines):
1. **Kernel time-of-day** — `nanotime_syscall` / `__nanotime` / the clock ISR (`ml/clksupport.c`, `ml/timer_r4000.c`, `os/clock.c`): source nanoseconds from a host real-time register instead of `Count`. Phase-2 override-object pattern (`dopatch.sh` links replacements ahead of `os.a`).
2. **Boot cpufreq calibration** (`findcpufreq` / the RPSS dance): neuter it — `cpufreq` becomes a hardcoded cosmetic constant.
3. **The 100 Hz tick**: drive it off a real-time source (IP54 pvclock pattern) so the scheduler tick is real-time-locked independent of Count.
4. **Userspace UST fast-path** (the hard one): IRIX exposes a userland-readable clock (shared page + cpufreq factor) that libc uses without a syscall; full uncoupling means backing that page with host real time too.

Building blocks already in place: `MC_REALTIME_CTR` (host wall-clock register at KSEG1 `0xbfa00050`, `hw/misc/sgi_mc.c`), the IP54 pvtimer/pvclock device, the `lboot`/`dopatch.sh` override-object link mechanism, and the `binary_re` decompile→rebuild→differential-verify pipeline. Beyond cleanliness, host-real-time-sourced time is also immune to virtual-clock subtleties (pause/resume, snapshots, migration, any future `icount`) that the cycle-matched approach technically still rides.

## Status
- **Fixed + shipped:** modeled-frequency match (`sgi_indy.c`/`sgi_virtuix.c`, default 66.67 MHz). Double-click works; clock ~1.02× at `-smp 16` default.
- **Open (the real effort):** the virtualization-native uncoupling above — make `cpufreq` cosmetic. Tracked under the CLAUDE.md principle; this is where the timing work goes next, not the env-var/RPSS directions (superseded).

## 2026-06-24 update — tick *delivery* now host-locked; canonical desktop kernel lacks the Phase-1 graft

Two QEMU commits locked HZ-tick **delivery** to the host clock (smoothness), beyond the cycle-match:
1. `sgi_virtuix.c` defaults `QEMU_MIPS_COUNT_REALTIME=1` → CP0 Count/Compare ride `QEMU_CLOCK_REALTIME` not `QEMU_CLOCK_VIRTUAL` (which bursts under MTTCG → jittery tick → idle UI stalls). Commit qemu-sgi-repo `732623c33c`.
2. `cpu_mips_timer_catchup()` (`cp0_timer.c`, wired into `mips_cpu_has_work()`) delivers an already-expired tick from the **vCPU** thread, so it isn't gated on a BQL-starved main loop during MMIO/TLB-shootdown-heavy activity (the `-smp 8` window-drag stall). Commit `ab95df1e`. Both realtime-gated (virtuix only); indy untouched. Verified: idle+drag `sar -u 1 25` shows even 1 s ticks, 0 stalls (was 5-6 s gaps).

These fix *when* the tick fires, not the *rate*. ⚠️ Crucially, **`unix.ip55.g` (the canonical desktop kernel) does NOT carry the Phase-1 `time_override.c`** — that host-sourced-gettimeofday graft lives only in the separate `unix.mp.rt`. The board fork came off the SMP-patched IP22.c lineage, not the `.rt` one. **Acceptance test on `unix.ip55.g` at `IP55_CPU_HZ=800000000`: FAIL — `date` advanced 46 guest-s over 22 host-s = 2.09× race** (hinv showed "368 MHZ"). So the desktop kernel's correct timing still depends entirely on the default cpuclk (66.67 MHz) matching the guest's believed frequency — a cycle-*match*, not a decoupling.

**To finish cosmetic-cpufreq for the desktop kernel (deferred — needs a build-host relink):** graft `time_override.o` ahead of `kernel.o` in the `unix.ip55.g` link (same `ld -n32 -r` first-wins trick that produced `unix.mp.rt`), giving host-sourced gettimeofday at any frequency; then Phase-2 the tick + Phase-3 the userspace UST page. **Functional status: timing on the default desktop is correct + smooth (requirement met); arbitrary-frequency cosmetic cpufreq is principle-completion, not a foundational blocker — keep `IP55_CPU_HZ` at its 66.67 MHz default.**
