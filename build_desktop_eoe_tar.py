#!/usr/bin/env python3
"""Build a tar of all desktop_eoe.sw.* files from IRIX 6.5.5 install media.

Uses pyirix.dist to parse the .idb and decompress LZW-compressed .sw entries,
emitting a tar that mirrors install_path with correct modes + symlinks. Drop
the tar under ip54_tftp_staging/ and untar it LIVE on the guest at /.
"""
from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path

from pyirix.dist.idb import parse_idb
from pyirix.dist.archive import extract_one


DIST_DIR = Path(
    "software_library/prepackaged_combo_discs/IRIX_6.5.5_full_extracted/"
    "IRIX_6.5_Applications_June_1998_-_812-0761-002"
)
IDB = DIST_DIR / "desktop_eoe.idb"
SW = DIST_DIR / "desktop_eoe.sw"

# Which subsystems to ship. Skip books/man/data/relnotes — purely docs that
# bloat the live transfer for no boot value. Confidence has the diagnostics
# (cmftest etc) — skip; not needed for clogin+4Dwm parity.
KEEP_SUBS = {
    "desktop_eoe.sw.envm",          # 4Dwm, FTRlib, faces, X11 schemes...
    "desktop_eoe.sw.toolchest",     # /usr/sbin/toolchest, tellwm
    "desktop_eoe.sw.Desks",         # multi-desk machinery
    "desktop_eoe.sw.control_panels",# audiopanel, background, colorscheme, ...
    "desktop_eoe.sw.share",         # shared icons
}


def main(out_path: str) -> int:
    idb = parse_idb(str(IDB))
    sw_bytes = SW.read_bytes()

    n_files = 0
    n_links = 0
    n_dirs = 0
    out_path_p = Path(out_path)
    out_path_p.parent.mkdir(parents=True, exist_ok=True)

    # gzip for the on-the-wire transfer (tftp can carry binary fine)
    with tarfile.open(out_path, "w:gz", format=tarfile.GNU_FORMAT) as tf:
        # Carry directories explicitly so chmod on the guest preserves modes.
        seen_dirs: set[str] = set()

        def ensure_dirs(install_path: str):
            parts = install_path.lstrip("/").split("/")[:-1]
            cur = ""
            for p in parts:
                cur = cur + "/" + p if cur else p
                if cur in seen_dirs:
                    continue
                seen_dirs.add(cur)
                ti = tarfile.TarInfo(name=cur)
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
                ti.uid = 0
                ti.gid = 0
                ti.uname = "root"
                ti.gname = "sys"
                tf.addfile(ti)

        for e in idb.entries:
            if e.subsystem not in KEEP_SUBS:
                continue
            # tar paths must be relative — strip leading /
            rel = e.install_path.lstrip("/")
            if e.type == "f":
                data = extract_one(sw_bytes, e)
                if e.size and len(data) != e.size:
                    print(f"WARN size mismatch {rel}: idb={e.size} got={len(data)}",
                          file=sys.stderr)
                ensure_dirs(e.install_path)
                ti = tarfile.TarInfo(name=rel)
                ti.size = len(data)
                ti.mode = e.mode & 0o7777
                ti.uid = 0
                ti.gid = 0
                ti.uname = e.owner or "root"
                ti.gname = e.group or "sys"
                ti.type = tarfile.REGTYPE
                tf.addfile(ti, io.BytesIO(data))
                n_files += 1
            elif e.type == "l":
                if not e.target:
                    print(f"WARN symlink without target: {rel}", file=sys.stderr)
                    continue
                ensure_dirs(e.install_path)
                ti = tarfile.TarInfo(name=rel)
                ti.type = tarfile.SYMTYPE
                ti.linkname = e.target
                ti.mode = 0o777
                ti.uid = 0
                ti.gid = 0
                ti.uname = "root"
                ti.gname = "sys"
                tf.addfile(ti)
                n_links += 1
            # 'd' / other types — directories implied by ensure_dirs above

        n_dirs = len(seen_dirs)

    sz = out_path_p.stat().st_size
    print(f"Wrote {out_path}: {n_files} files, {n_links} symlinks, "
          f"{n_dirs} dirs, {sz} bytes ({sz/1024:.1f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1
                          else "ip54_tftp_staging/desktop_eoe.tar.gz"))
