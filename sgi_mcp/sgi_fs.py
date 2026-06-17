"""SGI filesystem tools: read EFS and XFS from SGI disk images.

Supports raw .img and QEMU .qcow2 disk images. Provides volume header
parsing, EFS read/write, and XFS read-only access for the MCP server.
"""

import io
import os
import struct
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────

SECTOR_SIZE = 512
QCOW2_MAGIC = b'QFI\xfb'

# SGI Volume Header
VHMAGIC = 0x0BE5A941
NVDIR = 15
NPARTAB = 16

# Partition types
PTYPE_VOLHDR = 0
PTYPE_RAW = 3
PTYPE_SYSV = 5
PTYPE_VOLUME = 6
PTYPE_EFS = 7
PTYPE_XFS = 10
PTYPE_XFSLOG = 11

PTYPE_NAMES = {
    0: 'volhdr', 3: 'raw', 5: 'sysv', 6: 'volume',
    7: 'efs', 10: 'xfs', 11: 'xfslog',
}

# EFS
EFS_MAGIC = 0x072959
EFS_MAGIC_NEW = 0x07295A
EFS_BLOCK_SIZE = 512
EFS_INOPBB = 4
EFS_INODE_SIZE = 128
EFS_ROOT_INODE = 2
EFS_MAX_EXTENTS = 12
EFS_DIRBLK_MAGIC = 0xBEEF

# XFS
XFS_SB_MAGIC = 0x58465342      # 'XFSB'
XFS_DINODE_MAGIC = 0x494E       # 'IN'
XFS_DIR2_BLOCK_MAGIC = 0x58443242  # 'XD2B'
XFS_DIR2_DATA_MAGIC = 0x58443244   # 'XD2D'
XFS_DIR_LEAF_MAGIC = 0xfeeb        # old V1 IRIX leaf directory block
XFS_BMAP_MAGIC = 0x424d4150        # 'BMAP'
XFS_DIR2_FREE_TAG = 0xFFFF
XFS_DINODE_FMT_DEV = 0
XFS_DINODE_FMT_LOCAL = 1
XFS_DINODE_FMT_EXTENTS = 2
XFS_DINODE_FMT_BTREE = 3
NULLFSBLOCK = (1 << 64) - 1

# File types
S_IFMT  = 0o170000
S_IFDIR = 0o040000
S_IFREG = 0o100000
S_IFLNK = 0o120000
S_IFCHR = 0o020000
S_IFBLK = 0o060000
S_IFIFO = 0o010000


# ── Disk Image Layer ─────────────────────────────────────────────────

