# IP54 4Dwm continuation checkpoint — 2026-06-19 (later)

## What's new since the morning checkpoint

1. **Time-decoupling patch landed** (see
   `time_decoupling_impl_2026-06-19.md`): env var
   `QEMU_MIPS_COUNT_REALTIME=1` swaps CP0 timer source to wall clock.
   Boot tested with the flag set and without — both come up clean.
2. **Second-wave userspace crashes confirmed unchanged** by the new
   patch (as expected — orthogonal).

## What the live state actually shows (REALTIME boot)

Boot from `indigo_magic_dialog` backup with `QEMU_MIPS_COUNT_REALTIME=1`:

- Kernel boots, multi-user reaches `xdm`.
- Xsgi starts, renders the IRIS Motif `clogin` dialog (Login name field,
  Log In / Help buttons, "IRIS" branding, blank face-picker panel).
- Sending "root" + Return via QEMU monitor `sendkey`:
  - clogin process crashes (consistent with the "face picker doesn't
    fire" memory note),
  - xdm restarts to bare "X Window System" Login/Password dialog,
  - filling that in: dialog vanishes, screen goes solid blue with
    just the red-X cursor — Xsession has died.
- Serial console silent (getty replaced by Xsgi/xdm chain), telnet
  socket accepts but reads zero bytes (inetd telnetd forks a child
  that crashes immediately).

## Reading the disk while QEMU is stopped (read-only via pyirix)

`pyirix.xfs.operations.read_file_data` was used to extract live state
from `vm_instances/ip54-test/disk.qcow2` — this is the safe read path
that doesn't violate the "no offline writes" rule.

### Key SYSLOG findings (most recent boot)

```
Jan 10 05:38:03 Xsession: root: 295 Segmentation fault - core dumped
Jan 10 05:38:03 unix: |$(0x6dd)ALERT: Process [Xsession.dt] 300 generated trap, but has signal 11 held or ignored
Jan 10 05:38:03 unix: |$(0x6dd)ALERT: Process [Xsession.dt] 304 generated trap, but has signal 11 held or ignored
Jan 10 05:38:03 unix: |$(0x6dd)ALERT: Process [Xsession.dt] 308 generated trap, but has signal 11 held or ignored
Jan 10 05:38:14 unix: WARNING: Bad next_unlinked field (0) in XFS inode buffer 0x886b7150, starting blockno 5270224, offset 0x1c00
Jan 10 05:38:14 unix: WARNING: Bad magic # 0x0 in XFS inode buffer 0x886b7150, starting blockno 5270224, offset 0x1d00
Jan 10 05:38:14 unix: WARNING: Filesystem "/": corrupt, unmount and run xfs_repair
Jan 10 05:47:43 xdm[261]: server open failed for , giving up
Jan 10 05:47:43 xdm[219]: Display :0 cannot be opened
Jan 10 05:47:44 xdm[219]: Server for display :0 terminated unexpectedly: 1
```

### Two big new clues

**Clue A — empty display string in xdm error:**

```
xdm[261]: server open failed for , giving up
```

Note the missing display name between `for ` and `, giving up`. That's
xdm formatting an error message with a NULL/empty display string — the
sprintf got an empty argv that should have been `:0`. Symptom of the
same NULL-pointer chain seen in csh/sh crash signatures.

**Clue B — XFS in-memory corruption appearing after the trap cascade:**

```
WARNING: Bad next_unlinked field (0) in XFS inode buffer ...
WARNING: Bad magic # 0x0 in XFS inode buffer ...
WARNING: Filesystem "/": corrupt, unmount and run xfs_repair
```

These come *after* the Xsession.dt trap cascade, NOT before. The on-disk
state was verified clean by `scan_inode_buffers.py` (prior session). So
this is **in-memory** corruption: the buffer cache holds zeros where
the on-disk content is fine.

That matches the `pvdisk_read_fragility_fix` pattern: a read returns
zero where the disk had real data. The dcache-wbinval fix is supposedly
in place in this kernel (golden disk), so either:
1. There's a *second* code path that reads without the wbinval, OR
2. There's a cache-aliasing case (VIPT, R5000 secondary cache) that
   the fix doesn't fully cover, OR
3. Buffer eviction without write-back is producing zero-fill.

Either way, this is a separate root cause from the unaligned-load
crash that MO_UNALN fixed. Repairing it likely requires another pvdisk
or buffer-cache instrumentation pass.

## What I would chase next session

1. **Disassemble Xsgi around the crash PC.** Need to record the EPC.
   - Plant `hbreak` at the ALERT trap site (0x881bb4f8 — known from
     prior session). Each hit prints (PC, badvaddr, sig, pid, comm).
   - Specifically grep for comm=Xsgi or comm=Xsession.dt and inspect
     EPC; map back to a binary offset via `objdump -d` of /usr/bin/X11/Xsgi.
   - Cross-check with the existing core file under `/core` if one
     exists (sigcontext is at offset 0x438 in coreout; EPC at +0x120).

2. **Re-test the pvdisk fix coverage.** Add a kernel-side "BDRD-ZERO"
   counter inside the buffer cache layer that increments whenever a
   sector-sized read returns all zeros. If the counter goes up during
   Xsession.dt startup, the read-zeros bug is still latent.

3. **In-guest truss substitute.** `truss` isn't installed; `par(1)`
   may be on the disc images. If not, the QEMU monitor can
   `singlestep` a TCG-traced process and emit each syscall via the
   existing `qemu_scsi_trace` framework adapted for syscall0 vector.
   That's heavier work but pins the failing syscall definitively.

4. **Audit clogin source.** If the source is in
   `software_library/irix-655-source/` somewhere under `dt/` or
   `Cadmin/`, find where it parses login name + builds the env for
   the child — that's likely where the empty-string sprintf lives.

## Files / artifacts saved this session

- `progress_notes/time_decoupling_investigation_2026-06-19.md` — design
- `progress_notes/time_decoupling_impl_2026-06-19.md` — implementation
- `qemu-sgi-repo/target/mips/system/cp0_timer.c` — patch (+31/-6)
- `/tmp/qrt/screen.png`, `screen_post1.png`, `screen_xdm1.png` —
  evidence of clogin-dialog → fallback-xdm → blank-blue progression.
- This note.

## QEMU launch line (reproduces this exact state)

```bash
cd /home/jimmy/qemu-sgi
cp vm_instances/ip54-test/disk.qcow2.indigo_magic_dialog \
   vm_instances/ip54-test/disk.qcow2
env IP54_CAUSE_IP5_COUNT_PA=0x0829fee0 QEMU_MIPS_COUNT_REALTIME=1 \
  qemu-sgi-repo/build-linux/qemu-system-mips64 \
    -M sgi-ip54 -bios PROM_library/bins/cpu/ip54/ip54.bin -m 256M \
    -L qemu-sgi-repo/build-linux/pc-bios -display gtk \
    -drive if=mtd,file=vm_instances/ip54-test/disk.qcow2,format=qcow2,cache=writeback,file.locking=off \
    -nic user,tftp=ip54_tftp_staging,hostfwd=tcp::2325-10.0.2.15:23 \
    -audiodev pa,id=aud0 -global sgi-pvaudio.audiodev=aud0 \
    -monitor unix:/tmp/qrt/monitor.sock,server,nowait
```

The clogin dialog appears within ~90 s. Type "root" + Return into the
host's GTK window and you'll see the same fall-through to plain xdm,
then the blue-cursor screen.
