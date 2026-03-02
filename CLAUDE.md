# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Goal

Full system emulation of SGI systems, start with Indy (IP24), in QEMU to boot IRIX 6.5 with a functional desktop experience including graphics, sound, and networking. Target Indy first as it's the best documented SGI system. MAME has a full implementation. While Linux and NetBSD have partial ability to run on SGI hardware, and their code may include insights we could learn from. But the MAME implementation will be most instructive, as it has a full system emulated.

Document lessons learned in `progress_notes/`.

## Build & Run — Always Use MCP Tools

**IMPORTANT: Always use the `sgi` MCP tools for building, running, and analyzing QEMU. Never use raw Bash commands for these operations.** The MCP tools handle paths, timeouts, PROM selection, and output formatting automatically. **If the MCP server becomes unavailable or tools are missing from the tool list, STOP and notify the user immediately — do not fall back to Bash.**

**MCP parameter types:** Many tools have `integer`-typed parameters (e.g. `ram_mb`, `timeout`, `disk_size_mb`). These require actual integers, not floats or strings. MCP input validation will reject `256.0` or `"256"` — pass `256`.

```
qemu_build                           # Build QEMU (ninja)
qemu_configure                       # Run ../configure in qemu/build/
qemu_run_sgi                        # Run QEMU with default Indy PROM
qemu_run_sgi machine=indigo2-r10k   # Run IP28 variant
qemu_run_sgi timeout=45 ram_mb=64   # Custom RAM/timeout (64MB min, 30s escape countdown)
qemu_run_sgi debug_flags=unimp,int  # Custom -d flags
qemu_monitor command="info mtree -f" # QEMU monitor command
qemu_registers boot_wait=3           # Dump CPU registers
qemu_guest_disasm address=0xbfc00000 # Disassemble guest code
qemu_guest_memory address=0x1fb80000 # Dump guest physical memory
qemu_create_disk                     # Create SCSI disk image
qemu_disk_convert source=disk.img    # Convert raw → qcow2
qemu_serial_interact                 # Interactive serial session (expect/send)
qemu_snapshot_save                   # Boot + save VM snapshot (needs qcow2)
qemu_snapshot_restore                # Restore a saved VM snapshot
qemu_session_start                   # Start persistent QEMU session (interactive)
qemu_session_send                    # Send text / read output from session
qemu_session_snapshot                # Save snapshot on running session
qemu_session_stop                    # Stop a persistent session
qemu_session_cleanup                 # Kill all QEMU processes + sessions
```

### Manual Build (only if MCP is unavailable)

```bash
cd qemu && mkdir -p build && cd build
../configure --target-list=mips64-softmmu --disable-fuse --disable-fuse-lseek
ninja -j4
```

### Analysis Tools

```bash
python -m analysis_tools nvram <file>      # Analyze NVRAM/RTC
python -m analysis_tools prom <file>       # Analyze PROM image
python -m analysis_tools prom --all <dir>  # Batch analyze firmware
```

## Repository Structure

| Directory | Purpose |
|-----------|---------|
| `qemu/` | Upstream QEMU v10.2.0+ - implementation target |
| `qemu-irix/` | QEMU 2.11 fork with IRIX userland (potential reference only) |
| `mame/source/src/mame/sgi/` | MAME SGI implementation (hardware reference) |
| `PROM_library/bins/cpu/ip24/` | IP24 PROM images |
| `software_library/irix-657m-source/` | IRIX 6.5.7m source (kernel, ARCS, IP32prom, headers) |
| `software_library/irix-655-source/` | IRIX 6.5.5f + 6.5.5m source (kernel, headers; no ARCS/PROM) |
| `sgi_mcp/` | MCP server — build, run, install, PROM analysis, Ghidra, filesystem tools (enabled in .mcp.json) |
| `pyirix/` | General SGI/IRIX Python tools: EFS filesystem reader, dist package analysis, image catalog |
| `pyirix_qemu/` | QEMU orchestration: QEMUSession serial engine, disk management, IRIX installation harness |
| `analysis_tools/` | Python toolkit for SGI binary analysis |
| `gathered_documentation/` | Hardware docs, driver analysis, Newport graphics |
| `progress_notes/` | Implementation lessons and discoveries |
| `ip32prom-decompiler/` | IP32 PROM decompiler (Rust) - bit-identical disassembly/reassembly |
| `netbsd_source/` | NetBSD SGI port source (reference) |
| `vm_instances/` | VM instance storage (disk images, NVRAM, manifests per instance) |

## QEMU Implementation Files

**Machine definition:** `qemu/hw/mips/sgi_indy.c`
**Devices:** `qemu/hw/misc/sgi_mc.c`, `qemu/hw/misc/sgi_hpc3.c`, `qemu/hw/display/sgi_newport.c`
**Headers:** `qemu/include/hw/misc/sgi_mc.h`, `qemu/include/hw/misc/sgi_hpc3.h`, `qemu/include/hw/display/sgi_newport.h`
**Build config:** `qemu/hw/mips/Kconfig`, `qemu/hw/mips/meson.build`, `qemu/hw/display/Kconfig`

Reference implementations: `malta.c` (machine), `jazz.c` (R4000-era with WD33C93 SCSI)

