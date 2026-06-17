# Xsgi Reverse Engineering — Map & Toolkit

Reverse-engineering the closed SGI X server `Xsgi` (the Newport DDX + X core) to
recover what it expects from the kernel and hardware. Binary: `/workspace/xsgi.bin`
(ELF32 MSB, MIPS-III/N32, ET_EXEC @ 0x10xxxxxx, debug-stripped but **2,557 named
exported functions in `.dynsym`**). Absolute addressing (lui/addiu, not GOT) — so
static analysis resolves data/string refs directly.

## The RE toolkit (static, runs in the docker dev container)

All take `KELF`/`KSYMS` env vars (default to the golden kernel). For Xsgi:
`KELF=/workspace/xsgi.bin KSYMS=/workspace/xsgi_symbols.json`.

- `kernel_syms.py gen --elf xsgi.bin --out xsgi_symbols.json` — symbols → JSON (3347).
- `kdisasm.py <fn>` — capstone disasm of a function, jal/branch targets resolved.
- `kxref.py <fn>` — direct (`jal`) callers of a function.
- `kdataref.py <hexaddr>` — where a function's POINTER is stored (dispatch tables).
- `kstrings.py [substr]` / `--func <fn>` — string refs → which function uses each.
- `ksyscalls.py` / `--func <fn>` — ioctl-cmd constants, decoded (`_IOWR('Q',10)`…),
  per function.
- `kcallgraph.py build|callees <fn>|callers <fn>|path <a> <b>` — whole-binary direct
  call graph (`xsgi_callgraph.json`).
- **Ghidra 12.1.2** at `/workspace/tools/ghidra_pub/...` (JDK21 at
  `/workspace/tools/jdk21`), project `XsgiProj` imported+analyzed. **Working C
  decompilation** — see "Ghidra recipe" below. Decompile-with-$gp by name:
  `_ghidra_decomp_gp.sh 0x105498ec "fn1,fn2" out.json` (Java GhidraScript
  `SetGpAndDecompile.java` — PyGhidra not enabled, so use Java scripts).

### Ghidra recipe (two non-obvious fixes — both required)
1. **Language must be `MIPS:BE:64:64-32addr`**, NOT `MIPS:BE:32:default`. Xsgi is
   N32: 32-bit pointers but **64-bit registers + MIPS-III `sd`/`ld`**. The 32-bit
   language has no `sd`/`ld`, so SLEIGH fails "Unable to resolve constructor" at the
   2nd instruction of every function. (`_ghidra_import.sh` now passes `-processor
   MIPS:BE:64:64-32addr`.)
2. **The aarch64 decompiler native is not shipped** — built from source. The Makefile
   hardcodes `ARCH_TYPE=-m32` (x86-only); override with `make ARCH_TYPE= ghidra_opt`
   and install the result to `Ghidra/Features/Decompiler/os/linux_arm_64/decompile`
   (`_build_decompiler.sh` does this).
3. **Set `$gp=0x105498ec`** (`= .got 0x105418fc + 0x7ff0`) so gp-relative global
   accesses resolve to named symbols (`coreX`, `ddxCurrentScreen`, `corePriv`,
   per-screen tables) instead of `unaff_gp_lo+N`. `SetGpAndDecompile.java` sets the
   gp context register over all exec blocks, then decompiles.

Note: X-server DDX functions are dispatched through **function-pointer ops tables**
(e.g. `rex3WinOps8` holds `rex3DrawImage` at +0x8), so `kxref` finds 0 direct callers
for them — use `kdataref` to find the table, then trace who calls through the table.

