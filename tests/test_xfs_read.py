"""Integration tests: read XFS structures from IRIX disk, compare with sgi_fs.py.

Uses ip54-test qcow2 disk image. Skips if not available.
[CROSS-REF: sgi_mcp/sgi_fs.py xfs_read_superblock, xfs_read_inode, etc.]
"""

import os
import struct
import sys
import pytest

from pyirix.xfs.constants import (
    XFS_SB_MAGIC, XFS_DINODE_MAGIC,
    S_IFMT, S_IFDIR, S_IFREG, S_IFLNK,
    XFS_DINODE_FMT_LOCAL, XFS_DINODE_FMT_EXTENTS, XFS_DINODE_FMT_BTREE,
)

IRIX_DISK = '/workspace/vm_instances/ip54-test/disk.qcow2'
SKIP_REASON = "ip54-test disk image not found"

# ── Shared fixture ─────────────────────────────────────────────────

@pytest.fixture(scope="module")
def irix_disk():
    """Open ip54-test disk. Skip if not available."""
    if not os.path.exists(IRIX_DISK):
        pytest.skip(SKIP_REASON)

    from pyirix.xfs.image import open_disk_image, find_xfs_partition
    from pyirix.xfs.superblock import read_superblock

    with open_disk_image(IRIX_DISK) as f:
        part = find_xfs_partition(f)
        if part is None:
            pytest.skip("No XFS partition in ip54-test disk")
        part_offset, part_size = part
        sb = read_superblock(f, part_offset)
        if sb is None:
            pytest.skip("Cannot read XFS superblock")
        yield f, part_offset, sb


@pytest.fixture(scope="module")
def sgi_fs_disk():
    """Open the same disk with sgi_fs.py for comparison."""
    if not os.path.exists(IRIX_DISK):
        pytest.skip(SKIP_REASON)

    sys.path.insert(0, '/workspace/sgi_mcp')
    import sgi_fs

    from pyirix.xfs.image import open_disk_image, find_xfs_partition

    with open_disk_image(IRIX_DISK) as f:
        part = find_xfs_partition(f)
        if part is None:
            pytest.skip("No XFS partition")
        part_offset, _ = part
        old_sb = sgi_fs.xfs_read_superblock(f, part_offset)
        if old_sb is None:
            pytest.skip("sgi_fs cannot read superblock")
        yield f, part_offset, old_sb, sgi_fs


# ── Superblock ─────────────────────────────────────────────────────

@pytest.mark.skipif(not os.path.exists(IRIX_DISK), reason=SKIP_REASON)
class TestSuperblockRead:
    """Compare pyirix.xfs superblock fields with sgi_fs.py."""

    def test_magic(self, irix_disk):
        _, _, sb = irix_disk
        assert sb['sb_magicnum'] == XFS_SB_MAGIC

    def test_block_size_power_of_two(self, irix_disk):
        _, _, sb = irix_disk
        bs = sb['sb_blocksize']
        assert bs > 0 and (bs & (bs - 1)) == 0

    def test_blocklog_consistent(self, irix_disk):
        _, _, sb = irix_disk
        assert 1 << sb['sb_blocklog'] == sb['sb_blocksize']

    def test_sectlog_consistent(self, irix_disk):
        _, _, sb = irix_disk
        assert 1 << sb['sb_sectlog'] == sb['sb_sectsize']

    def test_inodelog_consistent(self, irix_disk):
        _, _, sb = irix_disk
        assert 1 << sb['sb_inodelog'] == sb['sb_inodesize']

    def test_inopblog_consistent(self, irix_disk):
        _, _, sb = irix_disk
        assert 1 << sb['sb_inopblog'] == sb['sb_inopblock']
        assert sb['sb_blocksize'] // sb['sb_inodesize'] == sb['sb_inopblock']

    def test_agblklog_consistent(self, irix_disk):
        _, _, sb = irix_disk
        assert (1 << sb['sb_agblklog']) >= sb['sb_agblocks']

    def test_compare_with_sgi_fs(self, irix_disk, sgi_fs_disk):
        _, _, sb = irix_disk
        _, _, old_sb, _ = sgi_fs_disk

        # Fields that both implementations parse
        shared_fields = [
            'sb_magicnum', 'sb_blocksize', 'sb_dblocks', 'sb_rootino',
            'sb_agblocks', 'sb_agcount', 'sb_versionnum', 'sb_sectsize',
            'sb_inodesize', 'sb_inopblock', 'sb_fname',
            'sb_blocklog', 'sb_sectlog', 'sb_inodelog', 'sb_inopblog',
            'sb_agblklog', 'sb_icount', 'sb_ifree', 'sb_fdblocks',
            'sb_dirblklog',
        ]
        for field in shared_fields:
            assert sb[field] == old_sb[field], \
                f"{field}: pyirix={sb[field]} vs sgi_fs={old_sb[field]}"