## QEMU O2 (IP32) Implementation Files

**Machine definition:** `qemu/hw/mips/sgi_o2.c`
**Devices:** `qemu/hw/misc/sgi_crime.c`, `qemu/hw/misc/sgi_mace.c`, `qemu/hw/display/sgi_gbe.c`, `qemu/hw/misc/sgi_crime_re.c`
**Headers:** `qemu/include/hw/misc/sgi_crime.h`, `qemu/include/hw/misc/sgi_mace.h`, `qemu/include/hw/display/sgi_gbe.h`

**Status:** PROM POST complete — real IP32 PROM rev 4.18 boots to System Maintenance Menu.
Use: `qemu-system-mips64 -M sgi-o2 -m 64 -bios PROM_library/bins/cpu/ip32/O2_ip32prom.rev4.18.bin -serial stdio`

## Memory Map (IP24)

```
0x08000000-0x17ffffff  Low System Memory (256MB max)
0x1f000000-0x1f3fffff  GIO64 Graphics slot (Newport)
0x1f400000-0x1f5fffff  GIO64 Expansion 0
0x1f600000-0x1f9fffff  GIO64 Expansion 1
0x1fa00000-0x1fa1ffff  Memory Controller (MC)
0x1fb80000-0x1fbfffff  HPC3 Peripheral Controller
0x1fc00000-0x1fc7ffff  PROM (512KB)
0x20000000-0x2fffffff  High System Memory
```

## Key Hardware Components

| Component | MAME Reference | QEMU File | Status |
|-----------|---------------|-----------|--------|
| MC (Memory Controller) | `mc.cpp` | `sgi_mc.c` | Working |
| HPC3 (Peripheral Controller) | `hpc3.cpp` | `sgi_hpc3.c` | Working |
| IOC2 (Interrupt Controller) | `ioc2.cpp` | in `sgi_hpc3.c` | Working |
| Newport (Graphics) | `newport.cpp` | `sgi_newport.c` | Working (X11 functional) |
| WD33C93B (SCSI) | via HPC3 | in `sgi_hpc3.c` | Working |
| HAL2 (Audio) | `hal2.cpp` | in `sgi_hpc3.c` | Stub (rev/ISR/volume) |
| Seeq (Ethernet) | via HPC3 | in `sgi_hpc3.c` | Working |

## Critical Implementation Details

### Register Address Alignment (BE vs LE)

SGI uses 64-bit bus with 32-bit registers. PROM accesses registers at both BE (+4) and LE (+0) offsets. Solution: normalize addresses with `addr &= ~7ULL` to handle both transparently.

```c
#define MC_CPU_CTRL0    0x0000   /* Use 64-bit aligned offset */
addr &= ~7ULL;  /* Normalize in read/write handlers */
```

### Memory Probing

The PROM probes memory by writing test patterns. Create "unimplemented device" regions for unmapped memory to return 0 on reads (allowing pattern mismatch detection) instead of bus errors.

### MEMCFG Register Format

Each 16-bit bank config: Base[7:0] (physical >> 22), Size[12:8] ((MB/4)-1), Valid[13], 2-subbanks[14]

### Performance & Boot Caveats

See `progress_notes/indy/benchmark_results.md` for full measured data.

- **Minimum RAM: 64MB.** 32MB causes PROM to hang before reaching the menu.
- **PROM boot baseline: ~30.5s.** The "Press Escape" countdown runs even with
  no SCSI devices. Adding a disk adds ~60s; disk + CD-ROM adds ~90s.
- **`-icount shift=0,sleep=off` only helps kernel boot, not PROM.** The PROM
  uses polling loops (never WAIT). PROM timing is wall-clock bound regardless.
- **SCSI device syntax: use `-drive if=scsi`**, not `-device scsi-hd`. The
  `-device` syntax fails with "No 'SCSI' bus found" because the WD33C93's bus
  is not QOM-discoverable.
- **SCSI drive suffixes in `scsi_drives`:** Append `:cdrom` for CD-ROM media
  (SCSI IDs 4+) or `:ro` for read-only disk (SCSI IDs 1+, `readonly=on`).
  **Use `:ro` for large EFS combo images** (>700MB) — attaching them as
  `:cdrom` causes QEMU to crash when the IRIX kernel issues oversized
  READ(10) commands during CD-ROM probe.

## Testing Practices

### Running Tests

```bash
python3 -m pytest tests/ -m "not slow" -v   # Fast tests (~0.2s)
python3 -m pytest tests/ -v                 # All tests including slow
python3 -m pytest tests/test_foo.py -v      # Single file
```

### Test Philosophy

Tests serve dual purposes: **regression detection** and **assumption documentation**.
Many assertions are best-guesses based on MAME, datasheets, or observed behavior —
not ground truth. When a test fails after a code change, ask: **did I break
something, or did I fix a wrong assumption?** Update the test if the old assertion
was wrong; revert the code if the new behavior is incorrect.

### When to Run Tests

- **Before committing** — always run `pytest -m "not slow"` to catch regressions
- **After modifying any device** — run the relevant test file(s) for that device
- **After debugging a boot failure** — if you discover a new ground truth,
  add or update a test to record it

### When to Add Tests

