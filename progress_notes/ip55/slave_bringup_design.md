# IP22-MP slave bringup — implementation design (milestone 1b)

State entering this phase: the `COMPLEX=MP` IP22 kernel (`/unix.mp`) builds and boots to a multi-user root shell on `-M virtuix -smp 2`, but runs uniprocessor — `IP22.c` still has `MAXCPU 1`, `getcpuid`→0, `sendintr`→panic, and `allowboot` never starts a slave. cpu1 exists in QEMU (powered-off). This doc specifies the port to bring cpu1 up.

The shared IRIX kernel is already MP-aware; `os/main.c:483` calls `allowboot()` (the platform hook) to release slaves. We mirror IP30's `ml/RACER/IP30.c` MP layer, but substitute the **`sgi-smp` BOOT_GO** mechanism (proven in `virtuix/tests/smp_bringup/smp_test.S`) for IP30's MPCONF/PROM-spin-loop model — on virtuix the secondary starts powered-off and QEMU jumps it to `boot_addr` when BOOT_GO is written, so there's no PROM slave loop to coordinate with.

## sgi-smp device interface (QEMU `hw/misc/sgi_smp.c`, base `SGI_INDY_SMP_BASE = 0x1fa80000`, 64-bit-aligned regs)

- `CPU_COUNT` (ro) — number of CPUs.
- `CPU_ID` (ro) — returns `current_cpu->cpu_index`; **`getcpuid` reads this**.
- `IPI_STATUS` (ro) — pending IPI for the reading CPU.
- `IPI_SET` (wo, value=target cpu) — raise IPI to target → its `env.irq[6]` (IP6). **`sendintr` writes this.**
- `IPI_CLEAR` (wo) — ack/lower this CPU's IPI.
- `BOOT_ADDR` (wo) — set the slave entry address.
- `BOOT_GO` (wo, value=target cpu) — release that powered-off secondary to `boot_addr` (PC sign-extended in QEMU).

(Exact offsets in `qemu-sgi-repo/include/hw/misc/sgi_smp.h`; access pattern already validated by `smp_test.S`.)

## Kernel changes (all via `dopatch.sh`, fresh-stage build)

1. **`IP22.c:117` `#define MAXCPU 1` → `2`** — sizes `maxcpus`, `pdaindr[MAXCPU]`, and the per-CPU arrays. Verify no other `[1]`-sized per-CPU array in IP22.c assumes UP.

2. **`IP22asm.s:45` real `getcpuid`** — currently `XLEAF(getcpuid)` aliasing `dummyret0_func` (→0). Replace with a read of the CPU_ID reg:
   `.set noreorder; li t0, 0xb fa80000|CPU_ID (KSEG1); lw v0,0(t0); j ra; nop; .set reorder` — KSEG1 (uncached) `0xb...` of phys `0x1fa80000+CPU_ID`. (Same address `smp_test.S` used.) C0-style read not needed; it's an MMIO load.

3. **`IP22.c:3458` `sendintr(cpuid_t destid, unchar status)`** — replace `panic` with: write `destid` to `IPI_SET` (KSEG1 MMIO). The target takes IP6; its ISR acks via `IPI_CLEAR`. Wire an IP6 handler (the `env.irq[6]` line) — IP22 vector table currently has no IP6 user; add an IPI dispatch that calls the shared `cpuintr()`/`doacvec` path.

4. **`IP22.c:4450` `allowboot`** — the headline. Mirror IP30:181–700 but with BOOT_GO. Sketch:
   ```c
   allowboot(void) {
       init_timebase();
       for (i = 1; i < maxcpus; i++) {          /* i=0 = master */
           if (!cpu_enabled(i)) continue;
           setup per-cpu pda[i], stack, kpda;   /* mirror IP30 dobootduty */
           *(volatile uint*)KSEG1(SMP_BOOT_ADDR) = (uint)slave_entry;
           *(volatile uint*)KSEG1(SMP_BOOT_GO)   = i;       /* release cpu i */
           /* wait for the slave to check in */
           timeout = BOOTTIMEOUT;
           while (--timeout && !slave_loop_ready[i]) us_delay(1000);
           if (slave_loop_ready[i]) slave_cpus++;
       }
   }
   ```
   Need a `slave_loop_ready[MAXCPU]` array (mirror IP30.c:106) the slave sets once it's in the kernel slave loop.

5. **`slave_entry` (new asm, in IP22asm.s or a new .s)** — the BOOT_GO target. The slave wakes in the boot/reset region; set up: CP0 Status (KX, IE off, IM for IP6), TLB-wired entries / page size to match the master, the slave's kernel stack + `pda` (from a master-prepared per-cpu block), `$gp`, then call the C slave init. Mirror IP30 `bootstrap`/`slave.s`. **This is the gdb-iterative part** — the slave's early MIPS state (SR/TLB/stack) is the most error-prone; debug with the QEMU gdbstub on cpu1.

