#!/usr/bin/env python3
"""Extract all compressed files and IRIX packages in the irixes directory tree."""

import gzip
import bz2
import zipfile
import subprocess
import shutil
from pathlib import Path
from collections import defaultdict

# Magic bytes for compression detection
def detect_compression(filepath: Path) -> str | None:
    """Detect compression type from magic bytes."""
    try:
        with open(filepath, 'rb') as f:
            header = f.read(16)

        if len(header) < 2:
            return None

        if header[:2] == b'\x1f\x8b':
            return 'gzip'
        if header[:2] == b'\x1f\x9d':
            return 'compress'
        if header[:2] == b'BZ':
            return 'bzip2'
        if header[:2] == b'PK':
            return 'zip'
        if header[:4] == b'\x0b\xe5\xa9\x41':
            return 'sgi-vh'

        return None
    except (IOError, OSError):
        return None


def format_size(size: int) -> str:
    """Format file size in human-readable form."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def extract_gzip(src: Path, dest_dir: Path) -> bool:
    """Extract a gzip file."""
    # Determine output filename
    if src.suffix.lower() == '.gz':
        out_name = src.stem
    elif src.suffix.lower() == '.tgz':
        out_name = src.stem + '.tar'
    else:
        out_name = src.name + '.extracted'

    dest_file = dest_dir / out_name

    # If it's a tar.gz or tgz, extract the tar too
    is_tar = out_name.endswith('.tar') or src.name.endswith('.tar.gz')

    try:
        with gzip.open(src, 'rb') as f_in:
            content = f_in.read()

        if is_tar:
            # Extract tar contents to a directory
            tar_dir = dest_dir / src.stem.replace('.tar', '')
            tar_dir.mkdir(parents=True, exist_ok=True)

            # Write temp tar and extract
            import tarfile
            import io
            with tarfile.open(fileobj=io.BytesIO(content), mode='r:') as tar:
                tar.extractall(path=tar_dir)
            return True
        else:
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_file, 'wb') as f_out:
                f_out.write(content)
            return True
    except Exception as e:
        print(f"  Error extracting {src}: {e}")
        return False


def extract_bzip2(src: Path, dest_dir: Path) -> bool:
    """Extract a bzip2 file."""
    if src.suffix.lower() == '.bz2':
        out_name = src.stem
    elif src.suffix.lower() == '.tbz2':
        out_name = src.stem + '.tar'
    else:
        out_name = src.name + '.extracted'

    dest_file = dest_dir / out_name
    is_tar = out_name.endswith('.tar') or src.name.endswith('.tar.bz2')

    try:
        with bz2.open(src, 'rb') as f_in:
            content = f_in.read()

        if is_tar:
            tar_dir = dest_dir / src.stem.replace('.tar', '')
            tar_dir.mkdir(parents=True, exist_ok=True)

            import tarfile
            import io
            with tarfile.open(fileobj=io.BytesIO(content), mode='r:') as tar:
                tar.extractall(path=tar_dir)
            return True
        else:
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_file, 'wb') as f_out:
                f_out.write(content)
            return True
    except Exception as e:
        print(f"  Error extracting {src}: {e}")
        return False


def extract_compress(src: Path, dest_dir: Path) -> bool:
    """Extract a Unix compress (.Z) file using uncompress or gzip -d."""
    if src.suffix.lower() == '.z':
        out_name = src.stem
    else:
        out_name = src.name + '.extracted'

    dest_file = dest_dir / out_name
    dest_file.parent.mkdir(parents=True, exist_ok=True)

    # Try using gzip which can decompress .Z files
    try:
        result = subprocess.run(
            ['gzip', '-dc', str(src)],
            capture_output=True,
            check=True
        )
        content = result.stdout

        # Check if it's a tar
        is_tar = out_name.endswith('.tar')
        if is_tar:
            tar_dir = dest_dir / src.stem.replace('.tar', '')
            tar_dir.mkdir(parents=True, exist_ok=True)

            import tarfile
            import io
            with tarfile.open(fileobj=io.BytesIO(content), mode='r:') as tar:
                tar.extractall(path=tar_dir)
            return True
        else:
            with open(dest_file, 'wb') as f_out:
                f_out.write(content)
            return True
    except subprocess.CalledProcessError as e:
        print(f"  Error decompressing {src}: {e}")
        return False
    except Exception as e:
        print(f"  Error extracting {src}: {e}")
        return False


def extract_zip(src: Path, dest_dir: Path) -> bool:
    """Extract a ZIP archive."""
    try:
        extract_to = dest_dir / src.stem
        extract_to.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(src, 'r') as zf:
            zf.extractall(extract_to)
        return True
    except Exception as e:
        print(f"  Error extracting {src}: {e}")
        return False


def extract_sgi_vh(src: Path, dest_dir: Path, efs2tar_path: str) -> bool:
    """Extract SGI volume header / EFS image using efs2tar."""
    try:
        tar_file = dest_dir / (src.stem + '.tar')
        extract_to = dest_dir / src.stem

        # Run efs2tar
        result = subprocess.run(
            [efs2tar_path, '-in', str(src), '-out', str(tar_file)],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            print(f"  efs2tar failed: {result.stderr}")
            return False

        # Extract the tar
        extract_to.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ['tar', '-xf', str(tar_file), '-C', str(extract_to)],
            check=True
        )

        # Optionally remove the intermediate tar
        tar_file.unlink()
        return True
    except Exception as e:
        print(f"  Error extracting {src}: {e}")
        return False


def main():
    base_dir = Path('.')
    extracted_dir = Path('extracted')
    extracted_dir.mkdir(exist_ok=True)

    # Find efs2tar
    efs2tar_path = Path.home() / 'go' / 'bin' / 'efs2tar'
    has_efs2tar = efs2tar_path.exists()
    if not has_efs2tar:
        # Try PATH
        result = subprocess.run(['which', 'efs2tar'], capture_output=True, text=True)
        if result.returncode == 0:
            efs2tar_path = result.stdout.strip()
            has_efs2tar = True

    # Skip patterns
    skip_names = {'extracted', '.git', '__pycache__'}

    # Collect files to extract
    files_to_extract = defaultdict(list)

    print("Scanning for files to extract...")

    for filepath in base_dir.rglob('*'):
        if any(part in skip_names for part in filepath.parts):
            continue
        if filepath.name.startswith('.'):
            continue
        if not filepath.is_file():
            continue

        comp_type = detect_compression(filepath)
        if comp_type:
            files_to_extract[comp_type].append(filepath)

    # Print summary
    print("\nFiles to extract:")
    for comp_type, files in sorted(files_to_extract.items()):
        total_size = sum(f.stat().st_size for f in files)
        print(f"  {comp_type}: {len(files)} files ({format_size(total_size)})")

    print()

    # Extract each type
    stats = {'success': 0, 'failed': 0, 'skipped': 0}

    # Gzip files
    if files_to_extract['gzip']:
        print(f"\n=== Extracting {len(files_to_extract['gzip'])} gzip files ===")
        for src in files_to_extract['gzip']:
            rel = src.relative_to(base_dir)
            dest_dir = extracted_dir / rel.parent
            dest_dir.mkdir(parents=True, exist_ok=True)

            # Skip if already extracted
            potential_dest = dest_dir / src.stem.replace('.tar', '')
            if potential_dest.exists():
                stats['skipped'] += 1
                continue

            print(f"  {rel}")
            if extract_gzip(src, dest_dir):
                stats['success'] += 1
            else:
                stats['failed'] += 1

    # Bzip2 files
    if files_to_extract['bzip2']:
        print(f"\n=== Extracting {len(files_to_extract['bzip2'])} bzip2 files ===")
        for src in files_to_extract['bzip2']:
            rel = src.relative_to(base_dir)
            dest_dir = extracted_dir / rel.parent
            dest_dir.mkdir(parents=True, exist_ok=True)

            potential_dest = dest_dir / src.stem.replace('.tar', '')
            if potential_dest.exists():
                stats['skipped'] += 1
                continue

            print(f"  {rel}")
            if extract_bzip2(src, dest_dir):
                stats['success'] += 1
            else:
                stats['failed'] += 1

    # Compress (.Z) files
    if files_to_extract['compress']:
        print(f"\n=== Extracting {len(files_to_extract['compress'])} compress files ===")
        for src in files_to_extract['compress']:
            rel = src.relative_to(base_dir)
            dest_dir = extracted_dir / rel.parent
            dest_dir.mkdir(parents=True, exist_ok=True)

            potential_dest = dest_dir / src.stem.replace('.tar', '')
            if potential_dest.exists():
                stats['skipped'] += 1
                continue

            print(f"  {rel}")
            if extract_compress(src, dest_dir):
                stats['success'] += 1
            else:
                stats['failed'] += 1

    # ZIP files
    if files_to_extract['zip']:
        print(f"\n=== Extracting {len(files_to_extract['zip'])} zip files ===")
        for src in files_to_extract['zip']:
            rel = src.relative_to(base_dir)
            dest_dir = extracted_dir / rel.parent
            dest_dir.mkdir(parents=True, exist_ok=True)

            potential_dest = dest_dir / src.stem
            if potential_dest.exists():
                stats['skipped'] += 1
                continue

            print(f"  {rel}")
            if extract_zip(src, dest_dir):
                stats['success'] += 1
            else:
                stats['failed'] += 1

    # SGI volume headers
    if files_to_extract['sgi-vh']:
        if has_efs2tar:
            print(f"\n=== Extracting {len(files_to_extract['sgi-vh'])} SGI volume headers ===")
            for src in files_to_extract['sgi-vh']:
                rel = src.relative_to(base_dir)
                dest_dir = extracted_dir / rel.parent
                dest_dir.mkdir(parents=True, exist_ok=True)

                potential_dest = dest_dir / src.stem
                if potential_dest.exists():
                    stats['skipped'] += 1
                    continue

                print(f"  {rel}")
                if extract_sgi_vh(src, dest_dir, str(efs2tar_path)):
                    stats['success'] += 1
                else:
                    stats['failed'] += 1
        else:
            print(f"\n=== Skipping {len(files_to_extract['sgi-vh'])} SGI volume headers (efs2tar not found) ===")
            stats['skipped'] += len(files_to_extract['sgi-vh'])

    # Summary
    print("\n" + "=" * 60)
    print("EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"  Successful: {stats['success']}")
    print(f"  Failed:     {stats['failed']}")
    print(f"  Skipped:    {stats['skipped']} (already extracted)")
    print(f"\nExtracted files are in: {extracted_dir.absolute()}")


if __name__ == '__main__':
    main()
