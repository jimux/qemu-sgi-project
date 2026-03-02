# IRIX 6.5.5 Build System Viability Analysis

An analysis of the build infrastructure in `software_library/irix-655-source/`,
determining what's needed to actually compile IRIX from source, what's missing,
and how viable it is.

## What the 6.5.5f Tree Contains

The `f/` tree is a complete, self-contained "source product" distribution:

| Component | Path | Contents |
|-----------|------|----------|
| Source code | `eoe/`, `irix/`, `hippi/` | Kernel, userland commands, libraries |
| Build root | `root/` (~114 MB) | Headers (`usr/include/`, 1245+ files), libraries (libc, libm, etc.) for all ABIs |
| Toolchain | `troot/` (~37 MB) | Complete MIPSpro 7.x compiler + SGI build tools |
| Build archives | `*-bld.cpio` | Pre-built .o files (bootstrap/speedup) |
| Top-level Makefile | `Makefile` | `#! smake` — orchestrates extract + build |
| File manifest | `file.lst` (17,726 entries) | Used by `make clean` to identify generated files |

The `m/` tree has everything except `troot/` (no toolchain).

## The Toolchain (troot/)

All binaries are **MIPS N32 ELF executables** — they can only run on IRIX (or under
MIPS emulation like QEMU user-mode or our full-system emulation).

### MIPSpro 7.x Compiler (`troot/usr/lib32/cmplrs/`, 27 files)

- `driver` — compiler driver (cc/CC/f77 are symlinks)
- `fecc` / `fec` — C/C++ front-ends (Edison Design Group)
- `be` / `be.so` — back-end optimizer (3.2 MB)
- `cg.so` — code generator (3.7 MB)
- `wopt.so` / `lno.so` / `ipa.so` — optimizer phases
- `r4000.so` / `r8000.so` / `r10000.so` — processor-specific backends
- `asm` — assembler
- `ld32` / `ld64` — linkers
- `cpp` — preprocessor

### Build Utilities (`troot/usr/bin/`, 23 entries)

- `cc`, `as`, `ar`, `ld`, `strip`, `size` — all symlinks to `../lib/driverwrap`
- `yacc`, `lex`, `flex`, `bison`, `gnum4` — parser generators
- `rpcgen`, `gencat`, `nroff`, `cord` — misc tools

### Kernel Build Tools (`troot/usr/sbin/`, 7 files)

- `lboot` (201 KB, MIPS ELF) — links kernel modules into bootable kernel
- `setsym` (127 KB, MIPS ELF) — embeds symbol table for kernel debugging
- `setmagic` (13 KB, MIPS ELF) — sets ELF magic numbers
- `mkversionnum` (2.5 KB, **KornShell script**) — generates 10-digit version numbers
- `tag` (23 KB, MIPS ELF) — binary tagging utility
- `tlink` (23 KB, MIPS ELF) — creates symlink forests for multi-ABI builds
- `toolrootsafe` (577 bytes, **shell script**) — backward-compat wrapper

## Critical Dependency: smake

The **single biggest blocker** is SGI's `smake` — a proprietary make variant that is
**NOT included in the source distribution** and was never open-sourced.

The top-level Makefile starts with `#! smake`, and the entire build system
(`commondefs`, 949 lines) uses smake-specific syntax throughout.

### smake-only features used in commondefs

1. **C preprocessor directives** — `#if defined(...)`, `#elif`, `#else`, `#endif`
   (76 occurrences in commondefs alone). GNU make has no equivalent.
2. **Variable modifiers** — `:S/pattern/replacement/g` (BSD make-style substitution),
   `:Mpattern` (match filter). Used for ABI selection logic.
3. **`!=` operator** — command substitution (`DIR != pwd`). BSD make has this but
   GNU make does not.
4. **Conditional operators** — `$(VAR:M64*) != ""` for pattern matching in conditionals.
5. **`!` dependency operator** — `irix eoe hippi! ${@}_buildable` (forced rebuild).

These aren't superficial — they're woven into the core ABI/ISA selection logic that
determines compiler flags for every source file. There are ~76 `#if`/`#elif` blocks
in commondefs alone, selecting between 7+ ABI/ISA combinations.

### What smake is NOT

