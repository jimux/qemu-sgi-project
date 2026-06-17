# Phase 1 — `atlantis` (real IRIS GL demo) coverage

Target: `/usr/demos/General_Demos/atlantis/atlantis` — the iconic SGI swimming-creatures demo,
confirmed IRIS GL (`libgl.so`). Driving it through the VirGL pipeline data-driven: implement
opcodes/queries as the trace reveals them. Harness: `run_p1_atlantis.py` (traces each unique
opcode once + frame count via `proxy_renderer`).

## 🎉 MILESTONE 1 ACHIEVED (2026-06-15)
**atlantis renders its swimming creatures inside the Indigo Magic Desktop**, host-rendered via the
DGL interception pipeline. Screendump `_p1_atlantis.png`: a dolphin, a whale, and a school of
sharks — correct **perspective** projection, correct **depth occlusion** (the shark school occludes
the whale behind it), composited into the app's window region over the live desktop. Background is
atlantis's ocean-blue (`cpack 0x953535` = RGB(53,53,149)). The demo runs continuously (swapbuffers
#1,#2,#3… ; `ps` shows `./atlantis` alive). **LIGHTING NOW DONE (2026-06-15):** `_p1_lit.png` shows the creatures properly **shaded** in
atlantis's blue-gray (1588 unique colors in the window vs 5 flat). `lmdef` materials are parsed as
a float-encoded token-value list (`AMBIENT=2.0,r,g,b  DIFFUSE=3.0,r,g,b  SHININESS=5.0,s  LMNULL=0`)
keyed by index; `lmbind` applies the matching material via `glMaterialfv(GL_FRONT_AND_BACK, …)` and
turns on a default white headlight (we don't yet translate atlantis's own light/lmodel defs) +
`GL_LIGHT_MODEL_TWO_SIDE` against the per-vertex `n3f` normals. Geometry, transforms, depth,
present, and shading are all correct. Minor polish remaining: a few whale-body triangles render
dark (inconsistent winding / zero normals on some polys).

### The three bugs that stood between "streams opcodes" and "renders" (all host-side)
1. **Variable-length sizing** (texgen/texdef2d/tevdef/lmdef) — solved authoritatively from
   `dgld_interpret` (see below). Without it, `swapbuffers` was swallowed → 0 frames.
2. **getmatrix replied as 16 bare floats** — but it's an ARRAY op (`mem_narrays=1`), so the reply
   must be `[array_bytelen=64][16 floats]`. Length-less, libgl read the first float (`0x3f800000`)
   as a ~1 GB array length and **crashed a few buffered ops later** → atlantis exited in ~3 s.
   This was THE reason it died after one partial frame. Fixed in `_reply_for`.
3. **gversion() mid-stream query** answered `[0]` instead of the `[scalar][len][string]` array —
   same malformed-array hazard; fixed in `_reply_for`.

### Earlier status (2026-06-14)
atlantis connected, completed the DGL login handshake, and streamed 39 unique opcodes cleanly but
presented **0 frames** — the variable-length opcodes consumed-rest and swallowed `swapbuffers`,
and the 3D translation was unimplemented.

## DONE this session (host-side, 14 tests green)
- **Value-returning query infrastructure** (`dgl_framed.py`): the reply-sync marker means the
  frame's LAST opcode is value-returning → `_reply_for()` answers it with the correct word count.
  - `getgdesc` (GD_* token table — screen size, color/Z depth, MULTISAMPLE=0, …)
  - `getgconfig` (GLC_* compat modes → 0)
  - `getmatrix` → **real OSMesa modelview readback** (16 floats) via a `query` hook to the renderer
  - `getbutton`/`qtest` → 0 (no input), `getvaluator` → 0
  - `REPLY_WORDS` length map + **robust fallback**: any reply-sync frame whose value-returning op
    isn't specifically handled gets a default reply (logged) so the app never hangs.
- **Multi-opcode frame walk** with correct lengths. **Fixed the major desync**: `loadmatrix`/
  `multmatrix` are array-encoded `[op][bytelen=0x40][16 floats]` = 18 words (`ARRAY_OPS`), not 17.

## atlantis's opcode set (the Phase-1 coverage work-list)
Handshake/setup: winopen, RGBmode, **doublebuffer**, gconfig, subpixel, lsetdepth, mmode, qdevice.
Queries (done): getgdesc, getgconfig, getmatrix, getbutton, qtest.
Transforms: **perspective**, **loadmatrix**, **multmatrix**, **rot**, **translate**, **scale**,
**pushmatrix**, **popmatrix**, mmode (matrix mode).
Geometry: **bgnpolygon**, **v3f**, **n3f** (normals), **endpolygon**, **cpack** (color), backface.
Lighting: **lmbind** (+ lmdef materials/lights).
Textures: **texdef2d** (128×128 image), **tevdef**, **texbind**, **texgen** (variable-length arrays).
Present: **swapbuffers** (double-buffered).

