# Validation audit — Xsgi / IRIS GL recovered claims

Auditing every documented claim against **ground truth** (shipped headers, the ELF's
own metadata, and — where no header exists — binary-RE evidence + planned dynamic
tests). Goal: be certain before building further.

Legend: ✅ confirmed against ground truth · 🟡 binary-RE only (no header) — needs dynamic
test · ⚠️ found wrong / corrected.

## Audit outcomes (summary)
- **Confirmed** (static headers AND/OR on-target compiler): the `_IO` encoding, `I_STR`,
  all cursor/shmiq/idev/gfx ioctls + their arg structs (incl. `gfx_getboardinfo_args`),
  and `$gp=0x105498ec` (now authoritative via `.reginfo`, not just convention).
- **Corrected (4)** — these WOULD have misdirected us:
  0. **IRIS GL renders through `dgld`, NOT Xsgi** (architectural). `/etc/inetd.conf` +
     `/etc/services` show `sgi-dgl 5232/tcp → /usr/etc/dgld dgld -IM -tDGLTSOCKET`. libgl
     `connect()`s to localhost:5232; inetd spawns `dgld`, which renders. Xsgi only manages
     the window (mixed model). ⚠️ FURTHER CORRECTED: `dgld` is **already installed and the
     service is enabled** on the ip54-test disk (`/usr/etc/dgld` 533892 B; inetd.conf has
     the sgi-dgl line) — my "must be staged / not in our trees" note was wrong (it's just
     not in the host-side *extracted* trees). Extracted offline to `dgld.elf`: ET_EXEC N32
     (cpic, $gp-based), links `libgl.so`; `dgld_interpret` dispatches DGL opcodes to
     `tgl_*` handlers — the server-side counterpart to libgl's dglcmds encoders.
  1. `struct ng1_pixeldma_args` field names were wrong and `flags`/`mode` swapped →
     fixed via `minigl3.c vdma()` (real: xlen,ylen,flags[NG1_WRITE=1/STRIDE=2],pmstride,buf…).
  2. `GFX_ATTACH_BOARD` arg is `struct gfx_attach_board_args {board, vaddr}`, not bare `&screen`.
  3. `ksyscalls.py` ioctl-size mask `0x1fff` → `0xff` (IOCPARM_MASK).
- **Clarified**: the GFX_GETBOARDINFO "40 bytes" is the reply-buffer length, not a fixed
  `sizeof(ng1_info)`.
- **Net**: no substantive Xsgi *behavioral* claim was wrong; the errors were in
  unshipped-struct field naming/shape — now corrected with independent ground truth.

## A. Shipped-header ioctls & structs (ground truth = irix-655-source headers)

| Claim | Ground truth | Status |
|---|---|---|
| `_IO` encoding: dir bits 0x20/40/80<<24, `(size&0xff)<<16`, `type<<8`, nr | `sys/ioccom.h` `_IOC(f,n,x,y)=f\|((n&0xff)<<16)\|(x<<8)\|y`, IOCPARM_MASK=0xff | ✅ |
| `0x5308` = STREAMS `I_STR` | `sys/stropts.h` `I_STR=('S'<<8)\|010 = 0x5308` | ✅ |
| `QIOCSETCPOS = _IOWR('Q',10,struct shmiqsetcpos)` = 0xc004510a | `sys/shmiq.h:200` exact | ✅ |
| `struct shmiqsetcpos { short x; short y; }` (4 bytes) | `sys/shmiq.h:184` exact | ✅ |
| `QIOCIISTR = _IOW('Q',7,struct muxioctl)` = 0x80085107, "double indirect I_STR" | `sys/shmiq.h:197` exact, incl. the comment | ✅ |
| `struct muxioctl { int index; int realcmd; }` (8 bytes) | `sys/shmiq.h:159` exact | ✅ |
| inner cmd `IDEVSETPTR = _IOWR('i',36,idevPtrVals)` = 0xc0086924 | `sys/idev.h:82` exact | ✅ |
| `GFX_ATTACH_BOARD=103/0x67`, `GFX_GETNUM_BOARDS=101/0x65`, `GFX_GETBOARDINFO=102/0x66` | `sys/gfx.h:57-60` (GFX_BASE=100) | ✅ |
| GFX_GETBOARDINFO arg `{board, buf, len}` | `sys/gfx.h:81` `struct gfx_getboardinfo_args` exact | ✅ |
| ~~GFX_ATTACH_BOARD arg = bare `&screen`~~ | **CORRECTED**: `sys/gfx.h:96` `struct gfx_attach_board_args {uint board; void *vaddr}`; decompile fills board + vaddr=(screen*RRMBOARDSIZE+RRMBOARDBASE) | ⚠️→✅ |

Tool note ⚠️: `ksyscalls.py` masks ioctl size with `0x1fff`; the real mask is `0xff`
(IOCPARM_MASK). Harmless for all documented (small-struct) cmds, but the tool is loose —
tighten to `0xff` to avoid false positives on words with bits 24–28 set.

## B. The `$gp` value (underpins EVERY gp-resolved Xsgi decompile)

| Claim | Ground truth | Status |
|---|---|---|
| `_gp = 0x105498ec` | xsgi.bin `.reginfo` `ri_gp_value` = **0x105498ec**; `readelf -A`: "Canonical gp value: 105498ec" | ✅ AUTHORITATIVE |

The `.got+0x7ff0` formula was right, but it is now confirmed by the ELF's own register
info, not just convention. (A `lui gp,0x43;daddiu` mid-binary was a red-herring intra-
function computation, not the startup gp load.)

