# IRIX Source Trees

We have two IRIX source trees in `software_library/`. Both were originally
mislabeled and have been corrected based on their `releasedefs` files.

## Version Identification

The actual version of each source tree is recorded in
`eoe/include/makerules/releasedefs` via the `RELEASE_NAME` macro.

| Directory | `RELEASE_NAME` | `RELEASE_NUM` | Actual Version |
|-----------|---------------|---------------|----------------|
| `irix-657m-source/` | `6.5.7m` | 127 | IRIX 6.5.7 maintenance |
| `irix-655-source/f/` | `6.5.5f` | 128 | IRIX 6.5.5 feature |
| `irix-655-source/m/` | `6.5.5m` | 127 | IRIX 6.5.5 maintenance |

RCS `$Source` tags in the 6.5.7m tree reference `/proj/irix6.5.7m/`, confirming
the version. The directory was originally named `irix-6517-source` (misread as
6.5.17) and renamed to `irix-657m-source` in February 2026.

## SGI's f/m Release Model

Starting with IRIX 6.5.1 (1998), SGI shipped two parallel release streams:

- **"m" (maintenance)** — bug fixes and security patches only. Intended for
  production systems where stability was critical.
- **"f" (feature)** — new functionality plus bug fixes. For systems that
  needed new hardware support, features, or APIs.

This dual-stream model ran from 6.5.1 through 6.5.22 (the final IRIX release).
The base "6.5" release (June 1998) had no suffix. Customers could choose to
track either stream independently.

## Tree Contents

### irix-657m-source (6.5.7m)

Top-level directories: `eoe/`, `irix/`, `stand/`

This is the more complete tree for our purposes:
- **`stand/arcs/`** — ARCS firmware source including IP32prom (O2 PROM).
  This is what the `prom-building/` project copies from.
- **`irix/kern/`** — Kernel source (sys headers, device drivers, VM, etc.)
- **`eoe/`** — Core OS (libraries, commands, headers, makerules)

No `.idea/` project files were added by us (JetBrains workspace artifacts).

### irix-655-source (6.5.5f + 6.5.5m)

Top-level: `f/` and `m/` subdirectories, each containing a complete tree.

Each subtree has: `eoe/`, `irix/`, `hippi/`, build archives (`*-bld.cpio`),
`root/` (build root), `Makefile`, `README`, `file.lst`.

Key differences from the 6.5.7m tree:
- **No `stand/` directory** — no ARCS or PROM firmware source
- **Has `hippi/`** — HIPPI networking source (not in 6.5.7m)
- **Has build archives** — `eoe-bld.cpio`, `irix-bld.cpio`, `hippi-bld.cpio`
- **`f/` has `troot/`** (build toolroot with MIPSpro 7.x compiler), `m/` does not
- **`f/` has `root/`** (~114 MB build root with headers and libraries for all ABIs)
- **Two parallel streams** allow comparing f vs m divergence

The `f/` tree is a self-contained "source product" distribution with everything
needed to build — except SGI's proprietary `smake`. See
[irix_655_build_system.md](irix_655_build_system.md) for a full analysis of the
build system, toolchain contents, and viability assessment.

## Which Tree to Use

| Task | Recommended Tree |
|------|-----------------|
| ARCS/PROM firmware reference | `irix-657m-source/stand/arcs/` (only option) |
| IP32prom building | `irix-657m-source/` (used by `prom-building/scripts/`) |
| Kernel headers (sys/*.h) | `irix-657m-source/irix/kern/sys/` (newer) |
| Kernel internals | Either; 6.5.7m is slightly newer |
| Comparing f vs m changes | `irix-655-source/` (has both streams side by side) |
| EOE libraries/commands | Either tree has `eoe/` |

## Key Files for QEMU Emulation

From `irix-657m-source/`:
- `irix/kern/sys/IP22.h` — Indy/Indigo2 memory map, register definitions
- `irix/kern/sys/crime.h` — CRIME chip registers (IP32/O2)
- `irix/kern/sys/sbd.h` — System board definitions
- `stand/arcs/include/` — ARCS firmware interface headers
- `stand/arcs/lib/libsk/` — Standalone kernel library (hardware init)
- `eoe/include/` — Build system makerules and base headers

## Content Overlap

Both trees share the same `eoe/` and `irix/` structure with minor version
differences (6.5.5 vs 6.5.7). The kernel headers and driver source are
largely identical — differences are limited to bug fixes accumulated between
the two releases. For hardware register definitions and driver behavior
reference, either tree works; prefer 6.5.7m as it's slightly newer and
contains two additional maintenance releases worth of fixes.
