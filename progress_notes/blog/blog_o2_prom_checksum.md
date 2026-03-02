# The Checksum That Guards the Gate: Booting a Real O2 PROM

## A New Challenge

While the SGI Indy and Indigo 2 emulation was maturing (it had graphics, networking, a full IRIX desktop) we had a parallel and more difficult goal: bring up the SGI O2 (IP32), a later-generation machine with completely different architecture. Where the Indy uses the MC memory controller, HPC3 peripheral controller, and Newport graphics, the O2 uses CRIME (Combined Rendering, I/O, and Memory Engine), MACE (Multimedia, Audio, and Communications Engine), and GBE (Graphics Backend). While MAME had provided given us a ship on which to sail and an experienced crew, I was in uncharted waters here. There was no reference implementation, but some experience in the belt and established patterns to inform going forward.

It was different chips, different memory maps, different boot sequences, etc. But the same methodology: let the PROM tell you what it needs, and hope I don't get lost in strange seas.

## The PROM Landscape

I had collected five O2 PROM images to work with. Four were in a `PROM` container format: They started with the magic bytes `50524f4d` ('PROM') and had an internal structure we hadn't decoded. One was a raw 512KB flash dump: `O2_ip32prom.rev4.18.bin`. It started with `SHDR` segment headers, the actual flash layout as it exists on the physical chip.

The four container-format PROMs all hung silently when loaded. No serial output, no register accesses beyond the reset vector. The raw flash dump was the only option that appeared to have life.

## The Flash Segment Format

The raw PROM had five segments, each with a 64-byte header:

| Offset | Name | Size | Purpose |
|--------|------|------|---------|
| 0x0000 | sloader | 16KB | First-stage loader |
| 0x4000 | env | 1KB | NVRAM environment variables |
| 0x4400 | post1 | 19.3KB | POST and hardware init |
| 0x9200 | firmware | 393KB | Main ARCS firmware |
| 0x69200 | version | 904B | Version string (4.18) |

Each segment header contains a magic value (`SHDR` = 0x53484452), the segment's total size, its name, and crucially a checksum complement. Both the header and the body have checksums: 32-bit word sums that must equal zero.

The sloader runs first. It initializes the most basic hardware (CRIME configuration, MACE serial, cache), then finds and validates the `post1` and `firmware` segments by scanning for `SHDR` magic at page-aligned offsets, verifying their checksums, and jumping to their entry points.

## Six Changes for One Boot

Getting the real O2 PROM to the System Maintenance Menu required six changes:

### 1. SEG1 RAM Aliases

The PROM's `SizeMEM()` routine probes memory through an unusual path. Instead of accessing RAM at its normal physical address (0x00000000), it uses TLB entries to map through the CRIME memory controller's SEG1 address space at physical 0x40000000+. Each of CRIME's 8 memory banks gets a 128MB window in SEG1.

With 32MB SIMMs, RAM mirrors four times within each 128MB window. The PROM writes a pattern, reads it back from the mirrored address, and uses the mirror behavior to determine SIMM size.

I had to create `memory_region_init_alias()` entries for each populated bank (with 4x mirroring) and `create_unimplemented_device()` entries for empty banks (returning zero, indicating no SIMM present). Without the aliases, the PROM's TLB-mapped accesses faulted. Without the empty-bank stubs, the PROM couldn't distinguish populated from unpopulated banks.

### 2. DS17287 RTC

The O2 uses a DS17287 RTC (Real-Time Clock), accessed via direct-mapped registers at 256-byte stride within MACE. Register N lives at offset `MACE_RTC_OFFSET + N * 256`. This is different from the Indy's DS1386, which uses index/data port access.

My initial implementation used the wrong access model. The PROM was reading RTC register 0x0A (oscillator status) and getting nonsensical values because we were interpreting the address as an index port write rather than a direct register read.

The fix: proper address decoding (`register = (offset - MACE_RTC_OFFSET) / 256`) with the key registers returning sensible defaults. Register A returns 0x20 (oscillator running), register B returns 0x06 (binary mode, 24-hour format), and register D returns 0x80 (VRT: Valid RAM and Time bit, indicating battery is good).

### 3. ISA DMA and Keyboard/Mouse Stubs

The PROM probes for ISA DMA channels and a PS/2 keyboard/mouse, both accessible through MACE. The DMA writes went to unmapped addresses, and the keyboard reads got bus errors. So simple stubs for now. Accept DMA writes silently, return 0 for keyboard reads, let the PROM decide "no keyboard attached" and continue.

### 4. GBE Register Expansion

The Graphics Backend Engine needed video timing registers (vsync, hsync, blanking, pixel enable), mode registers, CMAP/GMAP arrays, cursor registers, and CMAP FIFO status. The PROM initializes the display pipeline even when no monitor is attached. Without these registers, the PROM crashed during graphics initialization.

The GBE ID register must return a devilish `0x666`, the silicon revision that the PROM expects. Returning 0 or any other value causes the PROM to skip graphics entirely or panic.

### 5. The SimpleMEMtst Patch

And then I hit the wall.

The PROM's `SimpleMEMtst()` function performs a memory test by writing patterns through kseg1 (uncached virtual addresses mapping to physical RAM) and reading them back through kseg0 (cached). On real hardware, the L1 data cache ensures that cached and uncached accesses see the same data — the cache snoops the uncached write and invalidates the stale line.