- **New register or constant** — add to the relevant `test_*_source.py`
- **Behavioral fix from debugging** — write a test documenting the fix
  (like `test_hpc3_source.py::TestTXTimerGating` which records a real bug fix)
- **Cross-reference discovery** — when you verify behavior against MAME or
  a datasheet, capture it as a `[CROSS-REF]` test
- **Workaround or simplification** — document with an `[ASSUMPTION]` test
  so future changes know what's simplified

### Test Categories

| Tag | Purpose | May fail? |
|-----|---------|-----------|
| (none) | Verifies known-correct constants | Should not |
| `[CROSS-REF]` | Verified against MAME/datasheet/IRIX source | Should not |
| `[ASSUMPTION]` | Documents a simplification or workaround | Should not, but update if assumption changes |
| `[INVESTIGATIVE]` | Explores uncertain behavior | May fail — result teaches us something |
| `@pytest.mark.slow` | Requires QEMU boot (30-300s) | Depends on boot infrastructure |

### Test File Map

| File | Covers | Type |
|------|--------|------|
| `test_mc_source.py` | MC registers, MEMCFG, SysID, defaults | Fast |
| `test_hpc3_source.py` | HPC3 TX timer gating, NVRAM, SCC | Fast |
| `test_hpc3_subsystems.py` | INT3, PIT, SCSI DMA, RTC, keyboard | Fast |
| `test_scsi_source.py` | WD33C93 registers/commands, HPC3 DMA | Fast |
| `test_newport_source.py` | REX3 registers, DCB, status, defaults | Fast |
| `test_newport_drawing.py` | Logic ops, drawmodes, rwpacked, planes | Fast |
| `test_edge_cases.py` | Timer/RPSS/MC/Newport/SCC/ARCS assumptions | Fast |
| `test_machine_stubs.py` | Kernel trampoline, semaphores, DMA stubs | Fast |
| `test_machine_wiring.py` | CPU clock, IRQ wiring, board types | Fast |
| `test_kconfig.py` | Build system dependencies | Fast |
| `test_memory_map.py` | Address map constants, GIO slots | Fast |
| `test_nvram.py` | NVRAM binary format and contents | Fast |
| `test_hal2_stub.py` | HAL2 audio stub registers, trace events | Fast |
| `test_cp0_timer_source.py` | CP0 Count/Compare timer | Fast |
| `test_virtual_time.py` | WAIT, icount, PIT/CP0 clocks, sleep=off | Fast |
| `test_cpu_timing.py` | Bare-metal timing benchmarks, icount | Slow |
| `test_prom_boot.py` | PROM POST to System Maintenance Menu | Slow |
| `test_scsi_prom_irix.py` | SCSI probe, DMA, kernel SCSI | Slow |
| `test_miniroot_boot.py` | IRIX miniroot kernel boot | Slow |
| `test_trace_logs.py` | Debug trace analysis | Slow-ish |

## Debug Trace Events

SGI devices define QEMU trace events for per-subsystem debug logging,
modeled on MAME's `logmacro.h` categories. Zero overhead when disabled.

### Usage
```
qemu_run_sgi debug_flags="trace:sgi_hpc3_scsi_*"     # SCSI DMA only
qemu_run_sgi debug_flags="trace:sgi_newport_*"        # All Newport
qemu_run_sgi debug_flags="unimp,trace:sgi_hpc3_int3*" # Combine
```

### Available Events
| Pattern | Device | MAME Equivalent |
|---------|--------|-----------------|
| sgi_mc_read/write | MC | LOG_READS, LOG_WRITES |
| sgi_mc_rpss | MC | LOG_RPSS |
| sgi_mc_memcfg | MC | LOG_MEMCFG |
| sgi_mc_dma | MC | LOG_DMA |
| sgi_hpc3_scsi_dma* | HPC3 | LOG_SCSI_DMA |
| sgi_hpc3_scsi_irq | HPC3 | LOG_SCSI_IRQ |
| sgi_hpc3_int3* | HPC3 | LOG_INT3, LOG_IRQS |
| sgi_hpc3_pit | HPC3 | LOG_PIT |
| sgi_hpc3_enet_* | HPC3 | LOG_ENET |
| sgi_hpc3_scc_* | HPC3 | LOG_SERIAL |
| sgi_hpc3_nvram | HPC3 | LOG_EEPROM |
| sgi_hpc3_hal2_* | HPC3 | LOG_AUDIO |
| sgi_newport_rex3_* | Newport | LOG_REX3 |
| sgi_newport_rex3_cmd | Newport | LOG_COMMANDS |
| sgi_newport_vc2 | Newport | LOG_VC2 |
| sgi_newport_cmap | Newport | LOG_CMAP |
| sgi_newport_xmap | Newport | LOG_XMAP |
| sgi_newport_draw_* | Newport | LOG_COMMANDS |

### NewView Binary Logger
Record every Newport register access to a binary file for replay analysis:
```
qemu_run_sgi extra_args="-global sgi-newport.newview-log=/tmp/nv.log"
```
Format: 20-byte records (offset, data_hi, data_lo, mask_hi, mask_lo).
Bit 30 in offset = read; 0x80000000 = frame boundary.

## MCP Server Tools (sgi)

