#!/usr/bin/env python3
"""
tar2efs.py - Convert Tar Archives to SGI EFS Disk Images

This tool converts tar archives to SGI EFS filesystem images that can be
mounted as SCSI disks in MAME's Indy emulation. This enables transparent
filesystem access for Software Manager and other IRIX tools.

Usage:
    python3 tar2efs.py input.tar output.efs
    python3 tar2efs.py input.tar output.efs --size 4096
    python3 tar2efs.py corpus.tar --split 8192 --output-dir ./efs-images/

EFS Filesystem Reference:
    - Based on SGI documentation: https://techpubs.jurassic.nl/library/manuals/2000/007-2825-013/sgi_html/apa.html
    - NetBSD implementation: https://ftp.netbsd.org/pub/NetBSD/NetBSD-current/src/sys/fs/efs/
"""

import argparse
import io
import os
import stat
import struct
import sys
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Dict, List, Optional, Tuple


# EFS Constants
EFS_MAGIC = 0x072959          # EFS magic number
EFS_MAGIC_NEW = 0x07295A      # New EFS magic (dirty filesystem)
EFS_BLOCK_SIZE = 512          # Block size in bytes
EFS_INODES_PER_BLOCK = 4      # 512 / 128 = 4 inodes per block
EFS_INODE_SIZE = 128          # Size of inode structure
EFS_ROOT_INODE = 2            # Root directory inode number
EFS_MAX_EXTENTS = 12          # Maximum direct extents per inode
EFS_MAX_EXTENT_LENGTH = 248   # Maximum blocks per extent

# EFS directory block constants (efs_dir.h)
EFS_DIRBLK_MAGIC = 0xBEEF    # Magic number in every directory block header
EFS_DIRBSIZE = 512            # Directory block size (= EFS_BLOCK_SIZE)
EFS_DIRBLK_HEADERSIZE = 4    # header: magic(2) + firstused(1) + slots(1)
EFS_DENTSIZE = 6              # Min entry size: sizeof(efs_dent)-3+1 = 4+1+1


def _efs_dent_size(namelen: int) -> int:
    """Packed size of one EFS directory entry with the given name length.

    Matches IRIX efs_dentsizebynamelen() macro from efs_dir.h:
        EFS_DENTSIZE + (namelen) - 1 + (((namelen) ^ 1) & 1)
    = (5 + namelen), padded up to the next even (2-byte) boundary.
    """
    return (5 + namelen + 1) & ~1


def _efs_pack_dent(ino: int, name: str) -> bytes:
    """Pack one EFS directory entry.

    On-disk layout (efs_dir.h struct efs_dent, big-endian MIPS):
        [0..3]  ud_inum:  inode number as uint32 big-endian
        [4]     d_namelen: name length in bytes
        [5..5+namelen-1]  d_name: name characters (no NUL terminator)
        [padding to even byte boundary]
    There is NO d_reclen field; the record length is computed from d_namelen.
    """
    name_bytes = name.encode('ascii', errors='replace')
    namelen = len(name_bytes)
    size = _efs_dent_size(namelen)
    buf = bytearray(size)
    struct.pack_into('>I', buf, 0, ino)        # ud_inum as big-endian uint32
    buf[4] = namelen                            # d_namelen
    buf[5:5 + namelen] = name_bytes            # d_name[0..namelen-1]
    return bytes(buf)


def _efs_build_dirblk(entries: List[Tuple[int, str]]) -> bytes:
    """Build one 512-byte EFS directory block with the 0xBEEF magic header.

    Block layout (efs_dir.h struct efs_dirblk):
        [0..1]  magic    = 0xBEEF
        [2]     firstused: compacted offset (byte_offset >> 1) of the
                           lowest-address entry in the block
        [3]     slots    : number of entries
        [4..4+slots-1]   slot table: each byte is the compacted offset
                         of one entry within this block
        [free space in the middle]
        [entries packed from the end of the block, growing downward]

    Entries are placed from byte 511 downward; the slot table grows from
    byte 4 upward.  Compacted offset = real_byte_offset >> 1 (entries are
    always 2-byte aligned).
    """
    block = bytearray(512)
    write_pos = 512          # next write position (exclusive end)
    slot_compacted: List[int] = []

    for ino, name in entries:
        entry = _efs_pack_dent(ino, name)
        write_pos -= len(entry)
        block[write_pos:write_pos + len(entry)] = entry
        slot_compacted.append(write_pos >> 1)  # compacted = real / 2

    firstused = min(slot_compacted) if slot_compacted else 0

    struct.pack_into('>H', block, 0, EFS_DIRBLK_MAGIC)
    block[2] = firstused & 0xFF
    block[3] = len(entries) & 0xFF
    for i, comp_off in enumerate(slot_compacted):
        block[4 + i] = comp_off & 0xFF

    return bytes(block)

# File type constants (matches IRIX)
S_IFMT = 0o170000
S_IFDIR = 0o040000
S_IFREG = 0o100000
S_IFLNK = 0o120000
S_IFCHR = 0o020000
S_IFBLK = 0o060000
S_IFIFO = 0o010000


@dataclass
class EFSExtent:
    """EFS extent descriptor - 8 bytes"""
    bn: int = 0        # Starting block number (24 bits)
    length: int = 0    # Length in blocks (8 bits, 1-248)
    offset: int = 0    # Logical offset in file in blocks (24 bits)
    magic: int = 0     # Magic (0=direct, 1+=indirect)

    def pack(self) -> bytes:
        """Pack extent into 8 bytes (big-endian).

        IRIX EFS extent struct (efs_ino.h):
            unsigned int ex_magic:8,   /* bits 31:24 of word 1 */
                         ex_bn:24,     /* bits 23:0  of word 1 */
                         ex_length:8,  /* bits 31:24 of word 2 */
                         ex_offset:24; /* bits 23:0  of word 2 */
        """
        word1 = ((self.magic  & 0xFF)     << 24) | (self.bn     & 0xFFFFFF)
        word2 = ((self.length & 0xFF)     << 24) | (self.offset & 0xFFFFFF)
        return struct.pack('>II', word1, word2)

    @classmethod
    def unpack(cls, data: bytes) -> 'EFSExtent':
        """Unpack extent from 8 bytes"""
        word1, word2 = struct.unpack('>II', data[:8])
        return cls(
            magic =(word1 >> 24) & 0xFF,
            bn    = word1        & 0xFFFFFF,
            length=(word2 >> 24) & 0xFF,
            offset= word2        & 0xFFFFFF,
        )


@dataclass
class EFSInode:
    """EFS inode structure - 128 bytes"""
    di_mode: int = 0          # File type and permissions
    di_nlink: int = 1         # Link count
    di_uid: int = 0           # Owner uid
    di_gid: int = 0           # Owner gid
    di_size: int = 0          # File size in bytes
    di_atime: int = 0         # Access time
    di_mtime: int = 0         # Modification time
    di_ctime: int = 0         # Inode change time
    di_gen: int = 0           # Generation number
    di_numextents: int = 0    # Number of extents
    di_version: int = 0       # Inode version
    di_spare: int = 0         # Reserved
    di_extents: List[EFSExtent] = field(default_factory=list)

    def pack(self) -> bytes:
        """Pack inode into 128 bytes"""
        buf = io.BytesIO()

        # Header (48 bytes)
        buf.write(struct.pack('>H', self.di_mode))
        buf.write(struct.pack('>h', self.di_nlink))
        buf.write(struct.pack('>H', self.di_uid))
        buf.write(struct.pack('>H', self.di_gid))
        buf.write(struct.pack('>i', self.di_size))
        buf.write(struct.pack('>i', self.di_atime))
        buf.write(struct.pack('>i', self.di_mtime))
        buf.write(struct.pack('>i', self.di_ctime))
        buf.write(struct.pack('>I', self.di_gen))
        buf.write(struct.pack('>h', self.di_numextents))
        buf.write(struct.pack('>B', self.di_version))
        buf.write(struct.pack('>B', self.di_spare))

        # Extents (12 * 8 = 96 bytes)
        for i in range(EFS_MAX_EXTENTS):
            if i < len(self.di_extents):
                buf.write(self.di_extents[i].pack())
            else:
                buf.write(b'\x00' * 8)

        # Pad to 128 bytes (should already be 128)
        data = buf.getvalue()
        assert len(data) == EFS_INODE_SIZE, f"Inode size mismatch: {len(data)}"
        return data