## Variable-length opcode sizing — SOLVED authoritatively (2026-06-15)
The guessing is over: `decomp/dgld_interpret.json` is the **server-side interpreter** and its
per-`case` pointer advance gives the exact wire length of every opcode. The array-carrying ops all
follow `advance_bytes = p[base-1] + base*4`, i.e. a `base`-word header whose **last word is an
explicit byte-length**, then `ceil(bytelen/4)` data words:

| opcode | name | base | interpreter advance |
|--------|------|------|---------------------|
| 0x4f / 0x5c | loadmatrix / multmatrix | 2 | `p[1] + 8`  (`p[1]=0x40`) |
| 0x132 / 0x1a3 | winopen / swinopen | 2 | `p[1] + 8` |
| 0x1e5 | scrsubdivide | 3 | `p[2] + 0xc` |
| 0x1e6 / 0x1e8 / 0x16b | tevdef / texgen / lmdef | 4 | `p[3] + 0x10` |
| 0x1e7 | texdef2d | — | `p[5] + *(p+p[5]+0x1c) + 0x20` (TWO arrays) |

Key correction: **texgen's length is the explicit `p[3]` byte-count, NOT computed from the mode** —
`texgen(coord, TG_SPHEREMAP, NULL)` (atlantis's fish reflection) carries a *zero*-length array, so a
mode-derived formula over-consumes and swallows `swapbuffers`. Encoded in `dgl_framed.ARRAY_HEADER`
+ `_texdef2d_len`; regression tests `test_texgen_spheremap_zero_array`, `test_texdef2d_two_arrays`,
`test_multmatrix_array_sizing`. (Also: opcode `0x143` is `ismex()`, not gversion — the `[1]` reply
is coincidentally right either way.)

## DONE (Phase 1 coverage)
1. ✅ Variable-length opcode sizing (texgen/texdef2d/tevdef/lmdef/scrsubdivide) — authoritative
   from `dgld_interpret` (`ARRAY_HEADER` + `_texdef2d_len`).
2. ✅ 3D translation: perspective→glFrustum, mmode→glMatrixMode, loadmatrix/multmatrix, rot/rotate/
   translate/scale, push/popmatrix, n3f→glNormal3f, cpack→glColor (packed), zbuffer→GL_DEPTH_TEST,
   backface/frontface→glCullFace, czclear/zclear, shademodel, lmdef/lmbind→materials + default light.
3. ✅ swapbuffers/mswapbuffers → present → frame → pvrex3 compositor (animation loop runs).
4. ✅ Value-returning queries: getgdesc/getgconfig/getmatrix(**array reply**)/getbutton/qtest/
   getvaluator + robust fallback.

## Textures DONE (Milestone 3, 2026-06-15)
`texdef2d`→glTexImage2D (128×128; IRIS texels are ABGR-packed long words → reverse 4 bytes →
GL RGBA), `texbind`→glBindTexture+enable, `texgen`→glTexGeni(GL_SPHERE_MAP)+enable S/T,
`tevdef`/`tevbind`→glTexEnv(MODULATE). The fish now carry their signature sphere-environment-mapped
reflective skin (window unique colors 1368→2214). Encodings authoritative from `dgld_interpret`
(texdef2d image at word 6 = args[5], `w*h` texel words). Runs on OSMesa + the Mac GPU; artifact
`m3_atlantis_textured.png`. Regression `tests/test_textures.py` (2 tests). In `iris_gl_backend.py`.

## Remaining (polish / breadth, not blockers)
- **Atlantis's own lights/lmodel**: we use a default headlight; translate lmdef DEFLIGHT(1)/
  DEFLMODEL(2) + lmbind(LIGHT*/LMODEL) for atlantis's exact lighting.
- **Dark-triangle artifact**: a few whale-body polys shade dark (winding/zero-normal); investigate
  per-poly normals / `glFrontFace`.
- **Dynamic windowing** (#68): re-clip on move/resize via the Step-4 ValidateClip channel;
  multi-window by WID.

The protocol/transport/query/translation layers are solved and tested (17 host tests green); the
remainder is breadth (textures, exact lights) and the windowing integration.