# ── Inode ──────────────────────────────────────────────────────────

@pytest.mark.skipif(not os.path.exists(IRIX_DISK), reason=SKIP_REASON)
class TestInodeRead:
    def test_root_inode(self, irix_disk):
        from pyirix.xfs.inode import read_inode
        f, part_offset, sb = irix_disk
        root = read_inode(f, part_offset, sb, sb['sb_rootino'])
        assert root is not None
        assert root['di_magic'] == XFS_DINODE_MAGIC
        assert (root['di_mode'] & S_IFMT) == S_IFDIR
        assert root['di_nlink'] >= 2

    def test_root_inode_compare_sgi_fs(self, irix_disk, sgi_fs_disk):
        from pyirix.xfs.inode import read_inode
        f, part_offset, sb = irix_disk
        _, _, old_sb, sgi_fs = sgi_fs_disk

        new_root = read_inode(f, part_offset, sb, sb['sb_rootino'])
        old_root = sgi_fs.xfs_read_inode(f, part_offset, old_sb, old_sb['sb_rootino'])

        for field in ['di_magic', 'di_mode', 'di_version', 'di_format',
                       'di_uid', 'di_gid', 'di_nlink', 'di_size']:
            assert new_root[field] == old_root[field], \
                f"Root inode {field}: pyirix={new_root[field]} vs sgi_fs={old_root[field]}"

    def test_regular_file_inode(self, irix_disk):
        """Read /unix — should be a regular file."""
        from pyirix.xfs.inode import read_inode
        from pyirix.xfs.operations import resolve_path
        f, part_offset, sb = irix_disk
        ino = resolve_path(f, part_offset, sb, '/unix')
        if ino is None:
            pytest.skip("/unix not found on this disk")
        inode = read_inode(f, part_offset, sb, ino)
        assert inode is not None
        assert (inode['di_mode'] & S_IFMT) == S_IFREG
        assert inode['di_size'] > 0

    def test_symlink_inode(self, irix_disk):
        """Read /bin — should be a symlink to usr/bin."""
        from pyirix.xfs.inode import read_inode
        from pyirix.xfs.operations import resolve_path
        # /bin is typically a symlink on IRIX
        # Walk manually without following symlinks
        f, part_offset, sb = irix_disk
        root = read_inode(f, part_offset, sb, sb['sb_rootino'])
        from pyirix.xfs.directory import read_dir_entries
        entries = read_dir_entries(f, part_offset, sb, root)
        bin_ino = None
        for name, ino in entries:
            if name == 'bin':
                bin_ino = ino
                break
        if bin_ino is None:
            pytest.skip("/bin not found in root directory")
        inode = read_inode(f, part_offset, sb, bin_ino)
        assert inode is not None
        # /bin could be a symlink or a directory
        ftype = inode['di_mode'] & S_IFMT
        assert ftype in (S_IFLNK, S_IFDIR)


# ── Directory Shortform ────────────────────────────────────────────

