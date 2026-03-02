#!/usr/bin/env python3
"""
Unit tests for tar2efs.py - Tar to SGI EFS converter

Run with:
    python3 -m pytest test_tar2efs.py -v
    python3 test_tar2efs.py  # standalone
"""

import io
import os
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path

from tar2efs import (
    EFSBuilder,
    EFSDirEntry,
    EFSExtent,
    EFSGeometry,
    EFSInode,
    EFSSuperblock,
    EFS_BLOCK_SIZE,
    EFS_INODE_SIZE,
    EFS_MAGIC,
    EFS_MAX_EXTENTS,
    EFS_ROOT_INODE,
    S_IFDIR,
    S_IFLNK,
    S_IFREG,
    parse_tar,
    tar2efs,
)


class TestEFSExtent(unittest.TestCase):
    """Tests for EFSExtent packing/unpacking"""

    def test_pack_simple(self):
        """Test packing a simple extent"""
        ext = EFSExtent(bn=100, length=10, offset=0, magic=0)
        packed = ext.pack()

        self.assertEqual(len(packed), 8)

        # Verify packed format
        word1, word2 = struct.unpack('>II', packed)
        self.assertEqual((word1 >> 8) & 0xFFFFFF, 100)  # bn
        self.assertEqual(word1 & 0xFF, 10)              # length
        self.assertEqual((word2 >> 8) & 0xFFFFFF, 0)    # offset
        self.assertEqual(word2 & 0xFF, 0)               # magic

    def test_pack_large_values(self):
        """Test packing with larger block numbers"""
        ext = EFSExtent(bn=0xABCDEF, length=200, offset=0x123456, magic=1)
        packed = ext.pack()

        unpacked = EFSExtent.unpack(packed)
        self.assertEqual(unpacked.bn, 0xABCDEF)
        self.assertEqual(unpacked.length, 200)
        self.assertEqual(unpacked.offset, 0x123456)
        self.assertEqual(unpacked.magic, 1)

    def test_roundtrip(self):
        """Test pack/unpack roundtrip"""
        original = EFSExtent(bn=500, length=50, offset=100, magic=0)
        packed = original.pack()
        unpacked = EFSExtent.unpack(packed)

        self.assertEqual(unpacked.bn, original.bn)
        self.assertEqual(unpacked.length, original.length)
        self.assertEqual(unpacked.offset, original.offset)
        self.assertEqual(unpacked.magic, original.magic)


class TestEFSInode(unittest.TestCase):
    """Tests for EFSInode packing"""

    def test_pack_size(self):
        """Test that packed inode is exactly 128 bytes"""
        inode = EFSInode(
            di_mode=S_IFREG | 0o644,
            di_nlink=1,
            di_uid=0,
            di_gid=0,
            di_size=1024,
            di_atime=1700000000,
            di_mtime=1700000000,
            di_ctime=1700000000,
            di_gen=1,
            di_numextents=1,
            di_version=0,
            di_extents=[EFSExtent(bn=100, length=2, offset=0, magic=0)]
        )
        packed = inode.pack()
        self.assertEqual(len(packed), EFS_INODE_SIZE)

    def test_pack_directory(self):
        """Test packing a directory inode"""
        inode = EFSInode(
            di_mode=S_IFDIR | 0o755,
            di_nlink=2,
            di_uid=0,
            di_gid=0,
            di_size=512,
            di_atime=1700000000,
            di_mtime=1700000000,
            di_ctime=1700000000,
            di_gen=1,
            di_numextents=1,
            di_version=0,
            di_extents=[EFSExtent(bn=50, length=1, offset=0, magic=0)]
        )
        packed = inode.pack()

        # Verify mode is packed correctly (big-endian)
        mode = struct.unpack('>H', packed[0:2])[0]
        self.assertEqual(mode, S_IFDIR | 0o755)

    def test_pack_empty_extents(self):
        """Test packing inode with no extents"""
        inode = EFSInode(
            di_mode=S_IFREG | 0o644,
            di_nlink=1,
            di_size=0,
            di_numextents=0
        )
        packed = inode.pack()
        self.assertEqual(len(packed), EFS_INODE_SIZE)

        # Extent area should be zeros
        extent_area = packed[32:128]
        self.assertEqual(extent_area, b'\x00' * 96)

    def test_pack_max_extents(self):
        """Test packing inode with maximum extents"""
        extents = [EFSExtent(bn=i*10, length=10, offset=i*10, magic=0)
                   for i in range(EFS_MAX_EXTENTS)]

        inode = EFSInode(
            di_mode=S_IFREG | 0o644,
            di_nlink=1,
            di_size=EFS_MAX_EXTENTS * 10 * EFS_BLOCK_SIZE,
            di_numextents=EFS_MAX_EXTENTS,
            di_extents=extents
        )
        packed = inode.pack()
        self.assertEqual(len(packed), EFS_INODE_SIZE)


