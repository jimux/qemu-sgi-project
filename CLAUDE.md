# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Goal

Full system emulation of SGI systems, start with Indy (IP24), in QEMU to boot IRIX 6.5 with a functional desktop experience including graphics, sound, and networking. Target Indy first as it's the best documented SGI system. MAME has a full implementation. While Linux and NetBSD have partial ability to run on SGI hardware, and their code may include insights we could learn from. But the MAME implementation will be most instructive, as it has a full system emulated.

Document lessons learned in `progress_notes/`.

## Build & Run — Always Use MCP Tools

**MCP parameter types:** Many tools have `integer`-typed parameters (e.g. `ram_mb`, `timeout`, `disk_size_mb`). These require actual integers, not floats or strings. MCP input validation will reject `256.0` or `"256"` — pass `256`.

```
qemu_build                           # Build QEMU (ninja)
qemu_configure                       # Run ../configure in qemu/build/
qemu_run_sgi                         # Run QEMU with default Indy PROM
qemu_run_sgi machine=indigo2-r10k    # Run IP28 variant
qemu_run_sgi timeout=45 ram_mb=64    # Custom RAM/timeout (64MB min, 30s escape countdown)
qemu_run_sgi debug_flags=unimp,int   # Custom -d flags
qemu_monitor command="info mtree -f" # QEMU monitor command
qemu_registers boot_wait=3           # Dump CPU registers
qemu_guest_disasm address=0xbfc00000 # Disassemble guest code
qemu_guest_memory address=0x1fb80000 # Dump guest physical memory
qemu_boot_milestones                 # Identify where a boot stalls
qemu_create_disk                     # Create SCSI disk image
qemu_disk_convert source=disk.img    # Convert raw → qcow2
qemu_copy_file                       # Copy a file into/out of a running guest
qemu_serial_interact                 # Interactive serial session (expect/send)
qemu_serial_upload_binary            # Upload a binary file via serial
qemu_serial_write_file               # Write a text file into the guest via serial
qemu_snapshot_save                   # Boot + save VM snapshot (needs qcow2)
qemu_snapshot_restore                # Restore a saved VM snapshot
qemu_session_start                   # Start persistent QEMU session (interactive)
qemu_session_send                    # Send text / read output from session
qemu_session_snapshot                # Save snapshot on running session
qemu_session_stop                    # Stop a persistent session
qemu_session_cleanup                 # Kill all QEMU processes + sessions
newport_sendkey / newport_mouse      # Inject keyboard/mouse input
newport_screendump                   # Capture screenshot of the guest display
harness_install                      # Full IRIX install into a VM instance
harness_boot / harness_resume        # Boot or resume an installed instance
irix_quick_inspect                   # Snapshot of running IRIX state (ps, net, uptime)
irix_ps / irix_sysinfo / irix_netstat  # Live IRIX process/system/network info
nvram_dump / nvram_set               # Read or write NVRAM fields
parse_qemu_log                       # Parse -d unimp trace, map to SGI hardware
vm_instance_create/list/info/fork/reset/delete/migrate  # VM instance management
fs_info / fs_ls / fs_cat / fs_extract / fs_inject       # Filesystem tools
xfs_superblock / xfs_inode / xfs_path / xfs_block / xfs_check / xfs_repair_superblock
prom_build / prom_try_compile / prom_disasm / prom_symbols / prom_sections / prom_preprocess
list_proms / disassemble / analyze_function / build_call_graph  # PROM analysis
ghidra_analyze / ghidra_decompile / ghidra_functions / ghidra_xrefs  # Ghidra
library_scan / library_search / library_stage            # External software library
log_grep / log_context / log_uniq / log_range            # Log file tools
```

### Manual Build (only if MCP is unavailable)

```bash
cd qemu && mkdir -p build && cd build
../configure --target-list=mips64-softmmu --disable-fuse --disable-fuse-lseek
ninja -j4
```

## Repository Structure