@pytest.mark.skipif(not os.path.exists(IRIX_DISK), reason=SKIP_REASON)
class TestDirectoryReadSF:
    """Shortform directories have <= ~10 entries, stored inline."""

    def test_find_sf_directory(self, irix_disk):
        """Find and read a shortform directory (small subdir)."""
        from pyirix.xfs.inode import read_inode
        from pyirix.xfs.directory import read_dir_entries, read_dir_sf
        from pyirix.xfs.operations import resolve_path
        f, part_offset, sb = irix_disk

        # /hw is typically a small shortform directory on IRIX
        for candidate in ['/hw', '/ns', '/debug', '/proc']:
            ino = resolve_path(f, part_offset, sb, candidate)
            if ino is None:
                continue
            inode = read_inode(f, part_offset, sb, ino)
            if inode and inode['di_format'] == XFS_DINODE_FMT_LOCAL:
                entries = read_dir_sf(inode)
                assert isinstance(entries, list)
                # SF entries exclude . and ..
                for name, child_ino in entries:
                    assert isinstance(name, str)
                    assert isinstance(child_ino, int)
                    assert child_ino > 0
                return

        pytest.skip("No shortform directory found in expected paths")


# ── Directory V1 Leaf ──────────────────────────────────────────────

@pytest.mark.skipif(not os.path.exists(IRIX_DISK), reason=SKIP_REASON)
class TestDirectoryReadV1Leaf:
    """V1 leaf directory: root dir on IRIX 6.5.5 with 26+ entries."""

    def test_root_directory_entries(self, irix_disk):
        from pyirix.xfs.inode import read_inode
        from pyirix.xfs.directory import read_dir_entries
        f, part_offset, sb = irix_disk

        root = read_inode(f, part_offset, sb, sb['sb_rootino'])
        entries = read_dir_entries(f, part_offset, sb, root)
        assert len(entries) > 0

        names = [name for name, _ in entries]
        # Standard IRIX root dirs
        for expected in ['etc', 'usr', 'var', 'dev']:
            assert expected in names, f"Expected '{expected}' in root dir"

    def test_root_dir_compare_sgi_fs(self, irix_disk, sgi_fs_disk):
        """Both implementations should return the same root dir entries."""
        from pyirix.xfs.inode import read_inode
        from pyirix.xfs.directory import read_dir_entries
        f, part_offset, sb = irix_disk
        _, _, old_sb, sgi_fs = sgi_fs_disk

        root = read_inode(f, part_offset, sb, sb['sb_rootino'])
        new_entries = read_dir_entries(f, part_offset, sb, root)

        old_root = sgi_fs.xfs_read_inode(f, part_offset, old_sb, old_sb['sb_rootino'])
        old_entries = sgi_fs.xfs_read_dir_entries(f, part_offset, old_sb, old_root)

        new_set = {(n, i) for n, i in new_entries}
        old_set = {(n, i) for n, i in old_entries}
        assert new_set == old_set


# ── Path Resolution ───────────────────────────────────────────────

@pytest.mark.skipif(not os.path.exists(IRIX_DISK), reason=SKIP_REASON)
class TestPathResolution:
    def test_root(self, irix_disk):
        from pyirix.xfs.operations import resolve_path
        f, part_offset, sb = irix_disk
        ino = resolve_path(f, part_offset, sb, '/')
        assert ino == sb['sb_rootino']

    def test_etc(self, irix_disk):
        from pyirix.xfs.operations import resolve_path
        f, part_offset, sb = irix_disk
        ino = resolve_path(f, part_offset, sb, '/etc')
        assert ino is not None

    def test_etc_passwd(self, irix_disk):
        from pyirix.xfs.operations import resolve_path
        f, part_offset, sb = irix_disk
        ino = resolve_path(f, part_offset, sb, '/etc/passwd')
        # May or may not exist depending on IRIX install
        # Just test the function doesn't crash
        assert ino is None or isinstance(ino, int)

    def test_unix(self, irix_disk):
        from pyirix.xfs.operations import resolve_path
        f, part_offset, sb = irix_disk
        ino = resolve_path(f, part_offset, sb, '/unix')
        if ino is not None:
            assert isinstance(ino, int)
            assert ino > 0

    def test_nonexistent(self, irix_disk):
        from pyirix.xfs.operations import resolve_path
        f, part_offset, sb = irix_disk
        ino = resolve_path(f, part_offset, sb, '/this/does/not/exist')
        assert ino is None

    def test_var_sysgen(self, irix_disk):
        from pyirix.xfs.operations import resolve_path
        f, part_offset, sb = irix_disk
        ino = resolve_path(f, part_offset, sb, '/var/sysgen')
        assert ino is None or isinstance(ino, int)

    def test_compare_with_sgi_fs(self, irix_disk, sgi_fs_disk):
        """Both resolve_path implementations should agree."""
        from pyirix.xfs.operations import resolve_path
        f, part_offset, sb = irix_disk
        _, _, old_sb, sgi_fs = sgi_fs_disk

        test_paths = ['/unix', '/etc', '/var', '/usr', '/stand',
                      '/nonexistent', '/etc/passwd']
        for path in test_paths:
            new_ino = resolve_path(f, part_offset, sb, path)
            old_ino = sgi_fs._xfs_resolve_path(f, part_offset, old_sb, path)
            assert new_ino == old_ino, \
                f"Path {path}: pyirix={new_ino} vs sgi_fs={old_ino}"


