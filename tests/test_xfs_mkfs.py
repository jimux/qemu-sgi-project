"""V1 XFS create (mkfs) + read/write round-trip and repair, synthetic images.

These exercise pyirix.xfs.mkfs (build an IRIX V1-directory XFS from scratch),
the read path, the write path (mkdir/create_file → AGF/AGI/btree allocators),
and pyirix.xfs.repair. Fully self-contained — no real disk or mkfs.xfs needed.
"""

import shutil
import pytest

from fs_fixtures import build_corpus, KNOWN_XFS_FILES, KNOWN_XFS_DIRS
from pyirix.xfs.image import open_disk_image, find_xfs_partition
from pyirix.xfs.superblock import read_superblock, sash_compatible
from pyirix.xfs.constants import XFS_SB_MAGIC
from pyirix.xfs.operations import list_dir, resolve_path, create_file, mkdir
from pyirix.xfs.inode import read_inode, read_file_data
from pyirix.xfs import repair as xrep

XFS_PART = 64 * 512   # mkfs_xfs places the partition at sector 64


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    return build_corpus(str(tmp_path_factory.mktemp("xfs_corpus")))


# ── Build / read round-trip ─────────────────────────────────────────

class TestXFSReadRoundTrip:
    @pytest.mark.parametrize("key", ["xfs_valid", "xfs_valid_2ag"])
    def test_superblock_v1(self, corpus, key):
        with open_disk_image(corpus[key]) as f:
            po, _ = find_xfs_partition(f)
            sb = read_superblock(f, po)
            assert sb["sb_magicnum"] == XFS_SB_MAGIC
            assert (sb["sb_versionnum"] & 0x000F) == 4      # version 4
            assert not (sb["sb_versionnum"] & 0x2000)       # DIRV2 clear -> V1 dirs
            ok, why = sash_compatible(sb)
            assert ok, why

    @pytest.mark.parametrize("key", ["xfs_valid", "xfs_valid_2ag"])
    def test_files_and_dirs(self, corpus, key):
        with open_disk_image(corpus[key]) as f:
            po, _ = find_xfs_partition(f)
            sb = read_superblock(f, po)
            root = {e["name"]: e["type"] for e in list_dir(f, po, sb, "/")}
            assert root.get("etc") == "d"
            assert root.get("usr") == "d"
            for path, expected in KNOWN_XFS_FILES.items():
                ino = resolve_path(f, po, sb, path)
                assert ino is not None, path
                data = read_file_data(f, po, sb, read_inode(f, po, sb, ino))
                assert data == expected, path

    def test_nested_empty_dir(self, corpus):
        with open_disk_image(corpus["xfs_valid"]) as f:
            po, _ = find_xfs_partition(f)
            sb = read_superblock(f, po)
            assert resolve_path(f, po, sb, "/usr/lib") is not None


# ── Write into a freshly-made filesystem ────────────────────────────

class TestXFSWrite:
    def test_create_and_readback(self, corpus, tmp_path):
        dst = str(tmp_path / "w.img")
        shutil.copy(corpus["xfs_valid"], dst)
        with open_disk_image(dst, writable=True) as f:
            po, _ = find_xfs_partition(f)
            sb = read_superblock(f, po)
            mkdir(f, po, sb, "/new")
            create_file(f, po, sb, "/new/file", b"x" * 12000, mode=0o644)   # multi-block
        with open_disk_image(dst) as f:
            po, _ = find_xfs_partition(f)
            sb = read_superblock(f, po)
            ino = resolve_path(f, po, sb, "/new/file")
            assert read_file_data(f, po, sb, read_inode(f, po, sb, ino)) == b"x" * 12000

    def test_many_files_allocation(self, corpus, tmp_path):
        dst = str(tmp_path / "many.img")
        shutil.copy(corpus["xfs_valid_2ag"], dst)
        with open_disk_image(dst, writable=True) as f:
            po, _ = find_xfs_partition(f)
            sb = read_superblock(f, po)
            mkdir(f, po, sb, "/many")
            for i in range(30):
                create_file(f, po, sb, f"/many/f{i:02d}", b"y" * 1500, mode=0o644)
        with open_disk_image(dst) as f:
            po, _ = find_xfs_partition(f)
            sb = read_superblock(f, po)
            assert len(list_dir(f, po, sb, "/many")) == 30


# ── Diagnostics / repair ────────────────────────────────────────────

