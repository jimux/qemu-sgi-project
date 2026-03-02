# Direct Kernel Boot via `-kernel` with ARCS Firmware Stubs

## Status: Working — IRIX kernel boots to mount root

The IRIX 6.2.1 kernel successfully boots via `qemu-system-mips64 -kernel irix_unix.elf`
and prints its banner. It fails at mount root because no SCSI disk is attached, which is
the expected behavior.

## Serial Output

```
IRIX Release 6.2 IP22 Version 03131015 System V
Copyright 1987-1996 Silicon Graphics, Inc.
All Rights Reserved.

gtr0: missing
Unknown device name "".
Enter Root device on boot command line
Configured device names are:
dks
WARNING: Kernname environment variable not set by sash.
PANIC: vfs_mountroot: no root found
```

## Architecture

### Boot Sequence

1. **Trampoline** at 0x1FC00000 (PROM base): MIPS code that clears BEV/ERL in CP0_Status,
   sets a0=0, a1=0, a2=environ pointer, SP=0x80800000, then jumps to kernel entry.

2. **Kernel entry** at 0x880059C0: csu.s parses a0/a1/a2 (argc/argv/environ), calls
   getargs() to populate kopt table, then calls mlreset() for hardware init.

3. **ARCS hypercall device** at 0x1F000100: MMIO device that implements ARCS callbacks.
   MIPS stub routines in guest memory write function ID + args to MMIO registers and
   read back the result.

### Guest Memory Layout (physical addresses below 0x2000)

```
0x1000  SPB (System Parameter Block) — Signature, TransferVector, PrivateVector
0x1080  FirmwareVector — 35 function pointers to MIPS stubs
0x1110  PrivateVector — 13 function pointers to MIPS stubs
0x1200  Memory descriptors — ExceptionBlock, SPBPage, FirmwarePerm, Free
0x1400  Environment data — key\0value\0 pairs for GetEnvironmentVariable
0x1600  MIPS stub code — 48 stubs × 40 bytes = 1920 bytes
0x1E00  Scratch area — SystemID ("SGI"/"IP24"), TimeInfo
0x1E40  Environ pointer array — char*[] for kernel getargs()
0x1E80  Environ strings — "key=value\0" format for kopt_find()
```

**Critical constraint**: Physical addresses 0x2000+ alias to 0x08002000+ where the
kernel's LOAD segment starts. Data placed at 0x2000+ gets overwritten by the kernel image.

### Implemented ARCS Callbacks

**FirmwareVector (35 slots):**
- GetMemoryDescriptor — enumerates RAM layout
- GetEnvironmentVariable — returns eaddr, cpufreq, console, dbaud, etc.
- Write — outputs to QEMU log
- GetRelativeTime — monotonic counter
- FlushAllCaches — no-op
- Halt/PowerDown/Restart/Reboot — stops QEMU
- GetSystemID — returns "SGI" / "IP24"
- Others — return error codes or NULL

**PrivateVector (13 slots):**
- GetNvramTab — returns 0 (empty table, kernel gets default nvram_tab)
- sgivers — returns 3 (relocatable ELF support)
- cpuid — returns 0 (single CPU)
- cpufreq — returns 175 (MHz)
- Others — return error

### Key Bugs Fixed

1. **ROM overlap**: FirmwareVector (35 × 4 = 140 bytes at 0x1080) extends to 0x110C,
   overlapping original PrivateVector at 0x1100. Fixed: moved PV to 0x1110.

2. **PROM loading conflict**: MCP tool always passes `-bios`, so PROM was loaded even
   with `-kernel`. Fixed: check `kernel_filename` first, skip PROM.

3. **MIPS64 sign extension**: 32-bit entry point 0x880059C0 not sign-extended for
   MIPS64 CPU. Address 0x00000000880059C0 is in xuseg, not kseg0. Fixed: sign-extend.

4. **CPU reset ordering**: QEMU's Resettable mechanism re-resets CPU after
   qemu_register_reset callbacks, undoing PC override. Fixed: write MIPS trampoline
   to PROM ROM area (Malta pattern).

5. **PrivateVector crash**: PV entries were all zeros → kernel calls GetNvramTab(PV[1])
   → jalr $0 → crash. Fixed: generate MIPS stubs for all 13 PV entries.

6. **UART divide-by-zero**: kernel's kopt_find("dbaud") returns empty string → atoi("")=0
   → divide-by-zero in baud rate calculation. Root cause: a2=0 means no environ parsed.
   Fixed: build proper environ array with "key=value" strings and pass via a2.

7. **Environ data overwritten**: environ at physical 0x2000 aliases to 0x08002000 where
   kernel LOAD segment starts. Kernel image overwrites environ data before getargs().
   Fixed: moved environ below 0x2000 (to 0x1E40/0x1E80).

## Files

| File | Description |
|------|-------------|
| `qemu/hw/misc/sgi_arcs.c` | ARCS hypercall MMIO device + stub generator |
| `qemu/include/hw/misc/sgi_arcs.h` | Header with constants, function IDs, state |
| `qemu/hw/mips/sgi_indy.c` | Machine init: kernel loading + trampoline |
| `qemu/hw/misc/meson.build` | Build: `CONFIG_SGI_ARCS` → `sgi_arcs.c` |
| `qemu/hw/misc/Kconfig` | Config: `SGI_ARCS` selected by `SGI_INDY` |

## Note

PROM CD-ROM boot is the primary boot path for IRIX installation. The
`-kernel` path remains useful for quick kernel testing without PROM POST
overhead, but the PROM path exercises the full hardware stack and is where
active development focuses.
