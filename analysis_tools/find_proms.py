#!/usr/bin/env python3
"""PROM/firmware discovery script for SGI IRIX distribution trees.

This script walks through the irixes directory tree, identifies PROM/firmware
files using various detection strategies, determines what hardware each is for,
and collects them into a PROMs directory organized by hardware type.

Usage:
    python find_proms.py [--dry-run] [--verbose]
"""

import argparse
import hashlib
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Add parent directory to path for sgi_analyze import
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from analysis_tools.prom import (
    FirmwareType,
    detect_firmware_type,
    get_type_name,
    get_type_description,
    SGIFirmwareAnalyzer,
)


# =============================================================================
# Configuration
# =============================================================================

# Filename patterns that suggest PROM/firmware files
PROM_FILENAME_PATTERNS = [
    re.compile(r'.*prom.*', re.IGNORECASE),       # Contains "prom"
    re.compile(r'.*\.image$', re.IGNORECASE),     # .image files (IP32)
    re.compile(r'.*\.u$', re.IGNORECASE),         # .u microcode files
    re.compile(r'^ge\d+.*\.bin$', re.IGNORECASE), # GE engine microcode
    re.compile(r'^hq\d+.*\.bin$', re.IGNORECASE), # HQ command processor
    re.compile(r'^arm.*\.u$', re.IGNORECASE),     # ARM transport processor
    re.compile(r'^.*\.mex$', re.IGNORECASE),      # Crime .mex files
    re.compile(r'^.*\.vfo$', re.IGNORECASE),      # Video format .vfo files
]

# Paths that are likely to contain firmware
FIRMWARE_PATH_PATTERNS = [
    'usr/cpu/firmware',      # CPU PROMs
    'usr/gfx/ucode',         # Graphics microcode
    'usr/gfx/KONA',          # InfiniteReality specific
    'usr/diags',             # Diagnostic microcode
    'firmware/',             # Direct firmware dirs
]

# File extensions to skip (definitely not firmware)
SKIP_EXTENSIONS = {
    '.h', '.c', '.o', '.a', '.so', '.sl',    # Source/object files
    '.txt', '.html', '.htm', '.xml', '.sgml', # Text/doc files
    '.gif', '.jpg', '.jpeg', '.png', '.bw', '.rgb', '.sgi',  # Images
    '.tar', '.gz', '.z', '.Z', '.zip', '.bz2',  # Archives
    '.idb', '.sw', '.man', '.help',           # IRIX dist/help files
    '.ps', '.pdf', '.eps',                    # Document formats
    '.csh', '.sh', '.ksh', '.pl', '.py',      # Scripts
    '.css', '.js',                            # Web files
}

# Directories to skip entirely
SKIP_DIRS = {
    'include', 'man', 'catman', 'demos', 'data', 'examples',
    'lib', 'lib32', 'lib64', 'share', 'doc', 'bookshelves',
    'webserver', 'insight', 'relnotes', 'www',
}

# Size constraints for firmware files
MIN_FIRMWARE_SIZE = 512      # Minimum 512 bytes
MAX_FIRMWARE_SIZE = 8 * 1024 * 1024  # Maximum 8 MB


# =============================================================================
# Data Structures
# =============================================================================

@dataclass
class PromInfo:
    """Information about a discovered PROM file."""
    source_path: str
    filename: str
    size: int
    md5: str
    firmware_type: FirmwareType
    type_name: str
    type_description: str
    category: str              # e.g., "cpu/ip30", "graphics/kona"
    subcategory: str = ""      # e.g., "ge", "vs2"
    version_info: str = ""     # Extracted version string
    irix_version: str = ""     # IRIX version from path
    details: Dict = field(default_factory=dict)


# =============================================================================
# File Filtering
# =============================================================================

def should_skip_dir(dirname: str) -> bool:
    """Check if directory should be skipped."""
    return dirname.lower() in SKIP_DIRS or dirname.startswith('.')