6. **C slave init path** — `mlreset(is_slave=1)` already exists in the shared/IP30 pattern (`IP30.c:228 mlreset(int is_slave)`); IP22.c's `mlreset` is UP-only — add an `is_slave` branch: per-CPU clock/cache/intr init (`heart_mp_intr_init` equivalent → enable IP6 IPI), set `slave_loop_ready[cpuid()]=1`, then enter the scheduler slave loop (`cboot`/the shared `slave_loop`).

7. **Per-CPU IP6 IPI enable** — unmask IP6 (`env.irq[6]`) on each CPU's Status IM so `sendintr` IPIs are delivered.

## Test loop

`-M virtuix -smp 2` (serial fixed; `sgi_virtuix_nvram.bin` console=d). Boot `/unix.mp`; watch for the slave checking in. Validate `hinv` / `/sbin/sysinfo` → 2 processors, both schedulable (a 2-thread CPU-bound test uses both). Debug the slave's early execution with `gdb-multiarch` on the QEMU gdbstub (per the [[guest_gdb_ip54]] recipe: `set mips abi n64`), targeting cpu1; symbol file = `vm_instances/unix.mp` (not stripped). Expect several iterations on the `slave_entry` SR/TLB/stack setup.

## Stage A DONE; Stage B mechanism (what actually exists in the tree)

**Stage A (done):** `IP22.c:allowboot`→`virtuix_start_slaves()` writes sgi-smp BOOT_ADDR/BOOT_GO; cpu1 runs `IP22asm.s:virtuix_slave_entry` (KSEG0-direct marker+spin) → `marker=0x5a5e0001` confirmed. The kernel-driven start mechanism works.

**Stage B (cpu1 schedules → hinv=2) — the real remaining port.** Key finding from the source: the slave scheduler-join loop is **platform-specific and absent for IP22**:
- `slave_loop`/`start_slave_loop` exist only in `ml/EVEREST/evslave.s` (MPCONF/`MP_VIRTID` model) and `ml/SN/slave.s`. IP30 uses a different model — `ml/RACER/IP30asm.s:bootstrap`→`ml/RACER/IP30.c:cboot`.
- `csu.s:188` has a "We're a slave → `LA k0,slave_loop; jr k0`" branch, but it's `#if defined(SABLE)&&defined(SN)`; and `csu.s:281 jal start_slave_loop` is `#if EVEREST||(IP30&&MP)`. **IP22 gets neither** — IP22's `start` is master-only.

