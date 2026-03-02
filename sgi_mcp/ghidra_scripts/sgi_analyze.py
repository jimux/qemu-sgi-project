# SGI PROM post-import analysis script for Ghidra headless mode
# Creates MMIO memory blocks so Ghidra can resolve hardware register references.
# @category SGI
# @author SGI PROM MCP Server

import json

from ghidra.program.model.mem import MemoryBlockType
from ghidra.program.model.symbol import SourceType

args = getScriptArgs()
output_path = args[0] if args else "/tmp/sgi_analyze_result.json"

program = currentProgram
memory = program.getMemory()
space = program.getAddressFactory().getDefaultAddressSpace()

result = {"blocks_created": [], "errors": []}

# MMIO regions to create as uninitialized blocks
mmio_regions = [
    ("MC",      0x1fa00000, 0x10000),   # Memory Controller (64KB)
    ("HPC3",    0x1fb80000, 0x80000),   # HPC3 Peripheral Controller (512KB)
    ("REX3",    0x1f0f0000, 0x10000),   # Newport REX3 Graphics (64KB)
    ("IOC2",    0x1fbd9000, 0x1000),    # IOC2 Interrupt Controller (4KB)
    ("INT3",    0x1fbd9880, 0x100),     # INT3 (Indy variant)
    ("GIO_GFX", 0x1f000000, 0xf0000),  # GIO Graphics slot
]

for name, base, size in mmio_regions:
    try:
        addr = space.getAddress(base)
        existing = memory.getBlock(addr)
        if existing is not None:
            continue
        block = memory.createUninitializedBlock(
            name, addr, size, False
        )
        block.setRead(True)
        block.setWrite(True)
        block.setExecute(False)
        block.setVolatile(True)
        block.setComment("SGI MMIO region - added by sgi_analyze.py")
        result["blocks_created"].append({
            "name": name,
            "base": "0x%08x" % base,
            "size": size
        })
    except Exception as e:
        result["errors"].append("%s: %s" % (name, str(e)))

# Count functions found by auto-analysis
fm = program.getFunctionManager()
result["function_count"] = fm.getFunctionCount()

# Write result
with open(output_path, "w") as f:
    json.dump(result, f, indent=2)