## Entry-point map (from kstrings)
- **`irixKernInit`** — opens `/dev/gfx` (board attach) and `/dev/opengl`. ("Xsgi:
  /dev/gfx open failed" error string lives here.)
- **`ddxFindAvailableBoards..PDH`** — `/dev/opengl` (board enumeration).
- **`sgiOpenDevice`** — opens `/dev/input`.
- **`shared_space_malloc`** — `/dev/shmiq`, `/dev/qcntl%d`, `/dev/zero` (shmiq shared
  memory setup).
- **`_XSERVTransShmBufAccept`** — `/dev/xconns` (X shared-mem transport).

## Input / cursor / shmiq interface (from ksyscalls + sys/shmiq.h + sys/idev.h)

**Cursor positioning — SOLVED + decompiled** (full detail:
`recovered_headers/cursor_and_gfx_attach.md`). Xsgi does NOT write VC2 CURSOR_X/Y and
does NOT use `GFX_POSCURSOR`. `simpleSetPointer` wraps `QIOCSETCPOS` in a STREAMS
`I_STR` (0x5308) and sends it down the `/dev/shmiq` fd:
`ioctl(shmiqfds, I_STR, strioctl{cmd=0xc004510a QIOCSETCPOS, len=4, dp=&{short x,y}})`.
The 4-byte payload `struct shmiqsetcpos{short x; short y}` is now recovered. If an
idev pointer device is attached it also forwards `I_STR(QIOCIISTR 0x80085107 →
IDEVSETPTR 0xc0086924)`. Error string: "Warning: QIOCSETCPOS ioctl failed". The
kernel `shmiq.o` STREAMS module moves the VC2 hardware cursor — so any pvfb/QEMU
cursor logic keyed on VC2 CURSOR_X/Y writes will never fire.

| Xsgi function | ioctl | header name | meaning |
|---|---|---|---|
| shmiqInit | `_IOW('Q',1)` | QIOCATTACH | map the shared input queue |
| shmiqLink | `_IOWR('Q',8)` | QIOCGETINDX | get stream index from l_index |
| ddxChangeToScreen | `_IOW('Q',6)` | QIOCSETSCRN | set current screen |
| ProcessInputEvents | `_IO('Q',3)` | QIOCSERVICED | ack overflow |
| simpleAttachCursor | `_IOWR('Q',9)` | QIOCSETCURS | set cursor axes |
| simpleAttachCursor +others | `_IOW('Q',7)` | QIOCIISTR | double-indirect mux ioctl |
| **coreSetCursorPosition, simpleSetPointer** | `_IOWR('Q',10)` | **QIOCSETCPOS** | **set cursor position** |
| sgiOpenDevice | `_IOW('i',51)` | IDEVINITDEVICE | init device on open |
| simpleAttachCursor | `_IOW('i',33)` | IDEVSETPTRMODE | pointer-event mode + axes |
| simpleSetPointer | `_IOWR('i',36)` | IDEVSETPTR | set pointer position |
| simpleConstrainPointer | `_IOWR('i',35)` | IDEVSETPTRBOUNDS | clamp bounds |
| simpleScalePointer, simplePointerControl | `_IOWR('i',37)` | IDEVSETTRANSFORM | accel/transform |
| simpleSetResolution | `_IOW('i',14)` | IDEVSETVALUATORDESC | valuator range [0..xpmax] |
| simpleSetValuators | `_IOW('i',12)` | IDEVSETVALUATORS | enable X/Y axes |
| (idev get/set) | `_IO*('i',2/3/4/17/18/23/123…)` | IDEVGET*/SET* | keymap/strdpy/bells |

## Dispatch tables (DDX structure)
- `rex3WinOps8` (0x10548370) — 8bpp window ops; `rex3DrawImage` at +0x8. Likely
  `rex3WinOps24`/other-depth siblings exist (the depth-specific DDX ops).

## Recovered via decompile (see recovered_headers/)
- **Cursor positioning** — `I_STR(QIOCSETCPOS{short x,y})` on /dev/shmiq, +idev forward
  (`cursor_and_gfx_attach.md` §1). `struct shmiqsetcpos` recovered.
- **Board attach** — `GFX_ATTACH_BOARD(103)` on /dev/opengl; /dev/gfx opened separately
  (`cursor_and_gfx_attach.md` §2).
- **Board enumeration** — `GFX_GETNUM_BOARDS(0x65)` + `GFX_GETBOARDINFO(0x66, {int
  board; void *buf; int len=0x28})`; board-info "short" form = 40 bytes
  (`cursor_and_gfx_attach.md` §3).
- **NG1_PIXELDMA (0x520a)** — bulk-image path, `struct ng1_pixeldma_args`, the
  `w*h>=0x4000` PIO/DMA threshold (the task-#16 patch site), 1.5MB chunking
  (`ng1_pixeldma.md`). Depth variants: rex3DrawImage/12/24, rex3ReadImage/12/24.

## Open / next
- `struct ng1_info` full 40-byte field layout (width/height/depth offsets) — read by
  `ddxOpenScreens`/`rex3KernInit`; fixes pvfb's gfx_info.length / 24bpp. (+0xc = board type.)
- MAPALL step (tail of irixKernInit) — the gfx region mmap.
- `/dev/opengl` GL path (ddxFindAvailableBoards) — the GL future.
