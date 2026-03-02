# AGENTS.md

This document provides guidance to agentic coding agents (like Claude Code) working with this SGI emulator codebase.

## Quick Start

**Build and run always use MCP tools** (no raw bash):
```bash
qemu_configure  # Configure QEMU build
qemu_build      # Build with ninja
qemu_run_sgi    # Run QEMU with SGI PROM
```

## Project Structure

| Directory | Purpose |
|-----------|---------|
| `qemu/` | Upstream QEMU v10.2.0+ (target for SGI device emulation) |
| `sgi_mcp/` | MCP server for PROM analysis, build, run, debugging |
| `pyirix/` | SGI/IRIX Python tools (EFS reader, package analysis, catalog) |
| `pyirix_qemu/` | QEMU orchestration (sessions, disk management, harnesses) |
| `tests/` | Pytest test suite (fast source tests + slow boot tests) |
| `PROM_library/` | SGI PROM firmware images |
| `vm_instances/` | VM instance storage (disk images, NVRAM, manifests) |
| `progress_notes/` | Implementation lessons and discoveries |

## Build & Test Commands

### Running Tests
```bash
python3 -m pytest tests/ -m "not slow" -v   # Fast tests (~0.2s each)
python3 -m pytest tests/ -v                 # All tests including slow
python3 -m pytest tests/test_foo.py -v      # Single test file
python3 -m pytest tests/ -k "test_name" -v  # Single test by name
```

### Test Categories
- **Fast** (no QEMU): Register offsets, bitmasks, source code analysis
- **Slow** (30-300s): Full PROM/IRIX boot tests requiring QEMU

### Linting & Type Checking
```bash
cd sgi_mcp && python3 -m mypy sgi_mcp
cd pyirix && python3 -m mypy pyirix
cd pyirix_qemu && python3 -m mypy pyirix_qemu
```

## Code Style Guidelines

### Python (All Projects)

**Imports**
- Standard library first, then third-party, then local
- Import entire modules (not individual functions): `import struct`, not `from struct import unpack`
- Group imports: `os`, `struct`, `typing` → `pytest`, `mcp` → `pyirix.*`, `sgi_mcp.*`

**Types**
- Use `typing` module: `List[str]`, `Dict[str, int]`, `Optional[str]`, `Tuple[int, int]`
- For function parameters: `def func(arg: str) -> int:`
- Use `Any` sparingly; prefer specific types

**Naming**
- Classes: `PascalCase` (`EFSReader`, `SCSIDisk`)
- Functions/variables: `snake_case` (`read_superblock`, `max_extents`)
- Constants: `UPPER_SNAKE_CASE` (`EFS_MAGIC`, `SECTOR_SIZE`)
- Test classes: `Test<Feature>` (`TestMCRegisterOffsets`)
- Test methods: `test_<what>` (`test_cpu_ctrl0`)

**Error Handling**
- Use `pytest.skip()` in tests when prerequisites are missing
- For disk/image files, check existence and skip if not found
- In production code, raise descriptive exceptions for user errors

**Formatting**
- 4 spaces per indentation level
- Max line length: 100 characters
- Single blank line between top-level definitions
- Use docstrings for public modules and classes
- Comments should explain **why**, not **what** (code is self-documenting)

### C (QEMU Device Emulation)

**Header Includes**
- System headers first, then QEMU headers, then local headers
- Group: `<*.h>` → `"qemu/*.h"` → `"hw/*.h"` → `"include/hw/*.h"`

**Constants**
- Use `#define` for register offsets and bitmasks
- Register offsets: `#define REG_NAME 0x0000`
- Bitmasks: `#define REG_FIELD_MASK (1 << 3)`
- Comments indicate bit positions: `/* bit 13 */`

**Memory Access**
- SGI devices use 64-bit bus with 32-bit registers
- Normalize addresses: `addr &= ~7ULL` in read/write handlers
- Both BE (+4) and LE (+0) offsets are used by PROM

**Code Style**
- 4 spaces per indentation
- K&R brace style: `if (cond) {`
- Single line per statement
- Use `qemu_log_mask(LOG_UNIMP, ...)` for unimplemented features
- Include trace events: `trace_sgi_device_<event>(...)`

**Naming**
- Functions: `sgi_device_<action>` (`sgi_hpc3_read`, `sgi_newport_draw_block`)
- Structures: `SGI<Device>State` (`SGIHPC3State`, `SGINewportState`)
- Register names: `DEVICE_<REG>` (`HPC3_TX_TIMER`, `REX3_STATUS`)

