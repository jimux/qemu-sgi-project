# IP54 userspace segfault root cause — XFS corruption + klogpp cascade

**Date:** 2026-06-18 (segfault investigation session)

## TL;DR

**Disks are NOT corrupted on-disk.** A deep `scan_inode_buffers.py` walk of
every allocated inode chunk in every IP54/Indy candidate disk
finds **all allocated inodes have valid `IN` (0x494e) magic** —
24,331 inodes in the IP54 gold; 23,184 in ip54-fresh; etc. All CLEAN
on disk.

The "userspace segfaults" cascade is caused by the IP54 kernel
reporting `Bad magic # 0x0 in XFS inode buffer 0x886b7150…`. The
critical realisation: `0x886b7150` is a **kernel memory address**
(MIPS KSEG range), NOT a disk address. The buffer is uninitialised
or zeroed RAM. The IP54 paravirtual disk path (pvdisk) is returning
zeroed data for some reads — consistent with the prior
`pvdisk_read_fragility_fix` work
(progress_notes/pvdisk_read_fragility_fix.md, "the sgi-ip54 'large
reads return zeros' bug"), which was partially fixed for the
shared-lib/mmap coherency case but apparently doesn't catch every
read pattern XFS uses.

The visible cascade:

```
WARNING: Bad magic # 0x0 in XFS inode buffer 0x886b7150, starting blockno 5270224, offset 0x1c00
WARNING: Bad next_unlinked field (0) in XFS inode buffer 0x886b7150, starting blockno 5270224, offset 0x1c00
WARNING: Filesystem "/": corrupt, unmount and run xfs_repair
```

(Repeats at offsets 0x1d00, 0x1e00, 0x1f00 in the same buffer —
all in the same 4 KB kernel buffer at address 0x886b7150.)

Every kernel warning above is piped through `/usr/sbin/klogpp`, which
segfaults processing the message (probably because its `getmntent`
loop hits the corrupt area). The kernel then logs:

```
Process [klogpp] N generated trap, but has signal 10 held or ignored
Process has been killed to prevent infinite loop [filter /usr/sbin/klogpp failed: killed by signal 11]
```

The Xsession.dt segfaults (`Process [Xsession.dt] 288 generated trap…
signal 11`) follow the same shape — Xsession reads files near the
corrupt area on graphical login and dies.

## Evidence

### 1. First-boot telnet login works perfectly

Single fresh boot, single telnet login: `ps -ef`, `uname -a`, `uptime`
all return correct output, no faults. ~19 processes, init/syslogd/
xdm/Xsgi/inetd all running.

### 2. SYSLOG on the GOLDEN disk shows the corruption

```
Jan 10 05:38:14 2A:IRIS unix: magic # 0x0 in XFS inode buffer 0x886b7150, starting blockno 5270224, offset 0x1c00
Jan 10 05:38:14 4A:IRIS unix: WARNING: Filesystem "/": corrupt, unmount and run xfs_repair
WARNING: Bad next_unlinked field (0) in XFS inode buffer ..., offset 0x1c00
WARNING: Bad magic # 0x0 in XFS inode buffer ..., offset 0x1d00 [filter /usr/sbin/klogpp failed: killed by signal 11]
WARNING: Filesystem "/": corrupt, unmount and run xfs_repair [filter /usr/sbin/klogpp failed: killed by signal 11]
… (continues for offsets 0x1e00, 0x1f00)
```

Every "Bad magic" / "Bad next_unlinked" warning is followed by the
`[filter /usr/sbin/klogpp failed: killed by signal 11]` annotation —
confirming the cascade.

### 3. The XFS recovery loop

The disk has been doing XFS recovery on EVERY boot for months:

```
Mar 12 16:59:29 5A:IRIS unix: NOTICE: Starting XFS recovery on filesystem: /
Mar 12 16:59:29 5A:IRIS unix: NOTICE: Ending XFS recovery for filesystem: /
Mar 12 17:00:01 5A:IRIS unix: NOTICE: Starting XFS recovery on filesystem: /
… (15+ recovery cycles before the corrupt-block warnings appear)
```

Recovery succeeds (replays the log) but the on-disk corruption isn't
repaired — it just gets covered by the journal replay, then re-exposed
when the kernel walks the same inode buffer.

### 4. Xsession SIGSEGV on graphical login

```
Jan 10 05:38:03 6B:IRIS Xsession: root: 295 Segmentation fault - core dumped
Jan 10 05:38:04 1A:IRIS unix: ALERT: Process [Xsession.dt] 288 generated trap, but has signal 11 held or ignored
Jan 10 05:38:04 1A:IRIS unix: ALERT: Process [Xsession.dt] 300 generated trap, but has signal 11 held or ignored
Jan 10 05:38:04 1A:IRIS unix: ALERT: Process [Xsession.dt] 304 generated trap, but has signal 11 held or ignored
Jan 10 05:38:04 1A:IRIS unix: ALERT: Process [Xsession.dt] 308 generated trap, but has signal 11 held or ignored
```

This is what's blocking the clogin face picker and the full Indigo
Magic Desktop login path. Xsession.dt SEGVs because it walks the
filesystem (loading `.dt` config, `/usr/lib/X11/...`) and hits the
bad inode buffer.

### 5. The clock/file-time mismatch is real but secondary

The IRIX clock is at 2004 (DS1386 BCD → kernel decodes via
`year + DALLAS_YRREF (1940)` formula → host's `tm_year=126`, `% 100 =
26`, but kernel adjusts < 45 → 56 → +1940 = 1996; somehow extends to
2004 via `dallas_yrref` runtime tweak). The host writes file
timestamps in 2026. `nsd` warns:

```
nsd[194]: WARNING: Future date on /etc/passwd: Sun Mar  1 15:25:06 2026
nsd[194]: WARNING: Future date on /etc/services: Sun Mar  1 15:40:05 2026
nsd[194]: WARNING: Future date on /etc/group: ... /etc/hosts: ... /etc/rpc: ...
```

This causes `inetd[348]: /: : No such user` and may amplify the
klogpp segfaults (klogpp pulls mount info that references future-
dated dirs). But it's not the root cause.

## What's NOT the bug

- **chkconfig itself**: its binary doesn't call `getpwnam`/`getpwuid`;
  the segfault is in some other process (likely klogpp printing a
  kernel warning while chkconfig runs).
- **csh login startup**: `/etc/cshrc`, `/.cshrc`, `/.login` all clean.
  The "Segmentation fault" we saw after the IRIX banner on telnet
  was a TELNETD child dying — caused by inetd's pty allocation
  failing after the first session, NOT by csh.
- **visuallogin=on or visuallogin=off**: irrelevant. The XFS
  corruption is on the disk regardless of the chkconfig setting.
- **The xdm-config canonical fix from earlier today**: independent
  improvement. The XDM dialog renders cleanly either way.

## What `pyirix.xfs.check_xfs` reports + the new inode scanner

`check_xfs()` on the golden returns all PASS — superblock OK, AG
headers OK, root inode OK. The new `scan_inode_buffers.py` walks
every inode chunk and confirms **every inode slot, allocated AND
free, has valid 'IN' magic (0x494e)** across every candidate disk:

```
vm_instances/ip54-test/disk.qcow2.golden: CLEAN (24331 alloc / 309 free)
vm_instances/ip54-fresh/disk.qcow2:        CLEAN (23184 alloc / 112 free)
prebuilt_disks/ip54-6.5.5-gold.qcow2:      CLEAN (24264 alloc / 376 free)
prebuilt_disks/irix-6.5.5-complete.qcow2:  CLEAN (23102 alloc / 194 free)
prebuilt_disks/irix-6.5.5-base.qcow2:      CLEAN (37685 alloc / 267 free)
```

The on-disk state is FINE.

## pvdisk is innocent

Instrumented `qemu-sgi-repo/hw/misc/sgi_bootdisk.c` to emit a
`BDRD-MIX` log line whenever a returned sector contains both valid
'IN' magic in one 256-byte slot AND zero magic in another — exactly
the pattern that would trigger the kernel's `xfs_inobp_bwcheck`
warning. Booted IP54 to multi-user X login. Result over 32,001
sector reads:

```
Total BDRD: 32001
BDRD-MIX:   0
```

**pvdisk never returns a sector with a mix of valid+zero inode
slots.** Every sector it transfers is consistent with the on-disk
content. Whatever produces the "Bad magic" XFS warnings happens
inside the IRIX kernel between read and bwrite — NOT in the
QEMU↔guest data path.

The previous read-fragility fix (`dki_dcache_wbinval` after PIO
read) is still doing its job; this is a different bug, located
deeper in the IP54 kernel's XFS path.

## The fix

This is NOT a disk-repair problem — the disk is fine. It's a
**pvdisk read-path bug** in the IP54 emulation stack. Possible
locations:

### A. QEMU side: `qemu-sgi-repo/hw/block/sgi-pvdisk.c`

The pvdisk device may not be returning the actual block data for
some read patterns (DMA-vs-PIO, alignment, length). See the partial
fix already in place that handled the mmap/shared-lib case via
`dki_dcache_wbinval` in `pvdiskstrategy`. The XFS pattern likely
involves a different path (cluster reads for buffer cache, perhaps
8 KB reads that span an XFS block boundary).

Investigation tools: log every pvdisk request with sector + length
+ destination buffer pointer + first 16 bytes of returned data,
correlate against the kernel warning's "starting blockno 5270224"
to see what got transferred.

### B. IRIX side: pvdisk driver in `ip54_tftp_staging/pvdisk.c`

The kernel-side pvdisk driver may not be flushing its dcache or
issuing the right wait primitive when XFS asks for an inode-buffer
read. The existing `dki_dcache_wbinval` fix was for ONE path; XFS
may have multiple read paths into pvdisk.

### C. Defensive workaround

Make XFS retry on a "Bad magic # 0x0" inode read — re-issue the
disk read once before declaring corruption. This is a hack but
would unblock progress while the proper fix is investigated.

## Why this matters

Every clogin/4Dwm/Toolchest launch attempt on IP54 sits behind this
bug. The xdm-config canonical fix from the morning session got us to
the proper IRIS Motif dialog (great), but the moment Xsession.dt
launches, it hits the corrupt area and dies. That's why we saw the
blue screen + cursor only — no full desktop ever loaded.

It also explains the "subsequent telnet reconnects fail" pattern:
each reconnect spawns telnetd → login → csh, all of which read the
filesystem; the more we churn, the higher the chance of hitting the
bad inode buffer; once a critical process dies (like syslogd or
inetd), the system becomes incoherent.

## Reproducing

```bash
cd /home/jimmy/qemu-sgi
# Inspect the corruption messages in the golden disk's SYSLOG without booting:
python3 -c "
from pyirix.xfs.image import open_disk_image, find_xfs_partition
from pyirix.xfs.superblock import read_superblock
from pyirix.xfs.operations import resolve_path
from pyirix.xfs.inode import read_inode, read_file_data

with open_disk_image('vm_instances/ip54-test/disk.qcow2.golden') as f:
    po,_ = find_xfs_partition(f); sb = read_superblock(f, po)
    ino = resolve_path(f, po, sb, '/var/adm/SYSLOG')
    inode = read_inode(f, po, sb, ino)
    data = read_file_data(f, po, sb, inode).decode('utf-8', errors='replace')
    for L in data.splitlines():
        if 'Bad magic' in L or 'Bad next_unlinked' in L or 'klogpp failed' in L:
            print(L)
" | head -20
```

Output:

```
Bad magic # 0x0 in XFS inode buffer 0x886b7150, starting blockno 5270224, offset 0x1c00
Bad next_unlinked field (0) in XFS inode buffer 0x886b7150, ..., offset 0x1c00 [filter /usr/sbin/klogpp failed: killed by signal 11]
Bad magic # 0x0 in XFS inode buffer 0x886b7150, ..., offset 0x1d00 [filter /usr/sbin/klogpp failed: killed by signal 11]
WARNING: Filesystem "/": corrupt, unmount and run xfs_repair [filter /usr/sbin/klogpp failed: killed by signal 11]
…
```
