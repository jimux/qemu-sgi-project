# Building a full IRIX 6.5 MP kernel from source (IP55 / Virtuix)

How we got from "no kernel source, a half-broken compiler" to a complete `COMPLEX=MP` IP22 kernel that compiles, archives (`ar`), and links (`ld -r`). This is the recipe; the dead-ends are noted because each one cost a build cycle (~10–20 min boot+build).

Everything below runs on the **`irix-devel`** VM instance (machine `indy`), driven over the serial console by `/tmp/mp_build16.py` (boots the host, TFTPs the helper scripts, fresh-stages + patches + builds + lboots, polls). All host-side helper scripts live in `ip54_tftp_staging/` and are pulled into the guest `/tmp` via TFTP.

## 0. The big picture: why this was hard

`irix-devel` was described as "the canonical dev/build image" but is really a **partial MIPSpro userland install**: `cc` compiles `.c`→`.o`, and that's almost it. The full kernel build additionally needs `ar`, a standalone `as`, `/usr/bin/ld`, the n32 CRT/libc link path, and `libelf` — **none of which were present**. The real toolchain lives under `/usr/cpu/sysgen/root/` and is reached only through a few `/usr/bin` symlinks; the binutils-equivalents were simply missing. So most of the work was **reconstructing the toolchain**, one missing piece per build cycle, not fixing source.

Also: **IRIX ships IP22 (Indy) uniprocessor-only.** The shared kernel source is MP-aware (`hardlocks`, TLB shootdown, IPI dispatch) but `#if MP`-compiled-out in the shipped `os.a`. You cannot relink `hardlocks.a` against the SP `os.a` (the `osa_armap_surgery` dead-end). The only correct path is a **full `COMPLEX=MP` rebuild from source**, which is what this doc enables. That rebuild then surfaces genuine latent MP-source bugs (see §5b) that SGI never hit because IP22 never shipped MP.

## 1. Source tree + staging

Use the COMPLETE 6.5.7m tree: `software_library/irix-657m-source/irix/kern` (1506 `.c`). The 6.5.5m tree (`irix-655-source/m/`) is an incomplete snapshot (missing `wtree.c`, `batch.c`, ~20 files). A 6.5.7m kernel runs 6.5.5 userland fine (6.5.x ABI is stable).

The make framework (`commondefs`, `kcommondefs`, `releasedefs`, the `*defs`/`*rules`) comes from `irix-655-source/m/.../root/usr/include/make`; 657m lacks it. Stage that as `ROOT=/usr/tmp/v/root` and the kern tree as `/usr/tmp/v/irix/kern`.

Transfer: TFTP is hard-capped ~32 MB per QEMU process (slirp 16-bit block counter, cumulative). The 47 MB source tree therefore goes in on a **raw 2nd SCSI disk wrapped with an SGI volume header** (`vm_instances/virtuix_kern_src_disk.img`, built by `analysis_tools/create_boot_disk.build_vh`, tar at partition `s7`). In-guest: `dd if=/dev/rdsk/dks0d2s7 of=src.tar bs=512k && tar xf src.tar`. A bare raw disk with no VH reads as 0 bytes (the VH supplies geometry); EFS-mounting a `tar2efs` image gave I/O errors — just `dd` the raw partition.

