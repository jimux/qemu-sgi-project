#!/usr/bin/env python3
"""Extract IRIX dist packages (.idb/.sw files and .tardist archives).

IRIX dist packages consist of:
- .idb file: ASCII text index describing files, permissions, sizes
- .sw/.man file: Binary archive containing compressed files

Tardist files are tar archives containing the idb and sw files.
"""

import os
import re
import subprocess
import tarfile
import tempfile
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class IDBEntry:
    """Entry from an IDB file."""
    entry_type: str  # 'd' directory, 'f' file, 'l' symlink, 'c' character device
    mode: str
    owner: str
    group: str
    path: str
    source_path: str
    package: str
    cmpsize: int = 0
    size: int = 0
    symval: str = ""


def parse_idb_line(line: str) -> Optional[IDBEntry]:
    """Parse a single line from an IDB file."""
    line = line.strip()
    if not line:
        return None

    parts = line.split()
    if len(parts) < 7:
        return None

    entry_type = parts[0]
    if entry_type not in ('d', 'f', 'l', 'c', 'b', 'p'):
        return None

    mode = parts[1]
    owner = parts[2]
    group = parts[3]
    path = parts[4]
    source_path = parts[5]
    package = parts[6]

    # Parse attributes from rest of line
    rest = ' '.join(parts[7:])

    cmpsize = 0
    match = re.search(r'cmpsize\((\d+)\)', rest)
    if match:
        cmpsize = int(match.group(1))

    size = 0
    match = re.search(r'(?<![a-z])size\((\d+)\)', rest)
    if match:
        size = int(match.group(1))

    symval = ""
    match = re.search(r'symval\(([^)]+)\)', rest)
    if match:
        symval = match.group(1)

    return IDBEntry(
        entry_type=entry_type,
        mode=mode,
        owner=owner,
        group=group,
        path=path,
        source_path=source_path,
        package=package,
        cmpsize=cmpsize,
        size=size,
        symval=symval,
    )


def read_idb(idb_path: Path) -> List[IDBEntry]:
    """Read and parse an IDB file."""
    entries = []
    with open(idb_path, 'r', encoding='latin-1') as f:
        for line in f:
            entry = parse_idb_line(line)
            if entry:
                entries.append(entry)
    return entries


def normalize_path(path: str) -> str:
    """Normalize a path, removing leading dots and ensuring no absolute paths."""
    # Remove leading dots
    while path.startswith('.'):
        path = path[1:]
    # Remove leading slashes
    while path.startswith('/'):
        path = path[1:]
    return path


def extract_sw_sequential(sw_path: Path, idb_entries: List[IDBEntry], output_dir: Path) -> int:
    """Extract files from .sw archive by reading sequentially.

    The sw file format (IRIX 4+):
    - 12-byte header (e.g., "im001V500P00")
    - For each file entry:
      - 2 bytes: unknown/padding
      - filename (from IDB path, aligned)
      - compressed data (Unix compress format)

    Returns: number of files extracted
    """
    with open(sw_path, 'rb') as f:
        data = f.read()

    if len(data) < 12:
        return 0

    # Check header
    if not data[:2] == b'im':
        print(f"    Warning: Unknown sw format: {data[:12]}")
        return 0

    # Get file entries from IDB for this sw file
    sw_name = sw_path.stem  # e.g., "eoe1" from "eoe1.sw"
    file_entries = [e for e in idb_entries
                    if e.entry_type == 'f' and e.cmpsize > 0
                    and e.package.startswith(sw_name)]

    if not file_entries:
        return 0

    # Track position in sw file
    pos = 12  # Skip header
    extracted = 0

    for entry in file_entries:
        if pos >= len(data):
            break

        path = normalize_path(entry.path)
        if not path:
            continue

        cmpsize = entry.cmpsize

        # Find the filename in the data
        # The format seems to be: optional padding + filename + compressed data
        filename = path.split('/')[-1] if '/' in path else path

        # Search for this filename near current position
        search_start = pos
        search_end = min(pos + 1024, len(data))
        filename_bytes = filename.encode('latin-1')

        found_pos = -1
        for i in range(search_start, search_end - len(filename_bytes)):
            if data[i:i+len(filename_bytes)] == filename_bytes:
                # Verify this is followed by compress magic nearby
                check_start = i + len(filename_bytes)
                for j in range(check_start, min(check_start + 32, len(data) - 1)):
                    if data[j:j+2] == b'\x1f\x9d':
                        found_pos = j
                        break
                if found_pos >= 0:
                    break

        if found_pos < 0:
            # Try to find compress magic directly
            for i in range(pos, min(pos + 2048, len(data) - 1)):
                if data[i:i+2] == b'\x1f\x9d':
                    found_pos = i
                    break

        if found_pos < 0 or found_pos + cmpsize > len(data):
            pos += cmpsize + 64  # Skip ahead
            continue

        # Extract compressed data
        compressed_data = data[found_pos:found_pos + cmpsize]
        pos = found_pos + cmpsize

        # Create output path
        out_path = output_dir / path
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Write compressed data to temp file and decompress
        temp_z = out_path.with_suffix(out_path.suffix + '.Z')
        try:
            # Ensure it starts with compress magic
            if not compressed_data.startswith(b'\x1f\x9d'):
                continue

            with open(temp_z, 'wb') as f:
                f.write(compressed_data)

            # Decompress using gzip -d (handles .Z files)
            result = subprocess.run(
                ['gzip', '-df', str(temp_z)],
                capture_output=True,
                timeout=30
            )
            if result.returncode == 0:
                extracted += 1
            else:
                temp_z.unlink(missing_ok=True)
        except subprocess.TimeoutExpired:
            temp_z.unlink(missing_ok=True)
        except Exception:
            temp_z.unlink(missing_ok=True)

    return extracted


