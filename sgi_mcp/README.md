# SGI Emulation MCP Server

MCP server for SGI/IRIX emulation: QEMU build/run/debug, IRIX installation, PROM analysis, Ghidra decompilation, filesystem tools, and IRIX kernel inspection.

## Features

- **MIPS Disassembly** with hardware annotations using Capstone
- **Pattern Detection** for hardware probes, exception vectors, graphics init
- **Cross-PROM Comparison** with diff and common code detection
- **Hardware Register Definitions** from MAME and NetBSD sources

## Installation

```bash
cd sgi_mcp
pip install -e .
```

Or install dependencies directly:

```bash
pip install capstone>=5.0.0 mcp>=1.0.0
```

## Usage

### As MCP Server

Add to your Claude Code configuration:

```json
{
  "mcpServers": {
    "sgi": {
      "command": "python3",
      "args": ["-m", "sgi_mcp.server"],
      "cwd": "/path/to/workspace"
    }
  }
}
```

### Direct Python Usage

```python
from sgi_mcp.prom_loader import list_prom_files, get_prom_metadata
from sgi_mcp.disassembler import disassemble_prom
from sgi_mcp.pattern_detector import find_hardware_probes

# List available PROMs
proms = list_prom_files()

# Get metadata
meta = get_prom_metadata("Indy_ip24prom.070-9101-007.bin")
print(f"Platform: {meta.platform}, Entry: 0x{meta.entry_point:08x}")

# Disassemble
lines = disassemble_prom("Indy_ip24prom.070-9101-007.bin", max_instructions=50)

# Find hardware probes
from sgi_mcp.prom_loader import load_prom
data = load_prom("Indy_ip24prom.070-9101-007.bin")
probes = find_hardware_probes(data)
```

## Available Tools

### Basic Analysis
- `list_proms` - List available PROM files
- `info` - PROM metadata (size, platform, entry point, SHA256)
- `hexdump` - Simple hex dump
- `xxd` - Full xxd-compatible dump with all options
- `disassemble` - MIPS disassembly with annotations
- `strings` - Extract ASCII strings

### Structure Detection
- `find_entry_points` - Reset vector and entry point
- `find_vector_table` - Exception vectors (BEV mode)
- `find_function_prologues` - Function start patterns
- `find_jump_tables` - Switch/case tables

### Hardware Patterns
- `find_hardware_probes` - MMIO access patterns
- `find_graphics_init` - Newport/GR2 setup
- `find_memory_detection` - RAM sizing code
- `find_device_detection` - GIO slot probing

### Comparative Analysis
- `diff_proms` - Binary diff with context
- `find_common_code` - Shared routines
- `signature_search` - Pattern search across files
- `version_compare` - Revision comparison

### Cross-Reference
- `xref_address` - Find references to address
- `annotate_address` - Hardware annotation for address
- `list_devices` - Known devices and addresses
- `device_registers` - Registers for specific device

## Supported Platforms

| Platform | System | CPU |
|----------|--------|-----|
| IP4 | Professional IRIS 4D/50 | MIPS R2000 |
| IP6 | 4D/20 | MIPS R2000/R3000 |
| IP12 | Indigo R3000 / 4D/35 | MIPS R3000 |
| IP15 | 4D/4x0 | MIPS R4000 |
| IP17 | Crimson | MIPS R4000 |
| IP20 | Indigo R4000 | MIPS R4000 |
| IP22 | Indigo2 | MIPS R4000/R4400 |
| IP24 | Indy | MIPS R4600/R5000 |
| IP26 | Indigo2 Power | MIPS R8000 |
| IP28 | Indigo2 Impact | MIPS R10000 |
| IP30 | Octane | MIPS R10000/R12000 |
| IP32 | O2 | MIPS R5000/R10000 |

## Hardware Devices

The server includes register definitions for:

- **MC** (Memory Controller) - 0xbfa00000
- **HPC3** (Peripheral Controller) - 0xbfb80000
- **IOC2** (I/O Controller 2 / INT3) - 0xbfbd9000/0xbfbd9880
- **REX3** (Newport Graphics) - 0xbf0f0000

## License

BSD-3-Clause