def should_check_file(filepath: str, filename: str, size: int) -> bool:
    """Determine if a file should be checked for PROM content.

    Args:
        filepath: Full path to file
        filename: Base filename
        size: File size in bytes

    Returns:
        True if file should be analyzed
    """
    # Skip by size
    if size < MIN_FIRMWARE_SIZE or size > MAX_FIRMWARE_SIZE:
        return False

    # Skip by extension
    ext = os.path.splitext(filename)[1].lower()
    if ext in SKIP_EXTENSIONS:
        return False

    # Check if in a firmware-related path
    path_lower = filepath.lower()
    for pattern in FIRMWARE_PATH_PATTERNS:
        if pattern in path_lower:
            return True

    # Check filename patterns
    for pattern in PROM_FILENAME_PATTERNS:
        if pattern.match(filename):
            return True

    # Check for specific firmware directories in path
    if '/firmware/' in path_lower or '/ucode/' in path_lower:
        return True

    return False


def compute_md5(filepath: str) -> str:
    """Compute MD5 hash of file."""
    md5 = hashlib.md5()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            md5.update(chunk)
    return md5.hexdigest()


# =============================================================================
# Hardware Classification
# =============================================================================

def extract_irix_version(filepath: str) -> str:
    """Extract IRIX version from file path."""
    path = filepath.lower()

    # Look for version patterns in path
    version_patterns = [
        r'irix[_\s]*([\d.]+)',
        r'(\d+\.\d+)[_-]foundation',
        r'6\.5\.(\d+)',
    ]

    for pattern in version_patterns:
        match = re.search(pattern, path)
        if match:
            ver = match.group(1)
            if '.' not in ver and len(ver) <= 2:
                return f"6.5.{ver}"
            return ver

    return ""


def classify_hardware(filepath: str, filename: str, fw_type: FirmwareType) -> Tuple[str, str]:
    """Classify firmware by hardware type.

    Args:
        filepath: Full path to file
        filename: Base filename
        fw_type: Detected firmware type

    Returns:
        Tuple of (category, subcategory)
    """
    path_lower = filepath.lower()
    name_lower = filename.lower()

    # Check FIRST for tools/utilities (before any PROM checks)
    # flashprom is a utility binary to program PROMs, not a PROM itself
    if name_lower == 'flashprom':
        return "tools", ""

    # Check for obvious CPU PROM names (before any other checks)
    # This catches files named ip32prom.image etc even if in wrong directories
    if 'ip32prom' in name_lower or name_lower.startswith('ip32'):
        return "cpu/ip32", ""
    if 'ip30prom' in name_lower or name_lower.startswith('ip30'):
        return "cpu/ip30", ""
    if 'ip27prom' in name_lower or name_lower.startswith('ip27'):
        return "cpu/ip27", ""
    if 'ip35prom' in name_lower or name_lower.startswith('ip35'):
        return "cpu/ip35", ""

    # IO controllers - check these before other classifications
    # (they can appear in /IP19/ directories but aren't CPU proms)
    if 'io4prom' in name_lower or ('io4' in name_lower and 'prom' in name_lower):
        return "io/io4", ""
    if 'io6prom' in name_lower or ('io6' in name_lower and 'prom' in name_lower):
        return "io/io6", ""

    # CPU PROMs
    if fw_type in (FirmwareType.MIPS_EXCEPTION_VECTOR, FirmwareType.SHDR,
                   FirmwareType.SN0_CONTAINER, FirmwareType.SN1_CONTAINER):

        # Check for specific IP boards
        ip_patterns = {
            'ip4': 'ip4', 'ip6': 'ip6', 'ip12': 'ip12', 'ip15': 'ip15',
            'ip17': 'ip17', 'ip19': 'ip19', 'ip20': 'ip20', 'ip21': 'ip21',
            'ip22': 'ip22', 'ip24': 'ip24', 'ip25': 'ip25', 'ip26': 'ip26',
            'ip27': 'ip27', 'ip28': 'ip28', 'ip30': 'ip30', 'ip32': 'ip32',
            'ip35': 'ip35',
        }

        for pattern, ip in ip_patterns.items():
            if pattern in name_lower or pattern in path_lower:
                return f"cpu/{ip}", ""

        return "cpu/unknown", ""

    # Graphics microcode
    if fw_type == FirmwareType.KONA_ARM:
        return "graphics/kona", ""

    if fw_type == FirmwareType.MIPS_ELF:
        if 'vs2' in name_lower or 'vs2' in path_lower:
            return "graphics/venice", "vs2"
        return "graphics/venice", ""

    if fw_type == FirmwareType.IMPACT_MICROCODE:
        subcategory = ""
        if 'ge' in name_lower:
            subcategory = "ge"
        elif 'hq' in name_lower:
            subcategory = "hq"
        return "graphics/impact", subcategory

    if fw_type == FirmwareType.VPRO_BUZZ:
        return "graphics/vpro", ""

    if fw_type == FirmwareType.VOYAGER_X86:
        return "graphics/voyager", ""

    # System controllers
    if fw_type == FirmwareType.SYSCO_68K:
        if 'l1' in name_lower or 'l2' in name_lower:
            return "sysco/l1l2", ""
        return "sysco/origin3k", ""

    if fw_type == FirmwareType.PBAY_MCU:
        return "sysco/pbay", ""

    if fw_type == FirmwareType.MMSC_X86:
        return "sysco/mmsc", ""

    # Path-based classification for unknown types
    if '/kona/' in path_lower or 'kona' in name_lower:
        return "graphics/kona", ""
    if '/mgras/' in path_lower or 'mgras' in name_lower:
        return "graphics/impact", ""
    if '/re/' in path_lower:
        # RealityEngine
        if 'ge' in name_lower:
            return "graphics/re", "ge"
        if 'vs' in name_lower:
            return "graphics/re", "vs2"
        return "graphics/re", ""
    if '/ng1/' in path_lower or 'newport' in name_lower:
        return "graphics/newport", ""
    if '/gr2/' in path_lower or '/gr1/' in path_lower:
        return "graphics/gr2", ""
    if '/crm/' in path_lower or 'crime' in name_lower:
        return "graphics/crm", ""
    if '/mg/' in path_lower:
        return "graphics/impact", ""

    # Check filename for graphics hints
    if 'ge' in name_lower and 'prom' in name_lower:
        return "graphics/re", "ge"
    if 'dg2' in name_lower:
        return "graphics/gr2", ""
    if 'mg1' in name_lower:
        return "graphics/impact", ""
    if 'vof' in path_lower or name_lower.endswith('.vof'):
        # Video output format files
        if '/ng1/' in path_lower:
            return "graphics/newport", "vof"
        if '/gr2/' in path_lower:
            return "graphics/gr2", "vof"
        return "graphics/vof", ""

    return "unknown", ""