The `sgi` MCP server (configured in `.mcp.json`) provides all build, run, analysis, and debugging tools. **Always prefer these over raw Bash commands.**

### QEMU Build & Run

| Tool | Description |
|------|-------------|
| `qemu_configure` | Run `../configure` in `qemu/build/` |
| `qemu_build` | Build QEMU with ninja |
| `qemu_create_disk` | Create a SCSI disk image |
| `qemu_run_sgi` | Run QEMU SGI machine with PROM (machine, ram_mb, timeout, debug_flags, scsi_drives, extra_args, grep_filter, save_log). Supports `instance` param to use VM instance disk/NVRAM |
| `qemu_monitor` | Run QEMU monitor command (e.g., `info mtree -f`, `info registers`) |
| `qemu_registers` | Dump CPU registers after boot_wait seconds |
| `qemu_guest_disasm` | Disassemble guest code at a virtual address |
| `qemu_guest_memory` | Dump guest physical memory at an address |
| `qemu_serial_interact` | Interactive serial session with expect/send pairs. Pass `extra_args="-icount shift=0,sleep=off"` for IRIX **kernel** boot — eliminates wall-clock throttling during MIPS WAIT idle. **Note:** icount has no effect on PROM boot (PROM polls, never WAITs). **For interactive use after boot, omit icount to avoid networking/buffering issues.** |
| `qemu_disk_convert` | Convert disk image between formats (e.g., raw to qcow2 for snapshot support) |
| `qemu_snapshot_save` | Boot with serial interaction, save VM snapshot at desired state (requires qcow2). Pass `extra_args="-icount shift=0,sleep=off"` for kernel boot (no effect on PROM phase) |
| `qemu_snapshot_restore` | Restore a saved VM snapshot and collect serial output — much faster than re-booting |
| `qemu_session_start` | Start persistent QEMU session — stays running for interactive use. Supports `snapshot` and `instance` params |
| `qemu_session_send` | Send text to session serial console, read output. Use `expect` param to wait for pattern |
| `qemu_session_snapshot` | Save VM snapshot on a running session without stopping it. Supports `instance` + `description` params |
| `qemu_session_stop` | Stop a persistent session and clean up resources |
| `qemu_session_cleanup` | Kill all QEMU processes and clean up all sessions |
| `newport_sendkey` | Inject keyboard input via PS/2 controller. Use `keys` for raw key specs (e.g., `ret`, `ctrl-alt-delete`) or `text` to type a string (e.g., `root\n`). Optional `delay_ms` between keystrokes (default 100). |
| `newport_mouse` | Inject mouse input via PS/2 controller. `dx`/`dy` for relative movement, `buttons` bitmask (1=left, 2=middle, 4=right). |

### IP54 PROM Build

| Tool | Description |
|------|-------------|
| `prom_build` | Build the IP54 PROM (`make all`), with optional clean and target selection |
| `prom_try_compile` | Try compiling a single source file to check for errors |
| `prom_symbols` | List symbols in the PROM ELF (filter, undefined-only, sort) |
| `prom_disasm` | Disassemble a function or address range in the IP54 PROM ELF |
| `prom_sections` | Show ELF section headers (addresses, sizes, layout) |
| `prom_preprocess` | Run C preprocessor to see macro expansion (e.g., `PROM_STACK`) |

### PROM Analysis

| Tool | Description |
|------|-------------|
| `list_proms` | List available PROM files |
| `info` | PROM metadata (size, platform, entry point, SHA256) |
| `hexdump` | Simple hex dump of PROM data |
| `xxd` | Full xxd-compatible hex dump with options |
| `disassemble` | MIPS disassembly with hardware annotations |
| `strings` | Extract ASCII strings from PROM |
| `find_entry_points` | Find reset vector and entry point |
| `find_vector_table` | Find exception vectors (BEV mode) |
| `find_function_prologues` | Find function start patterns |
| `find_jump_tables` | Find jump tables (sequences of PROM addresses) |
| `find_hardware_probes` | Find MMIO access patterns (LUI-based) |
| `find_graphics_init` | Find Newport/REX3 graphics init sequences |
| `find_memory_detection` | Find memory sizing code (MEMCFG access) |
| `find_device_detection` | Find GIO slot device probing patterns |
| `analyze_function` | Detailed analysis of a single function |
| `build_function_database` | Build complete function database with naming |
| `build_call_graph` | Function call graph from PROM |
| `trace_boot_sequence` | Trace boot from reset vector with hardware accesses |
| `track_hardware_accesses` | Track all hardware register accesses |
| `find_string_refs` | Find code referencing ASCII strings |
| `identify_arcs_callbacks` | Identify ARCS callback functions |
| `xref_address` | Find references to a specific address |
| `annotate_address` | Get hardware annotation for an address |
| `list_devices` | List known hardware devices and base addresses |
| `device_registers` | List registers for a specific device |
| `export_symbols` | Export symbols (ghidra, ida, json formats) |

### Ghidra Integration

Ghidra 12.1 at `/home/dev/ghidra/` provides decompilation, recursive-descent function detection, and cross-references. Uses PyGhidra for headless script execution. Projects cached in `/workspace/ghidra_projects/` — first analysis takes ~12s, subsequent calls ~3s.

