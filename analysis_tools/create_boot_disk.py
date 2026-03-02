#!/usr/bin/env python3
"""
create_boot_disk.py -- SGI Volume Header disk image tools

Commands:
  extract <disk_image> <vd_name> <output_file>
    Extract a file from the volume directory of a disk image.

  list <disk_image>
    List the volume directory and partition table.

  create <output_image> --size <MB> [--add <name>:<file>] [--bootfile <name>]
    Create a new disk image with a valid SGI volume header.

  copy-vh <source_image> <dest_image> [--size <MB>]
    Copy the volume header (and its files) from one image to another.

Usage examples:
  # List contents of install media
  python -m analysis_tools.create_boot_disk list irix_install_patched.img

  # Extract sash from install media
  python -m analysis_tools.create_boot_disk extract irix_install_patched.img sash sash.ecoff

  # Create a bootable disk with sash
  python -m analysis_tools.create_boot_disk create boot.img --size 64 --add sash:sash.ecoff --bootfile sash

  # Copy volume header from install media
  python -m analysis_tools.create_boot_disk copy-vh irix_install_patched.img boot.img --size 64
"""

import struct
import sys
import os

# SGI Volume Header constants
VHMAGIC = 0x0be5a941
NVDIR = 15
NPARTAB = 16
VDNAMESIZE = 8
BFNAMESIZE = 16
SECTOR_SIZE = 512
VH_SIZE = 512  # Volume header is exactly one sector

# Partition types
PTYPE_VOLHDR = 0
PTYPE_RAW = 3
PTYPE_VOLUME = 6
PTYPE_EFS = 7
PTYPE_XFS = 10


def read_vh(f):
    """Read and parse an SGI volume header from file position 0."""
    f.seek(0)
    data = f.read(VH_SIZE)
    if len(data) < VH_SIZE:
        return None

    magic = struct.unpack('>i', data[0:4])[0]
    if magic != VHMAGIC:
        return None

    vh = {}
    vh['magic'] = magic
    vh['rootpt'] = struct.unpack('>h', data[4:6])[0]
    vh['swappt'] = struct.unpack('>h', data[6:8])[0]
    vh['bootfile'] = data[8:24].split(b'\x00')[0].decode('ascii', errors='replace')

    # Device parameters (24 bytes at offset 24)
    dp_offset = 24
    dp_size = 48  # sizeof(device_parameters)

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

    # Checksum
    csum_offset = pt_offset + NPARTAB * 12
    vh['csum'] = struct.unpack('>i', data[csum_offset:csum_offset+4])[0]

    vh['raw'] = data
    return vh


def compute_checksum(vh_data):
    """Compute SGI volume header checksum."""
    # Zero the checksum field first
    data = bytearray(vh_data)
    # Checksum is at offset: 24 (dp) + 48 (dp_size) + 15*16 (vd) + 16*12 (pt) = 504
    csum_offset = 24 + 48 + NVDIR * 16 + NPARTAB * 12
    struct.pack_into('>i', data, csum_offset, 0)

    # Sum all 32-bit words
    total = 0
    for i in range(0, VH_SIZE, 4):
        val = struct.unpack('>I', data[i:i+4])[0]
        total = (total + val) & 0xFFFFFFFF

    # Store 2's complement
    csum = (-total) & 0xFFFFFFFF
    # Convert to signed
    if csum >= 0x80000000:
        csum = csum - 0x100000000
    return csum


def build_vh(bootfile='', vd_entries=None, pt_entries=None):
    """Build a raw 512-byte volume header."""
    data = bytearray(VH_SIZE)

    # Magic
    struct.pack_into('>i', data, 0, VHMAGIC)
    # rootpt, swappt
    struct.pack_into('>h', data, 4, 0)
    struct.pack_into('>h', data, 6, 1)
    # bootfile
    bf = bootfile.encode('ascii')[:BFNAMESIZE]
    data[8:8+len(bf)] = bf

    # Device parameters (leave mostly zero)
    dp_offset = 24
    # dp_secbytes at offset 24+16 = 40
    struct.pack_into('>H', data, dp_offset + 16, SECTOR_SIZE)

    # Volume directory
    vd_offset = dp_offset + 48
    if vd_entries:
        for i, entry in enumerate(vd_entries[:NVDIR]):
            off = vd_offset + i * 16
            name = entry['name'].encode('ascii')[:VDNAMESIZE]
            data[off:off+len(name)] = name
            struct.pack_into('>ii', data, off+8, entry['lbn'], entry['nbytes'])

    # Partition table
    pt_offset = vd_offset + NVDIR * 16
    if pt_entries:
        for i, entry in enumerate(pt_entries[:NPARTAB]):
            off = pt_offset + i * 12
            struct.pack_into('>iii', data, off,
                             entry['nblks'], entry['firstlbn'], entry['type'])

    # Compute and store checksum
    csum_offset = pt_offset + NPARTAB * 12
    csum = compute_checksum(bytes(data))
    struct.pack_into('>i', data, csum_offset, csum)

    return bytes(data)


