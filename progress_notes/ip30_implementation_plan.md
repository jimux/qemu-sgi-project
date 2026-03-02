# IP30 (SGI Octane) Implementation Plan

## Summary

Implement SGI Octane (IP30) support in QEMU with dual-CPU support, >2GB RAM, and minimal BRIDGE device.

## Current Status

### Completed
- ✅ sgi_heart.c - HEART system controller implementation
- ✅ sgi_heart.h - HEART header file
- ✅ sgi_bridge.c - BRIDGE peripheral controller (minimal)  
- ✅ sgi_bridge.h - BRIDGE header file
- ✅ sgi_octane.c - Machine implementation
- ✅ Kconfig update - Added SGI_HEART and SGI_BRIDGE entries
- ✅ meson.build updates - Added new source files

### Build Issues
The build is failing with errors related to:
1. `Property` type not being defined
2. `DEFINE_PROP_UINT32` and `DEFINE_PROP_END_OF_LIST` not found

These are macro definitions from QEMU's property system that need proper headers.

## Build Errors
```
../hw/misc/sgi_heart.c:441:43: error: array has incomplete element type 'const Property'
../hw/misc/sgi_heart.c:442:5: error: call to undeclared function 'DEFINE_PROP_UINT32'
../hw/misc/sgi_heart.c:443:36: error: unexpected type name 'SGIHEARTState'
../hw/misc/sgi_heart.c:444:5: error: call to undeclared function 'DEFINE_PROP_END_OF_LIST'
```

## Next Steps

### Fix Build Issues (Priority: High)
1. Add missing header includes to sgi_heart.c:
   - `#include "hw/qdev-properties.h"` for Property and DEFINE_* macros
2. Ensure sgi_heart.h properly includes QEMU headers before type definitions

### Files Created
- `qemu/hw/misc/sgi_heart.c` - HEART device implementation (508 lines)
- `qemu/hw/misc/sgi_heart.h` - HEART header (175 lines)
- `qemu/hw/misc/sgi_bridge.c` - BRIDGE device implementation (179 lines)
- `qemu/hw/misc/sgi_bridge.h` - BRIDGE header (49 lines)
- `qemu/hw/mips/sgi_octane.c` - IP30 machine (331 lines)

### Build System Updates
- `qemu/hw/mips/Kconfig` - Added SGI_OCTANE config
- `qemu/hw/mips/meson.build` - Added sgi_octane.c
- `qemu/hw/misc/Kconfig` - Added SGI_HEART and SGI_BRIDGE configs
- `qemu/hw/misc/meson.build` - Added sgi_heart.c and sgi_bridge.c

### Test Commands
```bash
# Build QEMU
cd qemu/build && ninja mips64-softmmu/qemu-system-mips64

# Test with IP30 PROM
qemu-system-mips64 -M octane -m 64 \
  -bios PROM_library/bins/cpu/ip30/Octane_ip30prom.rev4.9.bin \
  -serial stdio

# Or with default PROM name (ip30prom.bin)
qemu-system-mips64 -M octane -m 64 -serial stdio
```

### Expected Behavior
1. PROM loads from 0x1fc00000
2. HEART device at 0x1ff00000 initialized
3. BRIDGE device at 0x1f400000 initialized
4. GBE graphics at 0x1f800000 initialized
5. Memory mapped starting at 0x20000000
6. PROM POST completes to System Maintenance Menu

### Memory Map (IP30)
```
0x20000000-0x2FFFFFFF  Main memory (512MB start, can be >2GB)
0x1FF00000-0x1FFFFFFF  HEART (system controller)
0x1F400000-0x1F4FFFFF  BRIDGE (PCI/XTalk bridge)
0x1F800000-0x1FBFFFFF  GBE (graphics framebuffer)
0x1FC00000-0x1FC7FFFF  PROM (512KB)
```

### Key Differences from IP32 (O2)
| Feature | IP32 (O2) | IP30 (Octane) |
|---------|-----------|---------------|
| System Controller | CRIME | HEART |
| Peripheral Controller | MACE | BRIDGE |
| CPU | R5000/R10000/R12000/R14000 | R10000 |
| Timer | CRIME_TIME (48-bit, 66.67MHz) | HEART_COUNT (52-bit, 12.5MHz) |
| Memory Start | 0x00000000 | 0x20000000 |
| Max RAM | 2GB (64GB via PVMEM) | >2GB (native) |