| Tool | Description |
|------|-------------|
| `ghidra_analyze` | Import PROM into Ghidra, run auto-analysis, import our function names |
| `ghidra_decompile` | Get C pseudocode for a function (primary value-add) |
| `ghidra_functions` | List all Ghidra-detected functions with metadata |
| `ghidra_xrefs` | Cross-references to/from an address |
| `ghidra_import_symbols` | Re-import our MCP function names into Ghidra project |
| `ghidra_disassemble` | Ghidra's disassembly with labels, comments, function boundaries |

### PROM Comparison

| Tool | Description |
|------|-------------|
| `diff_proms` | Binary diff between two PROMs |
| `find_common_code` | Shared code sequences across PROMs |
| `signature_search` | Search for byte pattern across all PROMs |
| `version_compare` | Compare two versions of same platform PROM |

### QEMU Debug Trace Analysis

| Tool | Description |
|------|-------------|
| `parse_qemu_log` | Parse QEMU `-d unimp` output, map to SGI hardware |
| `generate_expected_sequence` | Generate expected hardware access sequence from PROM analysis |
| `analyze_register_values` | Track LUI+ORI sequences, detect polling loops |
| `compare_execution` | Compare QEMU trace vs expected PROM sequence |

### Log File Tools

| Tool | Description |
|------|-------------|
| `log_grep` | Search log file for pattern (regex) |
| `log_context` | Show lines around a pattern match |
| `log_uniq` | Unique lines with counts (like `uniq -c`) |
| `log_range` | Show a range of lines from a log file |

### VM Instance Management

Organized storage for disk images, NVRAM files, and metadata in `vm_instances/{name}/`.
Each instance has `disk.qcow2`, `nvram.bin`, and `manifest.json`.

| Tool | Description |
|------|-------------|
| `vm_instance_create` | Create new VM instance with disk and manifest |
| `vm_instance_list` | List all instances with summary info |
| `vm_instance_info` | Show full manifest including snapshot descriptions |
| `vm_instance_delete` | Delete instance directory and all contents |
| `vm_instance_migrate` | Move existing disk/NVRAM into a new instance |

Many existing tools support `instance` parameter as alternative to explicit paths:
- `qemu_session_start instance="irix65-indy"` — uses instance disk/NVRAM
- `qemu_run_sgi instance="irix65-indy"` — same
- `qemu_session_snapshot instance="irix65-indy" description="..."` — records in manifest
- `qemu_snapshot_save instance="irix65-indy" description="..."` — records in manifest
- `harness_install instance="irix65-indy"` — creates instance, records snapshots
- `harness_boot instance="irix65-indy"` — uses instance disk
- `harness_resume instance="irix65-indy"` — uses instance disk

### Filesystem Tools

Read and modify SGI disk image contents (EFS and XFS filesystems).
Supports both raw `.img` and QEMU `.qcow2` formats transparently.

| Tool | Description |
|------|-------------|
| `fs_info` | Show volume header, partition table, and filesystem details |
| `fs_ls` | List files with permissions, uid, gid, size, path. Params: `path`, `recursive`, `max_entries`, `partition` |
| `fs_cat` | Read file contents (text directly, binary as hex dump). Params: `path`, `binary`, `max_size`, `partition` |
| `fs_extract` | Extract files/directories to host. Params: `dest`, `path` (filter), `partition` |
| `fs_inject` | Add file from host into EFS partition (rebuilds EFS). XFS write not supported. Params: `host_path`, `guest_path`, `uid`, `gid`, `mode` |

### External Library Tools

Scan and search external software collections (NAS/SMB mounts) containing
SGI disc images, tardist packages, and community software (Nekoware, tgcware).
Builds a persistent SQLite index for fast searching without re-scanning.

| Tool | Description |
|------|-------------|
| `library_scan` | Scan an external directory and build/update SQLite index. Params: `path` (required), `deep` (read magic bytes) |
| `library_search` | Search the index. Params: `query`, `category` (os/dev/freeware/nekoware/tgcware/application/graphics/etc.), `format` (efs_image/iso9660/tardist/tarball), `limit` |
| `library_stage` | Copy a file from external library to local staging. Params: `source_path` (required), `dest` |
| `library_info` | Show index statistics (counts by category and format) |

Example workflow:
```
library_scan path="/Volumes/Library/software/IRIX/sgi"  # First time: index
library_search query="Cosmo" category="graphics"         # Instant search
library_stage source_path="/Volumes/.../Cosmo.efs.img"   # Copy locally
```

## Milestones

1. **PROM Completes POST** - IOC2 interrupts, HPC3 serial console — **DONE**
2. **Kernel Loads** - HPC3 SCSI with WD33C93B, disk support — **DONE**
3. **IRIX Single-User** - Timer/interrupt refinement — **DONE**
4. **IRIX Desktop** - Newport graphics, X11 — **DONE** (Xsgi + 4Dwm + xdm login + keyboard/mouse input)
5. **Full System** - HAL2 audio, ~~Seeq ethernet~~ **DONE**, ~~keyboard/mouse input~~ **DONE**

## Current Status

Phase 1, Phase 2, Phase 3, and Phase 4 (partial) complete. IRIX 6.5 installed and booting from disk to graphical login screen. X11 server (Xsgi) functional — xdm login screen, 4Dwm window manager, xclock, xterm all run. Networking functional (ping, telnet via SLIRP). Keyboard/mouse input functional via PS/2 controller.