# =============================================================================
# Firmware Detection
# =============================================================================

def detect_and_classify(filepath: str) -> Optional[PromInfo]:
    """Detect if a file is firmware and classify it.

    Args:
        filepath: Path to file

    Returns:
        PromInfo if firmware detected, None otherwise
    """
    filename = os.path.basename(filepath)

    try:
        with open(filepath, 'rb') as f:
            data = f.read()
    except (IOError, PermissionError):
        return None

    if len(data) < MIN_FIRMWARE_SIZE:
        return None

    # Detect firmware type
    fw_type = detect_firmware_type(data, filename)

    # If unknown, check if it looks like microcode by other means
    if fw_type == FirmwareType.UNKNOWN:
        # Check if file is in a firmware path and has firmware-like content
        if not _looks_like_firmware(filepath, data):
            return None

    # Compute MD5
    md5 = hashlib.md5(data).hexdigest()

    # Classify hardware
    category, subcategory = classify_hardware(filepath, filename, fw_type)

    # Extract version info if possible
    version_info = ""
    try:
        analyzer = SGIFirmwareAnalyzer(filepath)
        info = analyzer.analyze()
        if info.specific_info:
            if isinstance(info.specific_info, dict):
                if 'version' in info.specific_info and info.specific_info['version']:
                    version_info = info.specific_info['version'].raw_string
                elif 'version_string' in info.specific_info:
                    version_info = info.specific_info['version_string']
    except Exception:
        pass

    return PromInfo(
        source_path=filepath,
        filename=filename,
        size=len(data),
        md5=md5,
        firmware_type=fw_type,
        type_name=get_type_name(fw_type),
        type_description=get_type_description(fw_type),
        category=category,
        subcategory=subcategory,
        version_info=version_info,
        irix_version=extract_irix_version(filepath),
    )