class TestXFSCheckRepair:
    def test_healthy_passes(self, corpus):
        with open_disk_image(corpus["xfs_valid"]) as f:
            po, _ = find_xfs_partition(f)
            assert xrep.check_xfs(f, po).ok

    def test_bad_version_detected_and_repaired(self, corpus, tmp_path):
        dst = str(tmp_path / "ver.img")
        shutil.copy(corpus["xfs_bad_version"], dst)
        with open_disk_image(dst, writable=True) as f:
            rep = xrep.check_xfs(f, XFS_PART)
            assert any(x.code == "sb_version" for x in rep.by_level("FAIL"))
            assert rep.repairable
            act = xrep.repair_version_bits(f, XFS_PART, dry_run=False)
            assert act["changed"]
            assert xrep.check_xfs(f, XFS_PART).ok

    def test_wiped_primary_recovered_from_secondary(self, corpus, tmp_path):
        dst = str(tmp_path / "wiped.img")
        shutil.copy(corpus["xfs_wiped_sb"], dst)
        with open_disk_image(dst, writable=True) as f:
            rep = xrep.check_xfs(f, XFS_PART)
            assert any(x.code == "sb_magic" for x in rep.by_level("FAIL"))
            # secondary must be advertised as available
            assert any(x.code == "secondary_sb" for x in rep)
            act = xrep.recover_superblock(f, XFS_PART, dry_run=False)
            assert act["changed"]
            assert xrep.check_xfs(f, XFS_PART).ok

    def test_dry_run_does_not_write(self, corpus, tmp_path):
        dst = str(tmp_path / "dry.img")
        shutil.copy(corpus["xfs_bad_version"], dst)
        with open_disk_image(dst, writable=True) as f:
            act = xrep.repair_version_bits(f, XFS_PART, dry_run=True)
            assert act["changed"] is False
            # still failing because we didn't write
            assert not xrep.check_xfs(f, XFS_PART).ok

    def test_truncated_fails_gracefully(self, corpus):
        # check_xfs must not raise on a truncated image; it should FAIL.
        with open_disk_image(corpus["xfs_truncated"]) as f:
            rep = xrep.check_xfs(f, XFS_PART)
            assert not rep.ok


class TestXFSSpecialFiles:
    """Symlink + device-node (mknod) creation — needed to build a bootable root."""

    def _fresh(self, tmp_path):
        from pyirix.xfs.mkfs import mkfs_xfs
        img = str(tmp_path / "special.img")
        mkfs_xfs(img, size_mb=4, with_volume_header=False)
        return img

    def test_symlink_roundtrip(self, tmp_path):
        from pyirix.xfs.operations import create_symlink, mkdir, resolve_path
        from pyirix.xfs.inode import read_inode, read_symlink
        img = self._fresh(tmp_path)
        with open(img, "r+b") as f:
            sb = read_superblock(f, 0)
            mkdir(f, 0, sb, "/dev")
            create_symlink(f, 0, sb, "/dev/root", "/hw/disk/root")
        with open(img, "rb") as f:
            sb = read_superblock(f, 0)
            ino = resolve_path(f, 0, sb, "/dev/root")
            assert read_symlink(f, 0, sb, read_inode(f, 0, sb, ino)) == "/hw/disk/root"
            # shows as a symlink in listings
            entries = {e["name"]: e for e in list_dir(f, 0, sb, "/dev")}
            assert entries["root"]["type"] == "l"
            assert entries["root"]["link_target"] == "/hw/disk/root"

    def test_mknod_char_device(self, tmp_path):
        from pyirix.xfs.operations import mknod, mkdir, read_dev
        from pyirix.xfs.constants import S_IFCHR
        img = self._fresh(tmp_path)
        with open(img, "r+b") as f:
            sb = read_superblock(f, 0)
            mkdir(f, 0, sb, "/dev")
            # real IRIX dev words
            mknod(f, 0, sb, "/dev/console", S_IFCHR | 0o622, 0x00E80000)
            mknod(f, 0, sb, "/dev/null", S_IFCHR | 0o666, 0x00040002)
        with open(img, "rb") as f:
            sb = read_superblock(f, 0)
            assert read_dev(f, 0, sb, "/dev/console") == 0x00E80000
            assert read_dev(f, 0, sb, "/dev/null") == 0x00040002
            entries = {e["name"]: e for e in list_dir(f, 0, sb, "/dev")}
            assert entries["console"]["type"] == "c"
            assert entries["null"]["type"] == "c"

    def test_mknod_rejects_regular_mode(self, tmp_path):
        from pyirix.xfs.operations import mknod
        from pyirix.xfs.constants import XFSError
        img = self._fresh(tmp_path)
        with open(img, "r+b") as f:
            sb = read_superblock(f, 0)
            with pytest.raises(XFSError):
                mknod(f, 0, sb, "/x", 0o100644, 0)   # not a device mode