### Rust (QEMU, IP32 PROM)

**Crate Layout**
- `qemu/rust/`: QEMU Rust components
- `ip32prom-decompiler/`: IP32 PROM decompiler

**Formatting**
- Use `cargo fmt` (config in `qemu/rust/rustfmt.toml`)
- Clippy enabled; run `cargo clippy` before committing

**Imports**
- Standard library, then `qemu-rust`, then local crates
- Use `use crate::module::symbol;` for local imports

### Bash

**Usage**
- Avoid bash for high-level operations (use MCP tools)
- Bash is acceptable for: file operations, grep, find, sed, awk

**Functions**
- Use `set -e` for fail-fast behavior
- Use `function name { ... }` or `name() { ... }`
- Comment complex logic

## Key Technical Conventions

### Register Address Normalization
SGI 64-bit bus with 32-bit registers requires address normalization:
```c
addr &= ~7ULL;  // Normalize to 64-bit boundary
```

### Memory Map (IP24)
```
0x08000000-0x17ffffff  Low System Memory (256MB)
0x1f000000-0x1f3fffff  GIO64 Graphics (Newport)
0x1fa00000-0x1fa1ffff  Memory Controller (MC)
0x1fb80000-0x1fbfffff  HPC3 Peripheral Controller
0x1fc00000-0x1fc7ffff  PROM (512KB)
```

### Test Tags
- `[CROSS-REF]` - Verified against MAME/datasheet
- `[ASSUMPTION]` - Documents simplifications
- `[INVESTIGATIVE]` - Explores uncertain behavior
- `@pytest.mark.slow` - Requires QEMU boot

### Debug Trace Events
- `trace:sgi_mc_*` - Memory Controller
- `trace:sgi_hpc3_*` - HPC3 Peripheral Controller
- `trace:sgi_newport_*` - Newport Graphics

## MCP Tools Reference

| Tool | Purpose |
|------|---------|
| `qemu_configure` | Configure QEMU build |
| `qemu_build` | Build QEMU with ninja |
| `qemu_run_sgi` | Run QEMU SGI machine |
| `qemu_serial_interact` | Interactive serial session |
| `qemu_session_start` | Persistent QEMU session |
| `qemu_snapshot_save` | Save VM snapshot |
| `newport_sendkey` | Inject keyboard input |
| `newport_mouse` | Inject mouse input |
| `prom_*` | PROM analysis tools |
| `fs_*` | Filesystem tools (EFS/XFS) |
| `library_*` | External library indexing |
| `vm_instance_*` | VM instance management |

## Debugging Workflow

1. **Source-level tests first**: Verify register offsets, bitmasks, constants
2. **QEMU trace logs**: Use `-d unimp,trace:sgi_hpc3_*` to see hardware accesses
3. **PROM analysis**: Use MCP tools to disassemble and analyze firmware
4. **Compare with MAME**: Verify behavior against MAME SGI implementation
5. **Incremental testing**: Small changes, test after each

## Common Pitfalls

- **MCP tools require integers**: `ram_mb=64`, not `64.0` or `"64"`
- **PROM timing is wall-clock bound**: `-icount` doesn't affect PROM boot
- **SCSI syntax matters**: Use `-drive if=scsi`, not `-device scsi-hd`
- **Address normalization**: Always `addr &= ~7ULL` for SGI MMIO
- **Test assumptions**: A failing test means either broken code or wrong assumption

## Testing Philosophy

Tests serve two purposes:
1. **Regression detection**: Catch breaking changes
2. **Assumption documentation**: Record why things work (or don't)

When a test fails after a change:
- Did I break something? → Fix the code
- Did I fix a wrong assumption? → Update the test

## Files Under Active Development

**QEMU device implementations:**
- `qemu/hw/mips/sgi_indy.c` - Main machine definition
- `qemu/hw/misc/sgi_mc.c` - Memory Controller
- `qemu/hw/misc/sgi_hpc3.c` - HPC3 Peripheral Controller
- `qemu/hw/display/sgi_newport.c` - Newport Graphics

**Python orchestration:**
- `pyirix/efs/reader.py` - EFS filesystem reader
- `pyirix/install/irix.py` - IRIX installation harness
- `pyirix_qemu/session.py` - QEMU session management
- `sgi_mcp/server.py` - MCP server (PROM analysis, VM management)