# MIPS64->RISC-V Translation Layer for IRIX

## Context

We have a working IRIX 6.5 kernel (`/unix.new`) compiled for MIPS III N32 big-endian (the IP54 custom paravirtual platform on QEMU). The goal of this side-project is to eventually boot a full IRIX system on real RISC-V hardware, starting with the minimum viable milestone: **single-user mode with serial console only**.

This is a green-field project adjacent to the main QEMU emulation work. It does not modify any existing QEMU files.

---

## Approach: Three-Phase Roadmap

### Why not just run QEMU on RISC-V Linux?

Running `qemu-system-mips64 -machine indy` on a RISC-V SBC (Milk-V Pioneer, SiFive Unmatched) would work today and gives ~8-15x overhead -- fine for a serial-only boot proof of concept. **That is Phase 1** and requires zero new code. But the long-term goal requires native execution: a translated IRIX kernel that runs directly on RISC-V hardware with no QEMU intermediary.

### Why not qemu-irix user mode?

`qemu-irix` (already in this repo) translates individual IRIX ELF binaries but cannot boot a kernel -- it requires a Linux host kernel for syscall handling. Not applicable here.

### Chosen approach: Static binary translation + Spike validation

1. **Phase 1 -- QEMU on RISC-V** (zero new code, immediate proof of concept)
2. **Phase 2 -- Static MIPS64->RV64 translator** (new `mips2rv/` Python toolchain)
3. **Phase 3 -- Bare-metal RISC-V boot** (hardware shim + SBI integration)

---

## Phase 1: QEMU on RISC-V Linux (Immediate PoC)

No new code required. Steps:
1. Cross-compile QEMU for `riscv64-linux-gnu` (or use a RISC-V SBC with Linux)
2. Transfer the existing IP54 disk image and PROM
3. Run `qemu-system-mips64 -machine indy ...` on RISC-V hardware
4. Confirm IRIX boots to single-user over serial

**Verification**: `harness_boot` / `qemu_serial_interact` on a RISC-V host.

This validates that the IP54 kernel and disk are correct before building the translator.

---

## Phase 2: Static Binary Translator (`mips2rv/`)

### Planned directory structure

```
mips2rv/
  README.md                  # Architecture overview + ISA delta table
  translator/
    elf_loader.py            # Parse MIPS64 N32 ELF, enumerate sections/symbols
    insn_decode.py           # MIPS32/64 instruction decoder (opcode tables)
    delay_slot.py            # Delay slot reordering (branch + next insn bundling)
    insn_translate.py        # Per-instruction MIPS -> RV64 mapping
    hi_lo.py                 # HI/LO register emulation (map to x5/x6 saved regs)
    cp0_translate.py         # CP0 MFC0/MTC0 -> CSR or software-emulated state
    tlb_rewrite.py           # MIPS TLB miss handler -> RISC-V Sv39 page table writes
    endian.py                # Big-endian load/store -> byte-swap sequences
    elf_writer.py            # Emit RV64 ELF with translated sections
  shim/
    sgi_shim.c               # Minimal SGI hardware register emulation in C (RV64)
    arcs_shim.c              # ARCS firmware vector table stub for RV64
    exception_vectors.S      # RV64 trap handler wired to translated IRIX exception code
    linker.ld                # Link script placing kernel at 0x80002000 (RV64 equiv)
  tests/
    golden/                  # Hand-written MIPS -> expected RV64 pairs
    test_insn.py             # Unit tests: each MIPS opcode -> RV64 sequence
    test_spike.py            # Integration: run translated snippet in Spike, compare regs
    test_compare.py          # Differential: QEMU-MIPS vs Spike-RV64 register traces
  spike_harness/
    run_snippet.py           # Launch Spike on a small ELF, capture register log
    compare_traces.py        # Align QEMU and Spike traces, diff register states
progress_notes/mips2rv/
  README.md                  # This file
  isa_delta.md               # MIPS64 vs RV64 instruction coverage table
  endianness.md              # Big->little endian strategy and edge cases
  tlb_strategy.md            # TLB emulation approach (Sv39 vs software TLB)
  spike_workflow.md          # How to use Spike for validation
```

