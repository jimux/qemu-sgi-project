# IRIS GL (libgl.so / DGL) Reverse Engineering — Map & Toolkit

Reverse-engineering `libgl.so`, the IRIS GL client library (the primary 3D/graphics
API for the SGI desktop era — Indigo Magic demos, `/usr/demos`, many desktop apps).
Binary: `/workspace/libgl.bin` (copy of `irix-655/f/root/var/arch/lib32/libgl.so`,
483 KB, **ET_DYN / PIC**, MIPS-III N32 BE, **1916 exported FUNC**). It is SGI's **DGL
(Distributed GL)** library: IRIS GL calls are encoded into a command FIFO and shipped
over a **TCP socket (port 5232, `sgi-dgl`) to the `dgld` GL-server daemon** (started by
inetd: `/usr/etc/dgld dgld -IM -tDGLTSOCKET`), which renders to `/dev/gfx`/REX3. The X
server (Xsgi) is used only for **window management** (the mixed model). libgl itself
touches **no hardware** (deps: libX11/libXi/libXext/libc).

> ⚠️ Correction (2026-06-13): an earlier version of this said IRIS GL renders "through
> Xsgi." It does NOT — rendering is done by **`dgld`** (separate closed daemon). Xsgi only
> owns the window. **`dgld` is ALREADY installed** on the ip54-test disk (`/usr/etc/dgld`,
> 533892 B) and the `sgi-dgl` inetd service is enabled — not missing. Extracted to
> `dgld.elf` for RE. See VALIDATION.md.