def ptype_name(t):
    names = {0: 'volhdr', 3: 'raw', 6: 'volume', 7: 'efs', 10: 'xfs', 11: 'xfslog'}
    return names.get(t, str(t))


def cmd_list(args):
    if len(args) < 1:
        print("Usage: list <disk_image>")
        return 1

    with open(args[0], 'rb') as f:
        vh = read_vh(f)

    if not vh:
        print(f"Error: {args[0]} does not have a valid SGI volume header")
        return 1

    print(f"SGI Volume Header: {args[0]}")
    print(f"  Magic: 0x{vh['magic']:08x}")
    print(f"  Boot file: \"{vh['bootfile']}\"")
    print(f"  Root partition: {vh['rootpt']}")
    print(f"  Swap partition: {vh['swappt']}")

    print("\nVolume Directory:")
    for i, vd in enumerate(vh['vd']):
        if vd['name']:
            print(f"  [{i:2d}] \"{vd['name']}\"\tlbn={vd['lbn']}\t"
                  f"size={vd['nbytes']} ({vd['nbytes']//1024}KB)")

    print("\nPartition Table:")
    for i, pt in enumerate(vh['pt']):
        if pt['nblks'] > 0:
            end_lbn = pt['firstlbn'] + pt['nblks'] - 1
            size_mb = pt['nblks'] * SECTOR_SIZE / (1024 * 1024)
            print(f"  [{i:2d}] type={ptype_name(pt['type']):8s}\t"
                  f"start={pt['firstlbn']}\tblocks={pt['nblks']}\t"
                  f"({size_mb:.1f}MB)")

    return 0


def cmd_extract(args):
    if len(args) < 3:
        print("Usage: extract <disk_image> <vd_name> <output_file>")
        return 1

    disk_path, vd_name, output_path = args[0], args[1], args[2]

    with open(disk_path, 'rb') as f:
        vh = read_vh(f)
        if not vh:
            print(f"Error: {disk_path} does not have a valid SGI volume header")
            return 1

        # Find the entry
        entry = None
        for vd in vh['vd']:
            if vd['name'] == vd_name:
                entry = vd
                break

        if not entry:
            print(f"Error: \"{vd_name}\" not found in volume directory")
            print("Available entries:")
            for vd in vh['vd']:
                if vd['name']:
                    print(f"  \"{vd['name']}\"")
            return 1

        # Read the file data
        offset = entry['lbn'] * SECTOR_SIZE
        f.seek(offset)
        data = f.read(entry['nbytes'])

    with open(output_path, 'wb') as f:
        f.write(data)

    print(f"Extracted \"{vd_name}\" ({len(data)} bytes) to {output_path}")
    return 0


def cmd_create(args):
    import argparse
    parser = argparse.ArgumentParser(description='Create SGI disk image')
    parser.add_argument('output', help='Output disk image path')
    parser.add_argument('--size', type=int, default=64, help='Disk size in MB')
    parser.add_argument('--add', action='append', default=[],
                        help='Add file: name:filepath')
    parser.add_argument('--bootfile', default='', help='Default boot file name')
    parsed = parser.parse_args(args)

    total_sectors = parsed.size * 1024 * 1024 // SECTOR_SIZE
    # VH partition must be large enough for all files in volume directory
    vh_sectors = 4096  # Default VH partition size (will be increased if needed)

    # Build volume directory entries
    vd_entries = []
    next_lbn = 2  # Start files at sector 2 (after VH itself)

    # Always add sgilabel
    vd_entries.append({'name': 'sgilabel', 'lbn': 0, 'nbytes': VH_SIZE})

    file_data = {}
    for add_spec in parsed.add:
        name, filepath = add_spec.split(':', 1)
        with open(filepath, 'rb') as f:
            data = f.read()
        # Align to sector boundary
        file_sectors = (len(data) + SECTOR_SIZE - 1) // SECTOR_SIZE
        vd_entries.append({'name': name, 'lbn': next_lbn, 'nbytes': len(data)})
        file_data[name] = (next_lbn, data)
        next_lbn += file_sectors

    # Ensure VH partition is large enough for all files
    if next_lbn + 16 > vh_sectors:
        vh_sectors = next_lbn + 16

    # Build partition table
    pt_entries = [{'nblks': 0, 'firstlbn': 0, 'type': 0}] * NPARTAB
    pt_list = list(pt_entries)
    # Partition 8: volume header
    pt_list[8] = {'nblks': vh_sectors, 'firstlbn': 0, 'type': PTYPE_VOLHDR}
    # Partition 10: entire volume
    pt_list[10] = {'nblks': total_sectors, 'firstlbn': 0, 'type': PTYPE_VOLUME}

    # Build the VH
    vh_data = build_vh(bootfile=parsed.bootfile, vd_entries=vd_entries,
                       pt_entries=pt_list)

    # Write the disk image
    with open(parsed.output, 'wb') as f:
        # Write VH at sector 0
        f.write(vh_data)
        # Pad to sector 1
        f.write(b'\x00' * (SECTOR_SIZE - len(vh_data)))

        # Write file data
        for name, (lbn, data) in file_data.items():
            f.seek(lbn * SECTOR_SIZE)
            f.write(data)
            # Pad to sector boundary
            remainder = len(data) % SECTOR_SIZE
            if remainder:
                f.write(b'\x00' * (SECTOR_SIZE - remainder))

        # Extend to full size
        f.seek(total_sectors * SECTOR_SIZE - 1)
        f.write(b'\x00')

    print(f"Created {parsed.output} ({parsed.size}MB, {total_sectors} sectors)")
    for vd in vd_entries:
        print(f"  VD: \"{vd['name']}\" at lbn {vd['lbn']}, {vd['nbytes']} bytes")

    return 0