def create_directories(idb_entries: List[IDBEntry], output_dir: Path):
    """Create directory structure from IDB entries."""
    for entry in idb_entries:
        if entry.entry_type == 'd':
            path = normalize_path(entry.path)
            if path:
                (output_dir / path).mkdir(parents=True, exist_ok=True)


def create_symlinks(idb_entries: List[IDBEntry], output_dir: Path) -> int:
    """Create symbolic links from IDB entries."""
    count = 0
    for entry in idb_entries:
        if entry.entry_type == 'l' and entry.symval:
            path = normalize_path(entry.path)
            if not path:
                continue

            link_path = output_dir / path
            link_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                if link_path.exists() or link_path.is_symlink():
                    link_path.unlink()
                link_path.symlink_to(entry.symval)
                count += 1
            except Exception:
                pass

    return count


def extract_dist_directory(dist_dir: Path, output_dir: Path, verbose: bool = False) -> Tuple[int, int, int]:
    """Extract all packages from a dist directory.

    Returns: (files_extracted, symlinks_created, errors)
    """
    total_files = 0
    total_symlinks = 0
    errors = 0

    # Find all .idb files
    idb_files = list(dist_dir.glob('*.idb'))

    for idb_path in idb_files:
        base_name = idb_path.stem

        # Find corresponding sw/sw32/sw64/man files
        sw_files = list(dist_dir.glob(f"{base_name}.sw*")) + list(dist_dir.glob(f"{base_name}.man*"))

        if not sw_files:
            continue

        if verbose:
            print(f"    {idb_path.name}")

        try:
            entries = read_idb(idb_path)

            # Create directories
            create_directories(entries, output_dir)

            # Extract files from each sw file
            for sw_path in sw_files:
                count = extract_sw_sequential(sw_path, entries, output_dir)
                total_files += count

            # Create symlinks
            symlinks = create_symlinks(entries, output_dir)
            total_symlinks += symlinks

        except Exception as e:
            if verbose:
                print(f"      Error: {e}")
            errors += 1

    return total_files, total_symlinks, errors


def extract_tardist(tardist_path: Path, output_dir: Path, verbose: bool = False) -> Tuple[int, int, int]:
    """Extract a .tardist file.

    Tardist files are tar archives containing idb and sw files.

    Returns: (files_extracted, symlinks_created, errors)
    """
    # Create temp directory to extract tardist contents
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        try:
            # Extract tar archive
            with tarfile.open(tardist_path, 'r:*') as tar:
                tar.extractall(tmppath, filter='data')

            # Find the dist directory or idb files
            idb_files = list(tmppath.rglob('*.idb'))

            if not idb_files:
                return 0, 0, 1

            # Use the directory containing the first idb file
            dist_dir = idb_files[0].parent

            return extract_dist_directory(dist_dir, output_dir, verbose)

        except Exception as e:
            if verbose:
                print(f"    Error extracting tardist: {e}")
            return 0, 0, 1


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Extract IRIX dist packages')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    parser.add_argument('-o', '--output', default='extracted/dist_contents',
                        help='Output directory')
    args = parser.parse_args()

    base_dir = Path('.')
    output_base = Path(args.output)
    output_base.mkdir(parents=True, exist_ok=True)

    skip_dirs = {'extracted', '.git', '__pycache__'}

    # Collect packages to process
    dist_dirs = []
    tardist_files = []

    print("Scanning for IRIX packages...")

    for item in base_dir.rglob('*'):
        if any(skip in item.parts for skip in skip_dirs):
            continue

        if item.is_dir() and item.name == 'dist':
            # Check if it contains idb files
            if list(item.glob('*.idb')):
                dist_dirs.append(item)

        elif item.is_file() and item.suffix == '.tardist':
            tardist_files.append(item)

    print(f"Found {len(dist_dirs)} dist directories")
    print(f"Found {len(tardist_files)} tardist files")
    print()

    total_files = 0
    total_symlinks = 0
    total_errors = 0

    # Process dist directories
    if dist_dirs:
        print("=" * 60)
        print("EXTRACTING DIST DIRECTORIES")
        print("=" * 60)

        for dist_dir in sorted(dist_dirs):
            # Create output matching source structure
            rel_path = dist_dir.relative_to(base_dir)
            out_dir = output_base / rel_path.parent

            print(f"\n{rel_path}")

            files, symlinks, errors = extract_dist_directory(dist_dir, out_dir, args.verbose)
            total_files += files
            total_symlinks += symlinks
            total_errors += errors

            if files > 0 or symlinks > 0:
                print(f"  -> {files} files, {symlinks} symlinks")

    # Process tardist files
    if tardist_files:
        print()
        print("=" * 60)
        print("EXTRACTING TARDIST FILES")
        print("=" * 60)

        for tardist_path in sorted(tardist_files):
            rel_path = tardist_path.relative_to(base_dir)
            out_dir = output_base / rel_path.parent / tardist_path.stem

            print(f"\n{rel_path}")

            files, symlinks, errors = extract_tardist(tardist_path, out_dir, args.verbose)
            total_files += files
            total_symlinks += symlinks
            total_errors += errors

            if files > 0 or symlinks > 0:
                print(f"  -> {files} files, {symlinks} symlinks")

    # Summary
    print()
    print("=" * 60)
    print("EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"  Files extracted:    {total_files}")
    print(f"  Symlinks created:   {total_symlinks}")
    print(f"  Errors:             {total_errors}")
    print(f"\nOutput directory: {output_base.absolute()}")


if __name__ == '__main__':
    main()