def _looks_like_firmware(filepath: str, data: bytes) -> bool:
    """Additional heuristics for files that might be firmware.

    Args:
        filepath: Path to file
        data: File contents

    Returns:
        True if file looks like firmware
    """
    path_lower = filepath.lower()
    filename = os.path.basename(filepath).lower()

    # Files in firmware directories are more likely to be firmware
    if '/firmware/' in path_lower or '/ucode/' in path_lower:
        # But skip obvious non-firmware
        if filename.endswith('.idb') or filename.endswith('.sw'):
            return False

        # Check for reasonable binary content
        # Firmware should have relatively high entropy but not be compressed
        zero_count = data[:1024].count(0)
        if zero_count > 900:  # Mostly zeros - probably not firmware
            return False

        return True

    # Check for specific firmware file patterns
    firmware_names = ['flashprom', 'prom.bin', 'prom.image']
    for name in firmware_names:
        if name in filename:
            return True

    return False


# =============================================================================
# Directory Walking
# =============================================================================

def walk_and_find_proms(base_dir: str, verbose: bool = False) -> List[PromInfo]:
    """Walk directory tree and find all PROM/firmware files.

    Args:
        base_dir: Base directory to search
        verbose: Print progress information

    Returns:
        List of discovered PromInfo objects
    """
    proms = []
    files_checked = 0

    for root, dirs, files in os.walk(base_dir):
        # Filter out directories to skip
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]

        for filename in files:
            filepath = os.path.join(root, filename)

            try:
                size = os.path.getsize(filepath)
            except OSError:
                continue

            if not should_check_file(filepath, filename, size):
                continue

            files_checked += 1
            if verbose and files_checked % 100 == 0:
                print(f"  Checked {files_checked} files...", file=sys.stderr)

            prom = detect_and_classify(filepath)
            if prom:
                proms.append(prom)
                if verbose:
                    print(f"  Found: {prom.category}/{prom.filename}", file=sys.stderr)

    if verbose:
        print(f"  Total files checked: {files_checked}", file=sys.stderr)

    return proms


# =============================================================================
# Deduplication
# =============================================================================

def deduplicate_proms(proms: List[PromInfo]) -> List[PromInfo]:
    """Remove duplicate PROM files based on MD5 hash.

    When duplicates exist, prefer files with more version info
    or from newer IRIX versions.

    Args:
        proms: List of PromInfo objects

    Returns:
        Deduplicated list
    """
    by_md5: Dict[str, List[PromInfo]] = {}

    for prom in proms:
        if prom.md5 not in by_md5:
            by_md5[prom.md5] = []
        by_md5[prom.md5].append(prom)

    result = []
    for md5, duplicates in by_md5.items():
        if len(duplicates) == 1:
            result.append(duplicates[0])
        else:
            # Pick the best one
            # Prefer: has version info > newer IRIX > shorter path
            best = max(duplicates, key=lambda p: (
                len(p.version_info) > 0,
                p.irix_version or "0",
                -len(p.source_path),
            ))
            result.append(best)

    return result


# =============================================================================
# Output Organization
# =============================================================================

def copy_to_output(proms: List[PromInfo], output_dir: str, dry_run: bool = False) -> None:
    """Copy PROM files to organized output directory.

    Args:
        proms: List of PromInfo objects
        output_dir: Output directory path
        dry_run: If True, only print what would be done
    """
    for prom in proms:
        # Build output path
        dest_dir = os.path.join(output_dir, prom.category)
        if prom.subcategory:
            dest_dir = os.path.join(dest_dir, prom.subcategory)

        dest_file = os.path.join(dest_dir, prom.filename)

        # Handle filename conflicts
        if os.path.exists(dest_file):
            # Check if it's the same file
            existing_md5 = compute_md5(dest_file)
            if existing_md5 == prom.md5:
                continue  # Same file, skip

            # Different file with same name - add suffix
            base, ext = os.path.splitext(prom.filename)
            suffix = prom.md5[:8]
            dest_file = os.path.join(dest_dir, f"{base}_{suffix}{ext}")

        if dry_run:
            print(f"Would copy: {prom.source_path}")
            print(f"        to: {dest_file}")
        else:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copy2(prom.source_path, dest_file)


