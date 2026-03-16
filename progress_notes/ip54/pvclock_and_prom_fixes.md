# IP54 Paravirtual Clock & PROM Fixes — Boot to Multi-User

**Date:** 2026-03-12
**Status:** IRIX kernel boots, XFS mounts, processes start (but crash-loop)

## Summary

Three critical bugs were found and fixed that together prevented the IP54 IRIX
kernel from booting.  With all three fixed, the kernel boots to multi-user mode
for the first time.

---

## Bug 1: PROM_STACK Overlapping .data (Linker Script)

**File:** `prom-building/link/prom.ld`
**Symptom:** `getenv("AutoLoad")` returned NULL; PROM fell through to interactive menu
**Root cause:** `PROM_STACK` was defined as `0x83F00000`, the same address as `_fdata`.
The BSS zeroing code in `csu.s` computes:

    bzero(firstBss, (PROM_STACK & 0x1FFFFFFF | 0x80000000) - 64 - firstBss)

With `PROM_STACK == _fdata`, the subtraction wraps to ~4GB, corrupting the entire
`.data` section (including `default_env[]` pointers and the `__ctype` table).

**Fix:** Set `PROM_STACK = 0x83FC0000` (above `_end = 0x83F66F38`).  BSS zeroing
now covers ~784KB — the correct range.

---

## Bug 2: tolower() Macro Double-Evaluation in getenv()

**File:** `prom-building/src/libsc/lib/getenv.c`
**Symptom:** `getenv("AutoLoad")` returned NULL even though `environ_str.strcnt=16`
and `environ` was correctly set.

**Root cause:** The `nvmatch()` function in `getenv.c` uses:

```c
while (tolower(*s1) == tolower(*s2++))
```

And `ctype.h` defines `tolower` as a macro:

```c
#define tolower(c)  (isupper(c) ? _tolower(c) : (c))
```

The expansion of `tolower(*s2++)` evaluates `*s2++` TWICE per iteration:
once in `isupper()` and once in the selected ternary branch.  This causes
`s2` to advance by 2 per loop iteration, skipping every other character.
The comparison "AutoLoad" vs "AutoLoad=Yes" fails at the second character
('u' vs 't').

**Fix:** Add `#undef tolower` and `extern int tolower(int c);` at the top
of `getenv.c`.  This forces use of the function version (defined in
`strcasecmp.c`), which evaluates its argument exactly once.

**Note:** This is a latent bug in the original IRIX PROM source.  The original
SGI toolchain (MIPSpro) may have used a different `tolower` implementation
that avoided double-evaluation, or the IRIX `ctype.h` may have used a
lookup-table macro without the ternary pattern.

---

## Bug 3: Paravirtual Clock Not Firing (IRIX clock() Never Called)

**File:** `qemu/hw/mips/sgi_ip54pv.c`
**Symptom:** Kernel stalled permanently at `XEXP` — `lbolt` never advanced.

**Root cause:** No hardware source was delivering the CP0 IP5 interrupt that
IRIX's `clock()` function requires.  The IOC1-mode clock chain is:

    IP8 → r4kcount_intr_r4000 → cause_ip5_count++ → SW2 → intr() → clock()

Without a periodic timer writing `cause_ip5_count=1` to guest RAM and
asserting SW2, the `intr()` function never synthesizes IP5.

**Fix:** Added `PVClockState` with a 100Hz QEMU virtual timer:

- **raise_timer** (every 10ms): writes `cause_ip5_count=1` to PA `0x0829ED00`,
  sets CP0 Cause SW2 bit `(1<<9)`, calls `cpu_interrupt(CPU_INTERRUPT_HARD)`
- **lower_timer** (2ms after raise): clears SW2 bit
- Starts 100ms after boot to avoid firing during PROM init

---

## PROM Environment Variable Defaults

**File:** `prom-building/src/libsk/ml/env.c`

Changed defaults to enable autoboot of `unix.new` from partition 0:

| Variable         | Old                       | New                |
|------------------|---------------------------|--------------------|
| AutoLoad         | No                        | Yes                |
| OSLoadPartition  | dksc(0,1,0)               | dksc(0,1,0)        |
| SystemPartition  | dksc(0,1,8)               | dksc(0,1,0)        |
| OSLoader         | dksc(0,1,8)sash           | unix.new           |
| OSLoadFilename   | unix                      | unix.new           |
| bootfile         | dksc(0,1,8)unix           | dksc(0,1,0)unix.new|

Also reduced `AUTO_DELAY` from 5 to 1 second in `startup.c`.

---

## Boot Result (2026-03-12)

With all fixes applied, the IP54 kernel boots to multi-user:

```
IP54 Paravirtual SGI Workstation
IRIX Release 6.5 IP54 Version 07151432 System V
Total real memory  = 262144 kbytes
CPU Frequency = 400Mhz
1 CPU(s)
NOTICE: pvdisk: IP54 paravirtual disk, 8388608 sectors (4096 MB)
Root on device /hw/scsi_ctlr/.../partition/0/block (fstype xfs)
Available memory on node [0] =  254884 kbytes
1738 buffers
```

### Remaining Issues

1. **kernel malloc: invalid size -- 1122342912** — suspicious large value during
   early init; needs investigation (possibly a struct field misread)

2. **Failed to add swap file (error 2)** — no swap partition configured on disk;
   ENOENT for /dev/swap

3. **Process crash loop** — after init starts, processes exec and immediately exit
   in a tight loop (`XEXP10P04` pattern).  Likely causes:
   - `/dev/console` major number mismatch (pvuart=260, on-disk expects 58)
   - Missing shared libraries or init script issues
   - Signal delivery problems

### Next Steps

1. Fix `/dev/console` major: change `DU_MAJOR` in `pvuart_cn.c` from 260 to 58
2. Investigate the process crash loop — check what `init` is trying to exec
3. Add swap support or suppress the warning
4. Run longer boot test (90s+) to see if login prompt appears

---

## MCP Server Fixes (Side Quest)

During this debugging session, the MCP server kept disconnecting.  Two fixes:

1. **stdin inheritance:** All `subprocess.Popen` calls now pass
   `stdin=subprocess.DEVNULL` to prevent child processes from consuming
   MCP JSON-RPC messages on stdin.

2. **UTF-8 decode crash:** `qemu_run_sgi` used `text=True` on Popen, causing
   `proc.communicate()` to decode output as UTF-8.  Kernel binary output
   crashed the decoder.  Fixed to use raw bytes + `latin-1` decode with
   `errors="replace"`.

## Debugging Technique: Tracing getenv() Failure

The `getenv("AutoLoad") == NULL` bug was particularly tricky because:

- `init_env()` successfully populated 16 environment variables
- `environ` pointer was correctly set to `environ_str.strptrs`
- `environ_str.strcnt == 16`
- Yet `getenv("AutoLoad")` returned NULL

The breakthrough came from adding diagnostic `printf()` calls at two points:
1. End of `init_env()` — confirmed environment was populated
2. In `fw_dispatcher()` before `startup()` — confirmed environ still valid

This proved the data was intact but the LOOKUP was broken, narrowing the bug
to `nvmatch()` → `tolower()` macro double-evaluation.
