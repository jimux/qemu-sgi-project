# SGI software NAS inventory — `/Volumes/Library/SGI/IRIX/sgi`

Shallow recon (2026-06-14). Spinning-rust NAS — keep listings shallow, no recursive `find`.
Access requires the terminal app to have **Full Disk Access** (TCC); else `EPERM`.

## Top-level (the promising ones)
- **`development/`** — the dev goldmine (see below).
- **`6.5_os/`** — EVERY IRIX 6.5.x point release `6.5.1`…`6.5.30` + `foundation_base` +
  `6.5_net_install` + all `IRIX 6.5 Applications <year>` discs. `6.5.5/` = our target
  (Install Tools+Overlays 1/2, Overlays 2/2 — base foundation is under `foundation_base/`).
- **`demos/`** — real IRIS GL/graphics demo discs (test apps better than hand-built gltri):
  Impact Demos 6.2, Infinite Reality Demos Vol 1/2, Maximum IMPACT Demos, SGI General/Platform
  Demos 6.5.11/6.5.12, Onyx2 Demos, `sgi-demos.tar.gz`.
- **`open_inventor/`**, **`cosmo/`** (Cosmo3D/Optimizer), **`impressario/`** (printing),
  **`performance_co-pilot/`**, **`patches/`**, **`irix_source/`**, **`freeware/`**,
  per-platform `o2/` `octane/`, `worldview/` `varsity/` `webforce/` (app bundles).

## `development/` (compilers + dev libs)
- **`mipspro/`** — full MIPSpro set: **All-Compiler CD May 1999 (812-0925-001)** (C/C++/F77/F90
  base 7.x), C 7.2/7.2.1/7.3/**7.4**, C++ 6.0.1…**7.4**, F77/F90 7.4, Auto-Parallelizing 7.3/7.4,
  Power C/Fortran, `MIPSPro_7.4_C_Compiler.tar`, `alldev.tar`(in development/).
- **`development_libraries/`** — **IRIX 6.5 Dev Libraries Feb 2002 (812-0766-003)** [= the
  `dev.sw`+`gl_dev`+`x_dev`+`motif_dev`+`dmedia_dev` source; we already have this one locally],
  June 1998 variant, `IRIX.6.5.Dev_Libraries.efs.img`, plus 6.2/6.3/6.4 dev libs.
- **`development_foundation/`** (Dev Foundation 1.3), **`prodev/`** (WorkShop/dbx/SpeedShop),
  **`workshop/`**, **`developer_toolbox/`**, **`casevision/`**, **`imagevision/`** (ImageVision
  GL library), **`iris_open_performer/`** + **`performer/`** (IRIS Performer 3D), `c++_translator`,
  `fortran` `ada95` `pascal`, `compiler_execution_environment/` (CEE runtime),
  `Developer Tools Maintenance Release 7.3.1.2m/3m`, `Silicon_Graphics_Developer_Magic` ISO.

## Decision for the comprehensive dev image
- **Core dev env is already LOCAL** — `irix655-full` has `dev.sw`+`gl_dev`+`x_dev`+`motif_dev`+
  `compiler_dev`+`c++_dev`+WorkShop installed, and `software_library/.../IRIX_6.5.5_full_extracted/`
  has every dev `.sw`. So the image can be built from local assets NOW; the NAS is a **top-up**.
- **High-value NAS top-ups (fold in later):** the **demos discs** (real IRIS GL test apps for
  Milestone-0/validation), **IRIS Performer + Open Inventor + ImageVision** (3D GL dev libs),
  and the **All-Compiler / C++ 7.4 / F77 / F90** discs for a truly complete MIPSpro.
- Copy chosen `.efs.img`/`.tardist` into `software_library/` (Finder reads NAS, writes repo) so
  the image catalog + `harness_addon` pick them up — no TCC needed at build time.