def generate_inventory(proms: List[PromInfo], output_dir: str) -> str:
    """Generate inventory report as Markdown.

    Args:
        proms: List of PromInfo objects
        output_dir: Output directory (for relative paths)

    Returns:
        Markdown content
    """
    lines = []
    lines.append("# SGI PROM/Firmware Inventory")
    lines.append("")
    lines.append("Automatically generated by find_proms.py")
    lines.append("")

    # Summary by category
    lines.append("## Summary")
    lines.append("")

    by_category: Dict[str, List[PromInfo]] = {}
    for prom in proms:
        cat = prom.category
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(prom)

    lines.append("| Category | Count |")
    lines.append("|----------|-------|")
    for cat in sorted(by_category.keys()):
        lines.append(f"| {cat} | {len(by_category[cat])} |")
    lines.append(f"| **Total** | **{len(proms)}** |")
    lines.append("")

    # Detailed listing by category
    lines.append("## Detailed Listing")
    lines.append("")

    for cat in sorted(by_category.keys()):
        lines.append(f"### {cat}")
        lines.append("")
        lines.append("| File | Size | Type | Version |")
        lines.append("|------|------|------|---------|")

        for prom in sorted(by_category[cat], key=lambda p: p.filename):
            size_str = _format_size(prom.size)
            version = prom.version_info[:50] if prom.version_info else "-"
            type_short = prom.type_name[:30]
            lines.append(f"| {prom.filename} | {size_str} | {type_short} | {version} |")

        lines.append("")

    # Source files for reference
    lines.append("## Source Paths")
    lines.append("")
    lines.append("Original source locations for each file:")
    lines.append("")
    lines.append("```")
    for prom in sorted(proms, key=lambda p: (p.category, p.filename)):
        rel_path = os.path.relpath(prom.source_path, os.path.dirname(output_dir))
        lines.append(f"{prom.category}/{prom.filename}")
        lines.append(f"  <- {rel_path}")
    lines.append("```")

    return "\n".join(lines)


def _format_size(size: int) -> str:
    """Format file size for display."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Discover and organize SGI PROM/firmware files"
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be done without copying files"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed progress information"
    )
    parser.add_argument(
        "--output", "-o",
        default="../PROMs",
        help="Output directory (default: ../PROMs)"
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default=".",
        help="Input directory to scan (default: current directory)"
    )

    args = parser.parse_args()

    # Resolve paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_dir = os.path.abspath(os.path.join(script_dir, args.input_dir))
    output_dir = os.path.abspath(os.path.join(script_dir, args.output))

    print(f"Scanning for PROM/firmware files in: {input_dir}")
    print()

    # Find all PROMs
    proms = walk_and_find_proms(input_dir, verbose=args.verbose)
    print(f"Found {len(proms)} potential PROM files")

    # Deduplicate
    proms = deduplicate_proms(proms)
    print(f"After deduplication: {len(proms)} unique PROMs")
    print()

    # Summary by category
    by_category: Dict[str, List[PromInfo]] = {}
    for prom in proms:
        cat = prom.category
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(prom)

    print("By category:")
    for cat in sorted(by_category.keys()):
        print(f"  {cat}: {len(by_category[cat])} files")
    print()

    if args.dry_run:
        print("Dry run - not copying files")
        print()
        copy_to_output(proms, output_dir, dry_run=True)
    else:
        # Copy files
        print(f"Copying to: {output_dir}")
        copy_to_output(proms, output_dir)

        # Generate inventory
        inventory = generate_inventory(proms, output_dir)
        inventory_path = os.path.join(output_dir, "INVENTORY.md")
        with open(inventory_path, 'w') as f:
            f.write(inventory)
        print(f"Inventory saved to: {inventory_path}")

    print()
    print("Done!")


if __name__ == "__main__":
    main()
