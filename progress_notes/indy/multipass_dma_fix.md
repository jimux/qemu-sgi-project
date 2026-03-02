# Multi-Pass DMA Fix for Large SCSI Transfers

## Problem

During IRIX 6.5 installation, `mkfs_xfs` failed with "dev_zero: writeb
failed: Invalid argument" when zeroing the XFS log. Single `write()` calls
of >=258048 bytes failed while <=257536 bytes succeeded.

## Root Cause

IRIX allocates 64 HPC3 DMA descriptors per SCSI channel (NSCSI_DMA_PGS=64
in `irix/kern/sys/IP22.h`). Each covers one 4KB page, so a single DMA map
can transfer at most ~256KB. For larger SCSI transfers, the IRIX WD93 driver
uses a **multi-pass DMA** mechanism:

1. Maps first <=64 pages into DMA descriptors
2. Programs WD33C93 transfer count (TC) to that chunk's size
3. Starts SELECT_AND_TRANSFER
4. When TC reaches 0, the WD33C93B raises an "unexpected phase" interrupt
5. Driver re-maps next chunk, reprograms TC, issues SELECT_ATN_XFER to continue

Our emulation was canceling the SCSI request when TC reached 0, killing the
transfer. The driver never got a chance to reprogram TC for the next chunk.

## Fix (3 key discoveries)

### 1. Status codes are from chip's perspective (not host's)

The WD33C93 status codes `UNEX_RDATA` (0x48) and `UNEX_SDATA` (0x49) are
named from the **chip's** perspective, not the host's:

- `UNEX_RDATA` (0x48) = chip **Receiving** = host Writing (DATA OUT)
- `UNEX_SDATA` (0x49) = chip **Sending** = host Reading (DATA IN)

Confirmed by both MAME (`SCSI_STATUS_UNEXPECTED_PHASE | S_PHASE_DATA_OUT = 0x48`)
and IRIX (`ST_UNEX_RDATA` with `!SCDMA_IN` = write direction).

### 2. BSY and CIP must both be cleared (MAME FINISHED state)

The IRIX driver's interrupt handler (`handle_intr`) starts with:
```c
while((aux = getauxstat()) & (AUX_CIP|AUX_BSY))
    ;
```
It spins until **both** CIP and BSY are clear. MAME confirms this in its
FINISHED state handler (wd33c9x.cpp:970):
```c
m_regs[AUXILIARY_STATUS] &= ~(AUXILIARY_STATUS_CIP | AUXILIARY_STATUS_BSY);
```

Initially we only cleared CIP and kept BSY set (thinking the bus was still
connected), which caused the driver to spin forever.

### 3. Command Phase must be 0x46 (PH_DATA / COMMAND_PHASE_TRANSFER_COUNT)

MAME sets `COMMAND_PHASE = 0x46` when raising the unexpected phase interrupt.
IRIX checks `phase == PH_DATA` (0x46) in its multi-pass DMA condition at
wd93.c:2931. Without this, the driver doesn't recognize the interrupt as a
multi-pass DMA event.

### 4. IRIX resumes via SELECT_ATN_XFER, not TRANSFER_INFO

The IRIX driver resumes a multi-pass transfer by issuing SELECT_ATN_XFER
(0x08) with the command phase pre-set to PH_IDENTRECV (0x45), not by using
TRANSFER_INFO (0x20). The `setdest()` function (wd93.c:1852) programs DESTID,
LUN, and then issues `C93SELATNTR` (SELECT_ATN_XFER).

The WD33C93 emulation must detect this resume case (pending data exists,
current_req active, phase == 0x44 or 0x45) and resume the DMA transfer
instead of creating a new SCSI request.

## Files Modified

- `qemu/include/hw/scsi/wd33c93.h` — Status codes, pending_len/pending_buf fields
- `qemu/hw/scsi/wd33c93.c` — TC=0 handling, SELECT_ATN_XFER resume, cleanup paths
- `qemu/hw/misc/sgi_hpc3.c` — DMA completion multi-pass path
- `tests/test_scsi_source.py` — 15 new multi-pass DMA tests
- `tests/test_scsi_lifecycle.py` — Updated TC=0 test

## Verification

- `dd bs=258048` (previously failing): **PASS**
- `dd bs=524288` (512KB): **PASS**
- `dd bs=1048576` (1MB): **PASS**
- `mkfs_xfs`: **PASS** — filesystem created, mounted, installer reached
- All 130 SCSI tests pass
- All 617 fast tests pass (1 pre-existing unrelated failure)
- 87 multi-pass DMA events handled during mkfs_xfs

## References

- IRIX wd93.c:2930-2956 — ST_UNEX_SDATA/RDATA handler, save_datap, setdest
- IRIX wd93.c:2424 — handle_intr busy-wait for CIP|BSY clear
- IRIX sys/wd93.h:243-244 — ST_UNEX_RDATA=0x48, ST_UNEX_SDATA=0x49
- IRIX sys/IP22.h:457 — NSCSI_DMA_PGS=64
- MAME wd33c9x.cpp:970 — FINISHED clears CIP|BSY
- MAME wd33c9x.cpp:1294-1300 — UNEXPECTED_PHASE status with xfr_phase OR'd in
- MAME wd33c9x.cpp:133 — COMMAND_PHASE_TRANSFER_COUNT = 0x46