class TestXFSIrixCompat:
    """Regression locks for the four issues IRIX's own xfs_check flagged
    (and we fixed) — see the 2026-06-18 real-test campaign.

    All checked structurally on the host; the authoritative proof was a clean
    `xfs_check -f` plus xfs_db reads inside real IRIX 6.5 under QEMU.
    """

    def _raw(self):
        from pyirix.xfs.mkfs import make_xfs_image
        return make_xfs_image(size_mb=2, with_volume_header=False)

    def test_default_versionnum_is_xfscheck_clean(self):
        # v4, DIRV2 clear (V1 dirs), and NO unaligned ALIGN bit (0x80).
        import io
        sb = read_superblock(io.BytesIO(self._raw()), 0)
        assert sb["sb_versionnum"] == 0x0004
        assert not (sb["sb_versionnum"] & 0x2000)   # DIRV2 clear
        assert not (sb["sb_versionnum"] & 0x0080)   # no ALIGN without alignment
        assert not (sb["sb_versionnum"] & 0x0020)   # no NLINK without v2 inodes

    def test_all_chunk_inodes_have_magic(self):
        # IRIX requires every inode in an allocated chunk to carry di_magic,
        # even free ones (xfs_check: "bad magic number 0 for inode N").
        import io, struct
        data = self._raw()
        sb = read_superblock(io.BytesIO(data), 0)
        base = 4 * sb["sb_blocksize"]              # inode chunk at block 4
        isz = sb["sb_inodesize"]
        for i in range(64):
            magic = struct.unpack(">H", data[base + i * isz:base + i * isz + 2])[0]
            assert magic == 0x494E, f"inode slot {i} missing di_magic"

    def test_v1_inode_onlink_set(self):
        # V1 inodes carry the link count in di_onlink (offset 6), mirrored to
        # di_nlink. mkfs must set both (xfs_check: "link count mismatch").
        import io, struct
        from pyirix.xfs.ondisk import ino_to_offset
        data = self._raw()
        sb = read_superblock(io.BytesIO(data), 0)
        off = ino_to_offset(sb, sb["sb_rootino"], 0)
        assert data[off + 4] == 1                  # di_version 1
        onlink = struct.unpack(">H", data[off + 6:off + 8])[0]
        nlink = struct.unpack(">I", data[off + 16:off + 20])[0]
        assert onlink == nlink == 2

    def test_mkdir_syncs_parent_onlink(self, tmp_path):
        # pyirix mkdir must bump BOTH di_nlink and di_onlink on the parent.
        import struct
        from pyirix.xfs.mkfs import mkfs_xfs
        from pyirix.xfs.ondisk import ino_to_offset
        img = str(tmp_path / "onlink.img")
        mkfs_xfs(img, size_mb=4, with_volume_header=False)
        with open(img, "r+b") as f:
            sb = read_superblock(f, 0)
            mkdir(f, 0, sb, "/sub")
        with open(img, "rb") as f:
            sb = read_superblock(f, 0)
            off = ino_to_offset(sb, sb["sb_rootino"], 0)
            f.seek(off)
            core = f.read(20)
        onlink = struct.unpack(">H", core[6:8])[0]
        nlink = struct.unpack(">I", core[16:20])[0]
        assert onlink == nlink == 3, (onlink, nlink)   # 2 + one subdir


class TestXFSCli:
    """End-to-end CLI: mkfs -> repair, via `python3 -m pyirix.xfs`."""

    def _run(self, *argv):
        import subprocess, sys, os
        env = dict(os.environ)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run([sys.executable, "-m", "pyirix.xfs", *argv],
                              capture_output=True, text=True, env=env, cwd=root)

    def test_mkfs_then_repair_fix(self, tmp_path):
        import struct
        img = str(tmp_path / "cli.img")
        assert self._run("mkfs", img, "--size-mb", "16").returncode == 0
        # healthy
        assert self._run("repair", img).returncode == 0
        # corrupt version bits, dry-run reports failure, --fix repairs
        with open(img, "r+b") as f:
            f.seek(XFS_PART + 0x64)
            v = struct.unpack(">H", f.read(2))[0]
            f.seek(XFS_PART + 0x64)
            f.write(struct.pack(">H", v | 0x4000))
        assert self._run("repair", img).returncode == 1          # detects
        fixed = self._run("repair", img, "--fix")
        assert fixed.returncode == 0 and "fixed" in fixed.stdout
        assert self._run("repair", img).returncode == 0          # stays fixed
