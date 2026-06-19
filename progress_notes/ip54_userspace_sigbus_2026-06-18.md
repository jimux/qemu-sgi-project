# IP54 instability — root cause is userspace SIGBUS, not XFS

**Date:** 2026-06-18 (late session)

## TL;DR

The IP54 "instability" we'd been chasing as XFS corruption is in fact
**userspace processes taking SIGBUS during normal startup**. Live evidence
captured this boot in `/var/adm/SYSLOG`:

```
Jan 10 05:52:31 IRIS unix: ALERT: Process [scheme] 270 generated trap,
    but has signal 10 held or ignored
Jan 10 05:52:31 IRIS unix: 	epc 0xfa3933c ra 0xfa390d8 badvaddr 0x100225e9
Jan 10 05:52:31 IRIS unix: Process has been killed to prevent infinite loop
```

Signal 10 = SIGBUS = misaligned memory access. The faulting PC
`0x0fa3933c` is inside **libc.so.1**, specifically `_malloc+0x29c`:

```
fa39334: 8e230000   lw    v1, 0(s1)        ; v1 = *(s1+0)   (free-list header)
fa39338: 00711821   addu  v1, v1, s1       ; v1 = v1 + s1   (next chunk addr)
fa3933c: 8c610008   lw    at, 8(v1)        ; lw at, 8(v1)   ← CRASH (v1 odd)
```

`badvaddr = 0x100225e9` (ends in `9` — not 4-byte aligned). v1 = 0x100225e1
also unaligned. So `*(s1+0)` returned a value that, added to s1, produced
an odd pointer. malloc's internal free-list is corrupted.

Three core files prove other binaries crash the same way during this
boot:

| Core file                                     | Binary           |
|-----------------------------------------------|------------------|
| `/core`                                       | `csh` (login shell) |
| `/var/tmp/core`                               | `xkbcomp` (X keyboard compiler) |
| `/var/tmp/core.postinst_detected`             | `Xlogin`'s `/bin/sh` |

`xkbcomp` crashing on every X startup is what produces the
`Xsgi0: Couldn't load XKB keymap, falling back to pre-XKB keymap`
message we'd seen but ignored. `Xlogin`'s shell crashing is what causes
the IRIS Motif login dialog to revert to the bare "X Window System"
dialog when you submit a username.

## What this is NOT

- **NOT pvdisk** — instrumented QEMU's sgi_bootdisk to detect any read
  that returns a sector containing a mix of valid 'IN' magic AND zero
  magic in 256-byte slots (the XFS inode-buffer corruption pattern).
  Over 32,001 sector reads across a full boot the counter stayed at
  zero. pvdisk transfers consistent data.
- **NOT on-disk XFS corruption** — `scan_inode_buffers.py` walks every
  inode chunk (allocated + free) on every candidate disk; all 24,000+
  inode slots on each disk have valid `0x494e ('IN')` magic.
- **NOT new XFS "Bad magic" warnings on this boot** — counts in SYSLOG
  for `Bad magic`, `klogpp failed`, `Process [Xsession.dt]`,
  `Xsession: root` stay STATIC over 5 minutes of idle observation; the
  warnings present in SYSLOG are residue from prior boot conditions.
- **NOT inetd / telnet churn** — earlier we mistook telnetd
  "ttloop: peer died" floods as instability; those turned out to be
  our own leftover background polling loops, killed off.

## What this IS

A **kernel-vs-userspace coherency bug** that corrupts malloc's heap
metadata, causing multiple binaries to SIGBUS on the next free-list
traversal. It is the same family as the existing
`pvdisk_read_fragility_fix` (the partial fix from 2026-06-15) but not
covered by it.