**Re-stage fresh every build.** `dopatch.sh` edits the staged tree in place; running it repeatedly on an already-patched tree corrupts files (e.g. a botched `sed` left `timer.c` lines mangled, and the second run's pattern no longer matched). `dostage.sh` begins with `rm -rf /usr/tmp/v`, and the driver runs it **before** `dotools.sh` (which creates `$ROOT/usr/lib32` and would otherwise be wiped). One clean stage + one clean patch pass per build = deterministic.

## 2. `smake` (the kernel needs it; `/sbin/make` does NOT work)

The kernel Makefiles use `#if defined()` directives that SysV `/sbin/make` chokes on (`kcommondefs:280 syntax error`). Provision `smake`: it is `pmake`, extracted from the dev product `dev.sw.make` in `IRIX_6.5.5_Overlays_2_of_2/dev_655m.{idb,sw}` via `pyirix.dist`. Install `/usr/sbin/pmake` + `ln -s pmake /usr/sbin/smake` + `/usr/include/make/system.mk`.

## 3. Build invocation

```
smake PRODUCT=4DACE1 COMPILATION_MODEL=N32 ROOT=/usr/tmp/v/root
```

`PRODUCT=4DACE1` → IP22 product → `CPUBOARD=IP22`. Force **MP** by sed-editing the staged product defs (`4DACE1defs`) so `COMPLEX=MP` (→ `-DMP`, selects `hardlocks`).

**Run `smake … headers` FIRST.** This installs in-tree headers (e.g. `os/ksync/klstat.h`→`sys/`, `fs/procfs/procfs.h`→`sys/`, `fs/xfs/xfs_dfrag.h`→`sys/fs/`) into `ROOT/usr/include`. Skipping it leaves a long tail of "could not open `<sys/*.h>`" plus genuine errors in `rt.c`/`cpuset.c`/`ip_input.c`. See `dobuild.sh`.

## 4. Toolchain reconstruction — the missing binutils

The build host's real toolchain is under `/usr/cpu/sysgen/root/` (`/usr/bin/cc` → there; `/usr/lib/ld` → `…/usr/lib/ld`). These pieces were missing and had to be added (all one-time; they persist on the disk because they are real files in `/usr/lib`,`/usr/bin`,`/usr/lib32`):

### 4a. `ar` (archiver) — for the `.a` libraries

`/usr/bin/ar` and `/usr/lib/ar` were absent. Extracted the **real `/usr/lib/ar`** (198 KB n32 ELF) + `driverwrap` from `compiler_dev.sw` (IRIX Development Foundation 1.3, dist-extracted to `/tmp/devdist`). pyirix.dist couldn't locate them — the FOUNDATION `compiler_dev.idb` has **no `off()` field** (unlike the Overlays idb), so its offset logic read garbage. Worked around it by walking the `.sw`'s sequential record format directly: `[u16-BE pathlen][path][\x1f\x9d-LZW payload]` — search for `len(path).to_bytes(2,'big')+path`, then `gunzip` the `cmpsize` payload. Installed `/usr/lib/ar` (chmod 755) + `/usr/bin/ar` → it (point straight at the real archiver — its MIPS armap is what `lboot`/`ld` accept; GNU `ar`'s armap is rejected). Helper: `doar.sh`.

### 4b. `ld` (linker) — for `ld -r` kernel.o and lboot

The real linker exists (`/usr/cpu/sysgen/root/usr/lib/ld`, 645 KB, reached via `/usr/lib/ld`) but the `/usr/bin/ld` symlink was missing. Fix: `ln -s /usr/lib/ld /usr/bin/ld`.

### 4c. `as` (assembler) — for the `.s` exception vectors

The host has **no standalone assembler** (no `as0`/`as1`/`driver`, no `/usr/bin/as`). The 700 `.c` files compile because MIPSpro `cc -c` generates objects via its integrated codegen; only **explicit `.s`** assembly (e.g. `ml/LOCORE/vec_*.s`, `spl.s`, `csu.s`, `asmsubr.s`) needs `as`. Fix: `/usr/bin/as` = a wrapper `#!/bin/sh / exec /usr/bin/cc -c "$@"`. On IRIX, `cc -c` preprocesses (cpp) and assembles `.s` files — the kernel `.s` files `#include`/`#define`, so a cpp-running assembler is required, and `cc` provides it.

### 4d. n32 CRT / libc link path — for the host helper programs

`ml` builds two **host programs**, `genassym` and `elfassym`, that are run on the build host to emit `assym.h`. They link with `-nostdlib -L$(ROOT)/usr/lib32 …`. Our minimal `ROOT` had no `crt1.o`/`crtn.o`/libc, so the link failed (`cc ERROR: crt files not found`). Fix: `ln -s /usr/lib32 $ROOT/usr/lib32` — the host's `/usr/lib32` (+`mips3/`) has the n32 `crt1.o`/`crtn.o`/`libc.so`.

### 4e. `cc -32` → `cc -n32` for the host tools

`ml/Makefile` builds `elfmain.o`/`elfdata32.o`/`elfdata64.o`/`elfassym` with `cc -32` (old o32 flag). The host's modern raw `cc` rejects `-32` (`cc ERROR parsing -32: unknown flag` / `abi should have been specified by driverwrap`), and the host has **no o32 CRT anyway** (`/usr/lib` lacks `crt1.o`). So o32 is dead; these host tools must be n32. Fix (dopatch sed): `$(CC) -32` → `$(CC) -n32 -mips3` in `ml/Makefile`.

### 4f. `libelf` (n32 static) — `elfassym` links `-lelf`

No `libelf` anywhere on the host. Extracted **`/usr/lib32/libelf.a` (341 KB, n32 static)** from `prebuilt_disks/irix-6.5.5-base.qcow2` (`fs_extract`), staged via TFTP, and `cp`'d to `/usr/lib32/libelf.a`.

### 4g. `elfassym` link path o32→n32

That link hardcodes the o32 lib dir: `-nostdlib -L$(ROOT)/usr/lib -lelf`. Since the tool is now n32, point it at lib32 (dopatch sed): → `-L$(ROOT)/usr/lib32/mips3 -L$(ROOT)/usr/lib32 -lelf`.

With 4a–4g in place the `assym.h` chain completes: `elfmain/elfdata/genassym` compile (n32) → `elfassym` links → runs → **`assym.h`** → `.s` vectors assemble (cc-wrapper `as`) → **`ml.a`** → `ld -r ml.a btool_lib.o prsgi.o` → **`kernel.o`**.

## 5. Source patches (`dopatch.sh`, run once on the freshly-staged tree)

IP22-as-MP and the 657m-on-655-framework combination trip several guards that are harmless under QEMU (which models a correct CPU and no caches):

1. **Neuter `WFATAL`** in `kcommondefs` — turns the `-diag_error` `#error` guards (1035) + implicit-decl (1196) into warnings. IP22-MP trips many "not mp safe" / WAR guards on per-CPU/`pda` paths not exercised here.
2. **Remove `-D_VCE_AVOIDANCE`/`_DEBUG`** — its `pf_flags:24` bit-field makes MP `pfdat_lock(&pf_flags)` take the address of a bit-field (~230 errors). QEMU has no virtually-indexed cache, so VCE avoidance is unneeded.
3. **`sys/pda.h`** — drop the `JUMP_WAR` "not mp safe" `#error` (belt-and-suspenders; WFATAL already neutered it).
4+5. **`clkreg_t` + `GET_LOCAL_RTC` undefined for IP22** (clksupport.h only defines them for EVEREST/SN/IP30). Replace `sys/clksupport.h` with a clean version that adds an IP22 branch (`#if !EVEREST && !SN && !IP30 typedef unsigned int clkreg_t; … getcyclecounter()`) **inside the include guard**. The earlier approach (`cat >>` appending after the guard's `#endif`) duplicated across runs and broke the parse — do a deterministic file copy instead, and overwrite the stale copy already installed into `ROOT/usr/include/sys/` by a prior headers pass.
6. **Exclude un-headered optional subdirs** — `autofs`/`dfs` (DCE) in `fs/Makefile`, `sesmgr` (IPsec session mgr) in `bsd/Makefile`.
7. **`ml/Makefile`**: `$(CC) -32`→`$(CC) -n32 -mips3` and the `elfassym` `-L$(ROOT)/usr/lib`→lib32 fixes (see 4e/4g).

### 5b. Genuine MP-source bugs (latent; IP22 never shipped MP)

A `COMPLEX=MP` rebuild compiles code paths SGI only ever ran on Origin/Octane, surfacing real bugs:

- **`ml/timer.c`** — `clkset()` and `enable_fastclock()` call `restoremustrun()` with no matching `setmustrun`. Under UP `restoremustrun(x)` is an empty macro (arg unused); under MP it's a real function taking a `cpu_cookie_t`, so the bare `cpuid` / undeclared `was_running` args fail to compile. They never pin, so the fix is to drop the orphan `restoremustrun` calls. (TODO for true SMP: re-add proper `setmustrun`/`restoremustrun` pairs.)

These per-file fixes are the actual milestone-1b porting surface; expect more (getcpuid/sendintr/slave bringup in `ml/IP22.c`) when wiring up the second CPU.

## 6. The IRIX kernel-link model

`kernel.o` is only `ml.a` + `btool_lib.o` + `prsgi.o` (`$(KLIBS)`, `ld -r`) — the static machine-dependent core. The big subsystem libraries (`os.a`, `fs*.a`, `io*.a`, `bsd.a`, ~50 `*stubs.a`) are the **loadable modules**; `lboot` links them into `/unix` per the system spec.

`ml.a` itself is built incrementally by several `ar ccrl ml.a …` calls — the LOCORE subdir, the softfp subdir, and the **top-dir `MAKELIB`** that archives `IP22.o`/`timer.o`/`csu.o`/`asmsubr.o`/`spl.o`/… If any top-dir object fails to compile, the ml `kdefault` target aborts **before** that MAKELIB runs, so `ml.a` silently ships with only the LOCORE+softfp objects. The symptom is a clean-looking `ml.a`/`kernel.o` but `lboot` failing with `Undefined text symbol "start"/"intr"/"splhi"/…` (the top-dir assembly). i.e. **link-time "undefined `start`" almost always means a top-dir ml `.c` failed to compile** — fix the compile, not the link.

All 106 `.a`'s + `kernel.o` + `ml.a` land in `/usr/tmp/v/irix/kern/IP22bootarea`. The build's own `lboot` parses the build-generated MP `system.kdebug` (it `INCLUDE`s `hardlocks` — already MP, no hand-edited spec needed) but points `-b` at the empty `$ROOT/usr/sysgen/IP22boot` (the `/etc/install -F` module-install step doesn't honor `ROOT`). So the link runs manually with `-b IP22bootarea` (`dolboot.sh`):

```
cd / ; lboot -v -m $ROOT/usr/sysgen/master.d \
   -b /usr/tmp/v/irix/kern/IP22bootarea -u /unix.mp \
   -s $ROOT/usr/sysgen/IP22boot/system.kdebug \
   -c $ROOT/usr/sysgen/stune -n $ROOT/usr/sysgen/mtune
```

## 7. Operational gotchas

- **Serial drops chars in complex inline commands** (backticks/quotes → `> ` continuation). Put every non-trivial command in a script file pulled via TFTP and run `sh /tmp/X.sh`.
- **`sed \t` is not a tab** in the IRIX/portable `sed` here — it inserts a literal `t`. Don't rely on `\t` in replacements (it silently produced `t/* … */`, an "identifier t undefined" compile error).
- **`cache=writethrough`** on the session disks = kill-safe; a SIGTERM'd QEMU doesn't corrupt the qcow2 (verified with `qemu-img check`).
- **`pgrep -f qemu-system-mips64` self-matches** the grep's own command line — use `ps aux | grep … | grep -v grep` to test "is QEMU running".
- **Read `build.log` offline** without rebooting: `fs_extract` the irix-devel qcow2 path `/var/tmp/build.log` (`/usr/tmp` → `/var/tmp` symlink) — only when no QEMU holds the disk.
- Always `init 0` (clean) and confirm no orphan QEMU before the next launch (orphan + `file.locking=off` on a shared disk = corruption).

## 8. Helper scripts (all in `ip54_tftp_staging/`)

| script | role |
|--------|------|
| `dostage.sh`  | `rm -rf /usr/tmp/v`; `dd` the source partition, untar, force `COMPLEX=MP` (fresh tree) |
| `dotools.sh`  | provision `as` (cc-wrapper) + `ld` symlink + `$ROOT/usr/lib32` symlink + `libelf.a` |
| `doar.sh`     | install `/usr/lib/ar` + `/usr/bin/ar` (one-time) |
| `dopatch.sh`  | the §5 source patches (clean clksupport.h copy, ml/Makefile + timer.c fixes) |
| `dobuild.sh`  | `smake headers` then `smake -k …` (writes `build.done` with EXIT=) |
| `doerr.sh`    | error summary (object count, FAILFILES, error lines) |
| `dolboot.sh`  | manual `lboot /unix.mp` with `-b IP22bootarea` |

Driver: `/tmp/mp_build16.py` (boot → TFTP scripts + `libelf.a` + `clksupport.h.virtuix` → `dostage` → `dotools` → `dopatch` → `dobuild` → poll → `dolboot` → `init 0`).

## 9. Status

Toolchain fully reconstructed; the whole kernel compiles and archives (106 `.a`'s) with `ar`. `kernel.o` (143 KB) + `ml.a` (276 KB) link via `ld -r` once all top-dir ml objects compile. Remaining: clear the genuine MP-source compile errors (§5b) on a fresh tree so `ml.a` is complete, then `lboot` → `/unix.mp`, graft onto a fork of `prebuilt_disks/irix-6.5.5-complete-fixed.qcow2`, boot `-M virtuix -smp 2`, then the IP22→MP platform-layer port (getcpuid / sendintr→IPI / slave bringup).
