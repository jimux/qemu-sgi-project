# IP32 (O2) PROM Reverse Engineering Lessons

Technical findings from Matt St-Amour's reverse engineering of the SGI O2 PROM
firmware, published 2026-02-08. This document synthesizes insights from the
[blog post](https://mattst88.com/blog/2026/02/08/Reverse_Engineering_the_PROM_for_the_SGI_O2/),
the [ip32prom-decompiler](https://github.com/mattst88/ip32prom-decompiler)
repository's `doc/reverse-engineering.md`, and its annotation files.

See also:
- `gathered_documentation/proms/IP32_O2.md` — hardware-level PROM analysis
- `progress_notes/o2/ip32prom_decompiler_tool.md` — the decompiler tool itself
- `progress_notes/o2/o2_implementation.md` — our IP54 PROM build and QEMU work

---

## 1. SHDR Binary Format (Definitive Reference)

The O2 PROM uses a segmented format with **SHDR** (Section Header) markers.
Each section begins with a 72-byte header (or 64 bytes for data-only sections
without the trailing metadata). This supersedes the less precise struct
definition in `gathered_documentation/proms/IP32_O2.md`.

### Complete Header Layout

```
Offset  Size  Field               Description
------  ----  ------------------  ------------------------------------------
0x00    8     Entry instructions  Branch over SHDR + delay slot (code sections)
                                  or nop/nop (data sections), or ELF magic (version)
0x08    4     Magic number        "SHDR" = 0x53484452
0x0c    4     Section length      Total section size in bytes (uint32 BE)
0x10    1     Name length         Length of name string (uint8)
0x11    1     Version length      Length of version string (uint8)
0x12    1     Section type        Bitfield: bit 0 = code(1)/data(0),
                                  bit 1 = has subsection metadata
0x13    1     Padding             Usually 0x00 (0x08 in version section = EM_MIPS)
0x14    32    Name string         Null-terminated, zero-padded to 32 bytes
0x34    8     Version string      Null-terminated, zero-padded to 8 bytes
0x3c    4     SHDR checksum       Two's complement over bytes [0x08-0x3c)
0x40    4     Metadata #1         Load address (type & 2), else 0 or N/A
0x44    4     Metadata #2         .text length (type & 2), else 0 or N/A
[end-4] 4     Section checksum    At (section_offset + section_length - 4)
```

**Total SHDR size**: 72 bytes for code sections (type & 1), 64 bytes for
data-only sections (type == 0, no metadata fields).

### Section Type Bitfield

| Bit | Meaning | Sections |
|-----|---------|----------|
| 0   | Contains executable code | sloader (1), post1 (1), firmware (3) |
| 1   | Has subsection metadata in trailing 8 bytes | firmware (3) |

When bit 0 is set, the entry instructions branch over the SHDR to code.
When bit 1 is set, metadata #1 and #2 contain the load VMA and .text length.

### Checksum Algorithm

**Two's complement checksum**: sum all 32-bit big-endian words, negate the
result. When the stored checksum is added back, the total is zero.

```c
uint32_t compute_checksum(uint32_t *data, size_t num_words) {
    uint32_t sum = 0;
    for (size_t i = 0; i < num_words; i++)
        sum += be32_to_cpu(data[i]);
    return -sum;  /* two's complement negation */
}
```

The PROM's verification function (`is_section_checksum_valid` at 0xbfc01874)
is unrolled 4x:

```
checksum_main_loop:
    lw      $t9, 0($v0)         # word[0]
    lw      $t0, 4($v0)         # word[1]
    lw      $t1, 8($v0)         # word[2]
    addu    $a2, $a2, $t9       # checksum += word[0]
    lw      $t2, 0xc($v0)       # word[3]
    addu    $a2, $a2, $t0       # checksum += word[1]
    addiu   $v0, $v0, 0x10      # advance 16 bytes
    addu    $a2, $a2, $t1       # checksum += word[2]
    bne     $v0, $a1, checksum_main_loop
     addu   $a2, $a2, $t2       # checksum += word[3]
checksum_done:
    jr      $ra
     sltiu  $v0, $a2, 1         # return (checksum == 0)
```

**SHDR checksum** covers bytes [0x08, 0x3c) — the magic through version string.
**Section checksum** covers data after the SHDR through end of section. Since a
valid SHDR checksum means the SHDR words sum to zero, the section checksum
calculation skips the SHDR entirely.

### Per-Section Checksums

| Section  | Section Checksum | SHDR Checksum  |
|----------|------------------|----------------|
| sloader  | `0x15d0fa4f`     | `0x8cb4693c`   |
| env      | `0xeba16bb0`     | `0x131811ae`   |
| post1    | `0x6c91c641`     | `0xc516c9e5`   |
| firmware | `0xd1c38847`     | `0x82b4a297`   |
| version  | `0x108fedea`     | `0x012d56b7`   |

---

## 2. Section Inventory with Virtual Addresses

### Complete Section Table

| ROM Offset | Name     | Size     | Type | Virtual Address | Content |
|------------|----------|----------|------|-----------------|---------|
| 0x00000000 | sloader  | 16,384   | 1    | 0xBFC00000      | Bootstrap loader, exception vectors |
| 0x00004000 | env      | 1,024    | 0    | N/A (data)      | Default environment variables |
| 0x00004400 | post1    | 19,780   | 1    | 0xBFC04400      | POST diagnostics + RAM subsection |
| 0x00009200 | firmware | 393,212  | 3    | 0x81000000      | Main firmware (3 subsections) |
| 0x00069200 | version  | 904      | 0    | N/A (ELF)       | Version info as ELF32 overlay |

**Notes**:
- sloader executes from KSEG1 (uncached, unmapped) at the MIPS reset vector
- env contains ASCII key=value pairs (AutoLoad, console, dbaud, etc.)
- post1 includes a relocatable subsection copied to RAM at 0xA0004000
- firmware runs from KSEG0 (cached) after being copied to physical 0x01000000
- Padding between sections rounds up to 0x100-byte boundaries
- Total used: ~422 KB of 512 KB PROM

### Firmware Subsection Table

The firmware section (type=3, bit 1 set) contains a subsection table.
Metadata #1 (0x81000000) is the load address; metadata #2 (0x00048e70) is the
.text length. Additional subsection headers are embedded at the boundary of
each subsection.

Each subsection header is 8 bytes: address (uint32 BE) + length (uint32 BE).
A zero-length entry serves as the sentinel.

| Subsection | Load Address | Length (bytes) | Length (hex) | Content |
|------------|--------------|----------------|--------------|---------|
| .text      | 0x81000000   | 298,608        | 0x00048e70   | Executable code |
| .rodata    | 0x81048e70   | 45,712         | 0x0000b290   | Strings, jump tables |
| .data      | 0x81054100   | 48,864         | 0x0000bee0   | Initialized read-write data |
| sentinel   | 0x81000000   | 0              | 0x00000000   | Terminates subsection list |

The firmware was likely compiled as a static ELF binary, then had its sections
extracted and repacked into the SHDR format with the custom subsection table.

### Post1 Relocation

The post1 section contains a code blob that is copied to RAM and executed at
a different address:

- **ROM storage**: 0xBFC06FD0 (within post1's ROM range)
- **RAM execution**: 0xA0004000 (KSEG1, uncached)
- **Jump targets**: `jal` instructions encode 0xB000xxxx targets, which resolve
  correctly when executing from 0xA0004000 (high 4 bits = 0xA, combined with
  28-bit field = 0x0004xxx)

---

## 3. MIPS Decompilation Techniques

### BFS Code Discovery

The decompiler uses breadth-first search starting from known entry points:

1. Queue the first branch target in each code section
2. Dequeue an address, disassemble the instruction
3. For branches: queue the target address as code
4. For jumps (`jal`, `j`): reconstruct full target and queue it
5. Continue until queue is empty

Initial results were poor (~10% identified) until jump target reconstruction
was corrected with proper VMA knowledge.

### Branch vs Jump Addressing

**Branches** use PC-relative offsets — they work regardless of execution address:
```
10000011  b  0x48    # Jumps 0x48 bytes forward from current PC
```

**Jumps** encode a 26-bit field, left-shifted by 2, providing the low 28 bits
of the target. The high 4 bits come from the current PC:
```
0ff0023c  jal  target
  target = (PC & 0xF0000000) | (field << 2)
```

This means disassembly requires knowing the execution VMA. Without
`--adjust-vma=0xbfc00000`, objdump produces wrong jump targets. The firmware
section executes from 0x81000000, not 0xBFC09200, so its jumps use different
high bits.

### Delay Slot Handling

MIPS branch/jump instructions always execute the following instruction (the
delay slot) regardless of whether the branch is taken. The decompiler
indents delay slot instructions to visually mark them:

```
beql    $t6, $t8, L_0xbfc05824
 addiu  $v1, $s1, 2              # delay slot — always executes
```

### Unreachable Code Detection

The compiler sometimes duplicates the delay slot instruction after an
unconditional branch, creating unreachable code:

```
beql    $t6, $t8, L_0xbfc05824
 addiu  $v1, $s1, 2              # delay slot
b       L_0xbfc05894
 ori    $v0, $v1, 0x100          # delay slot
addiu   $v1, $s1, 2              # unreachable — compiler artifact
```

The decompiler detects unknown data between identified code blocks and marks
it as unreachable with a comment. This appears to be a minor compiler bug
where delay slot filling leaves behind dead instructions.

### Jump Table Identification

Jump tables in .rodata contain sequences of code addresses. The decompiler
identifies these by finding `jr` instructions that load from .rodata
addresses, then treats those addresses as function pointers.

### Constructed Address Recognition

LUI + ORI/ADDIU pairs construct 32-bit addresses or constants:
```
lui     $t1, 0x8100          # Upper 16 bits
ori     $t1, $t1, 0x0370     # Lower 16 bits → 0x81000370
```

The decompiler tracks these pairs to identify code references, hardware
addresses, and magic constants. Notable constants found this way:
- `133333000` — clock frequency in Hz
- `31536000` — seconds per year (365 days)
- `0x53484452` — "SHDR" magic value

### Statically-Unreachable Functions

Some functions are never called via direct `jal` and cannot be found by BFS:
- Called via jump tables (indirect `jr` through table lookup)
- Called via constructed addresses (LUI+ORI then `jalr`)
- Actually dead code

These require manual annotation in `functions.json` to seed the BFS queue.

### Tools

- **Capstone library** — MIPS instruction disassembly in Rust
- **objdump**: `mips64-unknown-linux-gnu-objdump -D -b binary -m mips -EB --adjust-vma=0xbfc00000`
- **strings** / **file** for initial reconnaissance

---

## 4. XPM Visualization Technique

The decompiler generates XPM (X PixMap) images to visualize binary structure.
This proved invaluable for tracking analysis progress, identifying unclassified
regions, and providing visual motivation.

### Format

- **128 pixels per row**, each pixel represents one 32-bit word
- One row = 512 bytes of the binary image
- Naturally aligned with MIPS 4-byte instructions

### Color Scheme

| Color | Meaning |
|-------|---------|
| Red   | Executable code |
| Blue  | Headers and checksums |
| Green | ASCII string data |
| Yellow| Memory accessed by load/store instructions |
| Black | 0x00000000 (nop padding) |
| White | 0xFFFFFFFF (erased flash) |
| Gray  | Unknown / unclassified |

The progression from mostly-gray to fully-colored images tracks reverse
engineering completeness. The final PROM image is almost entirely classified.

---

## 5. CPU-Specific Initialization

The O2 supports multiple MIPS CPU families. The PROM detects the CPU via
CP0 PRID and dispatches to CPU-specific initialization routines.

### PRID-Based CPU Detection

| PRID Value | CPU | Constant |
|------------|-----|----------|
| 0x23       | R5000 | `PRID_IMP_R5000` |
| 0x28       | Nevada (R5000 variant) | `PRID_IMP_NEVADA` |
| 0x27       | RM7000 | `PRID_IMP_RM7000` |
| default    | R10000/R12000 | (fallback) |

Detection code reads CP0 register 15 (PRID), masks with `PRID_IMP_MASK`
(`0xff00`), shifts right 8, and compares against known values.

### CPU Init Functions (from functions.json)

| Address    | Function | CPU |
|------------|----------|-----|
| 0x810040c0 | `cpu_init` | Dispatcher |
| 0x810041a4 | `r10k_cpu_init` | R10000/R12000 |
| 0x810041b4 | `rm7k_cpu_init` | RM7000 |
| 0x810041c4 | `r5k_cpu_init` | R5000/Nevada |
| 0x810041d4 | `r4600_cpu_init` | R4600/R4700 |
| 0x810041e4 | `r4k_cpu_init` | R4000/R4400 |
| 0x810041f4 | `panic_unsupported_cpu` | Unknown CPU |

### TLB Initialization

The `tlb_init` function (0xBFC019C8) branches to CPU-specific TLB setup:

**RM7000 TLB**:
- 48 entries (index 0–47, `RM7000_NUM_TLB_ENTRIES-1` = 47)
- Page size: 8KB (`PAGEMASK = 0`, page offset mask = `0x1fff`)
- Virtual base: 0x0FFFE000 (descending)
- ENTRYLO flags: `ENTRYLO_G | ENTRYLO_C_UNCACHED` = 0x11

```
Loop: tlbwi → decrement index → subtract 0x2000 from VMA → bgtz loop
```

**R5000 TLB**: 48 entries (same as RM7000 count)
**R10000 TLB**: 64 entries

### Cache Operations

**L1 I-cache**: `INDEX_WRITEBACK_INV` (invalidate all lines)
- Size computed from CONFIG register: base 0x1000, shifted by `CONF_IC` bits
- Line size: 32 bytes (`CACHE_LINE_SIZE = 0x20`)
- Cache instruction opcode: `CACHE_TYPE_L1I | INDEX_WRITEBACK_INV` = 0

**L1 D-cache**: `INDEX_STORE_TAG` (clear tags to invalidate)
- RM7000-specific: `RM7K_TAGHI_PTAG_SHIFT = 8`
- Operates from KSEG0 base (0x80000000)
- Cache instruction opcode: `CACHE_TYPE_L1D | INDEX_STORE_TAG` = 9

**RM7000 tertiary cache**: Disabled via CONFIG register
```
mfc0    $t0, $CP0_CONFIG
li      $at, ~RM7K_CONF_TE      # Disable tertiary cache enable
and     $t0, $t0, $at
li      $at, ~CONF_CU           # 0xfffffff7
and     $t0, $t0, $at
mtc0    $t0, $CP0_CONFIG
```

### CP0 Registers Used

| Register | Number | Purpose |
|----------|--------|---------|
| INDEX    | 0      | TLB entry index for tlbwi |
| ENTRYLO0 | 2      | TLB physical page frame (even) |
| ENTRYLO1 | 3      | TLB physical page frame (odd) |
| PAGEMASK | 5      | TLB page size mask |
| ENTRYHI  | 10     | TLB virtual page number |
| PRID     | 15     | Processor ID — CPU detection |
| CONFIG   | 16     | Cache configuration |
| TAGHI    | 29     | Cache tag (RM7000 specific) |

---

## 6. ELF/SHDR Dual Header in Version Section

The version section's SHDR is simultaneously a valid ELF32 header. This
clever encoding allows the version section to be both a valid SHDR segment
and a standalone ELF binary.

### Field-by-Field Mapping

```
Offset  SHDR Field          ELF Field           Value
------  -----------------   -----------------   --------------------------
0x00    Entry instr [0:4]   e_ident[EI_MAG]     0x7f454c46 = "\x7fELF"
0x04    Entry instr [4:8]   e_ident[4:8]        0x01020100
                              EI_CLASS=ELFCLASS32(1)
                              EI_DATA=ELFDATA2MSB(2)
                              EI_VERSION=EV_CURRENT(1)
                              EI_OSABI=ELFOSABI_NONE(0)
0x08    Magic "SHDR"        e_ident[8:12]       Overlaid (ABI version + pad)
0x0c    Section length      e_ident[12:16]      Overlaid (padding bytes)
0x10    Name length (7)     e_type (low byte)   Overlaid
0x11    Version length (4)  e_type (high byte)  Overlaid
0x12    Section type (0)    e_machine [0]       0x00
0x13    Padding (8)         e_machine [1]       0x08 = EM_MIPS
```

### Full ELF Header Values

```c
Ehdr->e_machine   = EM_MIPS;           /* 0x0008 */
Ehdr->e_phoff     = 0x00000000;        /* No program headers */
Ehdr->e_shoff     = 0x00000244;        /* Section headers at offset 580 */
Ehdr->e_flags     = EF_MIPS_ARCH_2 | EF_MIPS_NOREORDER | EF_MIPS_PIC;
Ehdr->e_ehsize    = 52;
Ehdr->e_phentsize = 0;
Ehdr->e_phnum     = 0;
Ehdr->e_shentsize = 40;
Ehdr->e_shnum     = 8;                 /* 8 ELF sections */
Ehdr->e_shstrndx  = 7;
```

The version section can be extracted and identified by `file`:
```
$ file version.bin
version.bin: ELF 32-bit MSB MIPS, MIPS-II (SYSV)
```

---

## 7. Constants and Magic Numbers

| Value | Type | Description |
|-------|------|-------------|
| `0x53484452` | Magic | "SHDR" section header marker |
| `0x7f454c46` | Magic | "\x7fELF" ELF binary marker |
| 133,333,000 | Clock | CPU clock frequency in Hz |
| 31,536,000 | Time | Seconds in 365 days |
| 32 | Cache | Cache line size in bytes (`CACHE_LINE_SIZE`) |
| `0x1fff` | Mask | Page offset mask (8KB pages) |
| `0x20` | LED | ISA green LED control value |
| `0x30` | LED | ISA red+green LED control value |
| 0x80000000 | Address | KSEG0 base (cached, unmapped) |
| 0xA0000000 | Address | KSEG1 base (uncached, unmapped) |
| 0xBFC00000 | Address | MIPS reset vector (KSEG1) |
| 0xB4000000 | Address | CRIME base address |
| 0x81000000 | Address | Firmware execution VMA (KSEG0) |
| 0xA0004000 | Address | Post1 RAM subsection execution VMA |

---

## 8. Boot Flow Summary

From the annotation data, the complete boot flow is:

1. **Reset** (0xBFC00000): Branch to 0xBFC00048, then to `start_me_up` (0xBFC003A8)
2. **NMI/soft-reset check**: Test `ST0_NMI|ST0_SR` and CRIME_CONTROL flags
3. **GPR/FPR init**: Zero all general-purpose and floating-point registers
4. **KSEG0 config**: Set cacheable, noncoherent
5. **CRIME init**: Disable ECC, reset ISA controller, set LED green
6. **Cache init**: CPU-specific L1I/L1D invalidation
7. **TLB init**: CPU-specific TLB entry initialization
8. **Checksum verification**: Validate each section's checksum
9. **Copy firmware to RAM**: Load .text/.rodata/.data to physical 0x01000000
10. **Jump to firmware**: Enter `firmware_entry` at 0x81000000 (KSEG0 cached)
11. **BSS zeroing** (0x81000050): Clear uninitialized data
12. **Stack init** (0x81000090): Set up stack pointer
13. **firmware_init** (0x81000048) → **main** (0x81000370): Full PROM operation

---

## 9. Relevance to Our Project

### Complementary to MCP Analysis

Our MCP-based PROM analysis tools work with binary inspection (hexdump,
disassembly, pattern matching). The ip32prom-decompiler takes a different
approach — producing complete annotated assembly that reassembles
bit-identically. The two approaches complement each other:

- **MCP tools**: Quick queries, cross-PROM comparison, hardware annotation
- **ip32prom-decompiler**: Deep structural understanding, modifiable output

### SHDR Format Understanding

The definitive SHDR format documented here corrects imprecise field
definitions in our earlier analysis (e.g., `type_version` was actually
four separate fields: name_length, version_length, type, padding).

### Techniques Applicable to Other PROMs

While IP22/IP24 PROMs use monolithic (non-SHDR) format, several techniques
transfer:
- BFS code discovery from entry points
- LUI+ORI address reconstruction
- Delay slot and unreachable code handling
- XPM visualization for analysis progress tracking
- Two's complement checksums (if used by other SGI PROMs)

### Function Database Cross-Reference

The 267 annotated functions in `functions.json` provide ground truth for
IP32 PROM behavior. Key functions like `strlen`, `strcmp`, `malloc`, `printf`,
`putchar`, `getchar` can be compared against our IP54 PROM build and against
the IRIX kernel's expectations of ARCS firmware callbacks.
