# ip32prom-decompiler Tool

A Rust tool that decompiles the SGI O2 (IP32) PROM firmware into modifiable
MIPS assembly and reassembles it bit-identically.

- **Repository**: https://github.com/mattst88/ip32prom-decompiler
- **Author**: Matt St-Amour
- **License**: GPL-3.0-or-later
- **Language**: Rust (2024 edition)
- **Local clone**: `/workspace/ip32prom-decompiler/`

---

## Purpose

The SGI O2 PROM source code is lost. This tool enables modification of the
binary PROM image by:

1. Parsing the SHDR-segmented binary format
2. Disassembling MIPS code via BFS from known entry points
3. Identifying strings, data, headers, checksums
4. Producing GNU GAS assembly with symbolic labels
5. Reassembling to produce a bit-identical binary

The primary use case is enabling RM7900 (900 MHz) CPU support in the O2 PROM,
which requires modifying CPU detection and initialization routines.

---

## Prerequisites

- **Rust toolchain** (cargo)
- **MIPS cross-compiler** (e.g., `mips64-unknown-linux-gnu-gcc`)
- **C preprocessor** (cpp)
- **PROM binary**: `ip32prom.rev4.18.bin` (MD5: `c9725e036052cf1f3e6258eb9bc687fa`)

---

## Rust Dependencies

| Crate | Version | Purpose |
|-------|---------|---------|
| capstone | 0.14 | MIPS instruction disassembly |
| byteorder | 1.5 | Big-endian binary I/O |
| anyhow | 1.0 | Error handling |
| clap | 4 (derive) | CLI argument parsing |
| serde | 1.0 (derive) | Serialization for annotations |
| serde_json | 1.0 | JSON annotation file parsing |

---

## Output Structure

The decompiler produces a complete buildable project:

| File | Purpose |
|------|---------|
| `sloader.S` | Assembly for bootstrap section |
| `env.S` | Assembly for environment data section |
| `post1.S` | Assembly for POST diagnostics section |
| `firmware.S` | Assembly for main firmware section |
| `version.S` | Assembly for version/ELF section |
| `definitions.h` | Preprocessor `#define` constants |
| `macros.inc` | GNU GAS macro definitions |
| `sloader.ld` | Linker script for sloader |
| `post1.ld` | Linker script for post1 |
| `firmware.ld` | Linker script for firmware |
| `Makefile` | Build rules for reassembly |
| `*.xpm` | XPM visualization images |
| `*.dot` | Control flow graphs (Graphviz) |

---

## Annotation System

The decompiler uses 6 external JSON files to provide human knowledge that
cannot be derived automatically from the binary. These are stored in
`annotations/` and are separate from the Rust source code.

### annotations/functions.json — 267 entries

Maps addresses to function names. Seeds BFS code discovery for functions
unreachable via direct `jal` (called through jump tables or constructed
addresses).

```json
{ "0x81000000": "firmware_entry" }
{ "0x81000370": "main" }
{ "0xbfc01534": "strlen" }
{ "0x8103b1e4": "malloc" }
{ "0x810442dc": "printf" }
```

Notable function groups:
- CPU init: `cpu_init`, `r10k_cpu_init`, `rm7k_cpu_init`, `r5k_cpu_init`,
  `r4600_cpu_init`, `r4k_cpu_init`, `panic_unsupported_cpu`
- Cache: `cache_init`, `r5k_cache_init_uncached`, `r10k_l1i_index_wb_inv`,
  `rm7k_l1d_index_wb_inv`
- Memory: `malloc`, `free`, `realloc`, `calloc`
- String: `strlen`, `strcmp`, `strcpy`, `strncmp`, `strstr`
- I/O: `getchar`, `putchar`, `printf`, `sprintf`
- Boot: `start_me_up`, `firmware_entry`, `firmware_init`

### annotations/labels.json — 991 entries

Named addresses for all branch targets and data references. Includes loop
labels, local labels, jump table names, and literal pool entries.

### annotations/comments.json — 130 entries

Per-instruction comments documenting hardware access, initialization steps,
and algorithm behavior.

```json
{ "0x81000050": "Zero BSS" }
{ "0xbfc00118": "CRIME_MC_STATUS_CTRL; disable ECC" }
{ "0xbfc018c4": "checksum += word[0]" }
```

### annotations/operands.json — 376 entries

Replaces numeric operands with symbolic constants for readability.

```json
{ "0x81000130": { "0x20": "ISA_GREEN_LED" } }
{ "0x81004e0c": { "0x28": "PRID_IMP_NEVADA" } }
{ "0x81004da8": { "0xf": "CRIME_ID_REV" } }
```

Hardware register constants include CRIME registers, CP0 fields, cache
operations, TLB flags, and SHDR offsets.

### annotations/relocations.json — 1 entry

Defines code stored in ROM but executed from a different RAM address:

```json
{
  "section": "post1",
  "rom_start": "0xbfc06fd0",
  "rom_end": "0xbfc09120",
  "vma": "0xa0004000",
  "elf_section": "text_ram"
}
```

### annotations/bss.json — 4 entries

Named BSS (uninitialized data) symbols:

```json
{ "0x0": "bss_start" }
{ "0xaa0": "render_base" }
{ "0xaa4": "crime_base" }
{ "0xaa8": "gbe_base" }
```

---

## Usage

```bash
# Build the decompiler
cargo build --release

# Decompile a PROM image (output to output/)
cargo run --release -- ip32prom.rev4.18.bin

# Or use the Makefile
make decompile PROM_IMAGE=../ip32prom.rev4.18.bin

# Rebuild from decompiled sources
make rebuild

# Verify bit-identical reassembly
make check

# Full cycle: decompile + rebuild + verify
make all
```

---

## Verification

`make check` compares the reassembled `prom.bin` against the original binary.
Bit-identical output confirms that the PROM structure has been correctly
understood and that no information was lost during decompilation.

---

## Tested PROMs

- **PROM 4.18** (rev4.18): Primary target, fully tested
- Other versions are expected to work but not yet verified

---

## Relevance to Our Project

### Complementary Analysis

The ip32prom-decompiler complements our MCP-based analysis tools:

| Aspect | MCP Tools | ip32prom-decompiler |
|--------|-----------|---------------------|
| Scope | All SGI PROMs | IP32 only |
| Approach | Binary queries | Full decompilation |
| Output | On-demand results | Complete assembly source |
| Modification | Read-only analysis | Enables PROM patching |
| Speed | Instant queries | One-time processing |

### Annotation Import

The annotation JSON files could be imported into our MCP function database
for the IP32 PROM, providing 267 named functions, 991 labels, and 376
symbolic constant mappings. This would enrich our `build_function_database`
and `export_symbols` tool output for IP32.

### Structural Validation

The bit-identical reassembly proves the SHDR format is fully understood.
Our PROM analysis in `gathered_documentation/proms/IP32_O2.md` can be
validated against the decompiler's parsing code.
