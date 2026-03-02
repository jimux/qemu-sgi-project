# Teaching an Emulated SGI Indy the Difference Between Fast and Real

## The Problem: You Can't Be Fast *and* Correct

When you're emulating a 1994 SGI Indy workstation in QEMU, booting IRIX 6.5 takes forever by modern standards. The MIPS R4000 CPU spends most of its time in the `WAIT` instruction (the idle loop) while the kernel waits for disk I/O, timer interrupts, and device probes. By default, QEMU faithfully models wall-clock time during these idle periods: if the real hardware would wait 10 milliseconds for a disk response, QEMU waits 10 milliseconds too.

QEMU offers a flag to disable loyalty: `-icount shift=0,sleep=off`. Instead of sleeping during `WAIT`, virtual time advances instantly. The kernel's scheduling clock (driven by the R4000's CP0 Count/Compare timer) fires as fast as the host CPU can process each quantum. Boot times drop from minutes to seconds. The IRIX installer, which can takes hours, completes in minutes.

There's just one catch: **everything that cares about real time breaks.**

Ping replies come back with round-trip times of 47,000 milliseconds. TCP retransmit timers fire before packets reach the network stack. `sleep(3)` returns in a fraction of a real-world second. Animations play at warp speed. The system clock in the corner of the desktop drives at 88 miles-per-hour.

The `icount` setting gives us a choice. We can be fast and wrong, or slow and accurate. At the most impactful level, we can be fast with broken networking or slow and live on an island.