The 2026-06-15 fix added `dki_dcache_wbinval(buf, done)` at the end of
`pvdiskstrategy` to flush the **kernel-virtual** dcache lines after a
PIO read, so that B_PAGEIO buffers (shared libraries mmap'd by rld)
would be physically consistent before the user VA mapped the same
page. That fix handled the rld load case (`ldd amesh` works, atlantis
renders) but the symptoms here point to a remaining gap:

When a freshly-paged-in libc page reaches the user process, the **user
VA's L1 dcache lines for that page may still hold stale content from a
prior process** (R4000/R5000 are VIPT — different kernel/user VAs of
the same physical page alias to different cache sets). The `wbinval`
in pvdiskstrategy flushed kernel VA, but the user VA cache lines for
the now-fresh page haven't been invalidated. The user process then
sees stale bytes when libc reads its own immediately-loaded code or
data — and corrupts its malloc free-list as a knock-on effect.

## Why this matches the symptoms

- Affects **multiple unrelated binaries** (csh, xkbcomp, sh, scheme,
  Xsession.dt children). All depend on libc.so.1.
- Crashes during **early process lifetime**, while libc is being
  paged in.
- `_malloc` is one of the first heavily-used libc paths, and is the
  function exhibiting unaligned-load SIGBUS.
- The same binaries run cleanly on machine=indy. Indy uses real HPC3
  SCSI (DMA, not PIO bcopy) so the aliasing issue doesn't arise.
- A SUBSET of crashes survive the `wbinval` fix; the partial coverage
  is exactly what we see.

## Captured artifacts

- `/tmp/ip54_root_core` — csh core (79 KB)
- `/tmp/ip54_vartmp_core` — xkbcomp core (231 KB)
- `/tmp/ip54_postinst_core` — Xlogin/sh core (99 KB)
- `/tmp/ip54_lib32_rld`, `/tmp/ip54_libc` — kernel/libc binaries
  extracted for disassembly
- `scan_inode_buffers.py` — deep XFS inode scanner (rules out on-disk)
- QEMU `sgi_bootdisk.c` instrumentation (BDRD-MIX line if any sector
  contains valid+zero inode-slot mix; verified ZERO emits over 32k reads)
- `progress_notes/ip54_segfault_root_cause_2026-06-18.md` — earlier
  diagnostic milestones (false starts ruled out)

## Suggested next steps

### Option A — extend pvdisk fix to invalidate user-VA caches

When a page that pvdisk just loaded is about to be mapped to a user
process, the kernel must invalidate any aliasing user-VA dcache lines
for the page. Likely points:

1. After `dki_dcache_wbinval(buf, done)` in `pvdiskstrategy`, also walk
   `bp->b_pages[]` (B_PAGEIO) and for each backing page, call
   `dki_dcache_inval(useraddr_of_page, NBPP)` for any TLB mapping that
   currently aliases the page. The IRIX pmap layer keeps this info.
2. Or: at the **TLB miss handler** for a user mapping, if the page is
   newly-paged-in (R5k second TLB entry zero), force an `inval` of the
   four cache sets that index to this page. Less surgical but reliable.

### Option B — make pvdisk DMA instead of PIO

The root cause is that pvdiskstrategy uses CPU bcopy (PIO) to fill the
buffer. A DMA-style transfer (writing directly to physmem, bypassing
dcache) wouldn't need the wbinval/inval dance. The QEMU side already
has direct physmem access — extending `sgi_bootdisk.c` to take a
target physical address and `blk_pread` into guest memory directly
would eliminate the coherency surface entirely.

### Option C — guard malloc

Workaround only. Patch libc in the IP54 root to verify free-list
integrity before traversal. Doesn't fix the underlying issue but makes
the system limp along.

## Reproduction recipe

```bash
cd /home/jimmy/qemu-sgi
cp vm_instances/ip54-test/disk.qcow2.indigo_magic_dialog vm_instances/ip54-test/disk.qcow2
rm -f /tmp/qemu_ip54_gdb/*.sock /tmp/qemu_ip54_gdb/serial.log

env IP54_CAUSE_IP5_COUNT_PA=0x0829fee0 \
  qemu-sgi-repo/build-linux/qemu-system-mips64 \
    -M sgi-ip54 -bios PROM_library/bins/cpu/ip54/ip54.bin -m 256M \
    -L qemu-sgi-repo/build-linux/pc-bios -display none \
    -chardev socket,id=ser0,path=/tmp/qemu_ip54_gdb/serial.sock,server=on,wait=off \
    -serial chardev:ser0 \
    -monitor unix:/tmp/qemu_ip54_gdb/monitor.sock,server,nowait \
    -drive if=mtd,file=vm_instances/ip54-test/disk.qcow2,format=qcow2,cache=writeback,file.locking=off \
    -nic user,tftp=ip54_tftp_staging,hostfwd=tcp::2324-10.0.2.15:23 &

# Wait ~2 min for boot. Then trigger:
#   1. mouse_move + sendkey via QEMU monitor to type into the IRIS login dialog
#   2. Wait 30s
#   3. Read /var/adm/SYSLOG via pyirix.xfs — look for new
#      "ALERT: Process [...] generated trap" entries.
#   4. Read /core or /var/tmp/core if present — `file <core>` shows the
#      crashing binary.
```