class TestEFSSuperblock(unittest.TestCase):
    """Tests for EFSSuperblock packing"""

    def test_pack_size(self):
        """Test that packed superblock is exactly 512 bytes"""
        sb = EFSSuperblock(
            fs_size=1000,
            fs_firstcg=10,
            fs_cgfsize=100,
            fs_cgisize=32,
            fs_sectors=16,
            fs_heads=16,
            fs_ncg=5,
            fs_dirty=0,
            fs_time=1700000000,
            fs_magic=EFS_MAGIC,
            fs_fname=b'test',
            fs_fpack=b'pack',
            fs_bmsize=1000,
            fs_tfree=500,
            fs_tinode=100,
            fs_bmblock=2,
            fs_replsb=999,
            fs_lastialloc=50
        )
        packed = sb.pack()
        self.assertEqual(len(packed), EFS_BLOCK_SIZE)

    def test_pack_magic(self):
        """Test that magic number is packed correctly"""
        sb = EFSSuperblock(fs_magic=EFS_MAGIC)
        packed = sb.pack()

        # Magic is at offset 28
        magic = struct.unpack('>i', packed[28:32])[0]
        self.assertEqual(magic, EFS_MAGIC)

    def test_pack_names_truncated(self):
        """Test that long names are truncated to 6 bytes"""
        sb = EFSSuperblock(
            fs_fname=b'verylongname',
            fs_fpack=b'anotherlongname'
        )
        packed = sb.pack()

        # Names are at offsets 32 and 38
        fname = packed[32:38]
        fpack = packed[38:44]

        self.assertEqual(len(fname), 6)
        self.assertEqual(len(fpack), 6)
        self.assertEqual(fname, b'verylo')
        self.assertEqual(fpack, b'anothe')


class TestEFSDirEntry(unittest.TestCase):
    """Tests for EFSDirEntry packing"""

    def test_pack_simple(self):
        """Test packing a simple directory entry"""
        entry = EFSDirEntry(d_ino=10, d_name='test.txt')
        packed = entry.pack()

        # Should be aligned to 4 bytes
        self.assertEqual(len(packed) % 4, 0)

        # Verify structure
        reclen = struct.unpack('>H', packed[0:2])[0]
        namelen = packed[2]
        ino = struct.unpack('>I', packed[4:8])[0]
        name = packed[8:8+namelen].decode('ascii')

        self.assertEqual(reclen, len(packed))
        self.assertEqual(namelen, len('test.txt'))
        self.assertEqual(ino, 10)
        self.assertEqual(name, 'test.txt')

    def test_pack_short_name(self):
        """Test packing entry with short name"""
        entry = EFSDirEntry(d_ino=5, d_name='a')
        packed = entry.pack()

        # 8 bytes header + 1 byte name = 9, padded to 12
        self.assertEqual(len(packed), 12)

    def test_pack_dot_entries(self):
        """Test packing . and .. entries"""
        dot = EFSDirEntry(d_ino=2, d_name='.')
        dotdot = EFSDirEntry(d_ino=2, d_name='..')

        dot_packed = dot.pack()
        dotdot_packed = dotdot.pack()

        # Both should be valid
        self.assertEqual(len(dot_packed) % 4, 0)
        self.assertEqual(len(dotdot_packed) % 4, 0)


