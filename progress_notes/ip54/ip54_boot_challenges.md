# IP54 Boot Project: Challenges, Root Causes, and Paths Forward

*Written: 2026-03-04*

This document summarises every significant obstacle encountered during the effort to
boot an IRIX 6.5 kernel on the QEMU `sgi-ip54` paravirtual machine, explains what is
fundamentally wrong, and proposes concrete next steps.

---

## 1. What We Have Achieved

Before cataloguing the failures, it is worth being clear about what *works*:

| Milestone | Status |
|-----------|--------|
| IP54 PROM boots, shows System Maintenance Menu | ✅ |
| XFS version-number bug in PROM fixed | ✅ |
| PROM reads `/unix.new` from XFS via `boot -f dksc(0,1,0)/unix.new` | ✅ |
| ELF loader in PROM loads the N32 kernel segment | ✅ |
| Kernel entry point reached; PROM patches applied | ✅ |
| Memory detected (262 144 kB), CPU 100 MHz printed | ✅ |
| `pvdisk` initialises, reports disk size and partition table | ✅ |
| XFS root mounted on `/hw/scsi_ctlr/0/target/1/lun/0/disk/partition/0/block` | ✅ |
| `init` starts; `bcheckrc` and `brc` run | ✅ |
| Boot progresses as far as `/etc/lnsyscon` and `/etc/rc2` | ✅ |

We made it from "PROM can't read XFS" all the way to user-space scripts running
under init.  That is substantial progress.

---

## 2. The Disk Corruption Problem (Root Cause of Most Failures)

### What happened

The `ip54-test` disk image has been corrupted at **XFS inode block 1122400**
(absolute disk offset ~711 MB).  Three inode slots (offsets +0x100, +0x700, +0xf00)
were overwritten with the bytes `67 72 6f 75 70 3a 00 00 …` — the literal text
`"group:\x00\x00other:\x00\x00mask:\x00\x00user\x00…"`, which is POSIX ACL data.
The XFS inode magic should be `0x494e` ("IN"); the actual value was `0x6772` ("gr").

### Effect

Every time the IRIX kernel tries to read those inodes it emits:

```
WARNING: Bad magic # 0x6772 in XFS inode buffer, starting blockno 1122400
WARNING: Filesystem "/": corrupt, unmount and run xfs_repair
```

When this happens in a tight loop (because something keeps accessing those inodes
during boot), the machine produces an infinite stream of warnings and never
progresses.  QEMU eventually crashes with SIGSEGV after ~8 minutes of this.

### How it was partially fixed (2026-03-04)

We patched the raw disk in Python:
- Converted `disk.qcow2` → `ip54_disk.raw` (4 GB flat file, ~6 min)
- Wrote a valid XFS inode skeleton (magic `0x494e`, mode `0`, di_next_unlinked
  `0xFFFFFFFF`) over the three corrupt slots
- Converted back to qcow2

This eliminates the "bad magic" warning.  Whether subsequent warnings (mode=0
"invalid inode") still create a loop is unknown — a fresh boot test is pending.

### Root cause of the corruption

Unknown with certainty, but the most likely cause is a write from an earlier
session when the `pvdisk` driver did **not** yet have the B_PAGEIO fix (see §4).
Before the fix, `pvdiskstrategy()` used `bp->b_dmaaddr` on B_PAGEIO buffers that
had an invalid `b_dmaaddr`; this caused disk writes to go to arbitrary virtual
addresses — corrupting the mapping of filesystem data written to disk.

There is also a secondary cause: the `ip54-test` disk is a flattened qcow2 derived
from `irix655-full` via `qemu-img convert`.  If any backing-file block was read
incorrectly during flattening, those blocks could contain garbage.

---

## 3. The `lboot` Hangs Indefinitely

### Symptom

When we boot Indy IRIX from the `ip54-test` disk and run:
```
lboot -s /var/sysgen/system/IP54.sm
```
…lboot prints the XFS inode-corruption warning once, then produces **zero output
for 15 minutes** until the test script kills it.  The disk byte counter stops
advancing at 1,980 bytes.

