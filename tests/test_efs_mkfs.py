"""EFS create (mkfs) + read round-trip and repair, against synthetic images.

Fully self-contained: every image is built by pyirix.efs.mkfs_efs, so these
tests always run (no real disk, no external mkfs).
"""

import os
import pytest

from fs_fixtures import (
    build_corpus, KNOWN_EFS_FILES, KNOWN_EFS_SYMLINKS, KNOWN_EFS_DIRS,
)
from pyirix.efs.reader import (
    find_efs_partition, read_superblock, read_inode, read_dir_entries,
    read_file_data, read_symlink_target, extract_recursive, count_files,
    EFS_ROOT_INODE, EFS_MAGIC, EFS_MAGIC_NEW,
)
from pyirix.efs import repair as erep


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    return build_corpus(str(tmp_path_factory.mktemp("efs_corpus")))


def _open(path):
    return open(path, "rb")


def _resolve(f, po, sb, path):
    """Walk an absolute path to an inode dict, following directories."""
    ino = EFS_ROOT_INODE
    inode = read_inode(f, po, sb, ino)
    for comp in [c for c in path.split("/") if c]:
        entries = dict(read_dir_entries(f, po, sb, inode))
        ino = entries[comp]
        inode = read_inode(f, po, sb, ino)
    return inode


# ── Build / round-trip ──────────────────────────────────────────────

class TestEFSRoundTrip:
    def test_partition_and_superblock(self, corpus):
        with _open(corpus["efs_valid"]) as f:
            part = find_efs_partition(f)
            assert part is not None
            po, size = part
            sb = read_superblock(f, po)
            assert sb["fs_magic"] in (EFS_MAGIC, EFS_MAGIC_NEW)

    def test_file_contents(self, corpus):
        with _open(corpus["efs_valid"]) as f:
            po, _ = find_efs_partition(f)
            sb = read_superblock(f, po)
            for path, expected in KNOWN_EFS_FILES.items():
                inode = _resolve(f, po, sb, path)
                assert read_file_data(f, po, sb, inode) == expected

    def test_multi_extent_file(self, corpus):
        # /usr/share/data.bin is several blocks; verifies extent walking.
        with _open(corpus["efs_valid"]) as f:
            po, _ = find_efs_partition(f)
            sb = read_superblock(f, po)
            inode = _resolve(f, po, sb, "/usr/share/data.bin")
            assert inode["numextents"] >= 1
            assert read_file_data(f, po, sb, inode) == KNOWN_EFS_FILES["/usr/share/data.bin"]

    def test_symlink(self, corpus):
        with _open(corpus["efs_valid"]) as f:
            po, _ = find_efs_partition(f)
            sb = read_superblock(f, po)
            for path, target in KNOWN_EFS_SYMLINKS.items():
                inode = _resolve(f, po, sb, path)
                assert read_symlink_target(f, po, sb, inode) == target

    def test_directories_present(self, corpus):
        with _open(corpus["efs_valid"]) as f:
            po, _ = find_efs_partition(f)
            sb = read_superblock(f, po)
            root = read_inode(f, po, sb, EFS_ROOT_INODE)
            names = {n for n, _ in read_dir_entries(f, po, sb, root)}
            assert {"etc", "usr", "empty"} <= names

    def test_counts(self, corpus):
        with _open(corpus["efs_valid"]) as f:
            po, _ = find_efs_partition(f)
            sb = read_superblock(f, po)
            files, dirs, syms, total = count_files(f, po, sb, EFS_ROOT_INODE, "/")
            assert files == len(KNOWN_EFS_FILES)
            assert syms == len(KNOWN_EFS_SYMLINKS)

    def test_recursive_extract(self, corpus, tmp_path):
        out = str(tmp_path / "extract")
        with _open(corpus["efs_valid"]) as f:
            po, _ = find_efs_partition(f)
            sb = read_superblock(f, po)
            stats = extract_recursive(f, po, sb, EFS_ROOT_INODE, "/", out)
        assert stats["errors"] == 0
        with open(os.path.join(out, "usr/share/data.bin"), "rb") as g:
            assert g.read() == KNOWN_EFS_FILES["/usr/share/data.bin"]
        assert os.readlink(os.path.join(out, "etc/rc")) == "/etc/motd"


# ── Diagnostics / repair ────────────────────────────────────────────

class TestEFSCheckRepair:
    def test_healthy_passes(self, corpus):
        with _open(corpus["efs_valid"]) as f:
            rep = erep.check_efs(f)
            assert rep.ok, rep.summary()

    def test_no_partition_fails(self, corpus):
        with _open(corpus["efs_no_partition"]) as f:
            rep = erep.check_efs(f)
            assert not rep.ok
            assert any(x.code == "partition" for x in rep.by_level("FAIL"))

    def test_bad_checksum_detected(self, corpus):
        with _open(corpus["efs_bad_checksum"]) as f:
            ok, stored, computed = erep.verify_checksum(f, find_efs_partition(f)[0])
            assert not ok and stored != computed
            rep = erep.check_efs(f)
            assert any(x.code == "checksum" for x in rep.by_level("FAIL"))

    def test_bad_magic_detected_and_recovered(self, corpus, tmp_path):
        # work on a private copy so we can write the repair
        import shutil
        dst = str(tmp_path / "efs_repair.img")
        shutil.copy(corpus["efs_bad_magic"], dst)
        with open(dst, "rb") as f:
            rep = erep.check_efs(f)
            assert any(x.code == "sb_magic" for x in rep.by_level("FAIL"))
            assert any(x.code == "replica" for x in rep)   # recovery available
        with open(dst, "r+b") as f:
            po = find_efs_partition(f)[0]
            act = erep.recover_superblock(f, po, dry_run=False)
            assert act["changed"]
        with open(dst, "rb") as f:
            assert erep.check_efs(f).ok
