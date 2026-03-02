#!/usr/bin/env python3
"""Walk directory tree and identify compressed files and IRIX dist packages."""

import os
import struct
from pathlib import Path
from collections import defaultdict

# Magic bytes for common compression formats
MAGIC_SIGNATURES = {
    b'\x1f\x8b': 'gzip',
    b'\x1f\x9d': 'compress (.Z)',
    b'\x1f\xa0': 'compress (LZH)',
    b'BZ': 'bzip2',
    b'PK': 'zip/pk',
    b'\xfd7zXZ': 'xz',
    b'\x5d\x00': 'lzma',
    b'Rar': 'rar',
}

# File extensions that indicate compression
COMPRESSED_EXTENSIONS = {
    '.gz', '.z', '.bz2', '.bz', '.xz', '.lzma', '.lz',
    '.zip', '.tar', '.tgz', '.tbz2', '.txz',
    '.rar', '.7z', '.cab', '.arj',
}


def detect_file_type(filepath: Path) -> tuple[str, str]:
    """Detect file type from magic bytes.

    Returns:
        Tuple of (detected_type, description)
    """
    try:
        with open(filepath, 'rb') as f:
            header = f.read(64)

        if len(header) < 2:
            return 'empty', 'Empty or tiny file'

        # Check compression formats
        if header[:2] == b'\x1f\x8b':
            return 'gzip', 'gzip compressed'
        if header[:2] == b'\x1f\x9d':
            return 'compress', 'Unix compress (.Z)'
        if header[:2] == b'\x1f\xa0':
            return 'lzh', 'LZH compressed'
        if header[:2] == b'BZ':
            return 'bzip2', 'bzip2 compressed'
        if header[:2] == b'PK':
            return 'zip', 'ZIP archive'
        if header[:2] == b'\x5d\x00':
            return 'lzma', 'LZMA compressed'

        # IRIX-specific formats
        # IRIX dist/inst package - starts with ASCII package spec
        if header[:4] == b'prod' or header[:4] == b'upd ' or header[:5] == b'maint':
            return 'irix-idb', 'IRIX inst database'

        # Check for IRIX package data (often has specific patterns)
        # dist packages often start with file counts or headers

        # ELF binary
        if header[:4] == b'\x7fELF':
            return 'elf', 'ELF binary'

        # Shell/text script
        if header[:2] == b'#!':
            return 'script', 'Script/text'

        # COFF (old SGI format)
        if header[:2] in (b'\x01\x60', b'\x01\x62', b'\x01\x66'):
            return 'coff', 'MIPS COFF binary'

        # Check if it looks like ASCII text
        try:
            header[:32].decode('ascii')
            # Check for common text patterns
            if b'\n' in header or b'\r' in header:
                return 'text', 'ASCII text'
        except UnicodeDecodeError:
            pass

        # Check for SGI disk label / volume header
        if len(header) >= 8:
            # SGI volume header magic
            if header[0:4] == b'\x0b\xe5\xa9\x41':
                return 'sgi-vh', 'SGI volume header'

        return 'binary', 'Unknown binary'

    except (IOError, OSError) as e:
        return 'error', str(e)


def format_size(size: int) -> str:
    """Format file size in human-readable form."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def main():
    base_dir = Path('.')

    # Skip these directories/files
    skip_names = {'extracted', '.git', '__pycache__', 'find_compressed.py'}

    # Track findings by type
    files_by_type = defaultdict(list)
    total_count = 0
    total_size = 0

    # Track dist directories
    dist_dirs = set()

    print(f"Scanning {base_dir}...")
    print()

    for filepath in base_dir.rglob('*'):
        # Skip certain paths
        if any(part in skip_names for part in filepath.parts):
            continue
        if filepath.name.startswith('.'):
            continue

        if filepath.is_dir():
            if filepath.name == 'dist':
                dist_dirs.add(filepath)
            continue

        if not filepath.is_file():
            continue

        file_type, description = detect_file_type(filepath)
        size = filepath.stat().st_size
        rel_path = filepath.relative_to(base_dir)

        files_by_type[file_type].append((rel_path, size, description))
        total_count += 1
        total_size += size

    # Print summary by type
    print("=" * 70)
    print("FILE TYPE SUMMARY")
    print("=" * 70)
    print()

    for file_type in sorted(files_by_type.keys()):
        files = files_by_type[file_type]
        type_size = sum(f[1] for f in files)
        desc = files[0][2] if files else ''
        print(f"{file_type:15} {len(files):5} files  {format_size(type_size):>10}  ({desc})")

    print()
    print(f"TOTAL: {total_count} files ({format_size(total_size)})")
    print()

    # Print dist directories
    if dist_dirs:
        print("=" * 70)
        print("IRIX DIST DIRECTORIES")
        print("=" * 70)
        for d in sorted(dist_dirs):
            # Count files in dist
            dist_files = list(d.rglob('*'))
            file_count = sum(1 for f in dist_files if f.is_file())
            print(f"  {d.relative_to(base_dir)} ({file_count} files)")
    print()

    # Show samples of compressed files
    compressed_types = ['gzip', 'compress', 'lzh', 'bzip2', 'zip', 'lzma']
    has_compressed = False

    for ct in compressed_types:
        if ct in files_by_type:
            has_compressed = True
            print(f"\n--- {ct.upper()} FILES ---")
            for rel_path, size, desc in sorted(files_by_type[ct], key=lambda x: str(x[0]))[:20]:
                print(f"  {format_size(size):>8}  {rel_path}")
            if len(files_by_type[ct]) > 20:
                print(f"  ... and {len(files_by_type[ct]) - 20} more")

    if not has_compressed:
        print("No compressed files found.")

    # Show sample of each other type
    print("\n" + "=" * 70)
    print("SAMPLE FILES BY TYPE")
    print("=" * 70)

    for file_type in sorted(files_by_type.keys()):
        if file_type in compressed_types:
            continue
        files = files_by_type[file_type]
        print(f"\n--- {file_type.upper()} (first 5 of {len(files)}) ---")
        for rel_path, size, desc in sorted(files, key=lambda x: x[1], reverse=True)[:5]:
            print(f"  {format_size(size):>8}  {rel_path}")


if __name__ == '__main__':
    main()