### Why

After the XFS warning, the IRIX XFS code marks the affected inode block as bad and
returns `EIO` to whatever called it.  If lboot (or the linker it calls, `/usr/lib/ld`)
was trying to read or write a file whose inode is in that block, the operation fails
with EIO.  IRIX programs typically retry on I/O errors, entering a tight loop.  With
a disk I/O error, there is no progress and no output.

### Impact

We cannot rebuild `/unix.new` inside the running Indy IRIX environment until the
disk corruption is resolved.  This blocks the compile-link-test loop.

---

## 4. The B_PAGEIO Bug in `pvdisk.c`

### What it was

Before the fix, `pvdiskstrategy()` handled all buffers with:
```c
buf = bp->b_dmaaddr;
```
On B_PAGEIO buffers (which exec uses to load ELF segments), `b_dmaaddr` is
meaningless — the buffer is described by page frames (`b_pages`), not by a kernel
virtual address.  Reading from an invalid `b_dmaaddr` returned zeros or garbage;
writing to it went to arbitrary memory, not the disk.

### Effect on the kernel binary

The very first boot test after the initial kernel build showed `execve("/etc/init")`
returning `ENOEXEC`.  The ELF magic byte at offset 0 of the file on disk was **not**
`0x7f 0x45 0x4c 0x46` — because the B_PAGEIO write path had written zeros there.

### The fix applied

```c
if (!BP_ISMAPPED(bp)) {
    buf = bp_mapin(bp);
    need_mapout = 1;
} else {
    buf = bp->b_dmaaddr;
}
// ... I/O ...
if (need_mapout)
    bp_mapout(bp);
```

The fix is in `/workspace/ip54_tftp_staging/pvdisk.c` and the compiled object is
at `/workspace/ip54_tftp_staging/pvdisk.o` (8 512 bytes, compiled 2026-03-04).

### Remaining uncertainty

The kernel currently running on ip54-test **does** include this fix (confirmed by the
boot: PROM sees `BDRD sec=396056 d=7f454c46` — ELF magic).  But the **disk** may
have been corrupted by writes made *before* the fix existed.  We cannot be 100%
certain the fix is complete without running it on a provably clean disk.

---

## 5. User-Space Binary Crashes (ls, who)

### Symptom

After XFS mounts and init starts:
```
/etc/lnsyscon[15]: 23 Illegal instruction(coredump)
/etc/rc2[13]:     30 Illegal instruction(coredump)
```

Signal 23 is SIGSTOP; signal 30 is SIGXCPU.  Neither normally produces "Illegal
instruction".  Signal 4 (SIGILL) is what you'd expect for an illegal instruction.

### Candidates

| Hypothesis | Evidence for | Evidence against |
|------------|-------------|------------------|
| MIPS reserved-instruction exception delivered with wrong signal number | Kernel IP54-specific exception handler could have table bug | Haven't confirmed with `-d int` trace |
| `ls`/`who` execute an instruction QEMU doesn't emulate | Both are standard IRIX O32 binaries; QEMU MIPS3 should cover all instructions they use | — |
| Capability (`cap_*`) stubs return wrong errno that gets misused as a signal | `ip54_stubs.c` stubs cap_* functions | `ls` doesn't use capabilities |
| Console major number mismatch (DU_MAJOR=260 vs on-disk /dev/console major=58) | getty binds /dev/console; pvuart is 260 | `ls` doesn't open /dev/console |
| XFS disk I/O error propagated to exec path | Corrupted inodes could have caused execve to get garbled ELF | IRIX would deliver SIGSEGV or SIGBUS, not 23/30 |

### Blocking factor

We could not get past the XFS warning loop long enough to observe these crashes
in the patched disk.  In the session where they *were* seen, the disk had a
different state.  The priority is to get past the warning loop; the signal-number
mystery comes after.

---

## 6. qcow2 Disk Management Complexity

### Backing-file chain fragility