@dataclass
class EFSSuperblock:
    """EFS superblock structure"""
    fs_size: int = 0          # Filesystem size in blocks
    fs_firstcg: int = 0       # First cylinder group offset
    fs_cgfsize: int = 0       # Cylinder group size in blocks
    fs_cgisize: int = 0       # Inodes per cylinder group
    fs_sectors: int = 0       # Sectors per track
    fs_heads: int = 0         # Heads per cylinder
    fs_ncg: int = 0           # Number of cylinder groups
    fs_dirty: int = 0         # Dirty flag
    fs_padding: int = 0
    fs_time: int = 0          # Last superblock update
    fs_magic: int = EFS_MAGIC
    fs_fname: bytes = b''     # Filesystem name (6 bytes)
    fs_fpack: bytes = b''     # Filesystem pack name (6 bytes)
    fs_bmsize: int = 0        # Bitmap size in bytes
    fs_tfree: int = 0         # Total free blocks
    fs_tinode: int = 0        # Total free inodes
    fs_bmblock: int = 0       # Bitmap start block
    fs_replsb: int = 0        # Replicated superblock block
    fs_lastialloc: int = 0    # Last allocated inode
    fs_spare: bytes = b''     # Reserved (20 bytes)
    fs_checksum: int = 0      # Superblock checksum

    def pack(self) -> bytes:
        """Pack superblock into 512 bytes"""
        buf = io.BytesIO()

        buf.write(struct.pack('>i', self.fs_size))
        buf.write(struct.pack('>i', self.fs_firstcg))
        buf.write(struct.pack('>i', self.fs_cgfsize))
        buf.write(struct.pack('>h', self.fs_cgisize))
        buf.write(struct.pack('>h', self.fs_sectors))
        buf.write(struct.pack('>h', self.fs_heads))
        buf.write(struct.pack('>h', self.fs_ncg))
        buf.write(struct.pack('>h', self.fs_dirty))
        buf.write(struct.pack('>h', self.fs_padding))
        buf.write(struct.pack('>i', self.fs_time))
        buf.write(struct.pack('>i', self.fs_magic))

        # Filesystem name - pad or truncate to 6 bytes
        fname = (self.fs_fname + b'\x00' * 6)[:6]
        buf.write(fname)

        # Pack name - pad or truncate to 6 bytes
        fpack = (self.fs_fpack + b'\x00' * 6)[:6]
        buf.write(fpack)

        buf.write(struct.pack('>i', self.fs_bmsize))
        buf.write(struct.pack('>i', self.fs_tfree))
        buf.write(struct.pack('>i', self.fs_tinode))
        buf.write(struct.pack('>i', self.fs_bmblock))
        buf.write(struct.pack('>i', self.fs_replsb))
        buf.write(struct.pack('>i', self.fs_lastialloc))

        # Spare - pad to 20 bytes
        spare = (self.fs_spare + b'\x00' * 20)[:20]
        buf.write(spare)

        # Checksum is stored as signed but may overflow - use unsigned for packing
        buf.write(struct.pack('>I', self.fs_checksum & 0xFFFFFFFF))

        # Pad to 512 bytes
        data = buf.getvalue()
        data = data + b'\x00' * (EFS_BLOCK_SIZE - len(data))
        return data


@dataclass
class EFSDirEntry:
    """EFS directory entry"""
    d_ino: int = 0            # Inode number
    d_name: str = ""          # Filename

    def pack(self) -> bytes:
        """Pack directory entry with proper alignment"""
        name_bytes = self.d_name.encode('ascii', errors='replace')
        name_len = len(name_bytes)

        # Entry: 2 bytes reclen, 1 byte namelen, 1 byte unused, 4 bytes inode, name
        entry_len = 8 + name_len
        # Align to 4 bytes
        padded_len = (entry_len + 3) & ~3

        buf = io.BytesIO()
        buf.write(struct.pack('>H', padded_len))     # d_reclen
        buf.write(struct.pack('>B', name_len))       # d_namelen
        buf.write(struct.pack('>B', 0))              # unused
        buf.write(struct.pack('>I', self.d_ino))     # d_ino
        buf.write(name_bytes)                         # d_name

        # Pad to aligned length
        data = buf.getvalue()
        data = data + b'\x00' * (padded_len - len(data))
        return data


@dataclass
class FileEntry:
    """Internal representation of a file to be written to EFS"""
    path: str                   # Full path in filesystem
    name: str                   # Filename only
    mode: int                   # File mode (type + permissions)
    uid: int = 0                # Owner uid
    gid: int = 0                # Owner gid
    size: int = 0               # File size
    mtime: int = 0              # Modification time
    data: bytes = b''           # File content
    link_target: str = ''       # Symlink target
    inode: int = 0              # Assigned inode number
    parent_inode: int = 0       # Parent directory inode


@dataclass
class DirEntry:
    """Internal representation of a directory"""
    path: str                   # Full path
    name: str                   # Directory name
    mode: int                   # Directory mode
    uid: int = 0
    gid: int = 0
    mtime: int = 0
    inode: int = 0              # Assigned inode number
    parent_inode: int = 0       # Parent directory inode
    entries: List[str] = field(default_factory=list)  # Child paths


@dataclass
class EFSGeometry:
    """Filesystem geometry parameters"""
    total_blocks: int = 0       # Total blocks in filesystem
    first_cg: int = 0           # First cylinder group block
    cg_size: int = 0            # Cylinder group size in blocks
    inodes_per_cg: int = 0      # Inodes per cylinder group
    num_cgs: int = 0            # Number of cylinder groups
    bitmap_blocks: int = 0      # Blocks used for bitmap
    bitmap_start: int = 0       # First bitmap block
    sectors: int = 16           # Sectors per track
    heads: int = 16             # Heads per cylinder