### Key translation rules

| MIPS feature | RV64 translation | Notes |
|---|---|---|
| 32 GPRs, $zero=0 | 32 GPRs, x0=0 | 1:1 register map (see reg table below) |
| Delay slot (after branch) | Reorder: emit delay insn before branch | delay_slot.py |
| HI/LO registers | Map to x5/x6 (caller-saved scratch) | hi_lo.py |
| MIPS I-type 16-bit imm | RV I-type 12-bit imm; overflow -> LUI+ADDI | insn_translate.py |
| LWL/LWR/LDL/LDR | Multi-insn byte-swap sequence | endian.py |
| LL/SC | LR.W/SC.W (or .D for 64-bit) | Near 1:1 |
| CACHE | FENCE.I (I-cache) / FENCE (D-cache) | insn_translate.py |
| SYNC | FENCE rw,rw | insn_translate.py |
| MFC0/MTC0 (most CP0) | CSRR/CSRW on RV64 CSR equivalents | cp0_translate.py |
| MFC0 PRid | Load constant (MIPS IV PRid = 0x0900) | cp0_translate.py |
| TLBWI/TLBWR/TLBP/TLBR | Call into tlb_rewrite runtime shim | tlb_rewrite.py |
| Exception vectors (0x80000080) | Redirect to stvec-registered RV64 trap | exception_vectors.S |
| Big-endian loads/stores | Wrap with rev8 (Zbb) or manual byte swap | endian.py |
| MIPS FPU (CP1) | RV64 F/D extensions; FCR31 -> fcsr | Phase 3 |
| MSA 128-bit SIMD | RVV (if available) or scalar fallback | Phase 3 |

### Register mapping

```
MIPS $0  (zero) -> x0   (zero)
MIPS $2  ($v0)  -> x10  (a0 -- return value)
MIPS $3  ($v1)  -> x11  (a1)
MIPS $4-$7  ($a0-$a3) -> x12-x15 (a2-a5)
MIPS $8-$15 ($t0-$t7) -> x28-x31, x6-x7 (caller-saved)
MIPS $16-$23 ($s0-$s7) -> x18-x25 (callee-saved s2-s9)
MIPS $26 ($k0) -> x3  (gp -- kernel scratch 0)
MIPS $27 ($k1) -> x4  (tp -- kernel scratch 1)
MIPS $28 ($gp) -> x26 (s10)
MIPS $29 ($sp) -> x2  (sp)
MIPS $30 ($fp) -> x8  (s0/fp)
MIPS $31 ($ra) -> x1  (ra)
HI              -> x5  (t0)
LO              -> x6  (t1)
```

### Endianness strategy

MIPS big-endian N32 -> RV64 little-endian is the most pervasive challenge. Every load/store crossing a byte boundary must be byte-swapped. Strategy:

1. All LW/LH/LD -> load + `rev8` (RV64 Zbb) or explicit byte swap
2. All SW/SH/SD -> byte swap + store
3. Byte-granular accesses (LB/SB, strings, byte arrays) are unaffected
4. Internal kernel data structures are re-laid-out as little-endian at link time

### TLB strategy

MIPS software-managed TLB -> RISC-V hardware page table (Sv39):

- IRIX's TLB miss handler fills 2 MIPS TLB entries per call
- `TLBWI`/`TLBWR` -> write to Sv39 page table entry via satp-mapped root
- `TLBP` (probe) -> walk Sv39 page table
- `TLBR` -> read from Sv39 page table
- TLB miss handler source in IRIX 6.5.5: `software_library/irix-655-source/ml/MIPS3/utlbmiss.s`

The MIPS TLB miss handler is hand-written assembly and must be understood fully before rewriting. This is the hardest single translation problem.

Fallback: keep TLB handler as MIPS code + small MIPS interpreter just for TLB paths (rest of kernel runs natively translated).

---

## Spike Integration