`ip54-test` was created as a thin overlay on `irix655-full` (42 MB qcow2 on a 2.8 GB
backing file).  When we needed to patch a raw byte, we had to flatten it first
(`qemu-img convert` reads all 4 GB).  This takes ~6 minutes.  Every flatten/re-inflate
cycle is a potential source of data loss if the tool or process is interrupted.

### Current disk inventory

| File | Size | Contents |
|------|------|----------|
| `ip54-test/disk.qcow2` | 1,007 MB (flat) | Corrupted; inode fix applied; has `/unix.new` with B_PAGEIO-fixed kernel |
| `ip54-test/disk.qcow2.orig` | 42 MB (thin) | Pre-flatten; backed by irix655-dev; backed version of the original ip54-test fork |
| `ip54-test/disk.qcow2.pre_inode_fix` | 1,006 MB | Backup before 2026-03-04 inode magic fix |
| `irix655-full/disk.qcow2` | 2.8 GB (flat) | Clean IRIX 6.5.5 + MIPSpro 7.4.4m baseline |
| `irix655-full/disk_corrupt.qcow2` | 5.5 GB | Corrupted (created by mistake earlier) |

### The "clean baseline" problem

`irix655-full` is supposed to be the clean baseline.  But a snapshot of it
(`/tmp/irix655full_lboot_snap.qcow2`) tried to autoboot and got:

```
xfs: could not get block 0 of leaf directory.
scsi(0)disk(1)rdisk(0)partition(0)/unix: no such file or directory.
```

This suggests either (a) the snapshot qcow2 was created incorrectly and doesn't
have the right backing chain, or (b) the `irix655-full` disk itself has some
directory-level XFS damage to the root that prevents the Indy PROM from
traversing `/` to find `/unix`.  **We have not confirmed which.**

---

## 7. The Bootstrap Circular Dependency

This is the deepest architectural problem:

```
To run lboot → need Indy IRIX booted from the disk
To boot Indy IRIX → need a non-corrupted disk
To have a non-corrupted disk → need a repair tool or a fresh disk
To inject unix.new to a fresh disk → need lboot OR direct disk patching
To use lboot on a fresh disk → need to TFTP the .o files in, then run lboot
```

Every path to getting a working kernel involves either:

1. **Running inside IRIX** (which requires a healthy disk, and writing to the disk
   risks re-corruption if pvdisk has any remaining bugs), or

2. **Direct disk patching from Linux** (which requires understanding IRIX XFS
   format deeply enough to create a new file — non-trivial, though the Python
   `sgi_fs.py` library is a start).

---

## 8. Scripted Automation Fragility

We have accumulated ~20+ pexpect boot scripts in `/tmp/`, most of which fail
silently for one of these reasons:

| Failure mode | Example |
|---|---|
| Unix domain socket not created in time | `boot_irix_compile_pvdisk.py`: `FileNotFoundError` after 29.5 s wait |
| PROM menu not reached before sending commands | `b6aspfxp9`: sends TFTP commands that get echoed back as menu choices |
| `nc` not installed | `bswfkax02` GDB script |
| Indy PROM `autoload=false` causes PROM menu, not auto-boot | Multiple scripts that expect straight boot |
| Wrong SCSI unit number (0 vs 1) | Multiple scripts |
| stdin/stdout vs. Unix socket confusion for serial | Multiple scripts |
| `socat` not installed | Some scripts |

There is no single reliable, tested automation script for either boot path.

---

## 9. pvuart / Console Major Number Mismatch

The IP54 pvuart driver (`pvuart_cn.c`) registers with `DU_MAJOR = 260`.  But the
on-disk `/dev/console` has **major=58** (the standard SGI Z85130 DUART used by
Indy/IP22).  When the kernel initialises the console and user-space opens
`/dev/console`, it looks for major 58 which doesn't exist in the pvuart driver.

This means:
- Console I/O to `/dev/console` after kernel init may silently fail
- getty on `ttyd1` may not work because it opens `/dev/console`
- The STREAMS DUART device nodes on disk don't match pvuart's major