def cmd_copy_vh(args):
    if len(args) < 2:
        print("Usage: copy-vh <source_image> <dest_image> [--size <MB>]")
        return 1

    src_path = args[0]
    dst_path = args[1]
    size_mb = 64
    if '--size' in args:
        idx = args.index('--size')
        size_mb = int(args[idx + 1])

    with open(src_path, 'rb') as f:
        vh = read_vh(f)
        if not vh:
            print(f"Error: {src_path} does not have a valid SGI volume header")
            return 1

        # Read all files from VD
        file_data = {}
        for vd in vh['vd']:
            if vd['name'] and vd['nbytes'] > 0 and vd['lbn'] > 0:
                f.seek(vd['lbn'] * SECTOR_SIZE)
                file_data[vd['name']] = f.read(vd['nbytes'])

    total_sectors = size_mb * 1024 * 1024 // SECTOR_SIZE

    # Build new VD entries with files placed sequentially
    vd_entries = []
    next_lbn = 2
    vd_entries.append({'name': 'sgilabel', 'lbn': 0, 'nbytes': VH_SIZE})

    file_locations = {}
    for vd in vh['vd']:
        if vd['name'] and vd['name'] != 'sgilabel' and vd['name'] in file_data:
            data = file_data[vd['name']]
            file_sectors = (len(data) + SECTOR_SIZE - 1) // SECTOR_SIZE
            vd_entries.append({'name': vd['name'], 'lbn': next_lbn,
                               'nbytes': len(data)})
            file_locations[vd['name']] = next_lbn
            next_lbn += file_sectors

    # Build partition table
    vh_sectors = max(4096, next_lbn + 16)
    pt_entries = [{'nblks': 0, 'firstlbn': 0, 'type': 0}] * NPARTAB
    pt_list = list(pt_entries)
    pt_list[8] = {'nblks': vh_sectors, 'firstlbn': 0, 'type': PTYPE_VOLHDR}
    pt_list[10] = {'nblks': total_sectors, 'firstlbn': 0, 'type': PTYPE_VOLUME}

    new_vh = build_vh(bootfile=vh['bootfile'], vd_entries=vd_entries,
                      pt_entries=pt_list)

    with open(dst_path, 'wb') as f:
        f.write(new_vh)
        f.write(b'\x00' * (SECTOR_SIZE - len(new_vh)))

        for name, data in file_data.items():
            if name in file_locations:
                f.seek(file_locations[name] * SECTOR_SIZE)
                f.write(data)
                remainder = len(data) % SECTOR_SIZE
                if remainder:
                    f.write(b'\x00' * (SECTOR_SIZE - remainder))

        f.seek(total_sectors * SECTOR_SIZE - 1)
        f.write(b'\x00')

    print(f"Copied volume header from {src_path} to {dst_path} ({size_mb}MB)")
    for vd in vd_entries:
        print(f"  VD: \"{vd['name']}\" at lbn {vd['lbn']}, {vd['nbytes']} bytes")

    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1]
    args = sys.argv[2:]

    commands = {
        'list': cmd_list,
        'extract': cmd_extract,
        'create': cmd_create,
        'copy-vh': cmd_copy_vh,
    }

    if cmd not in commands:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(commands.keys())}")
        return 1

    return commands[cmd](args)


if __name__ == '__main__':
    sys.exit(main() or 0)