QEMU doesn't emulate the L1 data cache. So there is no snoop logic. When `SimpleMEMtst()` writes via kseg1 and reads via kseg0, the kseg0 read may return stale data from a previous cached access. Worse, the test function uses the stack, which is at a kseg0 (cached) address. The test patterns are written to the *same physical addresses* as the stack, they're just accessed through a different virtual mapping. Without cache coherency, the uncached writes to the test addresses corrupt the cached stack values, and when the function tries to return, it loads garbage into the return address register and takes an AdEL (Address Error on Load) exception.

The fix was blunt but effective: Patch the PROM to NOP out the `jal simple_memtst` instruction inside `SimpleMEMtst()`. The inner test function never runs, the stack stays coherent, and the PROM continues. It's the right trade-off. Without L1 cache emulation, this test can never pass, and its failure is not indicative of actual memory problems. After all, this is an emulated system, and *real* memory issues are up to the host to identify. While I'm not holding myself to the accuracy standards of MAME, it does feel unclean to do things this way, but I am *not* able to write L1 cache emulation. Way out of scope, and would add complexity and slow emulation down with no benefit.

```c
/* NOP out: jal simple_memtst (0x0FF01463)
 * at offset 0x5054 in the PROM binary */
rom[0x5054 / 4] = 0x00000000;  /* NOP */
```

### 6. The Next Key Fix: Flash Segment Body Checksum

With the SimpleMEMtst NOP in place, I expected the PROM to continue into post1 and then the main firmware. Instead, I got this on the serial console:

```
SL-9600-8E>
```

Huh? With some digging, it seems this is the sloader prompt. This is the firmware download mode. Sloader enters this state when it can't find or validate the post1 segment. It's waiting for someone to upload new firmware over the serial port.

I had changed one word in the PROM binary. That word lived inside the body of the `post1` flash segment. The sloader's `findFlashSegment()` function doesn't just locate segments by name, it validates them by computing the 32-bit word sum of the entire segment body. The sum must equal zero. My NOP patch changed a word from `0x0FF01463` to `0x00000000`, altering the body checksum by exactly `0x0FF01463`. The checksum was no longer zero. `validBody()` returned "invalid." `findFlashSegment("post1")` returned NULL. The sloader, following its programmed logic, entered firmware download mode.

The fix was to restore the checksum by adding the original instruction value to the last word of the post1 body:

```c
/* After NOP-ing the JAL, fix the body checksum.
 * The body checksum is a 32-bit word sum that must equal 0.
 * We removed 0x0FF01463, so we must add it back somewhere.
 * Add it to the last word of the post1 body segment. */
uint32_t original_insn = 0x0FF01463;
uint32_t last_word_offset = 0x9140;  /* Last word of post1 body */
uint32_t old_val = be32_to_cpu(rom[last_word_offset / 4]);
rom[last_word_offset / 4] = cpu_to_be32(old_val + original_insn);
```

The general solution I implemented is more robust: `sgi_o2_fix_segment_body_checksum()` scans flash segments by looking for `SHDR` magic at page-aligned offsets, finds the segment containing the patched address, computes the delta from our modification, and adjusts the last body word to compensate. This way, if I ever need to make additional patches, the checksum always gets fixed automatically.

## The Boot

With all six changes in place:

```
System Maintenance Menu

1) Start System
2) Install System Software
3) Run Diagnostics
4) Recover System
5) Enter Command Monitor
6) Reboot

Option?
```

The real O2 PROM rev 4.18, running on emulated CRIME/MACE/GBE hardware, completing full POST and reaching the System Maintenance Menu. It probed for Ethernet (found the MAC registers but no 1-Wire EEPROM for the MAC address is expected), looked for a keyboard (none is expected), and fell back to serial console mode.

About 245 "unimplemented" hardware accesses during boot, all non-blocking: Ethernet MAC initialization, audio probe, and some MACE registers we haven't implemented yet. The PROM handles all of these gracefully, printing its `ds2502_get_eaddr` failure messages and continuing.

A PROM boot is still a *looooong* way from IRIX working. And without MAME to hold my hand, I might never actually finish. It felt good getting this far.

## The Lesson

The checksum fix was the hardest issue to find because its symptoms pointed in the wrong direction. The `SL-9600-8E>` sloader prompt suggested we hadn't reached post1 — which was true, but not because post1 was broken. The sloader was doing exactly what it was designed to do: protecting the system from corrupted firmware. It's just that the way the checksum works wasn't a method familiar to me, but it did make sense.

In the real world, this checksum validation prevents a flipped bit in flash memory from bricking the machine. In our world, it prevented an intentional patch from doing the same. The solution is what any real firmware engineer would do when modifying production firmware. The sloader doesn't care *why* the checksum changed. It only cares that the math works out.

My ever-reliable hero, the instruction trace (`-d in_asm`), was what finally revealed the issue. I could see the sloader executing `validBody()`, computing the word sum, getting a non-zero result, and branching to the "segment invalid" path. Without the trace, I might have spent days looking at post1's code for bugs that weren't there, because post1 never ran at all. Many thanks to QEMU's tooling.