## dgld — the IRIS GL server (the renderer)
`/usr/etc/dgld` (extracted offline → `dgld.elf`): ELF32 **N32 (cpic, $gp-based like Xsgi,
so RE via Ghidra + SetGpAndDecompile)**, ET_EXEC. **NEEDED: libgl.so, libfm.so, libX11.so.1,
libc.so.1** — it *links libgl itself* and renders via libgl's `-DSERVER`/direct (`gl_d_*`)
mode (explains libgl's `-DSERVER` build + the gl_d_/gl_g_ split). Key functions:
`dgld_create_queue_socket`/`dglacceptqsocket` (accept the port-5232 connection),
`dgld_login_interpret` → `dgld_interpret`/`dgld_aux_interpret` (the DGL command-stream
interpreter), dispatching each opcode to a **`tgl_*` handler** (`tgl_polf`, `tgl_rpmv*`,
`tgl_readpixels`, `tgl_getmatrix`, …) — the **server-side opcode table**, the counterpart
to libgl's `dglcmds.c` encoders. Startup banner: "dgld started.". RE target: map
`tgl_*` ↔ the `dgl_opcodes.json` numbers and trace where the rendering actually hits REX3.

### dgld_interpret — the opcode Rosetta Stone (`decomp/dgld_interpret.json`)
`dgld_interpret(buf, ...)` is the server's command-stream dispatcher: a giant
`switch(opcode = *buf)` over cases 0..0x248 (opcodes ≥0x24a go to
`dgld_aux_interpret`/`dgld_fm_aux_interpret`). Each case **calls the real GL function and
shows its arg decode**, e.g.:
- `case 0: return 0` (end of stream)
- `case 2: pagecolor(*(short*)(buf+1)); buf+=2`
- `case 4: textport(short,short,short,short); buf+=3`
- `case 5: arc(buf[1], buf[3]); buf+=5`

The GL functions resolve into **libgl (server/direct mode)** → `/dev/gfx` → REX3. So this
is opcode → GL-function + args, the authoritative complement to the client `dgl_opcodes.json`.
**Bidirectional validation — COMPLETE** (`dgl_opcodes_server.json`): parsed all of
dgld_interpret's switch → **547 opcodes (0x0..0x248), 534 named** with their GL function.
Cross-checked vs the client `dgl_opcodes.json`: **512 overlap, 510 exact name-match** (the
2 "mismatches" are just a `gl_` prefix — lrectread/gl_lrectread, lrectwrite/gl_lrectwrite —
so effectively 512/512 agree). The server also yielded **22 opcodes the client-side parser
had missed** (v2d/v3d/v4d double verts, nurbscurve, pwlcurve, sincos). This is the
authoritative, bidirectionally-validated DGL opcode table.

### IRIS GL end-to-end path (now fully mapped)
```
app: arc(x,y,r) → libgl client gl_d_arc → [opcode 5][args] → socket :5232 →
dgld: dgld_interpret case 5 → arc(args) via libgl server-mode → /dev/gfx → REX3 (pvrex3)
```

## The DWARF windfall (this is NOT a stripped binary)
libgl.so ships **full DWARF2 debug info** (`.debug_line`, `.debug_aranges`,
`.debug_info`, `.debug_funcnames`). pyelftools' DIE parser chokes on SGI's MIPS_DWARF
dialect, but **`readelf --debug-dump=decodedline` decodes the line table cleanly** →
exact address→`source.c:line`. That gives a precise function→source-module partition
(`gl_modules.py`). The CU DIEs also carry `DW_AT_name` (`../dgl/comm.c`). Full
type/signature recovery from `.debug_info` is still TODO (DIE parser needed for SGI
DWARF) — would hand us the DGL structs directly.

## Toolkit / recipe
- Binary staged as `/workspace/libgl.bin`; symbols `libgl_symbols.json` (1956);
  Ghidra-resolved function map `libgl_map.json` (1370 funcs w/ PIC-resolved callees +
  refs); module partition `libgl_modules.json`; (jal-based `kcallgraph` is EMPTY on PIC
  — use `libgl_map.json` callees, which Ghidra resolves through the GOT).
- **Ghidra**: project `LibglProj`, imported `MIPS:BE:64:64-32addr`. PIC → **no `$gp`
  step** (Ghidra resolves per-function gp from the GOT). Decompile by name with the
  plain `DecompileNamed.java`:
  `_ghidra_decomp.sh`-style driver against LibglProj. Map export:
  `ExportLibglMap.java` (per-func callees + referenced `.c` strings).
- **`gl_modules.py`**: parses `.debug_line` → `libgl_modules.json`
  (`by_func`/`by_module`/`module_ranges`).

## Module map (1322/1370 bucketed, 19 modules)
| module | funcs | role |
|---|---|---|
| **dglcmds.c** | 620 | the per-call **DGL command encoders** (`gl_d_*`/`gl_dgl_*`) — opcode+args into the FIFO |
| **server_if.s** | 554 | hand-written **asm fast-path stubs** — the public IRIS GL entry points (`clear`,`v3f`,…) that tail-call the encoder |
| fakegl.c | 35 | dispatch glue / wrappers for complex calls (winopen, lrectwrite) |
| memory.c | 22 | bulk-data packers (`gl_mem_pack_array`/`_lrect`) + buffer mgmt |
| comm.c | 19 | transport: `gl_comm_flush`, `gl_comm_read_data` |
| sgionly.c | 14 | SGI-local-only path (mixed-model local render) |
| message.c | 12 | message framing |
| dglopen.c / mixedmodel.c | 9 / 9 | **connection + local render-into-X (G2)** |
| util.c | 9 | helpers (`gl_util_background`, WID helpers) |
| remote.c / socket.c / in_addr.c / decnet.c | 6/4/1/1 | **remote GL-over-net — OUT OF SCOPE** |
| initialize.c / data.c / branch.c / server.c | 3/2/1/1 | init / data tables |

## Confirmed architecture (3-layer)
```
public API (server_if.s asm)   e.g.  clear, v3f, RGBcolor, ortho, swapbuffers
        │ tail-call
encoder  (dglcmds.c)           gl_d_clear / gl_d_v3f / …  -- write [opcode][args] to FIFO
        │ when buffer full
transport(comm.c)              gl_comm_flush  -- ship buffer to Xsgi
```
Complex calls insert a `fakegl.c` wrapper (`gl_d_winopen`→`gl_dgl_winopen`,
`gl_d_lrectwrite`→`gl_dgl_lrectwrite`) that does setup + `gl_mem_pack_*` for bulk data
and resolves the GL window via `gl_dgl_wid_from_gl` (the WID↔X-resource association).

## Recovered: DGL command FIFO wire format
The command buffer is a **u32 stream**: `[opcode:u32][arg:u32]...`, written at
`comm_curbufpos`, bounded by `comm_endbuffer`; when `curbufpos > endbuffer`,
`gl_comm_flush` ships it. Examples (from decompile, `decomp/encoder_sample.json`):
- `gl_d_clear`:  `*p++ = 0x14;`                              (CLEAR, opcode 0x14, 0 args)
- `gl_d_v3f(v)`: `*p++ = 0x193; *p++ = v[0]; v[1]; v[2];`     (V3F, opcode 0x193, 3 args)

## Recovered: the DGL opcode table (`dgl_opcodes.json`)
All 620 `dglcmds.c` encoders decompiled (`decomp/dglcmds_all.json`) and parsed:
opcode = the constant stored to `*base` (offset 0); command length = the buffer advance
`curbufpos = base + N` (N words incl. the opcode word). **524 commands extracted with
clean opcode+length**, verified against known IRIS GL signatures:

| call | opcode | words | = opcode + |
|---|---|---|---|
| clear | 0x014 | 1 | (none) |
| color | 0x01b | 2 | 1 short |
| RGBcolor | 0x082 | 3 | r,g (packed) + b |
| cmov | 0x017 | 4 | 3 floats |
| v3f / v3i / n3f | 0x193/0x194/0x187 | 4 | 3 floats |
| translate | 0x095 | 4 | 3 floats |
| viewport | 0x096 | 3 | 4 shorts |
| ortho | 0x05f | 7 | 6 floats ✓ |
| czclear | 0x17b | 3 | 2 words |
| bgnpolygon/endpolygon/bgntmesh/swapbuffers | 0x171/0x17f/0x172/0x091 | 1 | (none) |

Variable-length bulk-data commands (`loadmatrix`=16 floats, `lrectwrite`=pixel array)
have **no static length** — they serialize via `gl_mem_pack_array`/`_lrect` and a
runtime count; flagged in the table.

## G2: transport + connection (RESOLVED)
The DGL transport is a **Berkeley TCP socket to a GL server** — proven by libgl's imports
(`socket`, `connect`, `gethostbyname`, `select`), **separate from** the X connection
(`XOpenDisplay`, used for window management). This is the "Distributed" in Distributed GL.

- **Connection setup** (`dglopen`→`FUN_0f0cfd24`): resolve target via
  `gl_dgl_default_display` (`getenv("DISPLAY")` → `host:display.screen`, else
  `gethostname()`), parse `host:display.screen`, `gethostbyname`+`socket`+`connect` to the
  host's GL port → the connection fd (`gfile->fd@+0x1c`). For local display this connects
  to the local host's GL listener.
- **Command flush** (`gl_comm_flush`): `gl_comm_io(gfile->fd, …, buffer, nbytes)` =
  `write()` the `[opcode][args]` buffer to the socket, chunked by `comm_stdsize`;
  `gl_comm_fatal_error("write")` on short write. `gfile`: +4 state(==1 closed), +8 max
  chunk, +0x1c fd. The command-buffer globals (named via DWARF, see below):
  `comm_stdsize@0x0f0ef000`, `comm_buffer@0x0f0ef004`, `comm_endbuffer@0x0f0ef008`,
  `comm_curbufpos`, `comm_curgfile`, `comm_gfiles`.

## DWARF type/symbol recovery (B1 — `dwarf_types.py`)
Built a from-scratch SGI-MIPS_DWARF2 DIE parser (`dwarf_types.py`, repo root) — pyelftools
can't parse this dialect. **Key fix:** SGI's `.debug_abbrev` tables are NOT null-terminated;
each CU's `abbrev_off` points at its own table and codes reset to 1 at the next table — so
the parser stops a table on a code reset (`code <= prev`), not just on a null.
- Usage: `dwarf_types.py <elf> structs|struct <n>|func <n>|vars [sub]|json <out>`.
- **libgl.so's DWARF is type-free** (compiled w/o full `-g`): only compile_unit/subprogram/
  variable DIEs → 1329 function names + 90 named globals (with addresses), but NO struct
  layouts/signatures. So it names the `PTR_comm_*` globals (above) but can't give the
  `gfile`/DGL struct types. Struct recovery via this parser awaits a full-`-g` binary
  (e.g. compile our own IRIS GL test progs `-g` on irix655-full).
- **Request/reply**: synchronous calls (e.g. `gl_dgl_winopen` = DGL opcode **0x132** +
  packed window-name) send then `gl_comm_read_data(n)` (`select`+read) to read the reply
  (e.g. the new WID), registered via `gl_dgl_wid_from_gl`. The X side (`XOpenDisplay` +
  GLX) runs in parallel for window association (`gl_glx_finddisplay` walks the display list).

**Server side (xsgi.bin, `decomp/glx_server.json`):** `GLXLoadExtension` does
`dlopen(module)` + `dlsym("__glXExtensionInit")` (fail → "Can't load GLX extension!"), so
Xsgi's **GLX/OpenGL support is a dynamically-loaded server module** (the dyDDX), distinct
from the DGL-socket path.

**Architectural implication:** to run IRIS GL apps locally, a **GL server must listen on
the DGL socket** (the dgld daemon / X server's GL transport). Both mechanisms exist (socket
DGL for the client + dlopen'd GLX-over-X module on the server); *which* the local desktop
actually uses is the one remaining question for a live trace.

## Status / next
- **G0 DONE**: corpus, PIC-resolved call map, DWARF module partition, architecture,
  wire format.
- **G1 mostly done**: DGL opcode+length table (`dgl_opcodes.json`, 524 clean / 606
  total). Remaining: 96 complex encoders whose opcode isn't a bare `*base` store
  (manual pass), and per-arg TYPE encoding (needs the SGI-DWARF DIE parser for
  signatures, or per-command decompile).
- **G2**: decompile dglopen/comm/mixedmodel/sgionly for the transport + the Xsgi
  rendering-node handshake; cross-ref Xsgi `GLXExtensionInit`/`GLXLoadExtension`.
- **G3/G4**: primitive→wire→REX3 table + live IRIS-GL-demo validation on ip54-test.