- **Schily smake** (Jorg Schilling's portable make — different tool, different syntax)
- **GNU make** (lacks preprocessor directives entirely)
- **BSD make/pmake** (has `:S` and `:M` but not `#if defined()`)

### What smake IS

- Bundled with IRIX (`/usr/bin/smake`), not separately distributed
- Proprietary, never open-sourced by SGI
- Required to be the system `make` on the build host

## Build Options

### Option A: Build on Real IRIX (or QEMU Emulation)

Requirements:
1. **Running IRIX 6.5 system** with `smake` installed (it's in the base OS)
2. **Set environment**: `ROOT=$PWD/root TOOLROOT=$PWD/troot WORKAREA=$PWD SRC_PRD=1`
3. **Extract build files**: `make buildable` (extracts `*-bld.cpio` archives)
4. **Build**: `smake -k` or `make default` (smake shebang auto-invokes)
5. **Disk space**: ~500 MB+ for full build with all ABIs
6. **RAM**: Origin-class machine recommended (README says "performed on an Origin")

This is theoretically possible in our QEMU Indy, but would require:
- Enough disk space on the emulated SCSI drive
- Working `smake` (should be in base IRIX 6.5 install)
- Keyboard/mouse input (currently unimplemented) or scripted serial interaction
- Patience — building on emulated R4400 would be extremely slow

### Option B: Cross-compile from Modern Host

This would require **rewriting the build system**:
1. Replace all 76+ `#if`/`#elif` blocks with GNU make `ifeq`/`ifdef`
2. Replace `:S` and `:M` modifiers with GNU make `$(subst ...)` / `$(filter ...)`
3. Replace `!=` with GNU make `$(shell ...)`
4. Replace `#! smake` with portable make
5. Swap MIPSpro for a MIPS cross-GCC (like `mips-elf-gcc` or `mips-linux-gnu-gcc`)
6. Handle MIPSpro-specific flags (`-woff`, `-fullwarn`, `-MDupdate`, etc.)
7. Handle struct-passing ABI incompatibilities between GCC and MIPSpro

Community project [irix-builder](https://github.com/mroach/irix-builder) uses Docker
+ cross-GCC for compiling userland software, but does NOT attempt to build the IRIX
kernel or use SGI's build system.

### Option C: QEMU User-mode Emulation

Run the MIPS N32 toolchain binaries on a Linux host:
1. Install `qemu-mips` (user-mode MIPS emulation)
2. Set up sysroot with the `root/` directory
3. Run `troot/` binaries through qemu-mips
4. Still need `smake` (could extract from an IRIX install image)

This is plausible for individual compilations but fragile for a full build.

## What Can't Be Built Even With smake

The source product is intentionally **incomplete**:

- **No graphics source** — Xsgi, DGL, OpenGL, Newport driver not included
- **No ARCS/PROM** — `stand/` directory absent (use `irix-657m-source/` for that)
- **Pre-built .o files** — `irix-bld.cpio` (26 MB) contains 815 pre-built kernel
  objects for platforms IP19-IP32. These are required for linking — they represent
  code not included in source form.
- **ISM/packaging tools** — `gendist`, `idbproto` etc. referenced but not all provided
- **Some libraries partial** — libc and others have mix of source + pre-built objects
- **MIPSpro itself** — the compiler is closed-source (EDG front-end is licensed).
  Open64/PathScale is the open-source descendant but targets IA-64/x86-64, not MIPS.

## Notable Portable Scripts

### mkversionnum

A 93-line KornShell script (readable, no MIPS dependency). Generates 10-digit
version numbers: `RRRHHHHHTB` where:
- `RRR` = release number (101-213; 127 = IRIX 6.5)
- `HHHHH` = hours since Jan 1, 1993 GMT
- `T` = tree ID (0-9)
- `B` = builder (0=developer, 1=project, 2=build group)

Uses `ksh` features (`typeset -r`, `$((...))`) but would work with bash/zsh.

### toolrootsafe

A 15-line shell script that sets `_RLD_LIST` / `_RLDN32_LIST` to inject
`libtoolroot.so` as a compatibility shim, allowing 6.5-linked binaries to run
on IRIX 6.2+. Only relevant on IRIX hosts.

## Build System Architecture

```
Top-level Makefile (#! smake)
  |-- make buildable -> cpio -icdu < {eoe,irix,hippi}-bld.cpio
  |-- make irix -> cd irix && make -k
  |     \-- includes $(ROOT)/usr/include/make/commondefs
  |           |-- includes releasedefs (RELEASE_NAME=6.5.5f)
  |           |-- 76 #if/#elif blocks for ABI/ISA selection
  |           |-- tools: CC=$(TOOLROOT)/usr/bin/cc
  |           \-- 7 ABI combos: 32/32_M2/32_ABI/N32/N32_M3/N32_M4/64/64_M3/64_M4/64_ABI
  \-- make eoe -> cd eoe && make -k
        \-- same commondefs infrastructure
```

## Value Assessment for Our QEMU Project

**Building the source: low practical value.** We already have a working IRIX 6.5
installation. The effort to get the build system running (especially the smake
dependency) far exceeds the benefit.

**Reading the source: high value.** This is where the real benefit lies:
- `irix/kern/sys/*.h` — 427 kernel headers with hardware register definitions
- `irix/kern/ml/IP22.c` (4787 lines) — platform init, interrupt handling, bus errors
- `irix/kern/io/` — device drivers showing expected hardware behavior
- `eoe/cmd/` — 250+ userland commands
- `eoe/include/makerules/` — understanding the build system itself

The 6.5.5f kernel headers (`irix/kern/sys/`) are essentially identical to
6.5.7m's — same IP22.h (707 lines both), same hpc3.h, mc.h, etc. Both
are equally valid as reference material.

## References

- [MIPSpro - Higher Intellect Wiki](https://wiki.preterhuman.net/MIPSpro)
- [IRIX Kernel Rebuild - Higher Intellect Wiki](https://wiki.preterhuman.net/IRIX_Kernel_Rebuild)
- [Nekonomicon - IRIX 6.5.5 Source Discussion](https://gainos.org/~elf/sgi/nekonomicon/forum/7/16723336/1.html)
- [irix-builder - Docker cross-compiler](https://github.com/mroach/irix-builder)
- [IRIX 3.x Source on GitHub](https://github.com/JohnDTX/irix-3.x-src)
- [Notes on Building Open Source on IRIX](https://www.yendor.com/sgi/irix-fw-howto.html)
- [schilytools smake (different tool)](https://codeberg.org/schilytools/schilytools)
- [lboot man page](https://nixdoc.net/man-pages/IRIX/man1/lboot.1.html)
