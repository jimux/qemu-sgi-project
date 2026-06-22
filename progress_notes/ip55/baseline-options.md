# IP55 — Baseline Options, Validation, and Decision

**Date:** 2026-06-20. **Status:** baseline chosen (Indy/IP22); SMP milestone-1 QEMU half landed + verified.

IP55 is a fresh restart of the SGI-in-QEMU desktop effort (IP54 stays in place), applying the `IP54-dev-lessons/` discipline — *validate before committing*. This document records the candidate baselines, what each actually does today, and why we chose Indy/IP22.

## Candidates and current state (empirically/documented)

| Machine | `-M` | Furthest boot today | SMP path | Graphics to Indigo Magic |
|---|---|---|---|---|
| **Indy IP24 / Indigo2 IP22** | `indy` | **Full 4Dwm desktop** | retrofittable (see below) | Newport — working |
| Indigo2 Impact IP28 | `indigo2-r10k` | PROM menu only | none (IRIX UP) | Newport (untested past menu) |
| O2 IP32 | `sgi-o2` | PROM menu; no IRIX kernel | none (IRIX UP) | GBE (incomplete) |
| Octane IP30 | `octane` | HEART+Bridge OK, **stalls — no serial banner** | **native (IRIX MAXCPU=2)** | Impact (not emulated) |
| IP54 PV | `sgi-ip54` | Indigo Magic desktop | retrofit | pvrex3 (works) |
| **Origin IP27 / IP35** | — | **no QEMU machine exists** | native (NUMA) | — |

Notes from validation boots: `-M indy` PROM POSTs and the documented 4Dwm desktop image (`prebuilt_disks/irix-6.5.5-complete-fixed.qcow2`) is the only end-to-end working desktop. `octane` reaches HEART/Bridge but produces no banner because the IOC3 UART is unimplemented. The `sgi-ip55` machine string that already exists is merely an **alias of IP54** (uses `sgi_ip54_class_init`), not a new machine.

## Origin verdict

**We cannot emulate any Origin (IP27/IP35) today — zero QEMU code.** It is *prepped* greenfield: both real PROMs are in hand and analyzed (`PROM_library/bins/cpu/ip27/ip27prom.img`, `ip35/ip35prom.img`), full IRIX SN-architecture kernel source exists (`software_library/irix-657m-source/irix/kern/ml/SN/`), and implementation notes exist (`progress_notes/origin200/`, `progress_notes/ip35/`). But no emulation has been written. Origin is the highest-effort path.

## SMP feasibility — the deciding analysis

- **Indy/IP22 SMP is achievable under QEMU.** Real Indy can't do SMP because its caches are non-coherent with no snoop bus (`ml/IP22.c:2599,2612` `PG_NONCOHRNT`) — but **QEMU TCG models no CPU caches**, so coherency is automatic. The shared IRIX kernel is already MP-aware (`os/tlbmgr.c:719`, scheduler, `hardlocks`); MP is a compile-time switch (`COMPLEX=MP`→`-DMP`, `kcommondefs:590`; IP22 builds `SP`). Only the platform layer is uniprocessor: `ml/IP22.c:117` `MAXCPU 1`, `:3458` `sendintr()=panic`, `IP22asm.s:45` `getcpuid`=0. The port is a bounded ~100-line-asm + ~50-line-C platform-layer change mirroring IP30, plus a small paravirtual IPI device in QEMU.
- **Octane/IP30 is native-MP** (IRIX `MAXCPU=2`, `slave.s`, IPI via HEART) — no kernel port needed — but the machine can't even emit a serial banner yet (IOC3 UART), so it is much further from a working desktop.
- **Indy/IP28/O2 IRIX kernels are uniprocessor**; only IP30 (and Origin) are MP in stock IRIX.

## Decision

**Baseline = Indy / IP22.** It is the only baseline that boots a full desktop today, and SMP is a bounded retrofit under QEMU (no shared-kernel changes). This beats adopting Octane's native-MP kernel that currently can't boot, and vastly beats greenfield Origin.

**Sequencing = SMP first** (user directive). Get 2 CPUs working on `-M indy -smp 2`, re-validate the desktop, then proceed to the other modern goals (PROM debuggability, clock-decoupled timing) and device-by-device paravirtual migration.

## Done so far (Milestone 1a — QEMU half)

`qemu-sgi-repo/hw/mips/sgi_indy.c`: N-CPU creation loop (per-CPU `cpu_mips_irq_init_cpu`/`cpu_mips_clock_init`; secondaries `start_powered_off`); reuse `hw/misc/sgi_smp.c` mapped at `SGI_INDY_SMP_BASE=0x1fa80000` (free hole between MC and HPC3); IPI on `env.irq[6]` (IP6/buserror — a HW line nothing else drives on emulated Indy, avoiding the CP0_Cause software-bit race that bit IP54); `max_cpus=2` on `indy`; SMP device instantiated only when `-smp>1` so UP boots stay byte-identical. **Verified:** `-smp 1` → 1 CPU to PROM (regression intact); `-smp 2` → 2 CPUs (#0,#1) to PROM. Not committed (awaiting request).

## Next

Milestone 1b — the IRIX IP22-MP kernel port (build on `irix-devel`, lboot, graft onto the working desktop disk), validate `hinv`=2 CPUs + desktop. Open packaging decisions: the IP55 sub-module's remote (GitHub vs local) and the eventual distinct machine-type/codename (vs evolving `indy` in place).