### What Works
- **PROM POST:** Completes fully, reaches System Maintenance Menu (both IP24 PROMs)
- **Memory probing:** Dynamic MEMCFG mapping with 4-bank support, aliasing detection
- **Newport graphics:** REX3 drawing engine (block, span, line, scr2scr, host data),
  DCB subsystem (VC2/CMAP/XMAP/RAMDAC), VRAM framebuffer, VRINT interrupt,
  DOSETUP, LENGTH32, SKIPFIRST/SKIPLAST, shade/RGB color, pixel word read
- **X11 desktop:** Xsgi X server runs, 4Dwm window manager, xclock, xterm all work.
  23 X extensions including GLX. Multiple visuals (PseudoColor 2/4/8-bit, TrueColor).
- **xdm graphical login:** Login screen appears at boot (with `grabServer: False` fix).
  Keyboard and mouse input via PS/2 controller (8042 in IOC2) is functional.
- **SCSI (PROM level):** WD33C93 detects targets, reads volume headers, loads sashARCS from CD
- **SCSI (kernel level):** 60+ commands, DMA transfers, partition detection, disk capacity
- **Serial console:** Z85C30 TX/RX with interrupt support, serial interaction working
- **RTC/NVRAM:** DS1386 with persistent file-backed NVRAM and auto-checksum
- **PIT timers:** Correct IRQ routing — Timer0→IP4, Timer1→IP5 (used by PROM only)
- **CP0 Count/Compare timer:** Scheduling clock on IP7 (IRIX uses R4000 timer, not PIT)
- **INT3 interrupt controller:** Centralized mapped interrupt cascade, DUART routing,
  spurious interrupt filtering for unimplemented hardware (PI1 parallel port)
- **Direct kernel boot:** `-kernel` loads IRIX 6.2 kernel via ARCS hypercall device
- **CD-ROM boot:** PROM autoboots sashARCS from IRIX install CD via volume header
- **Miniroot boot:** IRIX 6.5 kernel boots, mounts root, runs init, creates devices
- **Multi-platform:** IP22, IP24, IP28 all reach System Maintenance Menu
- **Z85C30 SCC fix:** WR0 register pointer masking corrected (0x0f → 0x07),
  STREAMS TX working correctly
- **IRIX installer:** Fully interactive — mkfs, package selection, install
- **IRIX 6.5 installed:** Boots from disk to multi-user login (root shell)
- **Persistent sessions:** Long-running QEMU workflows via session tools
- **Seeq 80C03 Ethernet:** TX/RX DMA, Seeq register bank selection,
  NIC via SLIRP user-mode networking. Ping works from IRIX to gateway.
  Use `-nic user,model=sgi-hpc3` to enable
- **Keyboard/mouse input:** PS/2 keyboard and mouse via 8042 controller in IOC2.
  IRIX pckm driver detects both devices (`pckm0: keyboard (id=83)`).
  Input delivered via HMP sendkey/mouse_move commands through `newport_sendkey`
  and `newport_mouse` MCP tools. IRQ path: PS/2 → INT3 map bit 0x10 →
  MAPPABLE0 → CPU IP2 → pckm_intr().

### Known Limitations
- **Direct kernel (6.2) mount root:** `-kernel` path still fails at `vfs_mountroot`
  with EINVAL because `irix_disk.img` has no EFS/XFS filesystem. Not critical
  since CD boot path works.

### Miniroot Boot Recipe
```
qemu_serial_interact
  extra_args="-icount shift=0,sleep=off"
  scsi_drives=["/workspace/irix_disk.img",
               "software_library/irix_6.5.22_images/IRIX 6.5 Installation Tools June 1998.img:cdrom"]
  boot_wait=15
  interactions=[
    {"expect": "Option", "send": "2\r", "timeout": 5},
    {"expect": "enter.*to start", "send": "\r", "timeout": 15},
    {"expect": "press.*enter", "send": "\r", "timeout": 10},
    {"expect": "c, f, r, or a", "send": "c\r", "timeout": 30}
  ]
  timeout=600  collect_after=300
```
Requires: `irix_disk.img` (partitioned with fx), original unpatched IRIX 6.5 install CD.
Use 'r' to reload miniroot if disk is corrupted from previous interrupted boot.

**Fast resume via fork/reset workflow:** Use `vm_instance_fork` to create a
disposable thin copy of a known-good instance, run tests on it, and reset when
broken — no snapshots needed.

**`-icount shift=0,sleep=off` is critical for kernel performance.** Without it,
QEMU virtual time tracks wall-clock time during WAIT idle periods, limiting
the kernel to ~100 scheduling quanta/second (the PIT timer rate). With
`sleep=off`, virtual time advances instantly during WAIT, so the PIT fires
as fast as the host can process each quantum. **Note:** This flag has no
effect on PROM boot timing — the PROM uses polling loops (never WAIT), so
the ~30s escape countdown and ~60s SCSI probe are wall-clock bound regardless.
See `progress_notes/indy/virtual_time_and_timing.md` and
`progress_notes/indy/benchmark_results.md` for full analysis and measured data.

