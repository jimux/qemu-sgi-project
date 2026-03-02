# IRIX CD-ROM Directory Layouts

Documented 2026-02-15 during recursive scanner implementation for
`tools/irix_pkg_analyzer.py`.

## Background

SGI IRIX software was distributed on EFS-formatted CD-ROMs. Each
installable product has a **spec file** (binary header starting with
`pd001` magic) plus companion `.sw`, `.idb`, `.man`, `.books` files.
The IRIX `inst` installer expects to find these in a `dist/` directory,
but over the years SGI used several different disc layouts.

The original scanner only looked for spec files directly in `/dist/`.
This missed products on ~15 disc images. A recursive scan across the
full filesystem (with deduplication by product name) finds everything.

## Layout Patterns

Across 207 scanned disc images, six distinct patterns emerge:

### 1. Standard `/dist/<spec>` (most common)

The majority of discs place spec files and their companions directly
in `/dist/`. Foundation discs, overlay discs, and most standalone
product CDs use this layout.

```
/dist/eoe              <- spec file (pd001 magic)
/dist/eoe.sw           <- software archive
/dist/eoe.idb          <- installation database
/dist/eoe.man          <- man pages
/dist/compiler_dev
/dist/compiler_dev.sw
...
```

Examples: IRIX 6.5 Foundation 1/2, all Overlay discs, most MIPSpro
single-product CDs (C 7.3, C++ 7.3, Fortran 77 7.4).

### 2. Version subdirectories inside `/dist/` (Development discs)

Development Foundation, IDO, and Compiler Execution Environment CDs
place specs inside version-specific subdirectories. The top-level
`/dist/` contains only metadata files (`.iscd`, `.redirect`) and the
subdirectory.

```
/dist/.iscd
/dist/.redirect
/dist/dist6.3/CaseVision         <- spec
/dist/dist6.3/CaseVision.sw
/dist/dist6.3/c++_dev             <- spec
/dist/dist6.3/compiler_dev        <- spec
...
```

Subdirectory naming varies:
- `dist6.3/`, `dist6.4/`, `dist6.5/` — Development Foundation, Compiler Exec Env
- `6.3/`, `6.4/` — IDO 7.1
- `5.2/`, `5.3/` — Developer Toolbox 5.0

Examples:
- IRIX 6.3/6.4/6.5 Development Foundation (all versions)
- IRIS Development Option 7.1 for IRIX 6.3 (27 specs in `dist/6.3/`)
- Compiler Execution Environment 7.3 (3 specs in `dist/dist6.5/`)
- Developer Toolbox 5.0 (specs in `dist/5.2/` and `dist/5.3/`)

### 3. Multi-version with top-level AND subdirectory specs

Some discs targeting multiple IRIX versions have the "default" spec
at the top level of `/dist/` plus version-specific copies in subdirs.
The top-level and subdir specs typically contain the same product with
the same or slightly different version info.

```
/dist/c_fe                        <- spec (top-level)
/dist/c_fe.sw
/dist/c_fe.idb
/dist/dist6.5/c_fe                <- spec (6.5-specific copy)
/dist/dist6.4/c_fe                <- spec (6.4-specific copy)
/dist/dist6.3/c_fe                <- spec (6.3-specific copy)
```

Some discs also have version-specific directories at the root level
with symlinks back to `/dist/`:

```
/dist6.3/CaseVision -> ../dist/dist6.3/CaseVision
/dist6.3/CaseVision.sw -> ../dist/dist6.3/CaseVision.sw
```

The scanner deduplicates by product name, keeping the first occurrence.

Examples: MIPSpro C 7.3, MIPSpro C++ 7.3, ONC3 NFS, Performance
Co-Pilot 2.1, ProDev Developers Suite.

### 4. Content subdirectories inside `/dist/`

Overlay and Applications discs sometimes organize products into
functional subdirectories within `/dist/`.

```
/dist/eoe                         <- spec (top-level)
/dist/dev/java2_dev               <- spec (in dev/)
/dist/extras/freeware             <- spec (in extras/)
/dist/unbundled/cosmo_dev         <- spec (in unbundled/)
/dist/miniroot/...                <- miniroot kernel (not specs)
```

Examples:
- IRIX 6.5 Applications (specs in `dist/dev/`, `dist/extras/`)
- IRIX 6.5.22 Overlays 2 of 3 (specs in `dist/unbundled/`)
- IRIX 6.5.5 Overlays 2 of 2 (specs in `dist/trix/`, `dist/unbundled/`)

### 5. Arbitrary deep paths (Demo and Toolbox discs)

Demo CDs and Developer Toolbox discs scatter installable products
throughout the filesystem at various depths.

```
/install/demos_octane             <- OCTANE Demos
/install/demos_O2                 <- O2 Demos
/toolbox/searchtools/dist/oasisIII
/toolbox/dist/netscape
/toolbox/src/apps/gvi/inst/gvi
/toolbox/src/exampleCode/speech/inst/speechManager
/toolbox/documents/DevDriver/DevDriver
/toolbox/public/fw_perl
/public/GNU/emacs.inst/emacs19
/public/fax/inst/flexfax
/public/ghostscript/inst/ghost
```

Examples:
- OCTANE Demos 1.3 (1 spec at `/install/demos_octane`)
- O2 Demos 1.3 (1 spec at `/install/demos_O2`)
- Developer Toolbox 5.1-6.5b (specs under `/toolbox/`)
- Developer Toolbox 3.x-4.x (specs under `/public/`, `/src/`, `/bin/`)

### 6. Root-level specs (ancient discs)

Very early IRIX discs (pre-5.x) place specs directly in the root
directory with no `dist/` subdirectory at all.

```
/ftn                              <- Fortran 77 3.4
/dev                              <- IDO 4.0
/c
/gl_x_dev
/IndiZone2                        <- IndiZone 2
/OutOfBox
```

Examples: Fortran 77 3.4, IRIS Development Option 4.0, IndiZone 1-3.

## Scanner Implementation

The recursive scanner (`EFSImageScanner._find_specs_recursive`) walks
the full directory tree up to 6 levels deep, checking every regular
file for `pd001` magic. It skips:

- Files starting with `.` (metadata)
- Files with known non-spec extensions (`.sw`, `.idb`, `.man`, etc.)
- Files larger than 1MB or smaller than 20 bytes
- Symlinks (avoids double-counting the root-level symlink directories)

Deduplication by `product_name` (from the parsed spec header) prevents
counting the same product multiple times when it appears in both
top-level `/dist/` and version-specific subdirectories.

## Results

| Metric | Old Scanner | Recursive Scanner |
|--------|-------------|-------------------|
| Images with 0 products | 20 | 5 |
| Total product entries | 1643 | 2159 |
| Distinct product names | 339 | 438 |

The 5 remaining zero-product images:
- 2 truncated disc dumps (data blocks beyond file boundary)
- 3 genuinely content-only discs (HTML docs, Yosemite demo data)

## Truncated Disc Images

Two disc images in the library are truncated — the SGI volume header
declares a partition larger than the actual file:

- `IRIX_6.3_Development_Foundation-812-0696-001.efs.img` (217MB file,
  455MB partition). Full copy exists as `IRIX 6.3 Development
  Foundation - 812-0696-001.efs.img` (455MB).
- `IRIX_6.4_Development_Foundation-812-0697-001.efs.img` (92MB file,
  458MB partition). Full copy exists as `IRIX 6.4 Development
  Foundation - 812-0697-001.efs.img`.

The truncated copies have directory inodes pointing to blocks beyond
the file boundary, so `read_file_data` returns empty and no specs
are found. The full copies scan correctly.