So Stage B must ADD an IP22 slave path (adapt IP30's `bootstrap`+`cboot`, which is closest — no MPCONF, fits the BOOT_GO model):
1. `virtuix_slave_entry` (replace the spin): mirror `IP30asm.s:bootstrap` — `MTC0(SR_KADDR-equiv, C0_SR)` (IP22 kernel SR: `SR_DEFAULT` then `SR_KERN_SET`, per csu.s:131/142), `LA gp,_gp`, set `C0_PGMASK`/`C0_TLBWIRED`, then **set up the per-CPU pda as a wired TLB entry**: `getcpuid` (real, CPU_ID reg) → index `pdaindr[id].pda` → `tlbwired(PDATLBINDEX, 0, PDAPAGE, pte)` (the per-CPU pda mech is `os/pda.c:699-717`), set `sp` from the pda boot stack, `j cboot`.
2. `cboot` (new, mirror `IP30.c:721`): the C slave entry — `mlreset(is_slave=1)` (IP22.c:1024 `mlreset(int junk)`; add the slave branch: per-CPU clock/cache/intr init, enable IP6 IPI), mark cpu alive, set `slave_loop_ready[id]`, enter the scheduler idle/slave loop.
3. Master side (`allowboot`/new `dobootduty` mirror `IP30.c:478,571`): for cpu1 — allocate its pda + boot stack, `pdaindr[1].pda=...`, BOOT_ADDR=`virtuix_slave_entry`, BOOT_GO=2, wait on `slave_loop_ready[1]`.
4. `MAXCPU 2`, real `getcpuid`/`sendintr`, per-CPU IP6 unmask.

The pda/wired-TLB + the slave's first C call (using `private`) are the **gdb-iterative** crux — debug the secondary with the QEMU gdbstub (`set mips abi n64`, symbol file `vm_instances/unix.mp`, target cpu1) since SR/TLB/stack errors fault silently. This is a deliberate multi-build/gdb effort, not a one-shot.

## Stage B Increment 2 — concrete anchored port (cpu1 → scheduler, hinv=2)

Research 2026-06-21 confirmed EVERY dependency for a faithful `cboot` port exists for IP22 (mirrors `ml/RACER/IP30.c:cboot` 721-819):

**Slave side — `virtuix_cboot` (replace the Inc1 spin), mirroring IP30 cboot:**
- `id=getcpuid(); wirepda(pdaindr[id].pda);` (Inc1, keep)
- **SKIP `mlreset(1)`** — IP22 `mlreset(int junk)` (IP22.c:1024) is master-global (junk unused); replicate only the per-CPU subset inline instead.
- `private.p_cpuid=id; CPUMASK_CLRALL(p_cpumask); CPUMASK_SETB(p_cpumask,id);`
- `private.common_excnorm=get_except_norm();` (IP22.c — exists) `private.p_kvfault=0;`
- copy `cpufreq/cpufreq_cycles/decinsperloop` from `pdaindr[master_procid].pda` (`master_procid` = os/pda.c:116, =0)
- `coproc_find();` (os/machdep.c)
- `s0=splock(bootlck);` (`bootlck` = os/pda.c:97, shared) then `cb_wait=id; while(cb_wait!=-1);`
- `private.p_flags|=(PDAF_ENABLED|PDAF_ISOLATED); numcpus++; CPUMASK_SETB(maskcpus,id); pdaindr[id].CpuId=id;`
- `stopclocks();` (ml/timer.c) `spinlock_init(&pdaindr[id].pda->p_special,"pdaS"); tlbinfo_init();` (os/tlbmgr.c) `clkstart();` (os/machdep.c) `spunlock(bootlck,s0);`
- `private.p_flags&=~PDAF_ISOLATED;`
- `add_cpuboard();` (IP22.c:4564 — adds INV_PROCESSOR/INV_CPUCHIP for `cpuid()` → **this is what makes hinv show cpu1**)
- `joinrunq();` (os/scheduler/runq.c:105) `init_mfhi_war();` (os/machdep.c)
- `spl0(); (void)splhi(); resumeidle(0);` (ml/process.s:544) — NOTREACHED. resumeidle's idle stack = `VPDA_LBOOTSTACK` = `offsetof(pda_t,p_bootlastframe)` (genassym.c:669), which **`alloc_cpupda(1)` already sets** (os/pda.c:83-84). So the idle stack is handled.

**Master side — `virtuix_start_slaves` (in allowboot ctx, may sleep):**
- `alloc_cpupda(1);` (sets cpu1 pda boot/idle stack p_bootstack/p_bootlastframe)
- set `pdaindr[1].pda->p_nodeid=0;`
- `BOOT_ADDR=virtuix_slave_entry; BOOT_GO=2;`
- service the handshake + wait: `for(;;){ if(cb_wait!=-1) dobootduty_local(); if(pdaindr[1].CpuId==1) break; DELAY(1);} ` with a timeout.
- replicate IP30 `dobootduty` (IP30.c:478-543) inline as `dobootduty_local`: allocate `pda->p_intstack=kvpalloc(btoc(intstacksize),VM_DIRECT,0)`, `p_intlastframe`, `ksaptr=kern_calloc_node(1,sizeof(struct ksa),node)`, `knaptr`, `kstr_lfvecp=str_init_alloc_lfvec(cpuid)`, `kstr_statsp=str_init_alloc_strstat(cpuid)`, `str_init_slave(...)`, then `cb_wait=-1`.

**IPI / cross-CPU (so cpu1 doesn't hang the master on the first flushcaches/TLB-shootdown after enable):**
- `sendintr(destid,status)` (IP22.c:3458 panic) → write `destid` to sgi-smp `IPI_SET` (KSEG1 0x1fa80010). cf IP30 `sendintr` writes `heart_piu->h_set_isr=HEART_IMR_IPI_(destid)`.
- sgi-smp raises target `env.irq[6]` = **IP6** = `CAUSE_IP6`. IP22 dispatches via `c0vec_tbl[6]` (IP22.c:195,1535). IP6 on real Indy is the kgmon profiling clock (`setkgvector`, IP22.c:1257) — repurpose it (profiling off by default); register an IPI ISR at `c0vec_tbl[6].isr` that acks via sgi-smp `IPI_CLEAR` then runs the shared cpuaction processor (the fn IP30's IPI ISR calls — verify in os/machdep.c `cpuaction`/doaction path).
- Enable IP6 in each CPU's SR IM (master + slave) so IPIs deliver.

**Increment ordering:** do the full cboot in one build (v37) — joinrunq without resumeidle is inconsistent (dispatcher would target a non-idle cpu1). Iterate faults with the QEMU gdbstub on cpu1 (`set mips abi n64`, syms=`vm_instances/unix.mp`) + monitor PC-sampling. Expect several iterations on SR/stack/handshake.

## Risk / fallback

This is the genuinely-hard, untested part (the plan flagged it). If the slave's early init proves intractable on IP22 specifics, the fallback is Octane/IP30 (ships MP objects — MP "for free") but it needs its IOC3 UART implemented first. Given the IP22 MP kernel now boots cleanly UP, finishing the slave bringup here is the shorter path.