class TestEFSBuilder(unittest.TestCase):
    """Tests for EFSBuilder class"""

    def test_init(self):
        """Test builder initialization"""
        builder = EFSBuilder(size_mb=100)
        self.assertEqual(builder.size_mb, 100)
        self.assertEqual(builder.total_blocks, 100 * 1024 * 1024 // EFS_BLOCK_SIZE)

    def test_max_size_capped(self):
        """Test that size is capped at 8192 MB"""
        builder = EFSBuilder(size_mb=10000)
        self.assertEqual(builder.size_mb, 8192)

    def test_calculate_geometry(self):
        """Test geometry calculation"""
        builder = EFSBuilder(size_mb=100)
        geo = builder.calculate_geometry()

        self.assertIsInstance(geo, EFSGeometry)
        self.assertGreater(geo.total_blocks, 0)
        self.assertGreater(geo.num_cgs, 0)
        self.assertGreater(geo.inodes_per_cg, 0)
        self.assertGreater(geo.first_cg, 1)  # After boot + superblock

    def test_add_file(self):
        """Test adding a file"""
        builder = EFSBuilder(size_mb=10)
        builder._init_root()

        builder.add_file(
            path='/test.txt',
            mode=S_IFREG | 0o644,
            uid=0,
            gid=0,
            size=100,
            mtime=1700000000,
            data=b'x' * 100
        )

        self.assertIn('/test.txt', builder.files)
        self.assertEqual(builder.files['/test.txt'].size, 100)

    def test_add_directory(self):
        """Test adding a directory"""
        builder = EFSBuilder(size_mb=10)
        builder._init_root()

        builder.add_directory(
            path='/subdir',
            mode=S_IFDIR | 0o755,
            uid=0,
            gid=0,
            mtime=1700000000
        )

        self.assertIn('/subdir', builder.dirs)

    def test_add_nested_file(self):
        """Test that parent directories are created automatically"""
        builder = EFSBuilder(size_mb=10)
        builder._init_root()

        builder.add_file(
            path='/a/b/c/file.txt',
            mode=S_IFREG | 0o644,
            uid=0,
            gid=0,
            size=10,
            mtime=1700000000,
            data=b'test'
        )

        self.assertIn('/a', builder.dirs)
        self.assertIn('/a/b', builder.dirs)
        self.assertIn('/a/b/c', builder.dirs)
        self.assertIn('/a/b/c/file.txt', builder.files)

    def test_allocate_inodes(self):
        """Test inode allocation"""
        builder = EFSBuilder(size_mb=10)
        builder._init_root()

        builder.add_file('/file1.txt', S_IFREG | 0o644, 0, 0, 10, 0, b'test')
        builder.add_file('/file2.txt', S_IFREG | 0o644, 0, 0, 10, 0, b'test')
        builder.add_directory('/subdir', S_IFDIR | 0o755, 0, 0, 0)

        builder.allocate_inodes()

        # Root should be inode 2
        self.assertEqual(builder.dirs['/'].inode, EFS_ROOT_INODE)

        # All files/dirs should have unique inodes
        inodes = set()
        for d in builder.dirs.values():
            self.assertNotIn(d.inode, inodes)
            inodes.add(d.inode)
        for f in builder.files.values():
            self.assertNotIn(f.inode, inodes)
            inodes.add(f.inode)


class TestParseTar(unittest.TestCase):
    """Tests for tar parsing"""

    def test_parse_simple_tar(self):
        """Test parsing a simple tar file"""
        # Create a tar file in memory
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w') as tf:
            # Add a file
            data = b'Hello, World!'
            info = tarfile.TarInfo(name='hello.txt')
            info.size = len(data)
            info.mode = 0o644
            info.uid = 1000
            info.gid = 1000
            info.mtime = 1700000000
            tf.addfile(info, io.BytesIO(data))

        # Write to temp file and parse
        tar_buffer.seek(0)
        with tempfile.NamedTemporaryFile(suffix='.tar', delete=False) as tmp:
            tmp.write(tar_buffer.read())
            tmp_path = tmp.name

        try:
            files, dirs = parse_tar(tmp_path)

            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].name, 'hello.txt')
            self.assertEqual(files[0].data, b'Hello, World!')
            self.assertEqual(files[0].uid, 1000)
        finally:
            os.unlink(tmp_path)

    def test_parse_tar_with_directories(self):
        """Test parsing tar with directories"""
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w') as tf:
            # Add a directory
            dir_info = tarfile.TarInfo(name='subdir/')
            dir_info.type = tarfile.DIRTYPE
            dir_info.mode = 0o755
            tf.addfile(dir_info)

            # Add a file in the directory
            data = b'content'
            file_info = tarfile.TarInfo(name='subdir/file.txt')
            file_info.size = len(data)
            file_info.mode = 0o644
            tf.addfile(file_info, io.BytesIO(data))

        tar_buffer.seek(0)
        with tempfile.NamedTemporaryFile(suffix='.tar', delete=False) as tmp:
            tmp.write(tar_buffer.read())
            tmp_path = tmp.name

        try:
            files, dirs = parse_tar(tmp_path)

            self.assertEqual(len(dirs), 1)
            self.assertEqual(len(files), 1)
            self.assertEqual(dirs[0].path, '/subdir')
            self.assertEqual(files[0].path, '/subdir/file.txt')
        finally:
            os.unlink(tmp_path)

    def test_parse_tar_with_symlink(self):
        """Test parsing tar with symbolic links"""
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w') as tf:
            # Add a symlink
            link_info = tarfile.TarInfo(name='link')
            link_info.type = tarfile.SYMTYPE
            link_info.linkname = 'target'
            tf.addfile(link_info)

        tar_buffer.seek(0)
        with tempfile.NamedTemporaryFile(suffix='.tar', delete=False) as tmp:
            tmp.write(tar_buffer.read())
            tmp_path = tmp.name

        try:
            files, dirs = parse_tar(tmp_path)

            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].mode & S_IFLNK, S_IFLNK)
            self.assertEqual(files[0].link_target, 'target')
        finally:
            os.unlink(tmp_path)