**This hasn't blocked us yet** because pvuart's polled `ducons_write` handles
kernel printf output before the STREAMS layer is involved, but it will block
reaching a working login prompt.

---

## 10. IP54 PROM Cannot Autoboot

The PROM's "Start System" (menu option 1) is stubbed to `return 0` — it does
nothing.  Every boot test requires interactive intervention:

```
Option? 5              # Enter Command Monitor
> boot -f dksc(0,1,0)/unix.new
```

NVRAM is set (`SystemPartition=dksc(0,1,8)`, `OSLoader=dksc(0,1,8)sash`) to point
at a nonexistent SASH, so auto-boot always fails.  The automation scripts must
either accept this two-step dance or we must fix the PROM stub.

---

## 11. QEMU IP54 Machine Exits After ~150 Seconds

In the sessions where we got past the XFS warning loop, QEMU exited with no
message after about 150 seconds.  This could be:

- The IRIX kernel executing a `halt` instruction (e.g., after `init 6` or a
  kernel panic with auto-reboot disabled)
- A QEMU bug where the `sgi-ip54` machine doesn't handle some register access
  and calls `cpu_abort`
- pvdisk returning EIO on a read that the kernel treats as fatal

We have not been able to capture the exact moment because the 150-second boundary
is at the edge of most script timeouts.

---

## Summary of Fundamental Problems

| # | Problem | Severity | Root Cause |
|---|---------|----------|------------|
| 1 | XFS disk corruption (inode block 1122400) | **Critical** | B_PAGEIO bug in old pvdisk or bad qcow2 flatten |
| 2 | lboot hangs on XFS I/O error | **Critical** | Cannot build kernel without clean disk |
| 3 | Bootstrap circular dependency | **Critical** | Architecture of the build/test loop |
| 4 | No clean way to write a file to an IRIX XFS disk from Linux | **High** | Python sgi_fs.py read-only; Linux XFS may not support dir_v1 |
| 5 | User-space binary crashes (signals 23/30 for "Illegal instruction") | **High** | Unknown; possibly IP54 kernel exception handler bug |
| 6 | pvuart DU_MAJOR=260 vs on-disk /dev/console major=58 | **High** | On-disk device nodes never updated for IP54 |
| 7 | qcow2 disk management (4 GB flat, slow conversions, fragile) | **Medium** | Architecture choice |
| 8 | Automation scripts unreliable | **Medium** | Each script was written for a single attempt; no regression |
| 9 | PROM autoboot stub does nothing | **Low** | Intentional deferral |
| 10 | QEMU exits at ~150 s | **Unknown** | Need longer-running capture to diagnose |

---

## Proposed Paths Forward

### Path A: Fix the disk from Linux (bypasses IRIX entirely)

1. **Repair the existing ip54-test disk** further:
   - Run `xfs_repair` (Linux) on the raw disk against the XFS partition
     (`offset=$(( 266240 * 512 ))`)
   - This may or may not work with IRIX XFS dir_v1 format — test first on a copy
   - If xfs_repair can fix inode 2311360's `size` field, lboot may run

2. **Create `/unix.new` on a clean disk from Linux** via NBD:
   - `vm_instance_reset ip54-test` → fresh fork from irix655-full (no unix.new)
   - `qemu-nbd -c /dev/nbd0 disk.qcow2` (needs root and nbd kernel module)
   - `mount -t xfs -o norecovery /dev/nbd0p?` (partition 0 of the disk)
   - `cp unix.new /mnt/unix.new; umount; qemu-nbd -d /dev/nbd0`
   - Risk: Linux XFS driver may not support IRIX dir_v1 format

3. **Extend sgi_fs.py to write files**:
   - The Python library already reads XFS; adding write support for the
     "extent data fork" case (one contiguous extent) is ~200 lines
   - Would let us create `/unix.new` from Python without Linux kernel XFS

### Path B: Fix lboot path (work within IRIX)