| Directory | Purpose |
|-----------|---------|
| `qemu/` | Upstream QEMU v10.2.0+ - implementation target |
| `qemu-irix/` | QEMU 2.11 fork with IRIX userland (reference only) |
| `mame/source/src/mame/sgi/` | MAME SGI implementation (hardware reference) |
| `PROM_library/bins/cpu/ip24/` | IP24 PROM images |
| `software_library/irix-657m-source/` | IRIX 6.5.7m source (kernel, ARCS, IP32prom, headers) |
| `software_library/irix-655-source/` | IRIX 6.5.5f + 6.5.5m source (kernel, headers) |
| `sgi_mcp/` | MCP server — build, run, install, PROM analysis, Ghidra, filesystem tools |
| `pyirix/` | General SGI/IRIX Python tools: EFS reader, dist package analysis, image catalog |
| `pyirix_qemu/` | QEMU orchestration: QEMUSession serial engine, disk management, install harness |
| `analysis_tools/` | Python toolkit for SGI binary analysis |
| `gathered_documentation/` | Hardware docs, driver analysis, Newport graphics |
| `progress_notes/` | Implementation lessons and discoveries |
| `ip32prom-decompiler/` | IP32 PROM decompiler (Rust) |
| `netbsd_source/` | NetBSD SGI port source (reference) |
| `vm_instances/` | VM instance storage (disk images, NVRAM, manifests per instance) |

## QEMU Implementation Files

**Indy (IP24) machine:** `qemu/hw/mips/sgi_indy.c`
**Indy devices:** `qemu/hw/misc/sgi_mc.c`, `qemu/hw/misc/sgi_hpc3.c`, `qemu/hw/display/sgi_newport.c`
**Indy headers:** `qemu/include/hw/misc/sgi_mc.h`, `qemu/include/hw/misc/sgi_hpc3.h`, `qemu/include/hw/display/sgi_newport.h`
**Build config:** `qemu/hw/mips/Kconfig`, `qemu/hw/mips/meson.build`, `qemu/hw/display/Kconfig`

**O2 (IP32) machine:** `qemu/hw/mips/sgi_o2.c`
**O2 devices:** `qemu/hw/misc/sgi_crime.c`, `qemu/hw/misc/sgi_mace.c`, `qemu/hw/display/sgi_gbe.c`, `qemu/hw/misc/sgi_crime_re.c`

## Testing

```bash
python3 -m pytest tests/ -m "not slow" -v   # Fast tests (~0.2s) — run before committing
python3 -m pytest tests/ -v                 # All tests including slow
python3 -m pytest tests/test_foo.py -v      # Single file
```

Tests serve dual purposes: **regression detection** and **assumption documentation**. Many assertions are best-guesses based on MAME, datasheets, or observed behavior — not ground truth. When a test fails after a code change, ask: **did I break something, or did I fix a wrong assumption?** Update the test if the old assertion was wrong; revert the code if the new behavior is incorrect. Use `[CROSS-REF]`, `[ASSUMPTION]`, and `[INVESTIGATIVE]` tags in test names to document confidence level.

## Snapshots

Avoid qcow2 internal snapshots — they are incompatible across QEMU builds and have caused data loss. Prefer `vm_instance_fork` + `vm_instance_reset` for all test workflows.

## Token Conservation

Binary analysis tools produce large output. Key patterns to keep context small:

- **`grep_filter`** on `qemu_run_sgi` / `qemu_serial_interact` — return only matching lines
- **`save_log` + `log_uniq`/`log_grep`** — write full output to file, query it; `log_uniq`
  collapses MMIO polling loops (thousands of identical lines → handful of unique ones)
- **Explore subagent** for broad analysis — isolates large outputs from main context window
- **Prefer summary tools**: `qemu_boot_milestones`, `xfs_check`, `irix_quick_inspect`,
  `parse_qemu_log` over raw logs or individual low-level tools
- **Ghidra**: one function at a time with `ghidra_decompile`; results cached after
  first `ghidra_analyze` so all queries in a session are fast
- **Limit disassembly ranges**: pass explicit byte counts to `disassemble` /
  `ghidra_disassemble`

## Documentation

Hardware details, memory maps, boot recipes, timing analysis, installation guides, and key technical findings all live in `progress_notes/`. Start there for context on any subsystem before modifying it.
