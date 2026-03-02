# Real O2 PROM Boot Milestone

**Date:** 2026-02-14
**PROM:** O2_ip32prom.rev4.18.bin (512KB, real SGI firmware)
**Result:** Boots to System Maintenance Menu

## Summary

The real O2 PROM rev 4.18 now boots to the full System Maintenance Menu
on QEMU's `-M sgi-o2` machine. This required 6 changes across CRIME,
MACE, and GBE device emulation, plus a binary patch with checksum fix.

## Changes Required

### 1. SEG1 RAM Aliases (sgi_o2.c)

The PROM's `SizeMEM()` probes memory via TLB entries mapping to physical
0x40000000+ (SEG1 address space). Each of 8 CRIME memory banks gets a
128MB window. With 32MB SIMMs, RAM mirrors 4 times within each window.

Fix: Create `memory_region_init_alias()` for populated banks (mirrored 4x)
and `create_unimplemented_device()` for empty banks (returns 0 = no SIMM).

### 2. DS17287 RTC Direct-Mapped Access (sgi_mace.c)

The O2 uses a DS17287 RTC with direct-mapped registers at 256-byte stride
within MACE. Register N is at offset `MACE_RTC_OFFSET + N * 256`.

The previous code used index/data port access (like the DS1386 on Indy).
Fixed to use direct-mapped access with proper byte lane handling.

Key registers: A=0x20 (oscillator on), B=0x06 (binary/24h), D=0x80 (VRT).

### 3. ISA DMA and Keyboard/Mouse Stubs (sgi_mace.c)

- ISA DMA: Accept writes to 0x8000/0x8020/0xC000/0xC020 silently
- Keyboard/mouse: Return 0 on reads for MACE_KBDMS_OFFSET region

### 4. GBE Register Expansion (sgi_gbe.c, sgi_gbe.h)

Added handlers for video timing registers (vsync, hsync, blanking, pixel
enable), mode registers, CMAP/GMAP arrays, cursor registers, and CMAP FIFO
status. Fixed GBE_ID to return 0x666.

### 5. SimpleMEMtst Binary Patch (sgi_o2.c)

NOP out `jal simple_memtst` inside `SimpleMEMtst()`. This test writes
patterns through kseg1 (uncached) at the same physical addresses as the
kseg0 (cached) stack. Without L1 data cache emulation, this corrupts saved
registers and causes an AdEL exception.

### 6. Flash Segment Body Checksum Fix (sgi_o2.c) -- THE KEY FIX

**Root cause of the "SL-9600-8E>" sloader prompt:**

The sloader validates flash segment body checksums before running post1.
The NOP patch (#5) changes a word inside the POST1 segment body, breaking
its 32-bit word-sum checksum. The sloader's `validBody()` returns 0
(invalid), `findFlashSegment("post1")` returns NULL, and the sloader
enters firmware download mode instead of calling `post1rtn()`.

Fix: After NOP-ing the JAL, add the original instruction value (0x0FF01463)
to the last word of the POST1 body (offset 0x9140), restoring the body
checksum to zero.

Implementation: `sgi_o2_fix_segment_body_checksum()` scans flash segments
by looking for SHDR magic at page-aligned offsets, finds the segment
containing the patched address, and adjusts its last body word.

## O2 PROM Flash Segment Format

The PROM binary contains 5 flash segments:

| Offset | Name     | Size   | Type | Description |
|--------|----------|--------|------|-------------|
| 0x0000 | sloader  | 16KB   | 1    | First-stage loader |
| 0x4000 | env      | 1KB    | 0    | NVRAM variables |
| 0x4400 | post1    | 19.3KB | 1    | POST and hardware init |
| 0x9200 | firmware | 393KB  | 3    | Main ARCS firmware |
| 0x69200| version  | 904B   | 0    | Version info (4.18) |

Each segment has a 64-byte header:
- Bytes 0-7: reserved
- Bytes 8-11: magic ('SHDR' = 0x53484452)
- Bytes 12-15: segLen (total segment size including header)
- Bytes 16-19: nameLen/vsnLen/segType/pad
- Bytes 20-51: name (null-terminated)
- Bytes 52-59: version (null-terminated)
- Bytes 60-63: header checksum complement

Both header and body checksums use 32-bit big-endian word sums equal to zero.

## Boot Flow

1. Reset vector -> sloader (0xBFC00000)
2. Sloader: hardware init (CRIME, MACE, RTC, serial)
3. Sloader: cache/TLB initialization
4. Sloader: `findFlashSegment("post1")` -> body checksum validation
5. Sloader: `findFlashSegment("firmware")` -> body checksum validation
6. Sloader: `jalr post1` -> calls post1rtn entry point
7. post1: Copy2MEM, DupSLStack, IP32processorTCI, SizeMEM
8. post1: Memory sizing via SEG1 probes (TLB-mapped)
9. Firmware: Full POST, serial/keyboard init, System Maintenance Menu

## Debug Log Notes

- `ds2502_get_eaddr` failures: No 1-Wire EEPROM for MAC address (expected)
- `Cannot connect to keyboard`: No PS/2 keyboard on O2 (uses USB/serial)
- RTC register 12 polling: PROM reads interrupt flags register; currently
  returns 0 (no pending interrupts). Not a problem but could add proper
  RTC periodic interrupt simulation later.

## PROM Compatibility

Only the 512KB raw flash dump boots. The smaller PROMs use a different
container format:

| File | Size | Format | Result |
|------|------|--------|--------|
| O2_ip32prom.rev4.18.bin | 512KB | Raw flash (SHDR segments) | **Boots** |
| ip32prom.image | 419KB | `PROM` container | Silent hang |
| ip32prom_6522.image | 422KB | `PROM` container | Silent hang |
| ip32prom_aef9320b.image | 418KB | `PROM` container | Silent hang |
| ip32prom_e6ed715c.image | 419KB | `PROM` container | Silent hang |

The raw flash dump starts with `SHDR` segment headers (sloader, env, post1,
firmware, version) and fills the entire 512KB ROM region. The `PROM` container
images have a different header starting with magic bytes `50524f4d` ('PROM')
and would need extraction/reformatting before loading.

## Unimplemented Hardware Accesses

~245 unimplemented accesses during boot, all non-blocking:

| MACE offset | Device | Count | Impact |
|-------------|--------|-------|--------|
| 0x280064 | Ethernet MAC (read) | 64 | None — MAC init continues |
| 0x28006c | Ethernet MAC (write) | ~32 | None — config accepted |
| 0x280074 | Ethernet MAC (write) | 64 | None — DMA ring setup |
| 0x300000 | Audio (probe) | 2 | None — no audio |

The PROM gracefully handles missing hardware (no graphics, no keyboard,
no ethernet MAC, no audio) and falls back to serial-only console mode.

## Key Lessons

1. **Binary patches break checksums.** Always verify and fix any
   checksummed data after patching. The sloader's flash segment validation
   is a safety check that caught our modification.

2. **Instruction traces (`-d in_asm`) are invaluable.** They revealed that
   post1rtn was never reached — the failure was in the sloader's segment
   validation, not in post1 execution.

3. **The sloader's `validBody()` return value is inverted from the source.**
   The compiled binary returns 1 for valid (via `sltiu v0, sum, 1`), while
   the source `validBody()` returns the raw checksum value (0 = valid).
   This is a compiler optimization or source version difference.