def _find_qemu_img():
    """Find qemu-img binary."""
    candidates = [
        Path('/workspace/qemu/build-linux/qemu-img'),
        Path('/workspace/qemu/build/qemu-img'),
        Path('/workspace/qemu/build-mac/qemu-img'),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    # Fall back to PATH
    return 'qemu-img'


def _is_qcow2(path):
    """Check if file is qcow2 format."""
    try:
        with open(path, 'rb') as f:
            magic = f.read(4)
            return magic == QCOW2_MAGIC
    except (IOError, OSError):
        return False


@contextmanager
def open_disk_image(path, writable=False):
    """Open a disk image for reading. Handles raw and qcow2 transparently.

    Yields an open file object positioned at byte 0.
    For qcow2, creates a temporary raw conversion.
    """
    path = str(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Disk image not found: {path}")

    if _is_qcow2(path):
        qemu_img = _find_qemu_img()
        tmpdir = tempfile.mkdtemp(prefix='sgi_fs_')
        tmp_raw = os.path.join(tmpdir, 'disk.raw')
        try:
            subprocess.run(
                [qemu_img, 'convert', '-O', 'raw', path, tmp_raw],
                check=True, capture_output=True, timeout=120
            )
            with open(tmp_raw, 'r+b' if writable else 'rb') as f:
                yield f
                if writable:
                    f.flush()
                    # Convert back to qcow2
                    subprocess.run(
                        [qemu_img, 'convert', '-O', 'qcow2', tmp_raw, path],
                        check=True, capture_output=True, timeout=120
                    )
        finally:
            try:
                os.unlink(tmp_raw)
                os.rmdir(tmpdir)
            except OSError:
                pass
    else:
        with open(path, 'r+b' if writable else 'rb') as f:
            yield f


# ── Volume Header ────────────────────────────────────────────────────

def read_vh(f):
    """Read and parse an SGI volume header from file position 0."""
    f.seek(0)
    data = f.read(512)
    if len(data) < 512:
        return None

    magic = struct.unpack('>I', data[0:4])[0]
    if magic != VHMAGIC:
        return None

    vh = {'magic': magic}
    vh['bootfile'] = data[8:24].split(b'\x00')[0].decode('ascii', errors='replace')

    dp_offset = 24
    dp_size = 48

    # Volume directory: 15 entries, each 16 bytes
    vd_offset = dp_offset + dp_size
    vh['vd'] = []
    for i in range(NVDIR):
        off = vd_offset + i * 16
        name = data[off:off+8].split(b'\x00')[0].decode('ascii', errors='replace')
        lbn, nbytes = struct.unpack('>ii', data[off+8:off+16])
        vh['vd'].append({'name': name, 'lbn': lbn, 'nbytes': nbytes})

    # Partition table: 16 entries, each 12 bytes
    pt_offset = vd_offset + NVDIR * 16
    vh['pt'] = []
    for i in range(NPARTAB):
        off = pt_offset + i * 12
        nblks, firstlbn, ptype = struct.unpack('>iii', data[off:off+12])
        vh['pt'].append({'nblks': nblks, 'firstlbn': firstlbn, 'type': ptype})

    return vh


def find_partition(f, ptype_wanted):
    """Find a partition by type. Returns (byte_offset, byte_size) or None."""
    vh = read_vh(f)
    if not vh:
        return None
    for pt in vh['pt']:
        if pt['type'] == ptype_wanted and pt['nblks'] > 0:
            return (pt['firstlbn'] * SECTOR_SIZE, pt['nblks'] * SECTOR_SIZE)
    return None


def find_efs_partition(f):
    """Find the EFS partition. Returns (byte_offset, byte_size) or None."""
    vh = read_vh(f)
    if vh:
        for pt in vh['pt']:
            if pt['type'] in (PTYPE_EFS, PTYPE_SYSV) and pt['nblks'] > 0:
                return (pt['firstlbn'] * SECTOR_SIZE, pt['nblks'] * SECTOR_SIZE)
        return None

    # No VH — check if it's a raw EFS
    f.seek(EFS_BLOCK_SIZE)
    sb_data = f.read(EFS_BLOCK_SIZE)
    if len(sb_data) >= 32:
        magic = struct.unpack('>I', sb_data[28:32])[0]
        if magic in (EFS_MAGIC, EFS_MAGIC_NEW):
            f.seek(0, 2)
            return (0, f.tell())
    return None


def find_xfs_partition(f):
    """Find the XFS partition. Returns (byte_offset, byte_size) or None."""
    return find_partition(f, PTYPE_XFS)


def detect_filesystem(f, part_offset):
    """Detect filesystem type at given partition offset. Returns 'efs', 'xfs', or None."""
    # Check EFS superblock at block 1
    f.seek(part_offset + EFS_BLOCK_SIZE)
    data = f.read(32)
    if len(data) >= 32:
        magic = struct.unpack('>I', data[28:32])[0]
        if magic in (EFS_MAGIC, EFS_MAGIC_NEW):
            return 'efs'

    # Check XFS superblock at sector 0
    f.seek(part_offset)
    data = f.read(4)
    if len(data) >= 4:
        magic = struct.unpack('>I', data[0:4])[0]
        if magic == XFS_SB_MAGIC:
            return 'xfs'

    return None


# ── EFS Reader ───────────────────────────────────────────────────────

def efs_read_superblock(f, part_offset):
    """Read and parse the EFS superblock at block 1 within the partition."""
    f.seek(part_offset + EFS_BLOCK_SIZE)
    sb_data = f.read(EFS_BLOCK_SIZE)

    sb = {}
    sb['fs_size'] = struct.unpack('>i', sb_data[0:4])[0]
    sb['fs_firstcg'] = struct.unpack('>i', sb_data[4:8])[0]
    sb['fs_cgfsize'] = struct.unpack('>i', sb_data[8:12])[0]
    sb['fs_cgisize'] = struct.unpack('>h', sb_data[12:14])[0]
    sb['fs_sectors'] = struct.unpack('>h', sb_data[14:16])[0]
    sb['fs_heads'] = struct.unpack('>h', sb_data[16:18])[0]
    sb['fs_ncg'] = struct.unpack('>h', sb_data[18:20])[0]
    sb['fs_dirty'] = struct.unpack('>h', sb_data[20:22])[0]
    sb['fs_time'] = struct.unpack('>i', sb_data[24:28])[0]
    sb['fs_magic'] = struct.unpack('>I', sb_data[28:32])[0]
    sb['fs_fname'] = sb_data[32:38].rstrip(b'\x00').decode('ascii', errors='replace')
    sb['fs_fpack'] = sb_data[38:44].rstrip(b'\x00').decode('ascii', errors='replace')
    sb['fs_bmsize'] = struct.unpack('>i', sb_data[44:48])[0]
    sb['fs_tfree'] = struct.unpack('>i', sb_data[48:52])[0]
    sb['fs_tinode'] = struct.unpack('>i', sb_data[52:56])[0]
    sb['fs_bmblock'] = struct.unpack('>i', sb_data[56:60])[0]
    sb['fs_replsb'] = struct.unpack('>i', sb_data[60:64])[0]

    if sb['fs_magic'] not in (EFS_MAGIC, EFS_MAGIC_NEW):
        return None
    return sb


def _efs_parse_extent(data):
    """Parse an 8-byte EFS extent descriptor."""
    word1, word2 = struct.unpack('>II', data[:8])
    return {
        'magic': (word1 >> 24) & 0xFF,
        'bn': word1 & 0xFFFFFF,
        'length': (word2 >> 24) & 0xFF,
        'offset': word2 & 0xFFFFFF,
    }


def _efs_inode_to_bb(sb, ino):
    """Convert inode number to basic block number."""
    ipcg = sb['fs_cgisize'] * EFS_INOPBB
    cg = ino // ipcg
    cgbb = (ino >> 2) % sb['fs_cgisize']
    return sb['fs_firstcg'] + cg * sb['fs_cgfsize'] + cgbb


def efs_read_inode(f, part_offset, sb, ino):
    """Read a single EFS inode by number."""
    bb = _efs_inode_to_bb(sb, ino)
    slot = ino & 0x3

    f.seek(part_offset + bb * EFS_BLOCK_SIZE)
    block_data = f.read(EFS_BLOCK_SIZE)

    inode_data = block_data[slot * EFS_INODE_SIZE:(slot + 1) * EFS_INODE_SIZE]
    if len(inode_data) < EFS_INODE_SIZE:
        return None

    mode = struct.unpack('>H', inode_data[0:2])[0]
    if mode == 0:
        return None

    nlink = struct.unpack('>h', inode_data[2:4])[0]
    uid = struct.unpack('>H', inode_data[4:6])[0]
    gid = struct.unpack('>H', inode_data[6:8])[0]
    size = struct.unpack('>i', inode_data[8:12])[0]
    numextents = struct.unpack('>h', inode_data[28:30])[0]

    extents = []
    for i in range(min(numextents, EFS_MAX_EXTENTS)):
        ext_offset = 32 + i * 8
        if ext_offset + 8 <= len(inode_data):
            extents.append(_efs_parse_extent(inode_data[ext_offset:ext_offset + 8]))

    return {
        'mode': mode, 'nlink': nlink, 'uid': uid, 'gid': gid,
        'size': size, 'numextents': numextents, 'extents': extents,
    }


def _efs_get_all_extents(f, part_offset, inode):
    """Get all extents for an inode, handling indirect extents."""
    numextents = inode['numextents']
    if numextents <= EFS_MAX_EXTENTS:
        return inode['extents'][:numextents]

    num_indirect = inode['extents'][0]['offset'] if inode['extents'] else 0
    if num_indirect > EFS_MAX_EXTENTS:
        return inode['extents']

    indirect_data = bytearray()
    for i in range(min(num_indirect, len(inode['extents']))):
        ext = inode['extents'][i]
        f.seek(part_offset + ext['bn'] * EFS_BLOCK_SIZE)
        indirect_data.extend(f.read(ext['length'] * EFS_BLOCK_SIZE))

    all_extents = []
    for i in range(numextents):
        off = i * 8
        if off + 8 <= len(indirect_data):
            all_extents.append(_efs_parse_extent(indirect_data[off:off + 8]))
    return all_extents


def efs_read_file_data(f, part_offset, sb, inode):
    """Read file data by following the extent chain."""
    if inode['size'] == 0:
        return b''

    extents = _efs_get_all_extents(f, part_offset, inode)
    extents.sort(key=lambda e: e['offset'])

    chunks = []
    for ext in extents:
        f.seek(part_offset + ext['bn'] * EFS_BLOCK_SIZE)
        chunks.append(f.read(ext['length'] * EFS_BLOCK_SIZE))

    if not chunks:
        return b''
    return b''.join(chunks)[:inode['size']]


def _efs_read_symlink(f, part_offset, sb, inode):
    """Read symlink target."""
    data = efs_read_file_data(f, part_offset, sb, inode)
    return data.rstrip(b'\x00').decode('ascii', errors='replace')


def efs_read_dir_entries(f, part_offset, sb, inode):
    """Read directory entries from an EFS inode."""
    entries = []
    extents = _efs_get_all_extents(f, part_offset, inode)

    for ext in extents:
        f.seek(part_offset + ext['bn'] * EFS_BLOCK_SIZE)
        ext_data = f.read(ext['length'] * EFS_BLOCK_SIZE)

        for blk_off in range(0, len(ext_data), EFS_BLOCK_SIZE):
            dirblk = ext_data[blk_off:blk_off + EFS_BLOCK_SIZE]
            if len(dirblk) < EFS_BLOCK_SIZE:
                break

            magic = struct.unpack('>H', dirblk[0:2])[0]
            if magic != EFS_DIRBLK_MAGIC:
                continue

            firstused = dirblk[2]
            slots = dirblk[3]

            for slot in range(slots):
                slot_val = dirblk[4 + slot]
                if slot_val < firstused:
                    continue
                entry_off = slot_val * 2
                if entry_off + 5 > EFS_BLOCK_SIZE:
                    continue
                ino = struct.unpack('>I', dirblk[entry_off:entry_off + 4])[0]
                namelen = dirblk[entry_off + 4]
                if entry_off + 5 + namelen > EFS_BLOCK_SIZE:
                    continue
                name = dirblk[entry_off + 5:entry_off + 5 + namelen].decode(
                    'ascii', errors='replace')
                if name not in ('.', '..'):
                    entries.append((name, ino))
    return entries


def _efs_list_recursive(f, part_offset, sb, ino_num, path, results, max_entries,
                        path_filter=None):
    """Recursively list EFS directory contents into results list."""
    if len(results) >= max_entries:
        return

    inode = efs_read_inode(f, part_offset, sb, ino_num)
    if not inode:
        return

    mode = inode['mode']
    ft = mode & S_IFMT

    # Apply path filter
    show = True
    if path_filter:
        stripped = path.lstrip('/')
        filt = path_filter.lstrip('/')
        show = (stripped == filt or stripped.startswith(filt + '/') or
                path == '/')

    if show and path != '/':
        entry = {
            'path': path,
            'type': _format_type(mode),
            'perms': _format_perms(mode),
            'uid': inode['uid'],
            'gid': inode['gid'],
            'size': inode['size'],
        }
        if ft == S_IFLNK:
            entry['link_target'] = _efs_read_symlink(f, part_offset, sb, inode)
        results.append(entry)

    if ft == S_IFDIR:
        entries = efs_read_dir_entries(f, part_offset, sb, inode)
        for name, child_ino in sorted(entries):
            child_path = path.rstrip('/') + '/' + name
            _efs_list_recursive(f, part_offset, sb, child_ino, child_path,
                                results, max_entries, path_filter)


def _efs_resolve_path(f, part_offset, sb, path):
    """Resolve a path to an inode number. Returns inode number or None."""
    parts = [p for p in path.strip('/').split('/') if p]
    if not parts:
        return EFS_ROOT_INODE

    current_ino = EFS_ROOT_INODE
    for part in parts:
        inode = efs_read_inode(f, part_offset, sb, current_ino)
        if not inode or (inode['mode'] & S_IFMT) != S_IFDIR:
            return None
        entries = efs_read_dir_entries(f, part_offset, sb, inode)
        found = False
        for name, child_ino in entries:
            if name == part:
                current_ino = child_ino
                found = True
                break
        if not found:
            return None
    return current_ino


# ── XFS Reader ───────────────────────────────────────────────────────

def xfs_read_superblock(f, part_offset):
    """Read and parse the XFS superblock at sector 0 of the partition."""
    f.seek(part_offset)
    data = f.read(256)
    if len(data) < 200:
        return None

    magic = struct.unpack('>I', data[0:4])[0]
    if magic != XFS_SB_MAGIC:
        return None

    sb = {}
    sb['sb_magicnum'] = magic
    sb['sb_blocksize'] = struct.unpack('>I', data[4:8])[0]
    sb['sb_dblocks'] = struct.unpack('>Q', data[8:16])[0]
    sb['sb_rootino'] = struct.unpack('>Q', data[0x38:0x40])[0]
    sb['sb_agblocks'] = struct.unpack('>I', data[0x54:0x58])[0]
    sb['sb_agcount'] = struct.unpack('>I', data[0x58:0x5C])[0]
    sb['sb_versionnum'] = struct.unpack('>H', data[0x64:0x66])[0]
    sb['sb_sectsize'] = struct.unpack('>H', data[0x66:0x68])[0]
    sb['sb_inodesize'] = struct.unpack('>H', data[0x68:0x6A])[0]
    sb['sb_inopblock'] = struct.unpack('>H', data[0x6A:0x6C])[0]
    sb['sb_fname'] = data[0x6C:0x72].rstrip(b'\x00').decode('ascii', errors='replace')
    sb['sb_blocklog'] = data[0x78]
    sb['sb_sectlog'] = data[0x79]
    sb['sb_inodelog'] = data[0x7A]
    sb['sb_inopblog'] = data[0x7B]
    sb['sb_agblklog'] = data[0x7C]
    sb['sb_icount'] = struct.unpack('>Q', data[0x80:0x88])[0]
    sb['sb_ifree'] = struct.unpack('>Q', data[0x88:0x90])[0]
    sb['sb_fdblocks'] = struct.unpack('>Q', data[0x90:0x98])[0]
    sb['sb_dirblklog'] = data[0xC0]
    if len(data) >= 0xCC:
        sb['sb_features2'] = struct.unpack('>I', data[0xC8:0xCC])[0]
    return sb


def _xfs_ino_to_offset(sb, ino, part_offset):
    """Convert XFS inode number to disk byte offset."""
    agblklog = sb['sb_agblklog']
    inopblog = sb['sb_inopblog']
    blocksize = sb['sb_blocksize']
    inodesize = sb['sb_inodesize']
    agblocks = sb['sb_agblocks']

    # Decompose inode number
    agno = ino >> (agblklog + inopblog)
    agino = ino & ((1 << (agblklog + inopblog)) - 1)
    agbno = agino >> inopblog
    ino_offset = agino & ((1 << inopblog) - 1)

    # Physical block = AG start + block within AG
    phys_block = agno * agblocks + agbno
    byte_offset = part_offset + phys_block * blocksize + ino_offset * inodesize
    return byte_offset


def xfs_read_inode(f, part_offset, sb, ino):
    """Read an XFS inode from disk."""
    offset = _xfs_ino_to_offset(sb, ino, part_offset)
    inodesize = sb['sb_inodesize']

    f.seek(offset)
    data = f.read(inodesize)
    if len(data) < 96:
        return None

    magic = struct.unpack('>H', data[0:2])[0]
    if magic != XFS_DINODE_MAGIC:
        return None

    inode = {}
    inode['di_magic'] = magic
    inode['di_mode'] = struct.unpack('>H', data[2:4])[0]
    inode['di_version'] = data[4]
    inode['di_format'] = data[5]
    inode['di_uid'] = struct.unpack('>I', data[8:12])[0]
    inode['di_gid'] = struct.unpack('>I', data[12:16])[0]
    inode['di_nlink'] = struct.unpack('>I', data[16:20])[0]
    inode['di_size'] = struct.unpack('>q', data[0x38:0x40])[0]
    inode['di_nblocks'] = struct.unpack('>Q', data[0x40:0x48])[0]
    inode['di_nextents'] = struct.unpack('>i', data[0x4C:0x50])[0]
    inode['di_forkoff'] = data[0x52]

    # Data fork starts at offset 0x64 (after di_next_unlinked at 0x60)
    inode['_data_fork_offset'] = 0x64
    inode['_raw'] = data
    return inode


def _xfs_parse_bmbt_rec(data):
    """Parse a 16-byte XFS extent record.

    Returns (startoff, startblock, blockcount, flag).
    """
    l0, l1 = struct.unpack('>QQ', data[:16])
    flag = (l0 >> 63) & 1
    startoff = (l0 >> 9) & 0x3FFFFFFFFFFFFF   # 54 bits
    startblock = ((l0 & 0x1FF) << 43) | (l1 >> 21)  # 52 bits
    blockcount = l1 & 0x1FFFFF                  # 21 bits
    return (startoff, startblock, blockcount, flag)


def _xfs_fsblock_to_disk(sb, part_offset, fsblock):
    """Convert XFS filesystem block number to disk byte offset.

    The fsblock is encoded as (agno << agblklog) | agbno,
    NOT as a sequential block number.
    """
    agblklog = sb['sb_agblklog']
    agblocks = sb['sb_agblocks']
    blocksize = sb['sb_blocksize']

    agno = fsblock >> agblklog
    agbno = fsblock & ((1 << agblklog) - 1)
    phys_block = agno * agblocks + agbno
    return part_offset + phys_block * blocksize


def _xfs_get_extents(f, part_offset, sb, inode):
    """Get extent list for an XFS inode. Returns list of (startoff, startblock, blockcount)."""
    fmt = inode['di_format']
    data = inode['_raw']
    fork_offset = inode['_data_fork_offset']
    inodesize = sb['sb_inodesize']

    # Compute data fork size
    if inode['di_forkoff']:
        dfork_size = inode['di_forkoff'] * 8
    else:
        dfork_size = inodesize - fork_offset

    fork_data = data[fork_offset:fork_offset + dfork_size]

    if fmt == XFS_DINODE_FMT_EXTENTS:
        # Extent list directly in the data fork
        nextents = inode['di_nextents']
        extents = []
        for i in range(nextents):
            rec_off = i * 16
            if rec_off + 16 > len(fork_data):
                break
            startoff, startblock, blockcount, flag = _xfs_parse_bmbt_rec(
                fork_data[rec_off:rec_off + 16])
            if blockcount > 0:
                extents.append((startoff, startblock, blockcount))
        return extents

    elif fmt == XFS_DINODE_FMT_BTREE:
        # B+tree root in data fork
        return _xfs_btree_get_extents(f, part_offset, sb, fork_data)

    return []


def _xfs_btree_get_extents(f, part_offset, sb, fork_data):
    """Read extents from a B+tree rooted in the data fork."""
    if len(fork_data) < 4:
        return []

    # On-disk root: xfs_bmdr_block_t (4 bytes header)
    level, numrecs = struct.unpack('>HH', fork_data[0:4])

    if numrecs == 0 or level > 10:
        return []

    if level == 0:
        # Leaf — records are directly here
        extents = []
        for i in range(numrecs):
            rec_off = 4 + i * 16
            if rec_off + 16 > len(fork_data):
                break
            startoff, startblock, blockcount, flag = _xfs_parse_bmbt_rec(
                fork_data[rec_off:rec_off + 16])
            if blockcount > 0:
                extents.append((startoff, startblock, blockcount))
        return extents

    # Internal node: keys then pointers
    # Keys start at offset 4, each is 8 bytes (xfs_bmbt_key_t = xfs_dfiloff_t)
    # Pointers start after MAX keys, each is 8 bytes (xfs_bmbt_ptr_t = xfs_dfsbno_t)
    # Max records = (fork_size - header_size) / (key_size + ptr_size)
    header_size = 4  # xfs_bmdr_block_t
    key_size = 8     # xfs_bmbt_key_t
    ptr_size = 8     # xfs_bmbt_ptr_t
    dmxr = (len(fork_data) - header_size) // (key_size + ptr_size)
    keys_off = header_size
    ptrs_off = keys_off + dmxr * key_size

    # Follow the first pointer down to the leftmost leaf
    if ptrs_off + 8 > len(fork_data):
        return []

    bno = struct.unpack('>Q', fork_data[ptrs_off:ptrs_off + 8])[0]
    blocksize = sb['sb_blocksize']
    max_blocks = sb['sb_dblocks']

    def _valid_bno(b):
        return b != 0 and b != NULLFSBLOCK and b != 0xFFFFFFFFFFFFFFFF

    if not _valid_bno(bno):
        return []

    # Walk down to leaf level
    cur_level = level
    while cur_level > 0:
        disk_off = _xfs_fsblock_to_disk(sb, part_offset, bno)
        f.seek(disk_off)
        block_data = f.read(blocksize)
        if len(block_data) < 24:
            return []

        # xfs_btree_lblock_t header: magic(4), level(2), numrecs(2), leftsib(8), rightsib(8)
        blk_magic, blk_level, blk_numrecs = struct.unpack('>IHH', block_data[0:8])
        if blk_magic != XFS_BMAP_MAGIC:
            return []

        cur_level = blk_level
        if cur_level > 0:
            # Internal node — follow first pointer
            # Keys at offset 24, numrecs * 8 bytes
            # Pointers after keys
            ptr_start = 24 + blk_numrecs * 8
            if ptr_start + 8 > len(block_data):
                return []
            bno = struct.unpack('>Q', block_data[ptr_start:ptr_start + 8])[0]
            if not _valid_bno(bno):
                return []

    # Now at leaf level — walk the linked list of leaf blocks
    extents = []
    visited = set()
    while _valid_bno(bno) and bno not in visited:
        visited.add(bno)
        disk_off = _xfs_fsblock_to_disk(sb, part_offset, bno)
        f.seek(disk_off)
        block_data = f.read(blocksize)
        if len(block_data) < 24:
            break

        blk_magic, blk_level, blk_numrecs = struct.unpack('>IHH', block_data[0:8])
        if blk_magic != XFS_BMAP_MAGIC:
            break
        blk_leftsib, blk_rightsib = struct.unpack('>QQ', block_data[8:24])

        # Records start at offset 24
        for i in range(blk_numrecs):
            rec_off = 24 + i * 16
            if rec_off + 16 > len(block_data):
                break
            startoff, startblock, blockcount, flag = _xfs_parse_bmbt_rec(
                block_data[rec_off:rec_off + 16])
            if blockcount > 0:
                extents.append((startoff, startblock, blockcount))

        # Follow right sibling
        bno = blk_rightsib

    return extents


def xfs_read_file_data(f, part_offset, sb, inode):
    """Read file data from an XFS inode."""
    size = inode['di_size']
    if size <= 0:
        return b''

    fmt = inode['di_format']

    if fmt == XFS_DINODE_FMT_LOCAL:
        # Inline data in the data fork
        fork_offset = inode['_data_fork_offset']
        raw = inode['_raw']
        return raw[fork_offset:fork_offset + size]

    extents = _xfs_get_extents(f, part_offset, sb, inode)
    if not extents:
        return b''

    blocksize = sb['sb_blocksize']
    result = bytearray()

    # Sort extents by file offset
    extents.sort(key=lambda e: e[0])

    for startoff, startblock, blockcount in extents:
        disk_off = _xfs_fsblock_to_disk(sb, part_offset, startblock)
        f.seek(disk_off)
        result.extend(f.read(blockcount * blocksize))

    return bytes(result[:size])


def _xfs_read_symlink(f, part_offset, sb, inode):
    """Read symlink target from XFS inode."""
    data = xfs_read_file_data(f, part_offset, sb, inode)
    return data.rstrip(b'\x00').decode('utf-8', errors='replace')


def xfs_read_dir_entries(f, part_offset, sb, inode):
    """Read directory entries from an XFS inode.

    Returns list of (name, inode_number) tuples, excluding '.' and '..'.
    """
    fmt = inode['di_format']

    if fmt == XFS_DINODE_FMT_LOCAL:
        return _xfs_read_dir_sf(inode, sb)
    else:
        return _xfs_read_dir_block(f, part_offset, sb, inode)


def _xfs_read_dir_sf(inode, sb=None):
    """Read shortform directory entries from inline data.

    Dispatches to V1 or dir2 shortform based on superblock version.
    """
    raw = inode['_raw']
    fork_offset = inode['_data_fork_offset']
    size = inode['di_size']
    data = raw[fork_offset:fork_offset + size]

    if sb is not None and (sb['sb_versionnum'] & 0x2000):
        ftype = bool((sb['sb_versionnum'] & 0x8000) and
                      (sb.get('sb_features2', 0) & 0x200))
        return _xfs_read_dir_sf_v2(data, ftype)

    # V1 format: parent(8) + count(1) = 9 bytes header
    if len(data) < 9:
        return []

    count = data[8]
    offset = 9

    entries = []
    for _ in range(count):
        if offset + 9 > len(data):
            break
        ino = struct.unpack('>Q', data[offset:offset + 8])[0]
        namelen = data[offset + 8]
        if offset + 9 + namelen > len(data):
            break
        name = data[offset + 9:offset + 9 + namelen].decode('ascii', errors='replace')
        offset += 9 + namelen

        if name not in ('.', '..') and ino > 0:
            entries.append((name, ino))

    return entries


def _xfs_read_dir_sf_v2(data, ftype=False):
    """Read dir2 shortform directory entries.

    Dir2 shortform: count(1) + i8count(1) + parent(4 or 8)
    Entry: namelen(1) + offset(2) + name[namelen] [+ ftype(1)] + ino(4 or 8)
    """
    if len(data) < 6:
        return []

    count = data[0]
    i8count = data[1]
    use_8byte = (i8count != 0)

    if use_8byte:
        hdr_size = 10  # count(1) + i8count(1) + parent8(8)
        real_count = i8count
    else:
        hdr_size = 6   # count(1) + i8count(1) + parent4(4)
        real_count = count

    if len(data) < hdr_size:
        return []

    ino_size = 8 if use_8byte else 4
    ftype_size = 1 if ftype else 0
    offset = hdr_size
    entries = []

    for _ in range(real_count):
        if offset + 3 > len(data):
            break
        namelen = data[offset]
        if offset + 3 + namelen + ftype_size + ino_size > len(data):
            break
        name = data[offset + 3:offset + 3 + namelen].decode('ascii', errors='replace')
        ino_off = offset + 3 + namelen + ftype_size
        if use_8byte:
            ino = struct.unpack('>Q', data[ino_off:ino_off + 8])[0]
        else:
            ino = struct.unpack('>I', data[ino_off:ino_off + 4])[0]
        offset = ino_off + ino_size

        if ino > 0:
            entries.append((name, ino))

    return entries


def _xfs_read_dir_block(f, part_offset, sb, inode):
    """Read block/data format directory entries."""
    blocksize = sb['sb_blocksize']
    dirblklog = sb['sb_dirblklog']
    dirblksize = blocksize << dirblklog

    extents = _xfs_get_extents(f, part_offset, sb, inode)
    if not extents:
        return []

    entries = []

    # Only scan data blocks (directory block numbers below the leaf block area)
    # In XFS dir2, data blocks are at directory offset 0..N-1
    # The leaf block starts at a large offset (XFS_DIR2_LEAF_OFFSET)
    # We can just check the magic of each block we read
    for startoff, startblock, blockcount in extents:
        for blk_idx in range(blockcount):
            disk_off = _xfs_fsblock_to_disk(sb, part_offset,
                                             startblock + blk_idx)
            f.seek(disk_off)
            block_data = f.read(blocksize)
            if len(block_data) < 16:
                continue

            # Check 4-byte and 2-byte magic (V1 leaf magic is 2 bytes in da_blkinfo)
            magic4 = struct.unpack('>I', block_data[0:4])[0]
            magic2 = struct.unpack('>H', block_data[8:10])[0]

            if magic2 == XFS_DIR_LEAF_MAGIC:
                # Old IRIX V1 leaf directory block
                _xfs_parse_dir_v1_leaf(block_data, entries)
                continue

            if magic4 not in (XFS_DIR2_BLOCK_MAGIC, XFS_DIR2_DATA_MAGIC):
                continue

            # For multi-fsblock dir blocks, read the full dir block
            if dirblklog > 0 and blk_idx % (1 << dirblklog) == 0:
                remaining = min(blockcount - blk_idx, 1 << dirblklog) - 1
                for extra in range(remaining):
                    extra_off = _xfs_fsblock_to_disk(
                        sb, part_offset, startblock + blk_idx + 1 + extra)
                    f.seek(extra_off)
                    block_data += f.read(blocksize)

            _xfs_parse_dir_data_block(block_data, entries, sb)

    return entries


def _xfs_parse_dir_v1_leaf(block_data, entries):
    """Parse old IRIX V1 XFS leaf directory block (magic=0xfeeb).

    Header layout (32 bytes):
      xfs_da_blkinfo_t (12): forw(4), back(4), magic(2), pad(2)
      count(2), namebytes(2), firstused(2), holes(1), pad(1)
      freemap[3]: 3 * (base:2, size:2) = 12 bytes

    Entry layout (8 bytes each, starting at offset 32):
      hashval(4), nameidx(2), namelength(1), pad(1)

    Name entry at nameidx (8-byte inum + name bytes):
      inum(8 big-endian), name[namelength]
    """
    if len(block_data) < 32:
        return
    count = struct.unpack('>H', block_data[12:14])[0]
    if count == 0 or count > 512:
        return
    for i in range(count):
        off = 32 + i * 8
        if off + 8 > len(block_data):
            break
        nameidx = struct.unpack('>H', block_data[off + 4:off + 6])[0]
        namelength = block_data[off + 6]
        if namelength == 0:
            continue
        name_off = nameidx
        if name_off + 8 + namelength > len(block_data):
            continue
        inum = struct.unpack('>Q', block_data[name_off:name_off + 8])[0]
        name = block_data[name_off + 8:name_off + 8 + namelength].decode(
            'ascii', errors='replace')
        if name not in ('.', '..') and inum > 0:
            entries.append((name, inum))


def _xfs_parse_dir_data_block(block_data, entries, sb):
    """Parse directory entries from a data/block format directory block."""
    magic = struct.unpack('>I', block_data[0:4])[0]

    # Data header is 16 bytes (magic + 3 bestfree pairs)
    data_start = 16

    blocksize = sb['sb_blocksize']
    dirblklog = sb['sb_dirblklog']
    dirblksize = blocksize << dirblklog

    if magic == XFS_DIR2_BLOCK_MAGIC:
        # Block format has a tail at the end
        # xfs_dir2_block_tail_t is 8 bytes at end of block
        tail_off = dirblksize - 8
        if tail_off > len(block_data):
            tail_off = len(block_data) - 8
        if tail_off >= 8:
            leaf_count, stale_count = struct.unpack(
                '>II', block_data[tail_off:tail_off + 8])
            # Leaf entries are before the tail, each 8 bytes
            endptr = tail_off - leaf_count * 8
        else:
            endptr = len(block_data)
    else:
        endptr = len(block_data)

    ptr = data_start
    while ptr < endptr:
        if ptr + 2 > len(block_data):
            break

        # Check for free entry
        freetag = struct.unpack('>H', block_data[ptr:ptr + 2])[0]
        if freetag == XFS_DIR2_FREE_TAG:
            # xfs_dir2_data_unused_t: freetag(2) + length(2) + ...
            if ptr + 4 > len(block_data):
                break
            free_length = struct.unpack('>H', block_data[ptr + 2:ptr + 4])[0]
            if free_length == 0:
                break
            ptr += free_length
            continue

        # Data entry: inumber(8) + namelen(1) + name(N) + tag(2), 8-aligned
        if ptr + 9 > len(block_data):
            break

        inumber = struct.unpack('>Q', block_data[ptr:ptr + 8])[0]
        namelen = block_data[ptr + 8]

        if ptr + 9 + namelen + 2 > len(block_data):
            break

        name = block_data[ptr + 9:ptr + 9 + namelen].decode(
            'ascii', errors='replace')

        # Entry size is 8-byte aligned: (8 + 1 + namelen + 2 + 7) & ~7
        entry_size = (8 + 1 + namelen + 2 + 7) & ~7
        ptr += entry_size

        if name not in ('.', '..'):
            entries.append((name, inumber))


def _xfs_list_recursive(f, part_offset, sb, ino_num, path, results,
                        max_entries, path_filter=None):
    """Recursively list XFS directory contents."""
    if len(results) >= max_entries:
        return

    inode = xfs_read_inode(f, part_offset, sb, ino_num)
    if not inode:
        return

    mode = inode['di_mode']
    ft = mode & S_IFMT

    show = True
    if path_filter:
        stripped = path.lstrip('/')
        filt = path_filter.lstrip('/')
        show = (stripped == filt or stripped.startswith(filt + '/') or
                path == '/')

    if show and path != '/':
        entry = {
            'path': path,
            'type': _format_type(mode),
            'perms': _format_perms(mode),
            'uid': inode['di_uid'],
            'gid': inode['di_gid'],
            'size': inode['di_size'],
        }
        if ft == S_IFLNK:
            entry['link_target'] = _xfs_read_symlink(f, part_offset, sb, inode)
        results.append(entry)

    if ft == S_IFDIR:
        entries = xfs_read_dir_entries(f, part_offset, sb, inode)
        for name, child_ino in sorted(entries):
            child_path = path.rstrip('/') + '/' + name
            _xfs_list_recursive(f, part_offset, sb, child_ino, child_path,
                                results, max_entries, path_filter)


def _xfs_resolve_path(f, part_offset, sb, path):
    """Resolve a path to an XFS inode number. Returns inode number or None."""
    parts = [p for p in path.strip('/').split('/') if p]
    root_ino = sb['sb_rootino']
    if not parts:
        return root_ino

    current_ino = root_ino
    for part in parts:
        inode = xfs_read_inode(f, part_offset, sb, current_ino)
        if not inode or (inode['di_mode'] & S_IFMT) != S_IFDIR:
            return None
        entries = xfs_read_dir_entries(f, part_offset, sb, inode)
        found = False
        for name, child_ino in entries:
            if name == part:
                current_ino = child_ino
                found = True
                break
        if not found:
            return None
    return current_ino


# ── Formatting Helpers ───────────────────────────────────────────────

def _format_perms(mode):
    """Format permission bits as rwxrwxrwx string."""
    chars = ''
    for i in range(3):
        shift = (2 - i) * 3
        val = (mode >> shift) & 7
        chars += 'r' if val & 4 else '-'
        chars += 'w' if val & 2 else '-'
        chars += 'x' if val & 1 else '-'
    return chars


def _format_type(mode):
    """Get single character for file type."""
    ft = mode & S_IFMT
    return {
        S_IFDIR: 'd', S_IFREG: '-', S_IFLNK: 'l',
        S_IFCHR: 'c', S_IFBLK: 'b', S_IFIFO: 'p',
    }.get(ft, '?')


def _format_entry(entry):
    """Format a file entry as an ls-style line."""
    suffix = ''
    if entry.get('link_target'):
        suffix = f" -> {entry['link_target']}"
    return (f"{entry['type']}{entry['perms']} {entry['uid']:5d} "
            f"{entry['gid']:5d} {entry['size']:10d} {entry['path']}{suffix}")


# ── Facade API ───────────────────────────────────────────────────────

def fs_info(image_path):
    """Show volume header, partition table, and filesystem details."""
    lines = []
    with open_disk_image(image_path) as f:
        vh = read_vh(f)
        if not vh:
            lines.append(f"No SGI volume header found in {image_path}")
            return '\n'.join(lines)

        lines.append(f"**SGI Volume Header:** `{os.path.basename(image_path)}`")
        lines.append(f"**Boot file:** `{vh['bootfile']}`")
        lines.append("")

        # Volume directory
        vd_entries = [vd for vd in vh['vd'] if vd['name']]
        if vd_entries:
            lines.append("**Volume Directory:**")
            for vd in vd_entries:
                lines.append(f"  `{vd['name']}` lbn={vd['lbn']} "
                             f"size={vd['nbytes']} ({vd['nbytes']//1024}KB)")
            lines.append("")

        # Partition table
        lines.append("**Partition Table:**")
        lines.append("| # | Type | Start | Blocks | Size |")
        lines.append("|---|------|-------|--------|------|")
        for i, pt in enumerate(vh['pt']):
            if pt['nblks'] > 0:
                tname = PTYPE_NAMES.get(pt['type'], str(pt['type']))
                size_mb = pt['nblks'] * SECTOR_SIZE / (1024 * 1024)
                lines.append(f"| {i} | {tname} | {pt['firstlbn']} | "
                             f"{pt['nblks']} | {size_mb:.1f}MB |")
        lines.append("")

        # Check for EFS
        efs_part = find_efs_partition(f)
        if efs_part:
            part_offset, part_size = efs_part
            sb = efs_read_superblock(f, part_offset)
            if sb:
                lines.append(f"**EFS Filesystem** at offset {part_offset}")
                lines.append(f"  Magic: 0x{sb['fs_magic']:06x}")
                lines.append(f"  Size: {sb['fs_size']} blocks "
                             f"({sb['fs_size'] * EFS_BLOCK_SIZE // (1024*1024)}MB)")
                lines.append(f"  Cylinder groups: {sb['fs_ncg']}")
                lines.append(f"  Free blocks: {sb['fs_tfree']}")
                lines.append(f"  Free inodes: {sb['fs_tinode']}")
                if sb['fs_fname']:
                    lines.append(f"  Volume name: {sb['fs_fname']}")
                lines.append("")

        # Check for XFS
        xfs_part = find_xfs_partition(f)
        if xfs_part:
            part_offset, part_size = xfs_part
            sb = xfs_read_superblock(f, part_offset)
            if sb:
                total_mb = sb['sb_dblocks'] * sb['sb_blocksize'] / (1024 * 1024)
                free_mb = sb['sb_fdblocks'] * sb['sb_blocksize'] / (1024 * 1024)
                lines.append(f"**XFS Filesystem** at offset {part_offset}")
                lines.append(f"  Block size: {sb['sb_blocksize']}")
                lines.append(f"  Total: {sb['sb_dblocks']} blocks ({total_mb:.1f}MB)")
                lines.append(f"  Free: {sb['sb_fdblocks']} blocks ({free_mb:.1f}MB)")
                lines.append(f"  Inodes: {sb['sb_icount']} (free: {sb['sb_ifree']})")
                lines.append(f"  AG count: {sb['sb_agcount']}, "
                             f"AG blocks: {sb['sb_agblocks']}")
                lines.append(f"  Root inode: {sb['sb_rootino']}")
                if sb['sb_fname']:
                    lines.append(f"  Volume name: {sb['sb_fname']}")
                lines.append("")

    return '\n'.join(lines)


def fs_ls(image_path, path='/', recursive=True, max_entries=500, partition=None):
    """List files in a disk image filesystem.

    Returns formatted ls-style listing.
    """
    with open_disk_image(image_path) as f:
        fs_type, part_offset, sb = _find_filesystem(f, partition)

        if not fs_type:
            return "Error: No EFS or XFS filesystem found"

        results = []
        path_filter = path if path != '/' else None

        if fs_type == 'efs':
            root_ino = EFS_ROOT_INODE
            if not recursive:
                # List just the immediate children of the path
                ino = _efs_resolve_path(f, part_offset, sb, path)
                if ino is None:
                    return f"Error: Path not found: {path}"
                inode = efs_read_inode(f, part_offset, sb, ino)
                if not inode:
                    return f"Error: Cannot read inode for {path}"
                if (inode['mode'] & S_IFMT) != S_IFDIR:
                    # Single file
                    results.append({
                        'path': path,
                        'type': _format_type(inode['mode']),
                        'perms': _format_perms(inode['mode']),
                        'uid': inode['uid'], 'gid': inode['gid'],
                        'size': inode['size'],
                    })
                else:
                    entries = efs_read_dir_entries(f, part_offset, sb, inode)
                    for name, child_ino in sorted(entries):
                        child_inode = efs_read_inode(f, part_offset, sb, child_ino)
                        if child_inode:
                            child_path = path.rstrip('/') + '/' + name
                            entry = {
                                'path': child_path,
                                'type': _format_type(child_inode['mode']),
                                'perms': _format_perms(child_inode['mode']),
                                'uid': child_inode['uid'],
                                'gid': child_inode['gid'],
                                'size': child_inode['size'],
                            }
                            if (child_inode['mode'] & S_IFMT) == S_IFLNK:
                                entry['link_target'] = _efs_read_symlink(
                                    f, part_offset, sb, child_inode)
                            results.append(entry)
            else:
                _efs_list_recursive(f, part_offset, sb, root_ino, '/',
                                    results, max_entries, path_filter)

        elif fs_type == 'xfs':
            root_ino = sb['sb_rootino']
            if not recursive:
                ino = _xfs_resolve_path(f, part_offset, sb, path)
                if ino is None:
                    return f"Error: Path not found: {path}"
                inode = xfs_read_inode(f, part_offset, sb, ino)
                if not inode:
                    return f"Error: Cannot read inode for {path}"
                if (inode['di_mode'] & S_IFMT) != S_IFDIR:
                    results.append({
                        'path': path,
                        'type': _format_type(inode['di_mode']),
                        'perms': _format_perms(inode['di_mode']),
                        'uid': inode['di_uid'], 'gid': inode['di_gid'],
                        'size': inode['di_size'],
                    })
                else:
                    entries = xfs_read_dir_entries(f, part_offset, sb, inode)
                    for name, child_ino in sorted(entries):
                        child_inode = xfs_read_inode(f, part_offset, sb, child_ino)
                        if child_inode:
                            child_path = path.rstrip('/') + '/' + name
                            entry = {
                                'path': child_path,
                                'type': _format_type(child_inode['di_mode']),
                                'perms': _format_perms(child_inode['di_mode']),
                                'uid': child_inode['di_uid'],
                                'gid': child_inode['di_gid'],
                                'size': child_inode['di_size'],
                            }
                            if (child_inode['di_mode'] & S_IFMT) == S_IFLNK:
                                entry['link_target'] = _xfs_read_symlink(
                                    f, part_offset, sb, child_inode)
                            results.append(entry)
            else:
                _xfs_list_recursive(f, part_offset, sb, root_ino, '/',
                                    results, max_entries, path_filter)

        if not results:
            return f"No files found at {path}"

        header = f"**{fs_type.upper()}** — {len(results)} entries"
        if len(results) >= max_entries:
            header += f" (truncated at {max_entries})"
        lines = [header, "```"]
        for entry in results:
            lines.append(_format_entry(entry))
        lines.append("```")
        return '\n'.join(lines)


def fs_cat(image_path, path, binary=False, max_size=65536, partition=None):
    """Read a file's contents from a disk image filesystem."""
    with open_disk_image(image_path) as f:
        fs_type, part_offset, sb = _find_filesystem(f, partition)

        if not fs_type:
            return "Error: No EFS or XFS filesystem found"

        if fs_type == 'efs':
            ino = _efs_resolve_path(f, part_offset, sb, path)
            if ino is None:
                return f"Error: Path not found: {path}"
            inode = efs_read_inode(f, part_offset, sb, ino)
            if not inode:
                return f"Error: Cannot read inode for {path}"
            ft = inode['mode'] & S_IFMT
            if ft == S_IFLNK:
                return f"Symlink: {_efs_read_symlink(f, part_offset, sb, inode)}"
            if ft == S_IFDIR:
                return f"Error: {path} is a directory"
            if ft != S_IFREG:
                return f"Error: {path} is not a regular file"
            if inode['size'] > max_size:
                return (f"Error: File is {inode['size']} bytes, exceeds "
                        f"max_size={max_size}. Use fs_extract instead.")
            data = efs_read_file_data(f, part_offset, sb, inode)

        elif fs_type == 'xfs':
            ino = _xfs_resolve_path(f, part_offset, sb, path)
            if ino is None:
                return f"Error: Path not found: {path}"
            inode = xfs_read_inode(f, part_offset, sb, ino)
            if not inode:
                return f"Error: Cannot read inode for {path}"
            ft = inode['di_mode'] & S_IFMT
            if ft == S_IFLNK:
                return f"Symlink: {_xfs_read_symlink(f, part_offset, sb, inode)}"
            if ft == S_IFDIR:
                return f"Error: {path} is a directory"
            if ft != S_IFREG:
                return f"Error: {path} is not a regular file"
            if inode['di_size'] > max_size:
                return (f"Error: File is {inode['di_size']} bytes, exceeds "
                        f"max_size={max_size}. Use fs_extract instead.")
            data = xfs_read_file_data(f, part_offset, sb, inode)

        if binary:
            return _hex_dump(data)

        # Try to decode as text
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            try:
                return data.decode('latin-1')
            except Exception:
                return _hex_dump(data)


def fs_extract(image_path, dest, path=None, partition=None):
    """Extract files/directories from a disk image to host filesystem."""
    os.makedirs(dest, exist_ok=True)

    with open_disk_image(image_path) as f:
        fs_type, part_offset, sb = _find_filesystem(f, partition)

        if not fs_type:
            return "Error: No EFS or XFS filesystem found"

        stats = {'files': 0, 'dirs': 0, 'symlinks': 0, 'errors': 0}
        path_filter = path.lstrip('/') if path else None

        if fs_type == 'efs':
            _efs_extract_recursive(f, part_offset, sb, EFS_ROOT_INODE, '/',
                                   dest, path_filter, stats)
        elif fs_type == 'xfs':
            root_ino = sb['sb_rootino']
            _xfs_extract_recursive(f, part_offset, sb, root_ino, '/',
                                   dest, path_filter, stats)

        lines = [
            f"**Extracted from {fs_type.upper()}** to `{dest}`",
            f"  Files: {stats['files']}",
            f"  Directories: {stats['dirs']}",
            f"  Symlinks: {stats['symlinks']}",
        ]
        if stats['errors']:
            lines.append(f"  Errors: {stats['errors']}")
        return '\n'.join(lines)


def _efs_extract_recursive(f, part_offset, sb, ino_num, path,
                           dest_dir, path_filter, stats):
    """Recursively extract files from EFS."""
    inode = efs_read_inode(f, part_offset, sb, ino_num)
    if not inode:
        return

    mode = inode['mode']
    ft = mode & S_IFMT

    in_scope = _in_scope(path, path_filter)

    if ft == S_IFDIR:
        entries = efs_read_dir_entries(f, part_offset, sb, inode)
        if in_scope and path != '/':
            rel = _rel_path(path, path_filter)
            if rel:
                os.makedirs(os.path.join(dest_dir, rel), exist_ok=True)
                stats['dirs'] += 1
        for name, child_ino in entries:
            child_path = path.rstrip('/') + '/' + name
            _efs_extract_recursive(f, part_offset, sb, child_ino, child_path,
                                   dest_dir, path_filter, stats)
    elif in_scope:
        rel = _rel_path(path, path_filter)
        if not rel:
            return
        host_path = os.path.join(dest_dir, rel)
        os.makedirs(os.path.dirname(host_path), exist_ok=True)
        try:
            if ft == S_IFLNK:
                target = _efs_read_symlink(f, part_offset, sb, inode)
                if os.path.lexists(host_path):
                    os.unlink(host_path)
                os.symlink(target, host_path)
                stats['symlinks'] += 1
            elif ft == S_IFREG:
                data = efs_read_file_data(f, part_offset, sb, inode)
                with open(host_path, 'wb') as out:
                    out.write(data)
                stats['files'] += 1
        except Exception:
            stats['errors'] += 1


def _xfs_extract_recursive(f, part_offset, sb, ino_num, path,
                           dest_dir, path_filter, stats):
    """Recursively extract files from XFS."""
    inode = xfs_read_inode(f, part_offset, sb, ino_num)
    if not inode:
        return

    mode = inode['di_mode']
    ft = mode & S_IFMT

    in_scope = _in_scope(path, path_filter)

    if ft == S_IFDIR:
        entries = xfs_read_dir_entries(f, part_offset, sb, inode)
        if in_scope and path != '/':
            rel = _rel_path(path, path_filter)
            if rel:
                os.makedirs(os.path.join(dest_dir, rel), exist_ok=True)
                stats['dirs'] += 1
        for name, child_ino in entries:
            child_path = path.rstrip('/') + '/' + name
            _xfs_extract_recursive(f, part_offset, sb, child_ino, child_path,
                                   dest_dir, path_filter, stats)
    elif in_scope:
        rel = _rel_path(path, path_filter)
        if not rel:
            return
        host_path = os.path.join(dest_dir, rel)
        os.makedirs(os.path.dirname(host_path), exist_ok=True)
        try:
            if ft == S_IFLNK:
                target = _xfs_read_symlink(f, part_offset, sb, inode)
                if os.path.lexists(host_path):
                    os.unlink(host_path)
                os.symlink(target, host_path)
                stats['symlinks'] += 1
            elif ft == S_IFREG:
                data = xfs_read_file_data(f, part_offset, sb, inode)
                with open(host_path, 'wb') as out:
                    out.write(data)
                stats['files'] += 1
        except Exception:
            stats['errors'] += 1


def _fs_inject_xfs(image_path, host_path, guest_path, uid, gid, mode):
    """Inject a file into an XFS partition via pyirix.xfs (V1 XFS write
    support, validated by tests/test_xfs_write.py).  Creates or
    overwrites guest_path.  NOTE: on qcow2 images this flattens any
    backing chain (convert-to-raw round trip in open_disk_image)."""
    from pyirix.xfs.image import (open_disk_image as xfs_open_disk_image,
                                  find_xfs_partition as xfs_find_part)
    from pyirix.xfs.superblock import read_superblock as xfs_read_sb
    from pyirix.xfs.operations import (resolve_path as xfs_resolve_path,
                                       create_file as xfs_create_file,
                                       write_file as xfs_write_file)

    with open(host_path, 'rb') as hf:
        data = hf.read()

    with xfs_open_disk_image(image_path, writable=True) as f:
        part = xfs_find_part(f)
        if not part:
            return "Error: No XFS partition found"
        part_offset, _part_size = part
        sb = xfs_read_sb(f, part_offset)
        if sb is None:
            return "Error: Cannot read XFS superblock"
        existing = xfs_resolve_path(f, part_offset, sb, guest_path)
        if existing is not None:
            xfs_write_file(f, part_offset, sb, guest_path, data)
            return (f"Overwrote {guest_path} ({len(data)} bytes) in XFS "
                    f"partition of {image_path}")
        ino = xfs_create_file(f, part_offset, sb, guest_path, data,
                              mode=(mode if mode else 0o644),
                              uid=uid, gid=gid)
        return (f"Created {guest_path} (inode {ino}, {len(data)} bytes) in "
                f"XFS partition of {image_path}")


def fs_inject(image_path, host_path, guest_path, uid=0, gid=0, mode=None):
    """Add a file from host into an EFS or XFS partition.

    EFS uses a rebuild approach: extract all files, add the new one,
    rebuild.  XFS delegates to pyirix.xfs.operations (in-place write).
    """
    if not os.path.exists(host_path):
        return f"Error: Host file not found: {host_path}"

    # XFS-only images (no EFS data partition) take the pyirix.xfs path.
    try:
        with open_disk_image(image_path) as probe:
            has_efs = find_efs_partition(probe) is not None
            has_xfs = find_xfs_partition(probe) is not None
    except Exception as e:
        return f"Error: cannot open image: {e}"
    if has_xfs and not has_efs:
        try:
            return _fs_inject_xfs(image_path, host_path, guest_path,
                                  uid, gid, mode)
        except Exception as e:
            return f"Error: XFS inject failed: {e}"

    # Import EFSBuilder for rebuild
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / 'analysis_tools'))
    try:
        from tar2efs import EFSBuilder, S_IFREG as T_IFREG, S_IFDIR as T_IFDIR
    except ImportError:
        return "Error: analysis_tools/tar2efs.py not found (needed for EFS write)"

    with open_disk_image(image_path, writable=True) as f:
        # Verify it's EFS
        vh = read_vh(f)
        if not vh:
            return "Error: No SGI volume header found"

        efs_part = find_efs_partition(f)
        if not efs_part:
            return "Error: No EFS partition found"

        xfs_part = find_xfs_partition(f)
        if xfs_part:
            # If there's also an XFS partition and no EFS, it's XFS-only
            if not efs_part:
                return "Error: XFS write is not supported. Use EFS images for inject."

        part_offset, part_size = efs_part
        sb = efs_read_superblock(f, part_offset)
        if not sb:
            return "Error: Cannot read EFS superblock"

        # Extract all current files to a temp dir
        tmpdir = tempfile.mkdtemp(prefix='efs_inject_')
        try:
            stats = {'files': 0, 'dirs': 0, 'symlinks': 0, 'errors': 0}
            _efs_extract_recursive(f, part_offset, sb, EFS_ROOT_INODE, '/',
                                   tmpdir, None, stats)

            # Add the new file
            guest_path = '/' + guest_path.lstrip('/')
            host_dest = os.path.join(tmpdir, guest_path.lstrip('/'))
            os.makedirs(os.path.dirname(host_dest), exist_ok=True)
            with open(host_path, 'rb') as src:
                file_data = src.read()
            with open(host_dest, 'wb') as dst:
                dst.write(file_data)

            # Rebuild the EFS partition
            import time
            size_blocks = part_size // EFS_BLOCK_SIZE
            size_mb = max(1, part_size // (1024 * 1024))
            builder = EFSBuilder(size_mb)

            # Walk the temp dir and add everything to the builder
            for root, dirs, files in os.walk(tmpdir):
                rel_root = os.path.relpath(root, tmpdir)
                if rel_root == '.':
                    rel_root = ''

                for d in dirs:
                    dir_path = '/' + os.path.join(rel_root, d) if rel_root else '/' + d
                    builder.add_directory(dir_path, 0o755, 0, 0, int(time.time()))

                for fname in files:
                    host_file = os.path.join(root, fname)
                    file_path = '/' + os.path.join(rel_root, fname) if rel_root else '/' + fname

                    if os.path.islink(host_file):
                        target = os.readlink(host_file)
                        builder.add_file(file_path, S_IFLNK | 0o777, uid, gid,
                                         len(target), int(time.time()),
                                         b'', link_target=target)
                    else:
                        with open(host_file, 'rb') as fdata:
                            data = fdata.read()
                        fmode = mode if (mode is not None and
                                         file_path == guest_path) else 0o644
                        fuid = uid if file_path == guest_path else 0
                        fgid = gid if file_path == guest_path else 0
                        builder.add_file(file_path, S_IFREG | fmode, fuid, fgid,
                                         len(data), int(time.time()), data)

            # Build to a temp file then write back
            tmp_efs = os.path.join(tmpdir, 'rebuilt.efs')
            builder.build(tmp_efs)

            # Read rebuilt data and write to partition
            with open(tmp_efs, 'rb') as rebuilt:
                efs_data = rebuilt.read()

            if len(efs_data) > part_size:
                return (f"Error: Rebuilt EFS ({len(efs_data)} bytes) exceeds "
                        f"partition size ({part_size} bytes)")

            f.seek(part_offset)
            f.write(efs_data)
            # Pad remainder with zeros
            remaining = part_size - len(efs_data)
            if remaining > 0:
                f.write(b'\x00' * remaining)

            return (f"**Injected** `{os.path.basename(host_path)}` into "
                    f"`{guest_path}` ({len(file_data)} bytes)")

        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── XFS Analysis Tools ───────────────────────────────────────────────

# Version constants
XFS_SB_VERSION_OKSASHBITS = 0x3FFF  # bits accepted by PROM/SASH for version 4

_XFS_VERSION_BITS = [
    (0x0010, 'ATTRBIT',    'Extended attributes'),
    (0x0020, 'NLINKBIT',   '32-bit link count'),
    (0x0040, 'QUOTABIT',   'Disk quota'),
    (0x0080, 'ALIGNBIT',   'Inode alignment'),
    (0x0100, 'DALIGNBIT',  'Data alignment'),
    (0x0200, 'SHAREDBIT',  'Shared filesystem'),
    (0x1000, 'EXTFLGBIT',  'Extent flag'),
    (0x2000, 'DIRV2BIT',   'Directory v2 format'),
]

_XFS_DINODE_FMT_NAMES = {
    0: 'FMT_DEV', 1: 'FMT_LOCAL', 2: 'FMT_EXTENTS',
    3: 'FMT_BTREE', 4: 'FMT_UUID',
}

_XFS_SB_FIELDS = [
    # (offset, size, name, format)  format: 'I'=uint32, 'H'=uint16, 'B'=uint8, 'Q'=uint64, 's6'=6-byte str
    (0,   4, 'sb_magicnum',    'I'),
    (4,   4, 'sb_blocksize',   'I'),
    (8,   8, 'sb_dblocks',     'Q'),
    (16,  8, 'sb_rblocks',     'Q'),
    (24,  8, 'sb_rextents',    'Q'),
    (32,  16, 'sb_uuid',       'uuid'),
    (48,  8, 'sb_logstart',    'Q'),
    (56,  8, 'sb_rootino',     'Q'),
    (64,  8, 'sb_rbmino',      'Q'),
    (72,  8, 'sb_rsumino',     'Q'),
    (80,  4, 'sb_rextsize',    'I'),
    (84,  4, 'sb_agblocks',    'I'),
    (88,  4, 'sb_agcount',     'I'),
    (92,  4, 'sb_rbmblocks',   'I'),
    (96,  4, 'sb_logblocks',   'I'),
    (100, 2, 'sb_versionnum',  'H'),
    (102, 2, 'sb_sectsize',    'H'),
    (104, 2, 'sb_inodesize',   'H'),
    (106, 2, 'sb_inopblock',   'H'),
    (108, 6, 'sb_fname',       's6'),
    (114, 6, 'sb_fpack',       's6'),
    (120, 1, 'sb_blocklog',    'B'),
    (121, 1, 'sb_sectlog',     'B'),
    (122, 1, 'sb_inodelog',    'B'),
    (123, 1, 'sb_inopblog',    'B'),
    (124, 1, 'sb_agblklog',    'B'),
    (125, 1, 'sb_rextslog',    'B'),
    (126, 1, 'sb_inprogress',  'B'),
    (127, 1, 'sb_imax_pct',    'B'),
    (128, 8, 'sb_icount',      'Q'),
    (136, 8, 'sb_ifree',       'Q'),
    (144, 8, 'sb_fdblocks',    'Q'),
    (152, 8, 'sb_frextents',   'Q'),
    (160, 8, 'sb_uquotino',    'Q'),
    (168, 8, 'sb_gquotino',    'Q'),
    (176, 2, 'sb_qflags',      'H'),
    (178, 1, 'sb_flags',       'B'),
    (179, 1, 'sb_shared_vn',   'B'),
    (180, 4, 'sb_inoalignmt',  'I'),
    (184, 4, 'sb_unit',        'I'),
    (188, 4, 'sb_width',       'I'),
    (192, 1, 'sb_dirblklog',   'B'),
]


def _xfs_sash_compat(vn):
    """Return (accepted: bool, reason: str) for PROM/SASH version check."""
    version_num = vn & 0xf
    if 1 <= vn <= 3:
        return True, f"v{vn} — accepted unconditionally"
    if version_num == 4:
        feature_bits = vn & 0xfff0
        bad = vn & ~XFS_SB_VERSION_OKSASHBITS
        if bad == 0:
            return True, f"v4 + feature bits 0x{feature_bits:04x} — all within 0x3FFF"
        return False, f"v4 but bits 0x{bad:04x} outside OKSASHBITS (0x3FFF)"
    return False, f"version_num={version_num} (>4 rejected by PROM/SASH)"


def xfs_superblock(image_path, partition=None):
    """Detailed XFS superblock dump with field-by-field annotation and SASH compat check."""
    try:
        with open_disk_image(image_path) as f:
            xfs_part = find_xfs_partition(f)
            if not xfs_part:
                return "No XFS partition found in disk image."
            part_offset, part_size = xfs_part
            f.seek(part_offset)
            raw = f.read(256)
    except Exception as e:
        return f"Error opening image: {e}"

    if len(raw) < 200:
        return f"Could not read superblock (got {len(raw)} bytes)."
    magic = struct.unpack('>I', raw[0:4])[0]
    if magic != XFS_SB_MAGIC:
        return f"No XFS superblock found (magic=0x{magic:08x}, expected 0x{XFS_SB_MAGIC:08x})."

    lba = part_offset // 512
    lines = [
        f"## XFS Superblock — `{image_path}`",
        f"",
        f"Partition: XFS at LBA {lba} (byte offset 0x{part_offset:x}, "
        f"size {part_size // (1024*1024)} MB)",
        f"",
        f"### Raw Hex (first 128 bytes)",
        f"```",
        _hex_dump(raw[:128], 16),
        f"```",
        f"",
        f"### Fields",
        f"```",
        f"{'Off':>4}  {'Size':>4}  {'Field':<18}  {'Raw Hex':<18}  Interpreted",
        f"{'---':>4}  {'----':>4}  {'-----':<18}  {'-------':<18}  -----------",
    ]

    def parse_field(off, size, fmt):
        chunk = raw[off:off+size]
        if fmt == 'Q':
            return struct.unpack('>Q', chunk)[0], chunk.hex()
        elif fmt == 'I':
            return struct.unpack('>I', chunk)[0], chunk.hex()
        elif fmt == 'H':
            return struct.unpack('>H', chunk)[0], chunk.hex()
        elif fmt == 'B':
            return chunk[0], chunk.hex()
        elif fmt in ('s6', 'uuid'):
            s = chunk.rstrip(b'\x00')
            try:
                return s.decode('ascii', errors='replace'), chunk.hex()
            except Exception:
                return repr(chunk), chunk.hex()
        return None, chunk.hex()

    parsed = {}
    for off, size, name, fmt in _XFS_SB_FIELDS:
        if off + size > len(raw):
            continue
        val, hexstr = parse_field(off, size, fmt)
        parsed[name] = val
        note = ''
        if name == 'sb_magicnum':
            note = "✓ 'XFSB'" if val == XFS_SB_MAGIC else "✗ WRONG MAGIC"
        elif name == 'sb_versionnum':
            ok, reason = _xfs_sash_compat(val)
            note = f"{'✓' if ok else '✗'} {reason}"
        elif name == 'sb_rootino':
            note = f"root directory inode"
        elif name == 'sb_blocksize':
            note = f"({val // 1024}KB)" if val >= 1024 else f"({val}B)"
        lines.append(f"{off:>4}  {size:>4}  {name:<18}  {hexstr:<18}  {val!s:<16} {note}")

    lines.append("```")

    # Version analysis
    vn = parsed.get('sb_versionnum', 0)
    version_num = vn & 0xf
    feature_bits = vn & 0xfff0
    ok, reason = _xfs_sash_compat(vn)

    lines += [
        "",
        "### Version Analysis",
        f"  `sb_versionnum` = 0x{vn:04x}",
        f"  version_num (bits[3:0]) = {version_num}",
        f"  feature bits (bits[15:4]) = 0x{feature_bits:04x}",
    ]
    for bit, bname, bdesc in _XFS_VERSION_BITS:
        if feature_bits & bit:
            lines.append(f"    {bname:<12} (0x{bit:04x})  {bdesc}")

    bad = vn & ~XFS_SB_VERSION_OKSASHBITS
    if bad:
        lines.append(f"  **Bits outside 0x3FFF: 0x{bad:04x} → PROM will REJECT**")

    lines += [
        "",
        f"### PROM/SASH Compatibility: {'✓ ACCEPTED' if ok else '✗ REJECTED'}",
        f"  {reason}",
        "",
        f"### Key Values",
        f"  Root inode:  {parsed.get('sb_rootino', '?')}",
        f"  Block size:  {parsed.get('sb_blocksize', '?')} bytes",
        f"  AG count:    {parsed.get('sb_agcount', '?')}",
        f"  AG blocks:   {parsed.get('sb_agblocks', '?')}",
        f"  Inode size:  {parsed.get('sb_inodesize', '?')} bytes",
        f"  Inodes/blk:  {parsed.get('sb_inopblock', '?')}",
        f"  Free blocks: {parsed.get('sb_fdblocks', '?')}",
        f"  Free inodes: {parsed.get('sb_ifree', '?')}",
    ]
    return '\n'.join(lines)


def xfs_inode(image_path, inode_num, partition=None):
    """Dump and annotate a single XFS inode: core fields, fork data, and directory entries."""
    try:
        with open_disk_image(image_path) as f:
            xfs_part = find_xfs_partition(f)
            if not xfs_part:
                return "No XFS partition found."
            part_offset, _ = xfs_part
            sb = xfs_read_superblock(f, part_offset)
            if not sb:
                return "Could not read XFS superblock."

            disk_offset = _xfs_ino_to_offset(sb, inode_num, part_offset)
            f.seek(disk_offset)
            raw = f.read(sb['sb_inodesize'])
            if not raw or len(raw) < 96:
                return f"Could not read inode {inode_num} at offset 0x{disk_offset:x}."

            inode = xfs_read_inode(f, part_offset, sb, inode_num)

            if not raw or struct.unpack('>H', raw[0:2])[0] != XFS_DINODE_MAGIC:
                return (f"Inode {inode_num}: bad magic "
                        f"(got 0x{struct.unpack('>H', raw[0:2])[0]:04x}, expected 0x{XFS_DINODE_MAGIC:04x})")

            agblklog = sb['sb_agblklog']
            inopblog = sb['sb_inopblog']
            agno = inode_num >> (agblklog + inopblog)
            agino = inode_num & ((1 << (agblklog + inopblog)) - 1)
            agbno = agino >> inopblog
            ino_off = agino & ((1 << inopblog) - 1)

            mode = inode['di_mode']
            fmt = inode['di_format']
            fmt_name = _XFS_DINODE_FMT_NAMES.get(fmt, f'FMT_{fmt}')
            ft = _format_type(mode)
            perms = _format_perms(mode)

            lines = [
                f"## XFS Inode {inode_num} — `{image_path}`",
                f"",
                f"Disk offset: 0x{disk_offset:x} (AG {agno}, block {agbno}, slot {ino_off})",
                f"",
                f"### Inode Core",
                f"```",
                f"  di_magic    0x{struct.unpack('>H', raw[0:2])[0]:04x}  ✓ 'IN'",
                f"  di_mode     0o{mode:06o}  ({ft}{perms})",
                f"  di_version  {inode['di_version']}",
                f"  di_format   {fmt}  ({fmt_name})",
                f"  di_uid      {inode['di_uid']}    di_gid {inode['di_gid']}",
                f"  di_nlink    {inode['di_nlink']}",
                f"  di_size     {inode['di_size']}",
                f"  di_nblocks  {inode['di_nblocks']}",
                f"  di_nextents {inode['di_nextents']}",
                f"  di_forkoff  {inode['di_forkoff']}",
                f"```",
            ]

            # Data fork raw bytes
            fork_off = inode['_data_fork_offset']
            fork_end = (inode['di_forkoff'] * 8 + fork_off) if inode['di_forkoff'] else len(raw)
            fork_data = raw[fork_off:fork_end]

            lines += ["", f"### Data Fork ({fmt_name}, {len(fork_data)} bytes)"]

            if fmt == XFS_DINODE_FMT_LOCAL:
                lines.append("```")
                if len(fork_data) <= 256:
                    lines.append(_hex_dump(fork_data))
                else:
                    lines.append(_hex_dump(fork_data[:256]))
                    lines.append(f"... ({len(fork_data)} bytes total)")
                lines.append("```")

            elif fmt == XFS_DINODE_FMT_EXTENTS:
                n_in_fork = len(fork_data) // 16
                n_valid = min(n_in_fork, inode['di_nextents'])
                lines.append(f"```")
                lines.append(f"  {'#':>3}  {'startoff':>14}  {'startblock':>12}  {'count':>8}  {'flag':>4}")
                for i in range(min(n_valid, 64)):
                    rec = fork_data[i*16:(i+1)*16]
                    if len(rec) < 16:
                        break
                    startoff, startblock, blockcount, flag = _xfs_parse_bmbt_rec(rec)
                    lines.append(f"  {i:>3}  {startoff:>14}  0x{startblock:010x}  {blockcount:>8}  {flag:>4}")
                if n_valid > 64:
                    lines.append(f"  ... ({n_valid} extents total, showing first 64)")
                lines.append("```")

            elif fmt == XFS_DINODE_FMT_BTREE:
                lines += [
                    "```",
                    f"  B+tree root in fork ({len(fork_data)} bytes)",
                    _hex_dump(fork_data[:64]),
                    "```",
                ]

            # Directory entries — read while file is still open
            is_dir = (mode & S_IFMT) == S_IFDIR
            if is_dir:
                try:
                    entries = xfs_read_dir_entries(f, part_offset, sb, inode)
                    lines += ["", f"### Directory Entries ({len(entries)})", "```"]
                    for name, child_ino in entries[:200]:
                        lines.append(f"  {name:<40} → {child_ino}")
                    if len(entries) > 200:
                        lines.append(f"  ... ({len(entries)} total, showing first 200)")
                    lines.append("```")
                except Exception as e:
                    lines.append(f"(Error reading directory entries: {e})")

            # Raw hex dump (first 128 bytes)
            lines += ["", "### Raw Inode Bytes (first 128)", "```", _hex_dump(raw[:128]), "```"]
    except Exception as e:
        return f"Error: {e}"

    return '\n'.join(lines)


def xfs_path(image_path, path, partition=None):
    """Walk an XFS path component by component, showing each directory lookup step."""
    try:
        with open_disk_image(image_path) as f:
            xfs_part = find_xfs_partition(f)
            if not xfs_part:
                return "No XFS partition found."
            part_offset, _ = xfs_part
            sb = xfs_read_superblock(f, part_offset)
            if not sb:
                return "Could not read XFS superblock."

            parts = [p for p in path.strip('/').split('/') if p]
            root_ino = sb['sb_rootino']

            lines = [
                f"## XFS Path Walk: `{path}` — `{image_path}`",
                f"",
                f"Root inode: {root_ino}",
                f"Path components: {parts if parts else ['(root)']}",
                f"",
            ]

            if not parts:
                inode = xfs_read_inode(f, part_offset, sb, root_ino)
                mode = inode['di_mode'] if inode else 0
                lines += [
                    f"Resolved to root inode {root_ino}",
                    f"Type: {_format_type(mode)}{_format_perms(mode)}  "
                    f"size={inode['di_size'] if inode else '?'}",
                    f"",
                    f"**RESULT: SUCCESS** (path is root)",
                ]
                return '\n'.join(lines)

            current_ino = root_ino
            for step_idx, component in enumerate(parts):
                inode = xfs_read_inode(f, part_offset, sb, current_ino)
                if not inode:
                    lines.append(f"Step {step_idx+1}: inode {current_ino} — **UNREADABLE**")
                    lines.append(f"")
                    lines.append(f"**RESULT: FAIL** (could not read inode {current_ino})")
                    return '\n'.join(lines)

                mode = inode['di_mode']
                if (mode & S_IFMT) != S_IFDIR:
                    lines.append(f"Step {step_idx+1}: inode {current_ino} — "
                                 f"**NOT A DIRECTORY** (mode=0o{mode:o})")
                    lines.append(f"**RESULT: FAIL**")
                    return '\n'.join(lines)

                fmt = inode['di_format']
                fmt_name = _XFS_DINODE_FMT_NAMES.get(fmt, f'FMT_{fmt}')
                entries = xfs_read_dir_entries(f, part_offset, sb, inode)
                lines.append(f"**Step {step_idx+1}**: directory inode {current_ino}  "
                             f"({fmt_name}, {len(entries)} entries)")
                lines.append(f"  Looking for `{component}` among {len(entries)} entries:")

                found_ino = None
                for name, child_ino in entries:
                    marker = "  ✓ FOUND" if name == component else ""
                    lines.append(f"    {name:<40} → {child_ino}{marker}")
                    if name == component:
                        found_ino = child_ino

                if found_ino is None:
                    lines.append(f"")
                    lines.append(f"**RESULT: FAIL** — `{component}` not found in inode {current_ino}")
                    return '\n'.join(lines)

                current_ino = found_ino
                lines.append(f"  → resolved `{component}` to inode {current_ino}")
                lines.append(f"")

            # Final inode info
            final_inode = xfs_read_inode(f, part_offset, sb, current_ino)
            if final_inode:
                mode = final_inode['di_mode']
                fmt = final_inode['di_format']
                fmt_name = _XFS_DINODE_FMT_NAMES.get(fmt, f'FMT_{fmt}')
                lines += [
                    f"**Resolved**: `{path}` → inode {current_ino}",
                    f"Type: {_format_type(mode)}{_format_perms(mode)}  "
                    f"size={final_inode['di_size']}  {fmt_name}  "
                    f"nextents={final_inode['di_nextents']}",
                    f"",
                    f"**RESULT: SUCCESS**",
                ]
            else:
                lines += [
                    f"**Resolved**: `{path}` → inode {current_ino} (unreadable)",
                    f"**RESULT: PARTIAL** (path found but final inode unreadable)",
                ]
    except Exception as e:
        return f"Error: {e}"

    return '\n'.join(lines)


def xfs_block(image_path, fsblock, partition=None):
    """Dump a raw XFS filesystem block by fsblock address, with format detection."""
    try:
        with open_disk_image(image_path) as f:
            xfs_part = find_xfs_partition(f)
            if not xfs_part:
                return "No XFS partition found."
            part_offset, _ = xfs_part
            sb = xfs_read_superblock(f, part_offset)
            if not sb:
                return "Could not read XFS superblock."

            blocksize = sb['sb_blocksize']
            agblklog = sb['sb_agblklog']
            agblocks = sb['sb_agblocks']

            agno = fsblock >> agblklog
            agbno = fsblock & ((1 << agblklog) - 1)
            phys_block = agno * agblocks + agbno
            disk_offset = part_offset + phys_block * blocksize

            f.seek(disk_offset)
            data = f.read(blocksize)
    except Exception as e:
        return f"Error: {e}"

    if not data:
        return f"Could not read block at disk offset 0x{disk_offset:x}."

    # Detect block type
    block_type = "unknown"
    annotation = []
    if len(data) >= 4:
        magic4 = struct.unpack('>I', data[0:4])[0]
        magic2_at8 = struct.unpack('>H', data[8:10])[0] if len(data) >= 10 else 0
        if magic4 == 0x58414742:  # 'XAGB' AGF
            block_type = "AGF (Allocation Group Free block list)"
        elif magic4 == 0x58414749:  # 'XAGI' AGI
            block_type = "AGI (Allocation Group Inode header)"
        elif magic4 == XFS_DIR2_BLOCK_MAGIC:
            block_type = "XFS dir2 block ('XD2B')"
        elif magic4 == XFS_DIR2_DATA_MAGIC:
            block_type = "XFS dir2 data ('XD2D')"
            if len(data) >= 16:
                best_free_0 = struct.unpack('>H', data[12:14])[0]
                annotation.append(f"  bestfree[0].offset = 0x{best_free_0:04x}")
        elif magic4 == 0x58443246:  # 'XD2F'
            block_type = "XFS dir2 freespace ('XD2F')"
        elif magic4 == XFS_BMAP_MAGIC:
            block_type = "XFS BMap B+tree ('BMAP')"
        elif magic2_at8 == XFS_DIR_LEAF_MAGIC:
            block_type = "XFS V1 leaf directory (0xfeeb at offset 8)"
            annotation.append(f"  V1 leaf: magic=0xfeeb at offset 8 (in xfs_da_blkinfo_t)")
        elif magic4 == XFS_SB_MAGIC:
            block_type = "XFS Superblock ('XFSB') — AG secondary superblock"
        elif magic4 == XFS_DINODE_MAGIC or (magic4 >> 16) == XFS_DINODE_MAGIC:
            block_type = "XFS Inode block"

    lines = [
        f"## XFS Block 0x{fsblock:x} — `{image_path}`",
        f"",
        f"fsblock = 0x{fsblock:x}",
        f"  agno  = {agno}",
        f"  agbno = {agbno}",
        f"  disk offset = 0x{disk_offset:x} (physical block {phys_block})",
        f"  block size  = {blocksize} bytes",
        f"",
        f"Detected type: **{block_type}**",
    ]
    if annotation:
        lines.extend(annotation)
    lines += [
        f"",
        f"### Hex dump",
        f"```",
        _hex_dump(data, 16),
        f"```",
    ]
    return '\n'.join(lines)


def xfs_check(image_path, partition=None):
    """Comprehensive XFS consistency check including PROM/SASH compatibility."""
    results = []   # list of (status, message)

    def chk(ok, msg):
        results.append(('PASS' if ok else 'FAIL', msg))
        return ok

    def info(msg):
        results.append(('INFO', msg))

    try:
        with open_disk_image(image_path) as f:
            # 1. Volume header
            f.seek(0)
            vh_data = f.read(512)
            if len(vh_data) >= 4:
                vh_magic = struct.unpack('>I', vh_data[0:4])[0]
                chk(vh_magic == VHMAGIC,
                    f"Volume header magic: 0x{vh_magic:08x} "
                    f"({'OK' if vh_magic == VHMAGIC else f'expected 0x{VHMAGIC:08x}'})")
            else:
                chk(False, "Could not read volume header")

            # 2. XFS partition
            xfs_part = find_xfs_partition(f)
            if not chk(xfs_part is not None, "XFS partition found (type 10 in volume header)"):
                _show_results(results)
                return _format_check_results(image_path, results)
            part_offset, part_size = xfs_part
            info(f"XFS partition: LBA {part_offset // 512}, "
                 f"size {part_size // (1024*1024)} MB")

            # 3. Superblock
            sb = xfs_read_superblock(f, part_offset)
            if not chk(sb is not None, "XFS superblock readable"):
                return _format_check_results(image_path, results)

            f.seek(part_offset)
            sb_raw = f.read(8)
            magic = struct.unpack('>I', sb_raw[0:4])[0]
            chk(magic == XFS_SB_MAGIC,
                f"Superblock magic: 0x{magic:08x} ('XFSB')")

            # 4. Version / SASH compatibility
            vn = sb['sb_versionnum']
            ok, reason = _xfs_sash_compat(vn)
            chk(ok, f"PROM/SASH version check: 0x{vn:04x} — {reason}")

            # 5. Root inode
            root_ino = sb['sb_rootino']
            info(f"Root inode: {root_ino}")
            root_inode = xfs_read_inode(f, part_offset, sb, root_ino)
            if not chk(root_inode is not None, f"Root inode {root_ino} readable"):
                return _format_check_results(image_path, results)
            chk((root_inode['di_mode'] & S_IFMT) == S_IFDIR,
                f"Root inode is a directory (mode=0o{root_inode['di_mode']:06o})")

            # 6. Root directory entries
            root_entries = xfs_read_dir_entries(f, part_offset, sb, root_inode)
            chk(len(root_entries) > 0,
                f"Root directory readable ({len(root_entries)} entries)")

            # 7. Path probes
            for probe_path in ['/unix', '/unix.new', '/stand', '/sash']:
                try:
                    ino = _xfs_resolve_path(f, part_offset, sb, probe_path)
                    if ino is not None:
                        probe_inode = xfs_read_inode(f, part_offset, sb, ino)
                        if probe_inode:
                            ft = _format_type(probe_inode['di_mode'])
                            sz = probe_inode['di_size']
                            sz_str = (f"{sz // (1024*1024)}.{(sz % (1024*1024)) // 102400}MB"
                                      if sz >= 1024*1024 else f"{sz}B")
                            boot_note = " ← PROM boot target" if probe_path == '/unix.new' else ""
                            info(f"Path {probe_path:<12} → inode {ino} ({ft}, {sz_str}){boot_note}")
                        else:
                            info(f"Path {probe_path:<12} → inode {ino} (unreadable)")
                    else:
                        info(f"Path {probe_path:<12} → not found")
                except Exception as e:
                    info(f"Path {probe_path:<12} → error: {e}")

    except Exception as e:
        results.append(('FAIL', f"Unexpected error: {e}"))

    return _format_check_results(image_path, results)


def _format_check_results(image_path, results):
    """Format xfs_check results as a report."""
    passes = sum(1 for s, _ in results if s == 'PASS')
    fails = sum(1 for s, _ in results if s == 'FAIL')
    lines = [
        f"## XFS Filesystem Check — `{image_path}`",
        f"",
    ]
    for status, msg in results:
        icon = {'PASS': '✓', 'FAIL': '✗', 'INFO': '·'}.get(status, ' ')
        lines.append(f"  [{status}] {icon} {msg}")
    lines += [
        f"",
        f"**Overall: {passes} PASS, {fails} FAIL**",
        f"{'✓ Ready for PROM boot' if fails == 0 else '✗ Issues found — see FAILs above'}",
    ]
    return '\n'.join(lines)


def xfs_repair_superblock(image_path, field, value, partition=None, dry_run=True):
    """Patch a single XFS superblock field.

    Supported fields: versionnum (offset 100, uint16), blocksize (offset 4, uint32),
    agcount (offset 88, uint32).

    With dry_run=True (default): shows what would change without writing.
    With dry_run=False: converts qcow2 to raw, patches the raw file in-place.
    The original qcow2 is NEVER modified. The patched raw file is left at
    <image>.patched.raw for inspection; use qemu-img convert to create a new qcow2.
    """
    SUPPORTED = {
        'versionnum': (100, 2, '>H'),
        'blocksize':  (4,   4, '>I'),
        'agcount':    (88,  4, '>I'),
    }
    if field not in SUPPORTED:
        return (f"Field `{field}` not supported. Supported fields: "
                + ", ".join(SUPPORTED.keys()))

    field_offset, field_size, field_fmt = SUPPORTED[field]

    try:
        with open_disk_image(image_path) as f:
            xfs_part = find_xfs_partition(f)
            if not xfs_part:
                return "No XFS partition found."
            part_offset, _ = xfs_part
            sb = xfs_read_superblock(f, part_offset)
            if not sb:
                return "Could not read XFS superblock."

            # Read current value
            f.seek(part_offset + field_offset)
            cur_bytes = f.read(field_size)
    except Exception as e:
        return f"Error reading image: {e}"

    cur_val = struct.unpack(field_fmt, cur_bytes)[0]
    new_bytes = struct.pack(field_fmt, value)

    # Version compatibility preview
    extra = []
    if field == 'versionnum':
        cur_ok, cur_reason = _xfs_sash_compat(cur_val)
        new_ok, new_reason = _xfs_sash_compat(value)
        extra = [
            f"  Current: 0x{cur_val:04x} — {'✓' if cur_ok else '✗'} {cur_reason}",
            f"  New:     0x{value:04x} — {'✓' if new_ok else '✗'} {new_reason}",
        ]

    lines = [
        f"## XFS Superblock Repair — `{image_path}`",
        f"",
        f"Field:         `{field}` (offset {field_offset}, {field_size} bytes, {field_fmt})",
        f"Current value: 0x{cur_val:0{field_size*2}x} ({cur_val})",
        f"New value:     0x{value:0{field_size*2}x} ({value})",
    ]
    lines.extend(extra)

    if cur_val == value:
        lines.append(f"\nField already has value 0x{value:x} — no change needed.")
        return '\n'.join(lines)

    if dry_run:
        lines += [
            f"",
            f"**[DRY RUN]** No changes made.",
            f"To apply: call with `dry_run=False`",
            f"",
            f"Note: if image is qcow2, a `.patched.raw` file will be created alongside it.",
        ]
        return '\n'.join(lines)

    # Apply the patch
    try:
        is_qcow = _is_qcow2(image_path)
        if is_qcow:
            qemu_img = _find_qemu_img()
            raw_path = image_path.replace('.qcow2', '').replace('.img', '') + '.patched.raw'
            result = subprocess.run(
                [qemu_img, 'convert', '-f', 'qcow2', '-O', 'raw', image_path, raw_path],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                return f"qemu-img convert failed:\n{result.stderr}"
            target_path = raw_path
        else:
            # Backup
            import shutil
            bak_path = image_path + '.bak'
            shutil.copy2(image_path, bak_path)
            lines.append(f"Backup created: `{bak_path}`")
            target_path = image_path

        # Patch
        write_offset = part_offset + field_offset
        with open(target_path, 'r+b') as f:
            f.seek(write_offset)
            f.write(new_bytes)

        lines += [
            f"",
            f"✓ **Patched** `{target_path}` at offset 0x{write_offset:x}",
            f"  Wrote: {new_bytes.hex()} (was: {cur_bytes.hex()})",
        ]
        if is_qcow:
            lines += [
                f"",
                f"Patched raw file: `{raw_path}`",
                f"To create a new qcow2:",
                f"```",
                f"qemu-img convert -f raw -O qcow2 {raw_path} new.qcow2",
                f"```",
            ]
    except Exception as e:
        lines.append(f"\n✗ **Error applying patch**: {e}")

    return '\n'.join(lines)


# ── Internal helpers ─────────────────────────────────────────────────

def _find_filesystem(f, partition=None):
    """Find filesystem on disk. Returns (fs_type, part_offset, superblock) or (None, 0, None).

    If partition is specified ('efs' or 'xfs'), look for that type.
    Otherwise auto-detect: try EFS first, then XFS.
    """
    if partition == 'efs' or partition is None:
        efs_part = find_efs_partition(f)
        if efs_part:
            part_offset, part_size = efs_part
            sb = efs_read_superblock(f, part_offset)
            if sb:
                return ('efs', part_offset, sb)

    if partition == 'xfs' or partition is None:
        xfs_part = find_xfs_partition(f)
        if xfs_part:
            part_offset, part_size = xfs_part
            sb = xfs_read_superblock(f, part_offset)
            if sb:
                return ('xfs', part_offset, sb)

    return (None, 0, None)


def _in_scope(path, path_filter):
    """Check if a path is within the filter scope."""
    if not path_filter:
        return True
    stripped = path.lstrip('/')
    filt = path_filter.lstrip('/')
    return (stripped == filt or stripped.startswith(filt + '/') or
            filt.startswith(stripped + '/') or path == '/')


def _rel_path(path, path_filter):
    """Compute relative path for extraction."""
    stripped = path.lstrip('/')
    if not path_filter:
        return stripped
    filt = path_filter.lstrip('/')
    if stripped.startswith(filt + '/'):
        return stripped[len(filt) + 1:]
    elif stripped == filt:
        return os.path.basename(stripped)
    return stripped


def _hex_dump(data, bytes_per_line=16):
    """Format data as a hex dump."""
    lines = []
    for i in range(0, len(data), bytes_per_line):
        chunk = data[i:i + bytes_per_line]
        hex_part = ' '.join(f'{b:02x}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append(f'{i:08x}  {hex_part:<{bytes_per_line * 3 - 1}}  |{ascii_part}|')
    return '\n'.join(lines)