1. **Boot Indy IRIX with the inode-fixed disk** (using today's inode-magic fix)
2. Run `xfs_repair /dev/rdsk/...` *inside* IRIX to fix inode 2311360 properly
3. Then run lboot with the pre-compiled `.o` files from staging:
   - `kernel.o` (529 KB), `pvdisk.o` (8.5 KB), `pvuart_cn.o`, `ip54_stubs.o`
     are already compiled and in `/workspace/ip54_tftp_staging/`
   - TFTP them in, then run `lboot -s IP54.sm`

   *Challenge:* xfs_repair inside IRIX uses the raw SCSI device; we need to make
   sure ip54-test isn't mounted read-write when repair runs (mount read-only first
   or unmount).

### Path C: Abandon ip54-test, build fresh (cleanest)

1. `vm_instance_reset ip54-test` → fresh fork of irix655-full
2. Boot Indy IRIX from the fresh disk (no corruption, autoboot works)
3. TFTP the pre-compiled `.o` files from staging into `/var/sysgen/boot/`
4. TFTP `IP54.sm` into `/var/sysgen/system/`
5. Run lboot → produces `/unix.new`
6. Boot ip54-test with the new kernel

*Challenge:* The previous attempt at this (task `b6aspfxp9`) failed because
irix655-full couldn't boot as a snapshot — `xfs: could not get block 0 of leaf
directory`.  Need to investigate whether this is a snapshot-creation problem or a
genuine corruption in irix655-full itself.  **The safe first step is to test
booting irix655-full directly (not via snapshot) on Indy.**

### Path D: Fix user-space crashes independently

Once we have a booting kernel (either the current one if disk corruption is fixed,
or a new one), the next layer of issues is the user-space crashes:

1. Boot with QEMU `-d int` and filter for exception codes to identify what MIPS
   exception causes signals 23/30
2. Look at the IP54 kernel's `trap.c` / `LOCORE.s` to see if the exception-code →
   signal-number table is correct for MIPS3 on N32 kernel
3. Fix `/dev/console` major: either create a device node with major=260 inside IRIX,
   or change `DU_MAJOR` in `pvuart_cn.c` to 58 and recompile

### Recommended immediate next step

**Test Path C first:**

```bash
# Check if irix655-full can boot normally on Indy (not via snapshot)
qemu_run_sgi machine=indy \
  scsi_drives=[{path: "/workspace/vm_instances/irix655-full/disk.qcow2", snapshot: true}] \
  timeout=120
```

If that succeeds, we have a clean starting point.  Then:

1. Boot into IRIX on that disk
2. TFTP the pre-compiled `.o` files from staging
3. Run lboot (no XFS corruption → should complete)
4. Grab `/unix.new` (now 6.something MB) off the disk
5. Use direct disk patching to write it into a fresh ip54-test fork

This avoids all the disk-corruption issues and uses only pre-compiled objects
(so no recompilation needed inside the VM).

---

## Appendix: Key File Paths

| Item | Path |
|------|------|
| IP54 kernel (compiled) | `/workspace/ip54_tftp_staging/unix.new` or inside qcow2 at inode 37089, sector 396056 |
| Pre-compiled kernel objects | `/workspace/ip54_tftp_staging/kernel.o`, `pvdisk.o`, `pvuart_cn.o`, `ip54_stubs.o` |
| IP54 PROM | `/workspace/prom-building/build/ip54.bin` |
| Staging directory | `/workspace/ip54_tftp_staging/` |
| ip54-test disk (patched) | `/workspace/vm_instances/ip54-test/disk.qcow2` |
| irix655-full baseline | `/workspace/vm_instances/irix655-full/disk.qcow2` |
| pvdisk.c with B_PAGEIO fix | `/workspace/ip54_tftp_staging/pvdisk.c` |
| pvuart driver | `/workspace/ip54_tftp_staging/pvuart_cn.c` (DU_MAJOR=260) |
| XFS challenge notes | `/workspace/progress_notes/ip54/xfs_challenges.md` |