[Spike](https://github.com/riscv-software-src/spike) is the official RISC-V ISA reference simulator (UC Berkeley). We use it to validate our translation layer at instruction, function, and subsystem granularity -- not for performance, but for correctness.

### Validation architecture

```
MIPS source -> [mips2rv translator] -> RV64 ELF
                                           |
                                    [Spike --log-commits]
                                           |
                                    per-insn register log
                                           |
                                    [compare_traces.py]
                                           |
                              QEMU-MIPS trace <-> Spike-RV64 trace
                                     register diff
```

### New MCP tools to add (`sgi_mcp/server.py`)

```python
mcp__sgi__spike_run(elf_path, args=[], isa="rv64gc_zba_zbb", timeout=30)
    # Run a RV64 ELF in Spike, return exit code + stdout/stderr

mcp__sgi__spike_trace(elf_path, start_pc=None, end_pc=None, timeout=30)
    # Run with --log-commits, return per-instruction register log

mcp__sgi__spike_compare(mips_elf, rv64_elf, function_name, input_regs={})
    # Run function in QEMU-MIPS and Spike-RV64 with same inputs,
    # compare register state at return, report diffs

mcp__sgi__spike_disasm(elf_path, address, count=32)
    # Disassemble RV64 ELF at address using Spike built-in disassembler
```

Spike is invoked as: `spike --isa=rv64gc_zba_zbb_zbc_zbs pk <elf>` for user-mode ELFs, or with `--no-pk` for bare-metal machine emulation of the translated kernel.

### Validation milestones

1. **Unit** (`test_insn.py`): Each MIPS opcode -> Spike confirms RV64 sequence produces identical register state
2. **Function** (`test_compare.py`): Simple kernel functions (`strlen`, `bcopy`, `bzero`) -- QEMU-MIPS vs Spike-RV64 register diff = zero
3. **Boot trace** (`test_spike.py`): Translated kernel in Spike reaches ARCS SPB read, then ARCS Write (serial output)

---

## Phase 3: Bare-Metal RISC-V Boot

Once translator produces a Spike-validated RV64 kernel image:

1. **Hardware shim** (`shim/sgi_shim.c`): SGI MMIO registers in software (ARCS SPB at 0x1000, DUART for serial)
2. **ARCS stub** (`shim/arcs_shim.c`): Minimal ARCS vector table (GetMemoryDescriptor, Write, GetEnvironmentVariable)
3. **SBI serial** (`shim/uart_sbi.c`): Wire IRIX DUART write path to `sbi_console_putchar()`
4. **Boot entry** (`shim/start.S`): M-mode -> S-mode transition, set up `stvec`, jump to translated kernel
5. **Target board**: SiFive Unmatched (rv64gc, 16GB, USB serial) -- primary target

---

## Files to Create/Modify

| Path | Action | Description |
|---|---|---|
| `mips2rv/README.md` | Create | Architecture doc |
| `mips2rv/translator/elf_loader.py` | Create | MIPS64 N32 ELF parser |
| `mips2rv/translator/insn_decode.py` | Create | MIPS opcode tables |
| `mips2rv/translator/insn_translate.py` | Create | Instruction-level MIPS->RV64 |
| `mips2rv/translator/delay_slot.py` | Create | Delay slot reordering |
| `mips2rv/translator/hi_lo.py` | Create | HI/LO register mapping |
| `mips2rv/translator/endian.py` | Create | Big-endian load/store wrapping |
| `mips2rv/translator/cp0_translate.py` | Create | CP0 -> CSR translation |
| `mips2rv/translator/tlb_rewrite.py` | Create | TLB handler rewrite -> Sv39 |
| `mips2rv/translator/elf_writer.py` | Create | Emit RV64 ELF |
| `mips2rv/shim/sgi_shim.c` | Create | SGI MMIO stub (RV64 C) |
| `mips2rv/shim/arcs_shim.c` | Create | ARCS firmware stub |
| `mips2rv/shim/exception_vectors.S` | Create | RV64 trap -> IRIX exception dispatcher |
| `mips2rv/shim/linker.ld` | Create | RV64 link script |
| `mips2rv/spike_harness/run_snippet.py` | Create | Run RV64 ELF in Spike, capture log |
| `mips2rv/spike_harness/compare_traces.py` | Create | QEMU-MIPS vs Spike diff |
| `mips2rv/tests/test_insn.py` | Create | Per-opcode unit tests with Spike |
| `mips2rv/tests/test_compare.py` | Create | Function-level differential tests |
| `sgi_mcp/server.py` | Modify | Add spike_run/trace/compare/disasm tools |

---

## Existing Assets to Reuse

| Asset | Location | How used |
|---|---|---|
| IRIX kernel source (TLB handlers) | `software_library/irix-655-source/ml/MIPS3/` | Reference for TLB rewrite |
| MIPS disassembly | `mcp__sgi__disassemble`, `mcp__sgi__ghidra_decompile` | Decode kernel functions before translating |
| QEMU MIPS TCG (ground truth for semantics) | `qemu/target/mips/tcg/translate.c` | Authoritative semantics for each MIPS opcode |
| QEMU RV64 codegen patterns | `qemu/target/riscv/insn_trans/trans_rvi.c.inc` | RV64 idioms to emit |
| Input kernel binary | `/workspace/ip54_tftp_staging/unix.new` | Translation input (6.1MB MIPS N32 ELF) |
| ARCS SPB layout | IRIX source + progress_notes | Reference for ARCS stub |

---

## Milestones & Verification

| # | Milestone | Verification |
|---|---|---|
| M1 | QEMU on RISC-V boots IRIX | `qemu_serial_interact` on RISC-V host -> single-user shell |
| M2 | Spike MCP tools working | `spike_run` returns correct exit code on "hello world" RV64 ELF |
| M3 | 100% instruction unit tests pass | `pytest mips2rv/tests/test_insn.py` -- all MIPS III opcodes covered |
| M4 | bcopy/bzero function tests pass | `compare_traces.py` zero register diffs on QEMU vs Spike |
| M5 | Translated kernel boots in Spike to ARCS SPB read | Spike trace shows memory read at 0x1000 |
| M6 | Translated kernel boots in Spike to serial output | Spike output shows IRIX banner via ARCS Write |
| M7 | Boot on real RISC-V hardware | SiFive Unmatched shows IRIX banner over UART |

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Big-endian complexity underestimated | Start with ARCS Write path only; fix endian shim iteratively using Spike |
| TLB miss handler rewrite too complex | Fall back to keeping TLB code as MIPS + mini-interpreter only for TLB paths |
| FPU required earlier than expected | Recompile kernel with -msoft-float for Phase 2 |
| Undocumented CP0 register usage | Use `qemu_run_sgi` with `-d unimp` to enumerate all CP0 accesses during boot |
| Register allocation conflicts (HI/LO vs t8/t9) | Add liveness analysis pass before register assignment |
| Spike version mismatch | Pin Spike to a known-good commit; add to Dockerfile |

---

## Prior Art & Research Notes

- **XBT** (Case Western Reserve, 2022, IEEE Xplore): FPGA-accelerated static MIPS->RISC-V translator. Key finding: ~95% of MIPS instructions map 1:1 to RISC-V. Main challenges: delay slots, 16-bit vs 12-bit immediates, address mapping.
- **LAST** (Chinese Academy of Sciences, ICA3PP 2023): In-place static binary translator for RISC architectures (MIPS/RISC-V->LoongArch). "In-place" works because all RISC ISAs use fixed 4-byte instructions -- translated code occupies the same address range, eliminating indirection tables.
- **qemu-irix**: User-mode IRIX binary emulator. Works for static N32 binaries and simple dynamic binaries. Fails for MIPSpro `be` backend (libCsup.so static initializers use IRIX-specific usynccntl/prctl). Already in repo at `qemu-irix/`.
- **Box64 RISC-V backend**: x86-64->RV64 dynamic translator. Notes that "RISC-V lacks many convenient instructions," requiring more instructions per translated op. RISC-V Zbb (bit manipulation) helps significantly.
- **RISC-V H extension**: Hardware hypervisor extension -- only accelerates RISC-V guests, irrelevant for MIPS emulation.
- **MIPS company pivot**: MIPS itself published a migration guide noting ~95% instruction coverage overlap between MIPS and RISC-V.
