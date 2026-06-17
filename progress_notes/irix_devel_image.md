# `irix-devel` — the canonical IRIX 6.5.5 dev/build image

Built 2026-06-14 to end the recurring "hunt for gl.h / crt / libX / dev headers on every fresh
instance" problem. **Use `irix-devel` as the build host**; deploy the resulting N32 binaries to
the target instances (ip54-test/desktop). `cc` is reliable here (machine=indy), unlike the
sgi-ip54 machine where the MIPSpro back-end segfaults.

## What it has
- **MIPSpro 7.4.4m** — `cc` / `CC` / `f77` / `f90` (from the combined image addon).
- **`gl_dev`** — IRIS GL + OpenGL headers (`/usr/include/gl/gl.h`, `gl/device.h`), `libgl`.
- **`x_dev`** — X11 dev (`/usr/include/X11/*`, `libX11`).
- **`motif_dev` / `dmedia_dev`** — Motif + Digital Media dev.
- **ProDev WorkShop / dbx / SpeedShop** (from the combined image).
- **Base C dev** — `/usr/include/stdlib.h|stdio.h|string.h|...` + `/usr/lib32/crt1.o,crtn.o`
  (the `dev.sw` content) — baked from `software_library/irix-655-source/f/root` because inst
  **skipped `dev.sw`** (it needs an eoe upgrade that `install_addon`'s `keep *eoe*` blocks).
- 742+ inst subsystems; disk ~1.38GB (golden saved).

## How it was built (reproducible)
1. `cp vm_instances/irix655-dev/disk.qcow2` → `vm_instances/irix-devel/disk.qcow2` (known-good
   boot base, has compiler_eoe).
2. `harness_addon(base_disk=irix-devel, addon_image="dev CDs/IRIX 6.5 Development Libraries
   February 2002 ...efs.img")` → gl_dev/x_dev/motif/dmedia_dev (dev.sw skipped on conflict).
3. `harness_addon(base_disk=irix-devel, addon_image="prepackaged_combo_discs/
   IRIX_6.5.5_full_with_MIPSpro_and_demos_patched.img")` → MIPSpro + WorkShop + apps (cross-CD
   dep resolution).
4. Overlay `devhdrs.tar` (= `irix-655-source/f/root/{usr/include, usr/lib32/crt*.o}`) via
   TFTP+`tar xf` in-guest → fills the `dev.sw` base-header/crt gap. (`ip54_tftp_staging/devhdrs.tar`)
5. `init 0` to persist; `disk.qcow2.golden` saved.

Runner: `run_a_devimg_verify.py` (boots, injects devhdrs, builds gltri, inventories, persists).

## Verified
`cc -n32 -O -o gltri gltri.c -lgl -lX11 -lm` → **CCRC=0**, `/gltri` is a 12400-byte N32 exe.
(`sgi_glremote/test_iris/gltri.c` — the minimal IRIS GL triangle; also installed at `/gltri`.)

## Build/deploy workflow
- Build: boot `machine="indy"` with `scsi_drives=[irix-devel/disk.qcow2]`, TFTP source from
  `10.0.2.2` (= `ip54_tftp_staging`), `cc -n32 ...`, then `init 0` (or extract the binary).
- Deploy to ip54-test: `fs_extract` the binary, `fs_inject` into the target disk (or TFTP).
- The DGL **capture** (#55) can run directly here (Indy + ec0 + slirp guestfwd + Xvfb).

## Future top-ups (NAS, see sgi_nas_inventory.md)
IRIS Performer + Open Inventor + ImageVision dev; the demos discs (real IRIS GL test apps);
full MIPSpro C++ 7.4 / F77 / F90. Drag the `.efs.img`s into `software_library/` and
`harness_addon` them onto this image.