### Starting the Desktop

xdm starts automatically at boot and displays the graphical login screen.
The `irix_disk.qcow2` disk has the xdm fix applied (`grabServer: False`
in `/var/X11/xdm/xdm-config`) — this is done automatically by `pyirix/install/irix.py`
during Phase 5. See `progress_notes/indy/xdm_graphical_login_fix.md`.

**Input:** Use `newport_sendkey` and `newport_mouse` MCP tools to interact with
the graphical login. For example, to log in as root:
```python
newport_sendkey(session_id="...", text="root\n")
```

To start desktop apps manually via serial console:
```bash
setenv DISPLAY :0
/usr/bin/X11/4Dwm &        # Window manager
/usr/bin/X11/xclock &       # Test app
/usr/bin/X11/xterm &        # Terminal
```

**For a fresh IRIX install**, apply the xdm fix:
```bash
sed 's/grabServer.*True/grabServer:              False/' /var/X11/xdm/xdm-config > /tmp/f && cp /tmp/f /var/X11/xdm/xdm-config
```

### Next Steps
1. ~~Complete IRIX 6.5 installation~~ **DONE** — boots to login prompt
2. ~~Boot from installed disk~~ **DONE** — `uname -a` shows IRIX 6.5 IP22
3. ~~Seeq ethernet~~ **DONE** — ping and telnet work via SLIRP
4. ~~HAL2 audio stub~~ **DONE** — rev/ISR/volume registers, trace events
5. ~~Debug X11/Newport graphics for desktop~~ **DONE** — Xsgi + 4Dwm run
6. ~~Debug xdm login~~ **DONE** — `grabServer: False` fix, login screen appears
7. ~~Keyboard/mouse input~~ **DONE** — PS/2 via 8042/IOC2, sendkey/mouse MCP tools
8. ~~Install MIPSpro + dev tools~~ **DONE** — MIPSpro 7.4.4m, dev headers/libs, ProDev on `irix65-desktop`
9. Newport rendering correctness (depth conversion, visual verification)
10. HAL2 full audio (PBUS DMA, Bresenham clocks, QEMU audio backend)
11. Compile and run a C program natively on IRIX (verify full toolchain)

### Installation Disk

**Preferred:** Use VM instances for organized storage:
```python
# Create a new installation in vm_instances/irix65-indy/
harness_install(version="6.5", instance="irix65-indy")

# Boot from instance (uses instance disk/NVRAM automatically)
qemu_session_start(
    instance="irix65-indy",
    autoload=True,
    extra_args="-icount shift=0,sleep=off -nic user,model=sgi-hpc3,hostfwd=tcp::2323-10.0.2.15:23",
    boot_wait=45
)

# Or migrate existing disk into an instance:
vm_instance_migrate(name="irix65-indy", disk_path="/workspace/irix_disk.qcow2",
                    nvram_path="/workspace/sgi_indy_nvram.bin",
                    machine="indy", irix_version="6.5")
```

**IRIX 6.5.5 with dev tools:** Instance `irix655-full` has IRIX 6.5.5 + MIPSpro 7.4.4m.
Boot fresh (no snapshot):
```python
qemu_session_start(
    instance="irix655-full",
    extra_args="-icount shift=0,sleep=off -nic user,model=sgi-hpc3,hostfwd=tcp::2323-10.0.2.15:23",
    boot_wait=45
)
```

### Test Instance Workflow (replaces snapshots)

Create a disposable thin copy for destructive work:
```python
vm_instance_fork(source="irix655-full", name="my-test",
                 description="IP54 lboot test — disposable")
qemu_session_start(instance="my-test", extra_args="-icount shift=0,sleep=off")
# when broken:
vm_instance_reset(name="my-test")  # discards all changes, re-forks from backing
```

### Snapshot Warning

**qcow2 internal snapshots are incompatible across QEMU builds.** Attempting
`-loadvm` with a snapshot saved on a different QEMU binary silently corrupts
the qcow2 disk (vmstate version mismatch). We have lost work to this twice.
- Never use `snapshot=` in session start unless you are certain the snapshot
  was saved with the current QEMU build.
- Never set `default_snapshot` in manifests — it auto-triggers `-loadvm`.
- Prefer `vm_instance_fork` + `vm_instance_reset` for all test workflows.

**Legacy:** `irix_disk.qcow2` in workspace root has a complete IRIX 6.5 installation.
Snapshots (`irix65_booted`, `install_complete`) are present but may be stale.

To create a fresh installation from scratch:
```python
harness_install(version="6.5", disk="/workspace/irix_disk.qcow2")
```

To boot the installed system:
```python
qemu_session_start(
    scsi_drives=["/workspace/irix_disk.qcow2"],
    autoload=True,
    extra_args="-icount shift=0,sleep=off -nic user,model=sgi-hpc3,hostfwd=tcp::2323-10.0.2.15:23",
    boot_wait=45
)
```

**Networking:** The `-nic user,model=sgi-hpc3` flag enables SLIRP user-mode
networking. The `hostfwd` option forwards host port 2323 to IRIX telnet (port 23).
After boot, configure the interface:
```
ifconfig ec0 inet 10.0.2.15 netmask 255.255.255.0 up
route add default 10.0.2.2
```
Then `telnet localhost 2323` from the host connects to the IRIX login prompt.