class EFSBuilder:
    """Builds an EFS filesystem image from files"""

    def __init__(self, size_mb: int = 8192):
        self.size_mb = min(size_mb, 8192)  # EFS max ~8GB
        self.total_blocks = (self.size_mb * 1024 * 1024) // EFS_BLOCK_SIZE

        self.files: Dict[str, FileEntry] = {}
        self.dirs: Dict[str, DirEntry] = {}
        self.next_inode = EFS_ROOT_INODE + 1
        self.geometry: Optional[EFSGeometry] = None

        # Block allocation
        self.block_bitmap: List[bool] = []
        self.next_data_block = 0

        # Initialize root directory
        self._init_root()

    def add_file(self, path: str, mode: int, uid: int, gid: int,
                 size: int, mtime: int, data: bytes, link_target: str = ''):
        """Add a file to the filesystem"""
        # Normalize path
        path = '/' + path.lstrip('/')

        # Create parent directories
        self._ensure_parent_dirs(path, uid, gid, mtime)

        # Add file entry
        name = os.path.basename(path)
        entry = FileEntry(
            path=path,
            name=name,
            mode=mode,
            uid=uid,
            gid=gid,
            size=size,
            mtime=mtime,
            data=data,
            link_target=link_target
        )
        self.files[path] = entry

        # Add to parent directory
        parent_path = os.path.dirname(path) or '/'
        if parent_path in self.dirs:
            self.dirs[parent_path].entries.append(path)

    def add_directory(self, path: str, mode: int, uid: int, gid: int, mtime: int):
        """Add a directory to the filesystem"""
        path = '/' + path.strip('/')
        if path == '/':
            return  # Root is handled separately

        self._ensure_parent_dirs(path, uid, gid, mtime)

        if path not in self.dirs:
            name = os.path.basename(path)
            entry = DirEntry(
                path=path,
                name=name,
                mode=mode | S_IFDIR,
                uid=uid,
                gid=gid,
                mtime=mtime,
                entries=[]
            )
            self.dirs[path] = entry

            # Add to parent
            parent_path = os.path.dirname(path) or '/'
            if parent_path in self.dirs and path not in self.dirs[parent_path].entries:
                self.dirs[parent_path].entries.append(path)

    def _ensure_parent_dirs(self, path: str, uid: int, gid: int, mtime: int):
        """Ensure all parent directories exist"""
        parts = path.strip('/').split('/')
        current = ''

        for i, part in enumerate(parts[:-1]):
            current = current + '/' + part
            if current not in self.dirs:
                self.add_directory(current, 0o755, uid, gid, mtime)

    def _init_root(self):
        """Initialize root directory"""
        now = int(time.time())
        self.dirs['/'] = DirEntry(
            path='/',
            name='/',
            mode=S_IFDIR | 0o755,
            uid=0,
            gid=0,
            mtime=now,
            inode=EFS_ROOT_INODE,
            parent_inode=EFS_ROOT_INODE,
            entries=[]
        )

    def calculate_geometry(self) -> EFSGeometry:
        """Calculate filesystem geometry.

        The EFS superblock fs_size must equal firstcg + ncg * cgfsize exactly.
        We compute the bitmap size from the actual layout size (not the image
        size), so the superblock is consistent and passes IRIX fsck.
        """
        total_files = len(self.files) + len(self.dirs)

        # Determine number of inodes needed (add some slack)
        needed_inodes = total_files + 100

        # Cylinder group sizing
        # EFS typically uses 32 inodes per CG (8 blocks of inodes)
        inodes_per_cg = 32
        inode_blocks_per_cg = inodes_per_cg // EFS_INODES_PER_BLOCK  # 8

        # Number of cylinder groups needed (driven by inode count)
        num_cgs = (needed_inodes + inodes_per_cg - 1) // inodes_per_cg
        num_cgs = max(num_cgs, 1)

        # CG layout: inode blocks + data blocks.
        # Base cg_data_blocks on actual total file data so all file content fits.
        total_data_bytes = sum(len(fe.data) for fe in self.files.values())
        total_data_blocks = (total_data_bytes + EFS_BLOCK_SIZE - 1) // EFS_BLOCK_SIZE
        # Add 10% slack, spread across CGs, minimum 100 data blocks per CG.
        cg_data_blocks = max(100,
                             (total_data_blocks + num_cgs - 1) // num_cgs + 50)
        cg_size = inode_blocks_per_cg + cg_data_blocks

        # Compute bitmap from the actual layout size (iterative convergence).
        # The bitmap must cover exactly fs_size = firstcg + ncg * cgfsize blocks.
        # Start with a small estimate and refine once.
        for _ in range(3):
            # Estimate actual fs size: firstcg + layout + 1 for replica sb
            approx_fs = 5 + num_cgs * cg_size + 1
            bitmap_bytes = (approx_fs + 7) // 8
            bitmap_blocks = (bitmap_bytes + EFS_BLOCK_SIZE - 1) // EFS_BLOCK_SIZE
            first_cg = 2 + bitmap_blocks  # boot block + superblock + bitmap

        # Actual filesystem size (must equal firstcg + ncg * cgfsize for fsck)
        actual_fs_blocks = first_cg + num_cgs * cg_size

        # Tighten bitmap to cover actual_fs_blocks
        bitmap_bytes = (actual_fs_blocks + 7) // 8
        bitmap_blocks = (bitmap_bytes + EFS_BLOCK_SIZE - 1) // EFS_BLOCK_SIZE
        first_cg = 2 + bitmap_blocks
        actual_fs_blocks = first_cg + num_cgs * cg_size

        # Ensure fs fits in the image (shrink cg_size if necessary)
        if actual_fs_blocks > self.total_blocks:
            available = self.total_blocks - first_cg
            cg_size = available // num_cgs
            cg_data_blocks = cg_size - inode_blocks_per_cg
            actual_fs_blocks = first_cg + num_cgs * cg_size

        self.geometry = EFSGeometry(
            # total_blocks is the ACTUAL filesystem size (firstcg + ncg*cgfsize),
            # NOT the disk image size. The image may be larger; extra blocks are
            # unused padding.  IRIX fsck requires fs_size == firstcg + ncg*cgfsize.
            total_blocks=actual_fs_blocks,
            first_cg=first_cg,
            cg_size=cg_size,
            inodes_per_cg=inodes_per_cg,
            num_cgs=num_cgs,
            bitmap_blocks=bitmap_blocks,
            bitmap_start=2,
            sectors=16,
            heads=16
        )

        return self.geometry

    def allocate_inodes(self):
        """Assign inode numbers to all files and directories"""
        # Root already has inode 2
        self.dirs['/'].inode = EFS_ROOT_INODE

        # Assign inodes to directories first
        for path in sorted(self.dirs.keys()):
            d = self.dirs[path]
            if d.inode == 0:
                d.inode = self.next_inode
                self.next_inode += 1

            # Set parent inode
            parent_path = os.path.dirname(path) or '/'
            if parent_path in self.dirs:
                d.parent_inode = self.dirs[parent_path].inode
            else:
                d.parent_inode = EFS_ROOT_INODE

        # Assign inodes to files
        for path in sorted(self.files.keys()):
            f = self.files[path]
            f.inode = self.next_inode
            self.next_inode += 1

            # Set parent inode
            parent_path = os.path.dirname(path) or '/'
            if parent_path in self.dirs:
                f.parent_inode = self.dirs[parent_path].inode
            else:
                f.parent_inode = EFS_ROOT_INODE

    def _allocate_blocks(self, num_blocks: int) -> List[EFSExtent]:
        """Allocate data blocks, skipping each CG's inode area.

        Each cylinder group starts with `inodes_per_cg / EFS_INODES_PER_BLOCK`
        inode blocks followed by data blocks.  Allocations must not overlap the
        inode area of any CG; crossing a CG boundary yields a new extent.
        """
        if num_blocks == 0:
            return []

        geo = self.geometry
        inode_blks = geo.inodes_per_cg // EFS_INODES_PER_BLOCK  # 8

        extents: List[EFSExtent] = []
        remain = num_blocks
        logical_off = 0

        while remain > 0:
            pos = self.next_data_block

            # Skip past any inode blocks at the current position.
            if pos >= geo.first_cg:
                rel = pos - geo.first_cg
                cg_idx = rel // geo.cg_size
                cg_off = rel % geo.cg_size
                if cg_off < inode_blks:
                    pos = geo.first_cg + cg_idx * geo.cg_size + inode_blks
                    self.next_data_block = pos

            if pos >= geo.total_blocks:
                break   # filesystem full

            # How many blocks until the end of this CG's data area?
            if pos >= geo.first_cg:
                rel = pos - geo.first_cg
                cg_idx = rel // geo.cg_size
                cg_data_end = geo.first_cg + (cg_idx + 1) * geo.cg_size
            else:
                cg_data_end = geo.total_blocks

            avail = min(cg_data_end - pos, remain,
                        EFS_MAX_EXTENT_LENGTH, geo.total_blocks - pos)
            if avail <= 0:
                # Advance to the next CG (inode-skip happens at loop top).
                self.next_data_block = cg_data_end
                continue

            extent = EFSExtent(bn=pos, length=avail,
                               offset=logical_off, magic=0)
            extents.append(extent)

            for i in range(avail):
                if pos + i < len(self.block_bitmap):
                    self.block_bitmap[pos + i] = True

            self.next_data_block = pos + avail
            remain -= avail
            logical_off += avail

        return extents

    def build(self, output_path: str):
        """Build the EFS filesystem image"""
        if not self.files and len(self.dirs) == 1:
            print("Warning: Empty filesystem (only root directory)")

        # Calculate geometry
        geo = self.calculate_geometry()
        print(f"Filesystem geometry:")
        print(f"  Total blocks: {geo.total_blocks}")
        print(f"  Cylinder groups: {geo.num_cgs}")
        print(f"  CG size: {geo.cg_size} blocks")
        print(f"  Inodes per CG: {geo.inodes_per_cg}")
        print(f"  Total inodes: {geo.num_cgs * geo.inodes_per_cg}")

        # Allocate inodes
        self.allocate_inodes()
        print(f"  Used inodes: {self.next_inode - 1}")

        # Initialize block bitmap
        self.block_bitmap = [False] * geo.total_blocks

        # Mark reserved blocks as used (boot, superblock, bitmap, CG inode blocks)
        for i in range(geo.first_cg):
            self.block_bitmap[i] = True

        # Mark CG inode blocks as used
        inode_blocks_per_cg = geo.inodes_per_cg // EFS_INODES_PER_BLOCK
        for cg in range(geo.num_cgs):
            cg_start = geo.first_cg + cg * geo.cg_size
            for i in range(inode_blocks_per_cg):
                if cg_start + i < len(self.block_bitmap):
                    self.block_bitmap[cg_start + i] = True

        # Data blocks start after inode blocks in first CG
        self.next_data_block = geo.first_cg + inode_blocks_per_cg

        # Create the image file
        with open(output_path, 'wb') as f:
            self._write_image(f, geo)

        print(f"\nCreated EFS image: {output_path}")
        print(f"  Size: {os.path.getsize(output_path)} bytes")

    def _write_image(self, f: BinaryIO, geo: EFSGeometry):
        """Write the complete EFS image"""
        # Block 0: Boot block (unused)
        f.write(b'\x00' * EFS_BLOCK_SIZE)

        # Block 1: Superblock (placeholder, will rewrite at end)
        sb_offset = f.tell()
        f.write(b'\x00' * EFS_BLOCK_SIZE)

        # Blocks 2-N: Bitmap (placeholder)
        bitmap_offset = f.tell()
        for _ in range(geo.bitmap_blocks):
            f.write(b'\x00' * EFS_BLOCK_SIZE)

        # Prepare inode table (all inodes)
        inodes: Dict[int, EFSInode] = {}

        # Precompute direct subdirectory counts for correct nlink values.
        # di_nlink for a directory = 2 + number_of_direct_subdirectories:
        #   1 for the named entry in the parent directory
        #   1 for the '.' entry within the directory itself
        #   +1 for each subdirectory's '..' entry pointing back here
        subdir_counts: Dict[str, int] = {path: 0 for path in self.dirs}
        for path in self.dirs:
            parent = str(PurePosixPath(path).parent)
            if parent in subdir_counts:
                subdir_counts[parent] += 1

        # Create inodes for directories
        for path, d in self.dirs.items():
            inode = EFSInode(
                di_mode=d.mode,
                di_nlink=2 + subdir_counts.get(path, 0),  # . + parent + subdirs
                di_uid=d.uid,
                di_gid=d.gid,
                di_size=0,  # Will be updated
                di_atime=d.mtime,
                di_mtime=d.mtime,
                di_ctime=d.mtime,
                di_gen=1,
                di_numextents=0,
                di_version=0
            )
            inodes[d.inode] = inode

        # Create inodes for files
        for path, fe in self.files.items():
            inode = EFSInode(
                di_mode=fe.mode,
                di_nlink=1,
                di_uid=fe.uid,
                di_gid=fe.gid,
                di_size=fe.size,
                di_atime=fe.mtime,
                di_mtime=fe.mtime,
                di_ctime=fe.mtime,
                di_gen=1,
                di_numextents=0,
                di_version=0
            )
            inodes[fe.inode] = inode

        # Write cylinder groups (inodes only first pass)
        inode_blocks_per_cg = geo.inodes_per_cg // EFS_INODES_PER_BLOCK

        for cg in range(geo.num_cgs):
            cg_start = geo.first_cg + cg * geo.cg_size
            # Seek to CG start
            f.seek(cg_start * EFS_BLOCK_SIZE)

            # Write inode blocks for this CG.
            # IRIX locates inode N at position (N - EFS_ROOT_INODE) within
            # the global inode area, so inode_base must include that offset.
            for block_idx in range(inode_blocks_per_cg):
                inode_base = (EFS_ROOT_INODE
                              + cg * geo.inodes_per_cg
                              + block_idx * EFS_INODES_PER_BLOCK)

                for slot in range(EFS_INODES_PER_BLOCK):
                    inode_num = inode_base + slot
                    if inode_num in inodes:
                        f.write(inodes[inode_num].pack())
                    else:
                        f.write(b'\x00' * EFS_INODE_SIZE)

        # Now allocate data blocks and write file/directory data
        # Process directories - write their directory entries
        for path, d in sorted(self.dirs.items()):
            dir_data = self._build_directory_data(d)
            if dir_data:
                blocks_needed = (len(dir_data) + EFS_BLOCK_SIZE - 1) // EFS_BLOCK_SIZE
                extents = self._allocate_blocks(blocks_needed)

                # Write directory data
                for ext in extents:
                    f.seek(ext.bn * EFS_BLOCK_SIZE)
                    offset = ext.offset * EFS_BLOCK_SIZE
                    chunk = dir_data[offset:offset + ext.length * EFS_BLOCK_SIZE]
                    # Pad to full extent size
                    chunk = chunk + b'\x00' * (ext.length * EFS_BLOCK_SIZE - len(chunk))
                    f.write(chunk)

                # Update inode
                inodes[d.inode].di_size = len(dir_data)
                inodes[d.inode].di_numextents = len(extents)
                inodes[d.inode].di_extents = extents

        # Process files - write their data
        for path, fe in sorted(self.files.items()):
            if fe.mode & S_IFMT == S_IFLNK:
                # Symlink - store target in data
                link_data = fe.link_target.encode('ascii', errors='replace')
                if len(link_data) > 0:
                    blocks_needed = (len(link_data) + EFS_BLOCK_SIZE - 1) // EFS_BLOCK_SIZE
                    extents = self._allocate_blocks(blocks_needed)

                    for ext in extents:
                        f.seek(ext.bn * EFS_BLOCK_SIZE)
                        offset = ext.offset * EFS_BLOCK_SIZE
                        chunk = link_data[offset:offset + ext.length * EFS_BLOCK_SIZE]
                        chunk = chunk + b'\x00' * (ext.length * EFS_BLOCK_SIZE - len(chunk))
                        f.write(chunk)

                    inodes[fe.inode].di_size = len(link_data)
                    inodes[fe.inode].di_numextents = len(extents)
                    inodes[fe.inode].di_extents = extents
            elif fe.data:
                # Regular file with data
                blocks_needed = (len(fe.data) + EFS_BLOCK_SIZE - 1) // EFS_BLOCK_SIZE
                extents = self._allocate_blocks(blocks_needed)

                for ext in extents:
                    f.seek(ext.bn * EFS_BLOCK_SIZE)
                    offset = ext.offset * EFS_BLOCK_SIZE
                    chunk = fe.data[offset:offset + ext.length * EFS_BLOCK_SIZE]
                    chunk = chunk + b'\x00' * (ext.length * EFS_BLOCK_SIZE - len(chunk))
                    f.write(chunk)

                inodes[fe.inode].di_numextents = len(extents)
                inodes[fe.inode].di_extents = extents

        # Rewrite inodes with updated extents
        for cg in range(geo.num_cgs):
            cg_start = geo.first_cg + cg * geo.cg_size
            f.seek(cg_start * EFS_BLOCK_SIZE)

            for block_idx in range(inode_blocks_per_cg):
                inode_base = cg * geo.inodes_per_cg + block_idx * EFS_INODES_PER_BLOCK

                for slot in range(EFS_INODES_PER_BLOCK):
                    inode_num = inode_base + slot
                    if inode_num in inodes:
                        f.write(inodes[inode_num].pack())
                    else:
                        f.write(b'\x00' * EFS_INODE_SIZE)

        # Write bitmap
        f.seek(bitmap_offset)
        bitmap_data = self._build_bitmap()
        f.write(bitmap_data)

        # Calculate free blocks and inodes
        free_blocks = sum(1 for used in self.block_bitmap if not used)
        total_inodes = geo.num_cgs * geo.inodes_per_cg
        free_inodes = total_inodes - len(inodes)

        # Write superblock
        now = int(time.time())
        sb = EFSSuperblock(
            fs_size=geo.total_blocks,
            fs_firstcg=geo.first_cg,
            fs_cgfsize=geo.cg_size,
            # fs_cgisize stores inode BLOCKS per CG (not inodes).
            # IRIX reads fs_cgisize as the inode-block count and multiplies
            # by EFS_INOPBB (4) to get inodes-per-CG.  We have
            # inodes_per_cg=32 inodes → 8 blocks.
            fs_cgisize=inode_blocks_per_cg,
            fs_sectors=geo.sectors,
            fs_heads=geo.heads,
            fs_ncg=geo.num_cgs,
            fs_dirty=0,
            fs_time=now,
            fs_magic=EFS_MAGIC,
            fs_fname=b'efs',
            fs_fpack=b'pack',
            fs_bmsize=(geo.total_blocks + 7) // 8,
            fs_tfree=free_blocks,
            fs_tinode=free_inodes,
            # fs_bmblock must be 0 for non-grown EFS (mkfs.c line 598:
            # "to force 3.2 defaults"). Non-zero signals a grown filesystem
            # with bitmap relocated to end; fsck rejects it as "overlaps
            # cylinder group space" when the old bitmap position is checked.
            fs_bmblock=0,
            fs_replsb=geo.total_blocks - 1,
            fs_lastialloc=self.next_inode - 1
        )

        # Calculate checksum
        sb_data = sb.pack()
        checksum = self._calculate_sb_checksum(sb_data)
        sb.fs_checksum = checksum

        f.seek(sb_offset)
        f.write(sb.pack())

        # Write superblock copy at end
        f.seek((geo.total_blocks - 1) * EFS_BLOCK_SIZE)
        f.write(sb.pack())

        # Pad file to full size
        f.seek(geo.total_blocks * EFS_BLOCK_SIZE - 1)
        f.write(b'\x00')

    def _build_directory_data(self, d: DirEntry) -> bytes:
        """Build directory data as one or more 512-byte EFS directory blocks.

        Each block uses the IRIX efs_dirblk format (magic 0xBEEF, slot table,
        entries packed from the end).  di_size = len(result), which is always
        a multiple of 512 per the IRIX kernel requirement (i_size & EFS_DIRBMASK).
        """
        # Collect all entries: . and .. first, then sorted children.
        all_entries: List[Tuple[int, str]] = [
            (d.inode, '.'),
            (d.parent_inode, '..'),
        ]
        for child_path in sorted(d.entries):
            if child_path in self.dirs:
                child = self.dirs[child_path]
                all_entries.append((child.inode, child.name))
            elif child_path in self.files:
                child = self.files[child_path]
                all_entries.append((child.inode, child.name))

        # Fit entries into 512-byte blocks.
        # Each block: EFS_DIRBLK_HEADERSIZE bytes of header +
        #             slots bytes (one per entry) + entry bytes.
        blocks: List[bytes] = []
        i = 0
        while i < len(all_entries):
            block_entries: List[Tuple[int, str]] = []
            used = EFS_DIRBLK_HEADERSIZE   # 4 bytes for magic+firstused+slots
            j = i
            while j < len(all_entries):
                ino, name = all_entries[j]
                name_enc = name.encode('ascii', errors='replace')
                entry_size = _efs_dent_size(len(name_enc))
                # Adding this entry costs 1 slot byte + entry_size data bytes.
                if used + 1 + entry_size > EFS_DIRBSIZE:
                    break
                block_entries.append((ino, name))
                used += 1 + entry_size
                j += 1
            blocks.append(_efs_build_dirblk(block_entries))
            i = j

        if not blocks:
            # Degenerate empty directory — should not happen, but be safe.
            blocks.append(_efs_build_dirblk([(d.inode, '.'),
                                             (d.parent_inode, '..')]))

        return b''.join(blocks)

    def _build_bitmap(self) -> bytes:
        """Build block allocation bitmap"""
        num_bytes = (len(self.block_bitmap) + 7) // 8
        bitmap = bytearray(num_bytes)

        for i, used in enumerate(self.block_bitmap):
            if used:
                byte_idx = i // 8
                bit_idx = i % 8
                bitmap[byte_idx] |= (1 << (7 - bit_idx))

        # Pad to block boundary
        padded_size = ((len(bitmap) + EFS_BLOCK_SIZE - 1) // EFS_BLOCK_SIZE) * EFS_BLOCK_SIZE
        bitmap = bitmap + b'\x00' * (padded_size - len(bitmap))

        return bytes(bitmap)

    def _calculate_sb_checksum(self, sb_data: bytes) -> int:
        """Calculate EFS superblock checksum matching IRIX efs_checksum().

        IRIX uses a rotating XOR of all 16-bit big-endian words from offset 0
        up to (but not including) the fs_checksum field at offset 88.
        From efs_vfsops.c efs_checksum():
            checksum ^= *sp++;
            checksum = (checksum << 1) | (checksum < 0);  // rotate left 1
        """
        checksum = 0
        for i in range(0, 88, 2):  # fs_checksum is at offset 88
            word = struct.unpack('>H', sb_data[i:i+2])[0]
            checksum ^= word
            # Rotate left by 1 bit (32-bit signed: old MSB -> bit 0)
            msb = (checksum >> 31) & 1
            checksum = ((checksum << 1) | msb) & 0xFFFFFFFF
        # Return as signed int (IRIX stores __int32_t)
        if checksum >= 0x80000000:
            checksum -= 0x100000000
        return checksum


def parse_tar(tar_path: str) -> Tuple[List[FileEntry], List[DirEntry]]:
    """Parse a tar file and extract all entries"""
    files = []
    dirs = []

    with tarfile.open(tar_path, 'r:*') as tf:
        for member in tf.getmembers():
            # Skip . and ..
            if member.name in ('.', '..') or member.name.endswith('/.') or member.name.endswith('/..'):
                continue

            path = '/' + member.name.lstrip('./')

            if member.isdir():
                dirs.append(DirEntry(
                    path=path,
                    name=os.path.basename(path.rstrip('/')),
                    mode=S_IFDIR | (member.mode & 0o7777),
                    uid=int(member.uid),
                    gid=int(member.gid),
                    mtime=int(member.mtime)
                ))
            elif member.isfile():
                # Extract file data
                file_obj = tf.extractfile(member)
                data = file_obj.read() if file_obj else b''

                files.append(FileEntry(
                    path=path,
                    name=os.path.basename(path),
                    mode=S_IFREG | (member.mode & 0o7777),
                    uid=int(member.uid),
                    gid=int(member.gid),
                    size=int(member.size),
                    mtime=int(member.mtime),
                    data=data
                ))
            elif member.issym():
                files.append(FileEntry(
                    path=path,
                    name=os.path.basename(path),
                    mode=S_IFLNK | 0o777,
                    uid=int(member.uid),
                    gid=int(member.gid),
                    size=len(member.linkname),
                    mtime=int(member.mtime),
                    data=b'',
                    link_target=member.linkname
                ))
            elif member.islnk():
                # Hard link - treat as regular file
                # Find the target and copy its data
                target_path = '/' + member.linkname.lstrip('./')
                # We'll need to resolve this later
                files.append(FileEntry(
                    path=path,
                    name=os.path.basename(path),
                    mode=S_IFREG | (member.mode & 0o7777),
                    uid=int(member.uid),
                    gid=int(member.gid),
                    size=0,
                    mtime=int(member.mtime),
                    data=b'',
                    link_target=target_path  # Store for later resolution
                ))
            elif member.ischr() or member.isblk():
                # Device files - create as special files
                mode = S_IFCHR if member.ischr() else S_IFBLK
                files.append(FileEntry(
                    path=path,
                    name=os.path.basename(path),
                    mode=mode | (member.mode & 0o7777),
                    uid=int(member.uid),
                    gid=int(member.gid),
                    size=0,
                    mtime=int(member.mtime),
                    data=b''
                ))
            elif member.isfifo():
                files.append(FileEntry(
                    path=path,
                    name=os.path.basename(path),
                    mode=S_IFIFO | (member.mode & 0o7777),
                    uid=int(member.uid),
                    gid=int(member.gid),
                    size=0,
                    mtime=int(member.mtime),
                    data=b''
                ))

    return files, dirs


def tar2efs(tar_path: str, efs_path: str, size_mb: int = 8192):
    """Convert a tar archive to an EFS filesystem image"""
    print(f"Converting: {tar_path}")
    print(f"Output: {efs_path}")
    print(f"Target size: {size_mb} MB")
    print()

    # Parse tar file
    print("Parsing tar archive...")
    files, dirs = parse_tar(tar_path)
    print(f"  Found {len(files)} files, {len(dirs)} directories")

    # Calculate total size needed
    total_data = sum(len(f.data) for f in files)
    print(f"  Total data size: {total_data / (1024*1024):.2f} MB")

    # Check if we need to split
    if total_data > size_mb * 1024 * 1024:
        print(f"\nWarning: Data size exceeds target image size.")
        print(f"  Consider using --split option for large archives.")

    # Build EFS image
    print("\nBuilding EFS filesystem...")
    builder = EFSBuilder(size_mb)

    # Add directories first
    for d in dirs:
        builder.add_directory(d.path, d.mode, d.uid, d.gid, d.mtime)

    # Add files
    for f in files:
        builder.add_file(f.path, f.mode, f.uid, f.gid,
                        f.size, f.mtime, f.data, f.link_target)

    # Build the image
    builder.build(efs_path)


def split_tar2efs(tar_path: str, output_dir: str, size_mb: int = 8192):
    """Split a large tar archive into multiple EFS images"""
    print(f"Converting with split: {tar_path}")
    print(f"Output directory: {output_dir}")
    print(f"Max size per image: {size_mb} MB")
    print()

    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Parse tar file
    print("Parsing tar archive...")
    files, dirs = parse_tar(tar_path)
    print(f"  Found {len(files)} files, {len(dirs)} directories")

    # Sort files by size (largest first for better bin packing)
    files_sorted = sorted(files, key=lambda f: len(f.data), reverse=True)

    # Calculate max data per image (leave room for metadata)
    max_data = int(size_mb * 1024 * 1024 * 0.9)  # 90% for data

    # Bin pack files into images
    images: List[List[FileEntry]] = []
    current_image: List[FileEntry] = []
    current_size = 0

    for f in files_sorted:
        file_size = len(f.data) + 1024  # Add overhead for metadata

        if current_size + file_size > max_data and current_image:
            images.append(current_image)
            current_image = []
            current_size = 0

        current_image.append(f)
        current_size += file_size

    if current_image:
        images.append(current_image)

    print(f"\nSplitting into {len(images)} images")

    # Create each image
    for i, image_files in enumerate(images):
        image_path = Path(output_dir) / f"dist{i+1:03d}.efs"
        print(f"\nCreating image {i+1}/{len(images)}: {image_path}")

        builder = EFSBuilder(size_mb)

        # Collect required directories for these files
        required_dirs = set()
        for f in image_files:
            parts = f.path.strip('/').split('/')
            for j in range(len(parts) - 1):
                required_dirs.add('/' + '/'.join(parts[:j+1]))

        # Add directories
        now = int(time.time())
        for d_path in sorted(required_dirs):
            builder.add_directory(d_path, 0o755, 0, 0, now)

        # Add files
        for f in image_files:
            builder.add_file(f.path, f.mode, f.uid, f.gid,
                           f.size, f.mtime, f.data, f.link_target)

        builder.build(str(image_path))

    print(f"\nCreated {len(images)} EFS images in {output_dir}")


def cmd_convert(args):
    """Convert tar to EFS"""
    tar_path = args.input
    efs_path = args.output

    if not Path(tar_path).exists():
        print(f"Error: Input file not found: {tar_path}")
        return 1

    try:
        tar2efs(tar_path, efs_path, args.size)
        return 0
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


def cmd_split(args):
    """Split tar into multiple EFS images"""
    tar_path = args.input
    output_dir = args.output_dir

    if not Path(tar_path).exists():
        print(f"Error: Input file not found: {tar_path}")
        return 1

    try:
        split_tar2efs(tar_path, output_dir, args.size)
        return 0
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


def verify_efs(efs_path: str) -> int:
    """Verify an EFS image for structural correctness before mounting in IRIX.

    Checks:
      1. Superblock magic and fs_size == firstcg + ncg * cgfsize
      2. fs_bmblock == 0 (non-grown EFS)
      3. Every directory block has magic 0xBEEF
      4. Directory di_size is a multiple of 512
      5. No file/dir extent overlaps a CG inode area
      6. All extents are within fs bounds

    Returns the number of errors found (0 = clean).
    """
    errors = 0

    def err(msg: str):
        nonlocal errors
        print(f"  ERROR: {msg}")
        errors += 1

    def warn(msg: str):
        print(f"  WARN:  {msg}")

    print(f"Verifying: {efs_path}")

    with open(efs_path, 'rb') as f:
        # --- Superblock ---
        f.seek(EFS_BLOCK_SIZE)
        sb = f.read(EFS_BLOCK_SIZE)

        fs_size    = struct.unpack('>i', sb[0:4])[0]
        fs_firstcg = struct.unpack('>i', sb[4:8])[0]
        fs_cgfsize = struct.unpack('>i', sb[8:12])[0]
        fs_cgisize = struct.unpack('>h', sb[12:14])[0]
        fs_ncg     = struct.unpack('>h', sb[18:20])[0]
        fs_magic   = struct.unpack('>i', sb[28:32])[0]
        fs_bmblock = struct.unpack('>i', sb[56:60])[0]

        if fs_magic not in (EFS_MAGIC, EFS_MAGIC_NEW):
            err(f"bad superblock magic 0x{fs_magic:06x}")
            return errors

        print(f"  Superblock: magic=0x{fs_magic:06x} firstcg={fs_firstcg} "
              f"ncg={fs_ncg} cgfsize={fs_cgfsize} cgisize={fs_cgisize}")

        expected_size = fs_firstcg + fs_ncg * fs_cgfsize
        if fs_size != expected_size:
            err(f"fs_size {fs_size} != firstcg {fs_firstcg} + {fs_ncg}x{fs_cgfsize} "
                f"= {expected_size}")
        else:
            print(f"  fs_size OK: {fs_size} == {fs_firstcg} + {fs_ncg}*{fs_cgfsize}")

        if fs_bmblock != 0:
            err(f"fs_bmblock={fs_bmblock} (must be 0 for non-grown EFS)")
        else:
            print(f"  fs_bmblock OK: 0")

        inode_blks = fs_cgisize // EFS_INODES_PER_BLOCK

        # Build set of all inode blocks for overlap checking
        inode_block_ranges: List[Tuple[int, int]] = []
        for cg in range(fs_ncg):
            cg_start = fs_firstcg + cg * fs_cgfsize
            inode_block_ranges.append((cg_start, cg_start + inode_blks))

        def overlaps_inode_area(bn: int, length: int) -> bool:
            end = bn + length
            for (ib_start, ib_end) in inode_block_ranges:
                if bn < ib_end and end > ib_start:
                    return True
            return False

        def in_bounds(bn: int, length: int) -> bool:
            return bn >= 0 and bn + length <= fs_size

        # --- Inode scan ---
        dir_errs = 0
        extent_errs = 0
        dirblk_errs = 0
        inodes_checked = 0

        for cg in range(fs_ncg):
            cg_start = fs_firstcg + cg * fs_cgfsize

            for blk_idx in range(inode_blks):
                f.seek((cg_start + blk_idx) * EFS_BLOCK_SIZE)
                blk_data = f.read(EFS_BLOCK_SIZE)

                for slot in range(EFS_INODES_PER_BLOCK):
                    ino_num = cg * fs_cgisize + blk_idx * EFS_INODES_PER_BLOCK + slot
                    raw = blk_data[slot * EFS_INODE_SIZE:(slot + 1) * EFS_INODE_SIZE]
                    mode = struct.unpack('>H', raw[0:2])[0]
                    if mode == 0:
                        continue
                    inodes_checked += 1

                    di_size      = struct.unpack('>i', raw[8:12])[0]
                    di_numext    = struct.unpack('>h', raw[28:30])[0]
                    is_dir = (mode & S_IFMT) == S_IFDIR

                    # Directory size must be multiple of 512
                    if is_dir and di_size % EFS_DIRBSIZE != 0:
                        if dir_errs < 5:
                            err(f"inode {ino_num}: dir di_size={di_size} "
                                f"not multiple of {EFS_DIRBSIZE}")
                        dir_errs += 1

                    # Check extents
                    num_ext = min(di_numext, EFS_MAX_EXTENTS)
                    for e in range(num_ext):
                        ext_raw = raw[32 + e * 8: 32 + e * 8 + 8]
                        w1, w2 = struct.unpack('>II', ext_raw)
                        bn     = w1 & 0xFFFFFF
                        length = (w2 >> 24) & 0xFF

                        if length == 0:
                            if extent_errs < 5:
                                err(f"inode {ino_num} extent {e}: length=0")
                            extent_errs += 1
                            continue

                        if not in_bounds(bn, length):
                            if extent_errs < 5:
                                err(f"inode {ino_num} extent {e}: "
                                    f"bn={bn}+{length} out of fs bounds ({fs_size})")
                            extent_errs += 1
                            continue

                        if overlaps_inode_area(bn, length):
                            if extent_errs < 5:
                                err(f"inode {ino_num} extent {e}: "
                                    f"bn={bn}+{length} overlaps a CG inode area")
                            extent_errs += 1

                    # For directories, check each data block for 0xBEEF magic
                    if is_dir:
                        for e in range(num_ext):
                            ext_raw = raw[32 + e * 8: 32 + e * 8 + 8]
                            w1, w2 = struct.unpack('>II', ext_raw)
                            bn     = w1 & 0xFFFFFF
                            length = (w2 >> 24) & 0xFF
                            if length == 0 or not in_bounds(bn, length):
                                continue
                            for blk in range(length):
                                f.seek((bn + blk) * EFS_BLOCK_SIZE)
                                dirblk = f.read(EFS_DIRBSIZE)
                                magic = struct.unpack('>H', dirblk[0:2])[0]
                                if magic != EFS_DIRBLK_MAGIC:
                                    if dirblk_errs < 5:
                                        err(f"inode {ino_num} dir block "
                                            f"bn={bn+blk}: magic=0x{magic:04x} "
                                            f"(expected 0xBEEF)")
                                    dirblk_errs += 1

        if dir_errs > 5:
            err(f"... and {dir_errs - 5} more directory size errors")
        if extent_errs > 5:
            err(f"... and {extent_errs - 5} more extent errors")
        if dirblk_errs > 5:
            err(f"... and {dirblk_errs - 5} more dir-block magic errors")

    print(f"\n  Inodes checked: {inodes_checked}")
    if errors == 0:
        print("  PASS — no structural errors found")
    else:
        print(f"  FAIL — {errors} error(s) found")
    return errors


def cmd_verify(args):
    """Verify EFS image structural correctness"""
    efs_path = args.input
    if not Path(efs_path).exists():
        print(f"Error: File not found: {efs_path}")
        return 1
    n = verify_efs(efs_path)
    return 0 if n == 0 else 1


def cmd_info(args):
    """Show EFS image information"""
    efs_path = args.input

    if not Path(efs_path).exists():
        print(f"Error: File not found: {efs_path}")
        return 1

    with open(efs_path, 'rb') as f:
        # Skip boot block
        f.seek(EFS_BLOCK_SIZE)

        # Read superblock
        sb_data = f.read(EFS_BLOCK_SIZE)

        # Parse superblock
        fs_size = struct.unpack('>i', sb_data[0:4])[0]
        fs_firstcg = struct.unpack('>i', sb_data[4:8])[0]
        fs_cgfsize = struct.unpack('>i', sb_data[8:12])[0]
        fs_cgisize = struct.unpack('>h', sb_data[12:14])[0]
        fs_sectors = struct.unpack('>h', sb_data[14:16])[0]
        fs_heads = struct.unpack('>h', sb_data[16:18])[0]
        fs_ncg = struct.unpack('>h', sb_data[18:20])[0]
        fs_dirty = struct.unpack('>h', sb_data[20:22])[0]
        fs_time = struct.unpack('>i', sb_data[24:28])[0]
        fs_magic = struct.unpack('>i', sb_data[28:32])[0]
        fs_fname = sb_data[32:38].rstrip(b'\x00').decode('ascii', errors='replace')
        fs_fpack = sb_data[38:44].rstrip(b'\x00').decode('ascii', errors='replace')
        fs_bmsize = struct.unpack('>i', sb_data[44:48])[0]
        fs_tfree = struct.unpack('>i', sb_data[48:52])[0]
        fs_tinode = struct.unpack('>i', sb_data[52:56])[0]
        fs_bmblock = struct.unpack('>i', sb_data[56:60])[0]
        fs_replsb = struct.unpack('>i', sb_data[60:64])[0]
        fs_lastialloc = struct.unpack('>i', sb_data[64:68])[0]

        print(f"EFS Filesystem Information: {efs_path}")
        print("=" * 60)
        print(f"Magic: 0x{fs_magic:06x} ({'valid' if fs_magic in (EFS_MAGIC, EFS_MAGIC_NEW) else 'INVALID'})")
        print(f"Dirty: {'yes' if fs_dirty else 'no'}")
        print(f"Volume name: {fs_fname}")
        print(f"Pack name: {fs_fpack}")
        print()
        print(f"Filesystem size: {fs_size} blocks ({fs_size * EFS_BLOCK_SIZE / (1024*1024):.2f} MB)")
        print(f"First cylinder group: block {fs_firstcg}")
        print(f"CG size: {fs_cgfsize} blocks")
        print(f"Number of CGs: {fs_ncg}")
        print(f"Inodes per CG: {fs_cgisize}")
        print(f"Total inodes: {fs_ncg * fs_cgisize}")
        print()
        print(f"Sectors per track: {fs_sectors}")
        print(f"Heads: {fs_heads}")
        print()
        print(f"Free blocks: {fs_tfree}")
        print(f"Free inodes: {fs_tinode}")
        print(f"Last allocated inode: {fs_lastialloc}")
        print()
        print(f"Bitmap start: block {fs_bmblock}")
        print(f"Bitmap size: {fs_bmsize} bytes")
        print(f"Superblock copy: block {fs_replsb}")

        if fs_time:
            from datetime import datetime
            dt = datetime.fromtimestamp(fs_time)
            print(f"Last modified: {dt}")

    return 0


def cmd_list(args):
    """List contents of EFS image"""
    efs_path = args.input

    if not Path(efs_path).exists():
        print(f"Error: File not found: {efs_path}")
        return 1

    with open(efs_path, 'rb') as f:
        # Read superblock
        f.seek(EFS_BLOCK_SIZE)
        sb_data = f.read(EFS_BLOCK_SIZE)

        fs_firstcg = struct.unpack('>i', sb_data[4:8])[0]
        fs_cgfsize = struct.unpack('>i', sb_data[8:12])[0]
        fs_cgisize = struct.unpack('>h', sb_data[12:14])[0]
        fs_ncg = struct.unpack('>h', sb_data[18:20])[0]
        fs_magic = struct.unpack('>i', sb_data[28:32])[0]

        if fs_magic not in (EFS_MAGIC, EFS_MAGIC_NEW):
            print(f"Error: Invalid EFS magic: 0x{fs_magic:06x}")
            return 1

        inode_blocks_per_cg = fs_cgisize // EFS_INODES_PER_BLOCK

        # Read all inodes
        inodes = {}
        for cg in range(fs_ncg):
            cg_start = fs_firstcg + cg * fs_cgfsize
            f.seek(cg_start * EFS_BLOCK_SIZE)

            for block_idx in range(inode_blocks_per_cg):
                inode_base = cg * fs_cgisize + block_idx * EFS_INODES_PER_BLOCK

                for slot in range(EFS_INODES_PER_BLOCK):
                    inode_num = inode_base + slot
                    inode_data = f.read(EFS_INODE_SIZE)

                    mode = struct.unpack('>H', inode_data[0:2])[0]
                    if mode == 0:
                        continue

                    nlink = struct.unpack('>h', inode_data[2:4])[0]
                    uid = struct.unpack('>H', inode_data[4:6])[0]
                    gid = struct.unpack('>H', inode_data[6:8])[0]
                    size = struct.unpack('>i', inode_data[8:12])[0]
                    mtime = struct.unpack('>i', inode_data[16:20])[0]
                    numextents = struct.unpack('>h', inode_data[28:30])[0]

                    # Parse extents
                    extents = []
                    for i in range(min(numextents, EFS_MAX_EXTENTS)):
                        ext_offset = 32 + i * 8
                        ext_data = inode_data[ext_offset:ext_offset+8]
                        extents.append(EFSExtent.unpack(ext_data))

                    inodes[inode_num] = {
                        'mode': mode,
                        'nlink': nlink,
                        'uid': uid,
                        'gid': gid,
                        'size': size,
                        'mtime': mtime,
                        'extents': extents
                    }

        # Read directory contents starting from root (inode 2)
        def read_dir_entries(inode_num: int) -> List[Tuple[str, int]]:
            if inode_num not in inodes:
                return []

            inode = inodes[inode_num]
            entries = []

            for ext in inode['extents']:
                f.seek(ext.bn * EFS_BLOCK_SIZE)
                dir_data = f.read(ext.length * EFS_BLOCK_SIZE)

                offset = 0
                while offset < len(dir_data) and offset < inode['size']:
                    if offset + 8 > len(dir_data):
                        break

                    reclen = struct.unpack('>H', dir_data[offset:offset+2])[0]
                    if reclen == 0:
                        break

                    namelen = dir_data[offset+2]
                    ino = struct.unpack('>I', dir_data[offset+4:offset+8])[0]
                    name = dir_data[offset+8:offset+8+namelen].decode('ascii', errors='replace')

                    if name not in ('.', '..'):
                        entries.append((name, ino))

                    offset += reclen

            return entries

        def list_recursive(path: str, inode_num: int, indent: int = 0):
            if inode_num not in inodes:
                return

            inode = inodes[inode_num]
            mode = inode['mode']
            size = inode['size']
            uid = inode['uid']
            gid = inode['gid']

            # Format mode string
            type_char = '-'
            if mode & S_IFMT == S_IFDIR:
                type_char = 'd'
            elif mode & S_IFMT == S_IFLNK:
                type_char = 'l'
            elif mode & S_IFMT == S_IFCHR:
                type_char = 'c'
            elif mode & S_IFMT == S_IFBLK:
                type_char = 'b'
            elif mode & S_IFMT == S_IFIFO:
                type_char = 'p'

            perms = mode & 0o7777
            mode_str = type_char + format_perms(perms)

            print(f"{mode_str} {uid:5d} {gid:5d} {size:10d} {path}")

            # If directory, list contents
            if mode & S_IFMT == S_IFDIR:
                entries = read_dir_entries(inode_num)
                for name, ino in sorted(entries):
                    child_path = path.rstrip('/') + '/' + name
                    list_recursive(child_path, ino, indent + 2)

        def format_perms(perms: int) -> str:
            chars = ''
            for i in range(3):
                shift = (2 - i) * 3
                val = (perms >> shift) & 7
                chars += 'r' if val & 4 else '-'
                chars += 'w' if val & 2 else '-'
                chars += 'x' if val & 1 else '-'
            return chars

        print(f"Contents of {efs_path}:")
        print("=" * 60)
        list_recursive('/', EFS_ROOT_INODE)

    return 0


def main():
    # Check for shorthand syntax: tar2efs.py input.tar output.efs [--size N]
    # before argparse, since argparse requires subcommand
    if len(sys.argv) >= 3:
        first_arg = sys.argv[1]
        if first_arg not in ('convert', 'split', 'info', 'list', 'verify', '-h', '--help'):
            # Assume shorthand convert syntax
            convert_args = argparse.Namespace()
            convert_args.input = sys.argv[1]
            convert_args.output = sys.argv[2]
            convert_args.size = 8192

            # Check for --size
            for i, arg in enumerate(sys.argv[3:], 3):
                if arg == '--size' and i + 1 < len(sys.argv):
                    convert_args.size = int(sys.argv[i + 1])

            return cmd_convert(convert_args)

    parser = argparse.ArgumentParser(
        description="Convert tar archives to SGI EFS disk images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # convert command (default)
    convert_parser = subparsers.add_parser('convert', help='Convert tar to EFS image')
    convert_parser.add_argument('input', help='Input tar file')
    convert_parser.add_argument('output', help='Output EFS image')
    convert_parser.add_argument('--size', type=int, default=8192,
                               help='Image size in MB (max 8192)')

    # split command
    split_parser = subparsers.add_parser('split', help='Split tar into multiple EFS images')
    split_parser.add_argument('input', help='Input tar file')
    split_parser.add_argument('--output-dir', required=True,
                             help='Output directory for EFS images')
    split_parser.add_argument('--size', type=int, default=8192,
                             help='Max size per image in MB (max 8192)')

    # info command
    info_parser = subparsers.add_parser('info', help='Show EFS image information')
    info_parser.add_argument('input', help='EFS image file')

    # list command
    list_parser = subparsers.add_parser('list', help='List EFS image contents')
    list_parser.add_argument('input', help='EFS image file')

    # verify command
    verify_parser = subparsers.add_parser(
        'verify', help='Verify EFS image structural correctness (no IRIX needed)')
    verify_parser.add_argument('input', help='EFS image file')

    args = parser.parse_args()

    if args.command == 'convert':
        return cmd_convert(args)
    elif args.command == 'split':
        return cmd_split(args)
    elif args.command == 'info':
        return cmd_info(args)
    elif args.command == 'list':
        return cmd_list(args)
    elif args.command == 'verify':
        return cmd_verify(args)
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())