# ── File Data ──────────────────────────────────────────────────────

@pytest.mark.skipif(not os.path.exists(IRIX_DISK), reason=SKIP_REASON)
class TestFileDataRead:
    def test_read_etc_passwd(self, irix_disk):
        """Read /etc/passwd if it exists — should be text starting with 'root'."""
        from pyirix.xfs.inode import read_inode, read_file_data
        from pyirix.xfs.operations import resolve_path
        f, part_offset, sb = irix_disk

        ino = resolve_path(f, part_offset, sb, '/etc/passwd')
        if ino is None:
            pytest.skip("/etc/passwd not found")
        inode = read_inode(f, part_offset, sb, ino)
        data = read_file_data(f, part_offset, sb, inode)
        assert len(data) > 0
        text = data.decode('ascii', errors='replace')
        assert 'root' in text


# ── Symlink ────────────────────────────────────────────────────────

@pytest.mark.skipif(not os.path.exists(IRIX_DISK), reason=SKIP_REASON)
class TestSymlinkRead:
    def test_read_symlink(self, irix_disk):
        """Find a symlink in root dir and read its target."""
        from pyirix.xfs.inode import read_inode, read_symlink
        from pyirix.xfs.directory import read_dir_entries
        f, part_offset, sb = irix_disk

        root = read_inode(f, part_offset, sb, sb['sb_rootino'])
        entries = read_dir_entries(f, part_offset, sb, root)

        for name, child_ino in entries:
            inode = read_inode(f, part_offset, sb, child_ino)
            if inode and (inode['di_mode'] & S_IFMT) == S_IFLNK:
                target = read_symlink(f, part_offset, sb, inode)
                assert isinstance(target, str)
                assert len(target) > 0
                return

        pytest.skip("No symlinks found in root directory")


# ── Extent B+tree ─────────────────────────────────────────────────

@pytest.mark.skipif(not os.path.exists(IRIX_DISK), reason=SKIP_REASON)
class TestExtentBtree:
    """Read a large file with B+tree extents (e.g. /unix ~6MB)."""

    def test_btree_file_read(self, irix_disk):
        from pyirix.xfs.inode import read_inode, read_file_data, get_extents
        from pyirix.xfs.operations import resolve_path
        f, part_offset, sb = irix_disk

        ino = resolve_path(f, part_offset, sb, '/unix')
        if ino is None:
            pytest.skip("/unix not found")
        inode = read_inode(f, part_offset, sb, ino)
        if inode['di_format'] != XFS_DINODE_FMT_BTREE:
            # /unix might use extents list instead of btree on small installs
            pytest.skip("/unix is not B+tree format")

        extents = get_extents(f, part_offset, sb, inode)
        assert len(extents) > 0

        # Verify extents are sorted by startoff
        for i in range(1, len(extents)):
            assert extents[i][0] >= extents[i-1][0], \
                f"Extents not sorted: {extents[i-1][0]} >= {extents[i][0]}"

        # Read data and verify it's an ELF binary
        data = read_file_data(f, part_offset, sb, inode)
        assert len(data) == inode['di_size']
        assert data[:4] == b'\x7fELF', "Expected ELF magic"