See `progress_notes/indy/irix_installation_guide.md` for the full installation procedure.

### Key Technical Findings
- **IRIX uses R4000 Count/Compare timer for scheduling**, not the 8254 PIT.
  The kernel checks `is_ioc1()` at boot; for IOC2 boards (Indy/Guinness),
  `is_ioc1_flag=2` selects `startrtclock_r4000()`. PIT counters 0/1 are
  never programmed. The scheduling clock fires on IP7 (SR_IBIT8).
- **INT3 local0_stat must only reflect emulated hardware.** Bits for
  unimplemented devices (PI1 parallel port = bit 1) must be masked to
  prevent stray interrupt storms that the kernel's `lcl_stray()` handler
  cannot clear.
- **Z85C30 WR0 register pointer uses bits [2:0], not [3:0].** The WR0
  register encodes the register pointer in bits [2:0] and a command in
  bits [5:3]. Old code used `val & 0x0f` which leaked command bits into
  register selection, corrupting STREAMS TX setup (WR5/WR11/WR14).
  Fix: `val & 0x07`.
- **Seeq 80C03 uses bank-selected register writes.** TX command register
  bits [6:5] (`TXC_B = 0x60`) select which register set regs 0-5 map to:
  bank 0 = station address, bank 0x20 = multicast filter low, bank 0x40 =
  multicast filter high + control/config. IRIX programs the MAC in bank 0
  then writes multicast hash in banks 0x20/0x40. Without bank selection,
  the hash writes overwrite the station address, causing all unicast
  packets (ARP replies, ICMP) to be dropped by the address filter.
- **HPC3 RX descriptor r_rown bit (bit 14) polarity:** 0 = software owns
  (data ready for driver), 1 = hardware owns (not yet filled). After RX
  DMA writes packet data, we must CLEAR r_rown so the driver's interrupt
  handler can process the descriptor (`while (!rd_chain->r_rown)`).
- **Newport VRINT must be a timed pulse, not level-held.** The IRIX ng1
  kernel driver handles the retrace interrupt by reading INT3 local1_stat
  (bit 7) and toggling the mask bit. It does NOT read REX3 STATUS to
  deassert the GIO interrupt — it expects the hardware to deassert on its
  own when VBLANK ends. Keeping the IRQ asserted until STATUS is read
  (MAME's model) causes INT3 local1_stat bit 7 to remain permanently set,
  blocking `open("/dev/graphics")` and preventing Xsgi from starting.
  Fix: assert IRQ on VBLANK timer, schedule deassert 500µs later via a
  second timer. Use `vrint_active` flag to prevent re-assertion during the
  pulse. See `progress_notes/indy/newport_xsgi_milestone.md`.
- **MAME's VRINT model vs IRIX kernel expectations differ.** MAME uses a
  read-to-clear model (reading STATUS clears VRINT and lowers the GIO
  interrupt line). This works for MAME because it runs at real-time speed
  and the screen device naturally cycles VBLANK. In QEMU with icount, the
  60Hz virtual timer fires much faster than real-time, and the kernel
  driver's interrupt handler doesn't clear the IRQ via STATUS read. The
  QEMU implementation must model the actual hardware pulse behavior.
- **xdm graphical login requires `grabServer: False`.** The default xdm
  config has `DisplayManager*grabServer: True`, which causes XGrabServer()
  to trigger a blocking path in the shmiq (shared memory input queue)
  subsystem. Without real keyboard/mouse interrupt hardware, the grab
  processing blocks indefinitely. Fix: set `grabServer: False` in
  `/var/X11/xdm/xdm-config`. The `-ac` flag is NOT needed (the existing
  `authorize: off` setting is sufficient). Applied automatically by
  `pyirix/install/irix.py` during Phase 5 verification boot.
  See `progress_notes/indy/xdm_graphical_login_fix.md`.
- **X keyboard repeat under icount + VNC.** With `-icount shift=0,sleep=off`,
  virtual time races through WAIT idle, causing X11's autorepeat timer to
  fire many times during a single human key press. Fix: `xset r off` to
  disable guest-side autorepeat. VNC clients handle their own repeat.
  Applied automatically via `/var/X11/xdm/Xsetup_0` by `pyirix/install/irix.py`.
- **Large EFS images crash QEMU when attached as CD-ROM.** The IRIX kernel's
  CD-ROM probe issues READ(10) commands that exceed the SCSI transfer size
  limit, causing "Too much data requested" and a QEMU process crash. Fix:
  attach as a read-only disk (`:ro` suffix or `readonly=on` in QEMU args)
  instead of `:cdrom`. The `combine_dist.py` tool builds EFS images >1GB
  for devtools combo discs.
- **IRIX `inst` overlay version resolution.** Foundation-era dev packages
  (version 1274627333) are incompatible with IRIX 6.5.5's eoe.sw.base
  (version 1275719131). Including the 6.5.5 Overlays CD **2** (not CD 1!)
  provides `dev_655m`, `irix_dev_655m`, etc. that supersede the foundation
  versions and resolve conflicts. CD 1 only has `eoe_655f/m` and boot tools.
  See `progress_notes/indy/mipspro_devtools_install.md`.