## C. NG1 / gfx-private claims (NO shipped `sys/ng1.h` — binary-RE only)

| Claim | Evidence | Status |
|---|---|---|
| `struct ng1_pixeldma_args` is the real struct name | `NEWPORT/sagfx.h` `typedef struct ng1_pixeldma_args pixdma_t` | ✅ name |
| `struct ng1_info` is the real struct name | `NEWPORT/local_ng1.h` (Ng1DacInit/VC2Init params) | ✅ name |
| `NG1_PIXELDMA = 0x520a` | rex3DrawImage decompile + "NG1_PIXELDMA failed" string; `sagfx.h` `PIXDMA=NG1_PIXELDMA` (value in unshipped ng1.h) | 🟡 dynamic test |
| ~~ng1_pixeldma_args = {width,height,mode,stride,flags,data,chunkbytes}~~ | **CORRECTED** via `minigl3.c` `vdma()`: real fields `xlen,ylen,flags(NG1_WRITE=1/NG1_STRIDE=2),pmstride,_0xa30,buf,_nbytes`. Original names were wrong + flags/mode swapped. | ⚠️→✅ names |
| DMA threshold `w*h >= 0x4000`; chunk `0x180000` (`NG1_DMA_MAXSIZE`) | rex3DrawImage decompile; sagfx.h has `DMA_MAXSIZE=NG1_DMA_MAXSIZE` (value unshipped) | 🟡 dynamic test |
| board-info "short" struct = 40 bytes | ddxFindAvailableBoards decompile (len=0x28) | 🟡 audit vs NEWPORT |

`sys/ng1.h`/`ng1hw.h` are `#include`d by sagfx.h but **not present** in any source tree
(build-time include path only). So NG1 numeric values come only from the binary — these
are the claims most worth a live test.

## D. IRIS GL / DGL claims (libgl.so)

| Claim | Evidence | Status |
|---|---|---|
| libgl = DGL, pure IRIS GL, no hardware access | DWARF module names + deps (libX11/Xi/Xext) + no /dev/gfx strings | ✅ |
| module partition (dglcmds.c=620, server_if.s=554, …) | DWARF `.debug_line` (decodedline) — exact addr→source | ✅ |
| DGL wire format `[opcode:u32][args:u32…]`, flush via `gl_comm_flush` | encoder decompiles (gl_d_clear/v3f/RGBcolor) | ✅ (static) |
| opcode table (clear=0x14, v3f=0x193, ortho=0x5f/7w, …) | 620 encoder decompiles; lengths verified vs known IRIS GL signatures | ✅ (self-consistent) 🟡 live trace to be 100% |

## Dynamic confirmation (on-target, `irix_tests/`) — DONE for §A core

Ran `validate_consts.c` on the live IP54 guest (ip54-test): compiled with the target's
own MIPSpro `cc` against the **installed** system headers. The compiler emitted exactly
our recovered values (`irix_tests/results/validate_consts_ontarget.s`):

| constant | recovered | on-target `cc -S` |
|---|---|---|
| I_STR | 0x5308 | `.word 21256` = 0x5308 ✅ |
| QIOCSETCPOS | 0xc004510a | `.word -1073458934` = 0xc004510a ✅ |
| QIOCIISTR | 0x80085107 | `.word -2146938617` = 0x80085107 ✅ |
| sizeof(shmiqsetcpos) | 4 | `.word 4` ✅ |
| sizeof(muxioctl) | 8 | `.word 8` ✅ |
| GFX_GETNUM/GETBOARDINFO/ATTACH | 101/102/103 | `.word 101/102/103` ✅ |

So §A is now confirmed **both** statically (source headers) **and** dynamically (target's
compiler + installed headers). The idev constants compile-failed only because their
arg-types are gated off in userland mode — already confirmed statically from `idev.h`.

Harness/disk lessons (reusable; see memory `irix_in_guest_testing`):
- ip54-test golden is a **kernel-build host**: has `sys/*` headers but **no `stdio.h`,
  no `crt1.o`/libc link objects** → can't link userland exes; use `cc -S` (compile-only)
  and read values from the `.s`.
- `/usr/bin/cc` defaults to **K&R mode**; `shmiq.h` uses `_IOWR` but doesn't include
  `sys/ioccom.h` (include it yourself). MIPSpro `cc -S` writes `<base>.s` to **cwd**.
- PROM stops at the **System Maintenance Menu** → send `1` (Start System); after login
  `tset` prompts `TERM = (vt100)` → send Enter. Networking (`pvnet0`) is **down** on
  this boot → TFTP won't work; deliver files via **serial heredoc** instead.
- ⚠️ `pgrep -f qemu-system-mips64` matches its OWN command line (false "still up");
  check `/proc/*/exe` symlinks instead.

## Remaining dynamic tests (the 🟡 NG1 rows + DGL) — need the graphical desktop
1. **constants oracle** (`validate_consts.c`): include the shipped headers, print every
   ioctl number + `sizeof`/`offsetof` → reconfirms §A on-target (catches version skew).
2. **live ioctl trace** (`par` on the running Xsgi, or during a draw): observe the actual
   cmd numbers Xsgi issues → validates `NG1_PIXELDMA=0x520a`, `GFX_ATTACH_BOARD=0x67`,
   `I_STR(QIOCSETCPOS)`.
3. **IRIS GL smoke** (tiny winopen+clear+v3f+swapbuffers): `par`-trace + pvrex3 trace →
   validates the DGL opcode/flush flow end-to-end.