class TestTar2EFS(unittest.TestCase):
    """Integration tests for tar2efs conversion"""

    def test_convert_simple_tar(self):
        """Test converting a simple tar to EFS"""
        # Create source tar
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w') as tf:
            data = b'Test file content'
            info = tarfile.TarInfo(name='test.txt')
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))

        tar_buffer.seek(0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = Path(tmpdir) / 'test.tar'
            efs_path = Path(tmpdir) / 'test.efs'

            with open(tar_path, 'wb') as f:
                f.write(tar_buffer.read())

            tar2efs(str(tar_path), str(efs_path), size_mb=10)

            # Verify EFS was created
            self.assertTrue(efs_path.exists())
            self.assertGreater(efs_path.stat().st_size, 0)

            # Verify superblock magic
            with open(efs_path, 'rb') as f:
                f.seek(EFS_BLOCK_SIZE + 28)  # Skip boot block, go to magic offset
                magic = struct.unpack('>i', f.read(4))[0]
                self.assertEqual(magic, EFS_MAGIC)

    def test_convert_empty_tar(self):
        """Test converting an empty tar file"""
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w') as tf:
            pass  # Empty tar

        tar_buffer.seek(0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = Path(tmpdir) / 'empty.tar'
            efs_path = Path(tmpdir) / 'empty.efs'

            with open(tar_path, 'wb') as f:
                f.write(tar_buffer.read())

            tar2efs(str(tar_path), str(efs_path), size_mb=10)

            # Should still create a valid EFS with just root directory
            self.assertTrue(efs_path.exists())

    def test_convert_nested_structure(self):
        """Test converting tar with nested directories"""
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w') as tf:
            # Create nested structure
            for path in ['a/', 'a/b/', 'a/b/c/']:
                info = tarfile.TarInfo(name=path)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tf.addfile(info)

            data = b'deep file'
            info = tarfile.TarInfo(name='a/b/c/deep.txt')
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))

        tar_buffer.seek(0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = Path(tmpdir) / 'nested.tar'
            efs_path = Path(tmpdir) / 'nested.efs'

            with open(tar_path, 'wb') as f:
                f.write(tar_buffer.read())

            tar2efs(str(tar_path), str(efs_path), size_mb=10)

            self.assertTrue(efs_path.exists())


class TestEFSImageValidation(unittest.TestCase):
    """Tests that validate the generated EFS image structure"""

    def _create_test_efs(self, files_data: list) -> bytes:
        """Helper to create a test EFS image"""
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w') as tf:
            for name, data in files_data:
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                info.mode = 0o644
                tf.addfile(info, io.BytesIO(data))

        tar_buffer.seek(0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = Path(tmpdir) / 'test.tar'
            efs_path = Path(tmpdir) / 'test.efs'

            with open(tar_path, 'wb') as f:
                f.write(tar_buffer.read())

            tar2efs(str(tar_path), str(efs_path), size_mb=10)

            with open(efs_path, 'rb') as f:
                return f.read()

    def test_superblock_checksum(self):
        """Test that superblock checksum is valid"""
        efs_data = self._create_test_efs([('test.txt', b'hello')])

        # Read superblock (block 1)
        sb_data = efs_data[EFS_BLOCK_SIZE:EFS_BLOCK_SIZE*2]

        # Calculate checksum
        checksum = 0
        for i in range(0, len(sb_data), 4):
            word = struct.unpack('>I', sb_data[i:i+4])[0]
            checksum = (checksum + word) & 0xFFFFFFFF

        # Should sum to zero (including checksum field)
        self.assertEqual(checksum, 0)

    def test_root_inode_exists(self):
        """Test that root inode (2) exists and is a directory"""
        efs_data = self._create_test_efs([('test.txt', b'hello')])

        # Read superblock to find first CG
        sb_data = efs_data[EFS_BLOCK_SIZE:EFS_BLOCK_SIZE*2]
        firstcg = struct.unpack('>i', sb_data[4:8])[0]

        # Read inode 2 (third inode, at block firstcg, offset 2*128)
        inode_offset = firstcg * EFS_BLOCK_SIZE + 2 * EFS_INODE_SIZE
        inode_data = efs_data[inode_offset:inode_offset + EFS_INODE_SIZE]

        mode = struct.unpack('>H', inode_data[0:2])[0]

        # Should be a directory
        self.assertEqual(mode & S_IFDIR, S_IFDIR)

    def test_superblock_copy_matches(self):
        """Test that superblock copy at end matches primary"""
        efs_data = self._create_test_efs([('test.txt', b'hello')])

        # Read primary superblock
        sb_primary = efs_data[EFS_BLOCK_SIZE:EFS_BLOCK_SIZE*2]

        # Get replicated superblock location
        fs_replsb = struct.unpack('>i', sb_primary[60:64])[0]

        # Read copy
        sb_copy = efs_data[fs_replsb * EFS_BLOCK_SIZE:(fs_replsb + 1) * EFS_BLOCK_SIZE]

        self.assertEqual(sb_primary, sb_copy)


if __name__ == '__main__':
    unittest.main(verbosity=2)