The root cause is a single kernel variable: `lbolt`. It increments once per scheduling tick, 100 times per second on real hardware, but thousands of times per second under `sleep=off`. Nearly every timing-sensitive path in the IRIX kernel derives its sense of elapsed time from `lbolt`. And looking at [the source for IRIX 6.5.5](https://vetusware.com/download/IRIX%20source%20code%206.5.5/?id=13477) `lbolt` has **448 references across 113 source files**.

We could slow `lbolt` down. But then we'd lose the boot speedup. The whole point of `sleep=off` is that `lbolt` races.

## Two Clocks for Two Purposes

How to fix this? First we need to think about the actual conflict. What we essentially have two classes of needs for a clock. One need real-time and the other doesn't.

The kernel scheduler, disk I/O timeouts, and SCSI probes can keep using `lbolt`. They *should* race. That's what makes boot fast. But user-visible operations like `gettimeofday()`, `ping` round-trip measurement, `select()` timeouts, and TCP retransmit timers all need a clock that tracks real wall-clock time.

QEMU already has such a clock internally: `QEMU_CLOCK_REALTIME`, which tracks the host's actual wall clock regardless of what virtual time is doing. We just needed to expose it to the guest.

```
lbolt (unchanged)       -> scheduler, disk I/O, SCSI (fast -- good)
QEMU_CLOCK_REALTIME     -> gettimeofday, ping, networking (real time -- correct)
DS1386 RTC              -> boot-time epoch (already real time)
```

## Getting Started: Adding the Register to QEMU

The SGI Indy's Memory Controller (MC) chip lives at physical address `0x1fa00000` and provides various system registers. I added a new one at offset `0x0050`:

```c
#define MC_REALTIME_CTR  0x0050

case MC_REALTIME_CTR:
    val = (uint32_t)(qemu_clock_get_us(QEMU_CLOCK_REALTIME) & 0xFFFFFFFF);
    break;
```

A single 32-bit register returning microseconds of real wall-clock time. It wraps every ~71 minutes, but uint32_t subtraction handles that correctly. From the IRIX kernel's perspective, it's just another memory-mapped counter at `0xbfa00050` (the KSEG1 uncached mapping of the MC's physical address).

Simple enough.

## Side-Quest: Compiling a Kernel Inside the Emulator

IRIX is a proprietary operating system from agest past. There is no cross-compiler. To build a kernel module, you need MIPSpro (SGI's own compiler suite) running on IRIX itself. Which means we need to compile our kernel patch *inside the emulator we're trying to fix*. Nothing *too* crazy, we just need to be sure to gather everything we need into the instance.

### Getting the Compiler

For IRIX 6.5, MIPSpro is distributed across multiple CDs with a layered package system: base packages on one disc, maintenance overlays on another, and version dependencies between them that the IRIX `inst` package manager enforces rigidly.

An combo-image of the base install and some dev tools I put together had the updates but not the base packages. The All-Compiler CD had the base packages but was in an EFS filesystem format that IRIX refused to mount, despite our own tools being able to read it perfectly on the host.

The solution was creative plumbing:

1. Extract the base MIPSpro distribution files from the All-Compiler CD image using our host-side Python EFS reader
2. Build a fresh EFS filesystem image using `tar2efs.py`
3. Prepend an SGI volume header (a custom 32KB binary structure with partition tables and magic numbers that IRIX requires before it will acknowledge a disk exists)
4. Attach three SCSI disks to the VM simultaneously: the boot disk (ID 1), the combo disc (ID 2), and our freshly-built base packages image (ID 3)
5. Run `inst` pointing at both sources to resolve the dependency chain

One complication: the MCP server's instance management has a subtle bug where providing explicit `scsi_drives` alongside an `instance` parameter causes the instance disk to be silently dropped instead of added. For now, I worked around this by explicitly including the instance disk as the first entry.

### A Minor Sub-Side-Quest: The License Nag

MIPSpro checks for a FLEXlm license on every invocation. Without a license server (which long since no longer exists, SGI went effectively defunct in 2009), it prints a 20-line warning to stderr before proceeding with compilation anyway. Thankfully [Laurent Chardon wrote a shell wrapper to squelch the nag](https://github.com/LaurentChardon/mipspro), though in practice the compiler works fine with it, the warnings are cosmetic.

### Missing Kernel Headers

The installed system headers don't include everything the kernel source needs. Two files required special attention:

- **`sys/prf.h`**: Declares `prfintr()`, the profiling interrupt handler. Our first attempt used a two-argument prototype (`void *, void *`). The compiler informed us with three errors and remarkable specificity that the actual call site passes four `k_machreg_t` arguments. Fixed.

- **`sys/runq.h`**: Provides `cpu_cookie_t` and `restoremustrun()`. I wrote a minimal version with the correct MP/non-MP conditional typedefs.

### Uploading Over Serial

The VM has no network file sharing and no USB. Everything goes through the serial console. I wrote a tool that splits files into batches of 25 lines, sends each batch as `printf` commands, and uses sentinel echoes to verify delivery. `timer.c` was 1,176 lines, which was 48 batches.

One gotcha: IRIX's default shell is `csh`, which interprets backticks inside heredocs even when quoted. I learned to `exec sh` before any file transfer operations.

## Getting Down to Business: The Patch

The actual kernel modification is remarkably small: three changes to a single file (`ml/timer.c`):

### 1. The Real-Time Epoch

```c
volatile uint32_t *qemu_rt_ctr = (volatile uint32_t *)0xbfa00050;
static time_t   qemu_rt_epoch_sec = 0;
static uint32_t qemu_rt_epoch_us  = 0;

void qemu_rt_init_epoch(time_t rtc_sec)
{
    qemu_rt_epoch_us  = *qemu_rt_ctr;
    qemu_rt_epoch_sec = rtc_sec;
}
```

Three global variables and a four-line function. `qemu_rt_epoch_sec` records what Unix timestamp the DS1386 RTC reported at boot. `qemu_rt_epoch_us` records what the real-time counter read at that same moment. From these two values, we can reconstruct the current wall-clock time at any point: `epoch_sec + (current_counter - epoch_counter) microseconds`.

### 2. The Fast Path in `nanotime_syscall()`

```c
void nanotime_syscall(timespec_t *tvp)
{
    /* ... existing variable declarations ... */
    uint32_t rt_now, rt_elapsed;
    uint64_t rt_abs_us;

    if (qemu_rt_epoch_sec != 0) {
        rt_now     = *qemu_rt_ctr;
        rt_elapsed = rt_now - qemu_rt_epoch_us;
        rt_abs_us  = (uint64_t)qemu_rt_epoch_sec * 1000000ULL + rt_elapsed;
        tvp->tv_sec  = (time_t)(rt_abs_us / 1000000ULL);
        tvp->tv_nsec = (long)((rt_abs_us % 1000000ULL) * 1000ULL);
        return;
    }
    /* ... original hrestime interpolation code unchanged ... */
}
```

`nanotime_syscall()` is the function behind `gettimeofday()`. Every call to `date`, every timestamp on a ping reply, every `select()` timeout calculation flows through here. The fast path is eight lines: read the counter, compute elapsed microseconds since boot, add the boot epoch, convert to seconds and nanoseconds. If the epoch hasn't been initialized (shouldn't be impossible in practice, but defensive), fall through to the original code.

### 3. Self-Calibration via `settime()`

```c
void settime(long sec, long usec)
{
    /* ... */
    if (sec > 0) {
        qemu_rt_epoch_us  = *qemu_rt_ctr;
        qemu_rt_epoch_sec = (time_t)sec;
    }
    /* ... original code unchanged ... */
}
```

`settime()` is called by `inittodr()` during boot when the kernel reads the DS1386 real-time clock chip to establish wall-clock time. By hooking here instead of patching `inittodr()` in the platform-specific `IP22.c`, we avoid touching a second file and get automatic recalibration whenever anything sets the system clock.

## Build and Install

Compilation:
```
cc -c -n32 -mips3 -O2 -G 8 -non_shared -TENV:kernel \
   -DIP22 -DR4000PC -DTRITON -D_KERNEL \
   -D_MIPS_SIM=_ABIN32 -D_PAGESZ=4096 \
   -I/usr/include timer.c -o timer.o
```

Every flag matters. `-n32` selects the new 32-bit ABI (not o32, not 64-bit). `-G 8` sets the GP-relative addressing threshold to match the kernel's. `-non_shared` avoids PIC relocations that would cause an ELF `e_flags` mismatch during linking. `-TENV:kernel` tells the compiler this is kernel code.

Linking uses the relocatable merge trick: `ld -r` combines our new `timer.o` with the existing monolithic `kernel.o`, with our symbols taking priority:

```
ld -n32 -r -o kernel_new.o timer.o kernel.o
```

The linker emits 48 "Multiply defined (2nd definition ignored)" warnings. One for every function and global variable in timer.c. This is exactly correct: our definitions win.

The new `kernel.o` goes into `/var/sysgen/boot/`, and `lboot` (IRIX's kernel configurator) links it against all the device drivers and produces a new `/unix`. Backup the old kernel, install the new one, `sync`, and `init 6` for a clean reboot (For some reason calling `reboot` was causing XFS panics.)

## The Results

```
# ping -c 5 10.0.2.2
PING 10.0.2.2 (10.0.2.2): 56 data bytes
64 bytes from 10.0.2.2: icmp_seq=0 ttl=255 time=1.614 ms
64 bytes from 10.0.2.2: icmp_seq=1 ttl=255 time=1.220 ms
64 bytes from 10.0.2.2: icmp_seq=2 ttl=255 time=0.838 ms
64 bytes from 10.0.2.2: icmp_seq=3 ttl=255 time=0.888 ms
64 bytes from 10.0.2.2: icmp_seq=4 ttl=255 time=0.936 ms

----10.0.2.2 PING Statistics----
5 packets transmitted, 5 packets received, 0.0% packet loss
round-trip min/avg/max = 0.838/1.099/1.614 ms
```

Five packets, zero loss, sub-2ms round trips. On an unpatched kernel with `sleep=off`, these would be tens of thousands of milliseconds, if the packets arrived at all.

`gettimeofday()` now returns real wall-clock time. The `date` command advances at one second per second. Boot is still fast because `lbolt` still races. The 448 references to `lbolt` across 113 kernel files remain untouched.

## What's Left (After Phase 1)

This patch fixes `gettimeofday()` and everything that calls it, which covers `ping` timestamps and `date`. But several timing subsystems still derive from `lbolt`:

- **`sleep()`/`nanosleep()`**: The callout queue that wakes sleeping processes is tick-driven. `sleep 10` completes in roughly 14 real seconds instead of 10, because each of the 1,000 required ticks takes about 14ms of real host time to process.
- **TCP/UDP retransmit timers**: The `sockd` kernel thread's `periodic_timeouts()` loop uses `delay()` which is lbolt-based.
- **`select()` timeouts**: `dopoll()` converts timevals to tick counts via `hzto()`.

Each of these could be patched with the same approach: read `QEMU_REALTIME_US()` instead of relying on lbolt-derived ticks. The plan identifies the exact functions and files. But the current patch already makes the system usable for interactive work. You can telnet in, run commands, and the network behaves like a real network.

## Fixing the Remaining Timing Paths

The same real-time gate technique extends naturally to the remaining paths. Two more standalone patch files, each compiled and linked ahead of `kernel.o` in the same `ld -r` merge:

### `nano_delay()` - Every Kernel `delay()` Call

`nano_delay()` in `os/clock.c` is the single choke point for all kernel delay operations, including the `sockd` thread that drives TCP/UDP timers via `periodic_timeouts()`. The patch is the same gate loop as before, but using `kt_timedwait` instead of `ut_timedsleepsig`:

```c
void
nano_delay(timespec_t *ts)
{
    uint32_t rt_start, rt_now, rt_elapsed_us, rt_target_us;
    timespec_t rem;

    if (ts->tv_sec == 0 && ts->tv_nsec == 0)
        return;

    /* Cap at ~35 min */
    if (ts->tv_sec > 2000) {
        kthread_t *kt = curthreadp;
        int s = kt_lock(kt);
        kt_timedwait(kt, 0, s, 1, ts, NULL);
        return;
    }

    rt_target_us = (uint32_t)(ts->tv_sec * 1000000)
                 + (uint32_t)(ts->tv_nsec / 1000);
    if (rt_target_us == 0)
        rt_target_us = 1;
    rt_start = *qemu_rt_ctr;

    /* Sleep 1 virtual tick at a time; real-time check controls duration */
    rem.tv_sec = 0;
    rem.tv_nsec = 10000000;  /* 1 tick = 10ms virtual */

    for (;;) {
        kthread_t *kt = curthreadp;
        int s = kt_lock(kt);
        kt_timedwait(kt, 0, s, 1, &rem, NULL);

        rt_now = *qemu_rt_ctr;
        rt_elapsed_us = rt_now - rt_start;
        if (rt_elapsed_us >= rt_target_us)
            break;
    }
}
```

Fixing this one function automatically fixes `periodic_timeouts()`: `sockd()` calls `delay(HZ/5)`, which calls `nano_delay()`, which now truly sleeps 200ms of real time. The TCP fasttimo/slowtimo timers accumulate correct real elapsed time as a result. No changes to `bsd/net/netisr.c` needed.

### A Critical Subtlety: Why 1 Tick?

The loop above deserves explanation. The natural-seeming approach of sleeping for the *remaining* real time, converted to a virtual-time delay, leads to catastrophic overshoot.

Under `-icount shift=0,sleep=off`, QEMU executes MIPS instructions at maximum host CPU speed. The kernel's tick rate (100Hz = one 10ms tick) doesn't translate to 10ms of real time. Each virtual tick carries a fixed weight in emulated instruction cycles, but the real host time to process those cycles depends on how much actual kernel work happens per tick: interrupt dispatch, context switches, scheduler decisions, cache behavior. In practice, one virtual 10ms tick takes roughly 85ms of real host time.

The first version of this loop used `rem = *ts`. Sleeping for the full requested delay as a virtual-time target. A `sleep(3)` requests a 3-second = 3,000,000 µs delay. Under icount, those 3,000,000 virtual microseconds span about 300 virtual ticks. At ~85ms real per tick: 25.5 real seconds for a nominal 3-second sleep. An 8.5× overshoot.

This wasn't immediately obvious. The ratio is host-load-dependent and doesn't announce itself. It only became clear after testing: `time sleep 3` came back as `0:25`, and `time select(3s)` matched exactly. Both timing paths, same ratio, same bug.

The fix: always sleep *one virtual tick* (10ms nominal) regardless of how much real time remains. The `qemu_rt_ctr` check controls when the loop exits, not the virtual timer. The loop becomes a real-time poll: sleep one tick (~85ms real), check the wall clock, either exit or repeat. A 3-second sleep requires about 35 iterations of ~85ms each. Close enough.

The same 1-tick principle applies to `nanosleep_common()` and, as we'll see, `dopoll()`.

### `nanosleep_common()` - User-Space Sleep Calls

`sleep()` and `nanosleep()` bypass `nano_delay()` entirely. They call `ut_timedsleepsig()` → `sv_bitlock_timedwait_sig()` → `sv_set_timeout()` → `itimeout_nothrd()` — a completely separate code path through the scheduler sync variable machinery. It needs its own gate loop.

One complication: the `ksys/vproc.h` header that `ptimers.c` normally includes isn't installed on a running IRIX system (it's a kernel-build-time header). The solution is to pull in `sys/uthread.h` instead, which provides `curuthread`, `ut_lock`, and `ut_timedsleepsig` — everything our override needs. The `GANG_NONE` and `GANG_UNDEF` constants from `sys/space.h` are also unavailable (that header pulls in `ksys/vpag.h`), so we define them inline with their known values.

### Linking (so far)

```sh
ld -n32 -r -o kernel_new.o timer.o clock_patch.o ptimers_patch.o kernel.o
```

Three `.o` files ahead of `kernel.o`. The linker emits around 50 "Multiply defined" warnings — one for each overridden symbol — and `lboot` confirms the overrides at install time. There's a third timing path still unpatched: `select()` timeouts. That requires a fourth patch file and a different kind of header archaeology.

## `select()` Timeouts

`select()` and `poll()` take a third path through the kernel. `dopoll()` in `sgi/select.c` converts the caller's `struct timeval` to lbolt ticks via `hzto()`, registers a callout, and sleeps via `tsleep()` — completely independent of both `nano_delay()` and `nanosleep_common()`. Under `sleep=off`, the callout fires nearly instantly, so `select(0, NULL, NULL, NULL, &tv)` with a 3-second timeout returns in a few milliseconds of real time regardless.

### The Missing Headers

Overriding `dopoll()` requires the kernel's internal types for file descriptors, poll heads, vsockets, and virtual processes. In the IRIX source tree these live under `ksys/`. On a running IRIX system, `/usr/include/ksys/` doesn't exist — it's a kernel build-time directory not shipped with the OS.

Nine headers needed extracting from the IRIX 6.5.5 source tree and uploading over serial:

| Header | Provides |
|--------|----------|
| `ksys/fdt.h` | `fdt_nofiles()`, `fdt_select_convert()` |
| `ksys/vfile.h` | `vfile_t` type |
| `ksys/vproc.h` | `curvprocp`, `VPROC_GETRLIMIT` |
| `ksys/behavior.h` | `bhv_head_t`, behavior dispatch |
| `ksys/kqueue.h` | `kqueue_t` type |
| `ksys/pid.h` | kernel-context pid type |
| `ksys/kcallout.h` | callout list types |
| `ksys/cell_config.h` | cell configuration |
| `ksys/vpgrp.h` | process group types |

Plus one from `sys/`: `vsocket.h` (`vsock_t`, `VSOP_SELECT` macro).

`ksys/vpgrp.h` has a dependency problem. It unconditionally includes `sys/space.h`, which pulls in `ksys/vpag.h`, which pulls in `sys/arsess.h`, `sys/extacct.h`, `sys/pfdat.h` — a cascade of eight files with inline struct definitions that reference types from four more headers. But `vpgrp_t` and `pgrp_ops_t` don't actually *use* any type from `sys/space.h`. The include is an artifact of the kernel build environment that assumes all headers are co-present.

Solution: upload a modified `vpgrp.h` with that one `#include <sys/space.h>` line removed. The struct definitions remain complete and correct.

`GANG_NONE` and `GANG_UNDEF` (needed by `sginap()`) are also defined in `sys/space.h`. Since importing that header is impractical, they get defined directly in the patch file with their known values:

```c
/* Gang scheduling states — from sys/space.h, which can't be included */
#ifndef GANG_NONE
#define GANG_NONE  0
#define GANG_UNDEF 7
#endif
```

### The dopoll() Patch

The patch adds a real-time gate around the existing `tsleep()` call. Two additions: capture the start time and cap `selticks` to 1 on entry, and check the real clock on return to either exit or retry with another 1-tick sleep:

```c
/* QEMU: capture real-time start; cap selticks to 1 per iteration */
if (atv && selticks) {
    dp_rt_start     = *qemu_rt_ctr;
    dp_rt_target_us = (uint32_t)(atv->tv_sec * 1000000 + atv->tv_usec);
    selticks = 1;
}

/* ... existing tsleep / fd polling logic unchanged ... */

/* QEMU: check real elapsed time, loop if not yet done */
if (dp_rt_target_us) {
    uint32_t rt_elapsed = *qemu_rt_ctr - dp_rt_start;
    if (rt_elapsed < dp_rt_target_us) {
        uint32_t remain = dp_rt_target_us - rt_elapsed;
        atv->tv_sec  = remain / 1000000;
        atv->tv_usec = remain % 1000000;
        selticks = 1;   /* 1-tick sleep, not hzto(atv) */
        goto retry;
    }
}
```

The `selticks = 1` on the retry path is the critical detail. Without it, `hzto(atv)` would recompute the remaining time as a large virtual tick count, and the next `tsleep()` would take many real seconds — the same 8.5× overshoot in a new location.

### Linking All Four Patches

```sh
ld -n32 -r -o kernel_new.o \
    timer.o clock_patch.o ptimers_patch.o select_patch.o kernel.o
```

`lboot` confirms the three function-level overrides it can see:

```
multiply defined:(nano_delay) in kernel.o and os.a(clock.o)
multiply defined:(nanosleep) in kernel.o and os.a(ptimers.o)
multiply defined:(sginap) in kernel.o and os.a(ptimers.o)
```

`select`, `dopoll`, and `poll` don't appear here — they were already resolved in the pre-linked `kernel_new.o` before `lboot` sees it.

## Final Results

```
# time sleep 3
0.0u 0.0s 0:03 0% 0+0k 0+0io 0pf+0w

# time sleep 10
0.0u 0.0s 0:10 0% 0+0k 0+0io 0pf+0w

# time /var/tmp/seltest      # select(0, NULL, NULL, NULL, {3, 0})
0.0u 0.0s 0:03 0% 0+0k 1+0io 0pf+0w

# ping -c 5 10.0.2.2
PING 10.0.2.2 (10.0.2.2): 56 data bytes
64 bytes from 10.0.2.2: icmp_seq=0 ttl=255 time=1.611 ms
64 bytes from 10.0.2.2: icmp_seq=1 ttl=255 time=1.590 ms
64 bytes from 10.0.2.2: icmp_seq=2 ttl=255 time=0.890 ms
64 bytes from 10.0.2.2: icmp_seq=3 ttl=255 time=1.430 ms
64 bytes from 10.0.2.2: icmp_seq=4 ttl=255 time=1.136 ms

----10.0.2.2 PING Statistics----
5 packets transmitted, 5 packets received, 0.0% packet loss
round-trip min/avg/max = 0.890/1.331/1.611 ms
```

`sleep 3` returns in 3 real seconds. `sleep 10` in 10. A `select()` with a 3-second timeout returns after exactly 3 real seconds. Ping RTTs are sub-2ms with zero packet loss.

The `lbolt` counter still races — 448 references across 113 source files, all untouched. The scheduler, disk I/O timeouts, SCSI probes, and boot sequence continue to run at emulator speed. The user-visible time-keeping layer now runs at human speed.

## Reflections

The most striking thing about this project isn't the patches themselves — it's the toolchain archaeology. To modify a handful of kernel functions, we had to:

- Add a register to emulated hardware in C
- Understand SGI's EFS filesystem well enough to create disk images with valid volume headers (special thanks to [jkbenaim's efsextract](https://github.com/jkbenaim/efsextract)!)
- Navigate a 25-year-old package dependency system across multiple installation media
- Work around a license server for a company that no longer exists
- Reconstruct nine kernel build-time headers from source and upload them over a serial console
- Know which header includes to surgically remove to avoid cascades of unavailable types
- Know that IRIX csh does backtick expansion inside quoted heredocs
- Remember to use `init 6` instead of `reboot`

The actual C code in each patch is ten to thirty lines. The environment to compile and deploy it is the real challenge.

The 8.5× timing overshoot was the most instructive surprise. The first-pass logic was correct in intent — compute remaining time, sleep that long, check, repeat — but incorrect in execution. Under icount, "sleep 100ms virtual" and "sleep 100ms real" are not the same thing, and conflating them silently produces results that are wrong by a factor of nearly ten. The lesson generalizes: when patching a system where virtual time and real time are decoupled, be explicit about which clock controls loop termination. Use the real clock for that, always, and make each virtual sleep as short as practical.
