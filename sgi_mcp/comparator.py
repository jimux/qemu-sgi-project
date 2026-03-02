# SGI PROM Comparative Analysis - Cross-PROM Comparison
"""
Tools for comparing multiple PROM binaries.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Set
import hashlib

from .config import PROM_BASE, detect_platform
from .prom_loader import load_prom, get_prom_metadata, list_prom_files, normalize_data


@dataclass
class DiffRegion:
    """A region of difference between two PROMs."""
    offset: int
    length: int
    prom1_data: bytes
    prom2_data: bytes
    address: int = 0

    def __post_init__(self):
        if self.address == 0:
            self.address = PROM_BASE + self.offset


@dataclass
class CommonCode:
    """A region of common code shared between PROMs."""
    hash: str
    length: int
    locations: List[Tuple[str, int]]  # List of (filename, offset) pairs


def diff_binary(
    prom1: str,
    prom2: str,
    context_bytes: int = 16,
    min_diff_size: int = 1,
    max_diffs: int = 100
) -> List[DiffRegion]:
    """
    Compare two PROM binaries and find differences.

    Args:
        prom1: First PROM filename
        prom2: Second PROM filename
        context_bytes: Bytes of context around differences
        min_diff_size: Minimum difference size to report
        max_diffs: Maximum number of differences to return

    Returns:
        List of DiffRegion objects
    """
    data1 = load_prom(prom1)
    data2 = load_prom(prom2)

    if data1 is None:
        raise ValueError(f"Could not load {prom1}")
    if data2 is None:
        raise ValueError(f"Could not load {prom2}")

    # Normalize endianness if needed
    meta1 = get_prom_metadata(prom1)
    meta2 = get_prom_metadata(prom2)

    if meta1 and meta1.endian != "big":
        data1 = normalize_data(data1, meta1.endian)
    if meta2 and meta2.endian != "big":
        data2 = normalize_data(data2, meta2.endian)

    # Find differences
    diffs = []
    min_len = min(len(data1), len(data2))

    i = 0
    while i < min_len and len(diffs) < max_diffs:
        if data1[i] != data2[i]:
            # Start of a difference
            start = i

            # Find end of difference
            while i < min_len and data1[i] != data2[i]:
                i += 1

            length = i - start

            if length >= min_diff_size:
                diffs.append(DiffRegion(
                    offset=start,
                    length=length,
                    prom1_data=data1[start:i],
                    prom2_data=data2[start:i]
                ))
        else:
            i += 1

    # Report size difference if any
    if len(data1) != len(data2):
        if len(data1) > len(data2):
            diffs.append(DiffRegion(
                offset=len(data2),
                length=len(data1) - len(data2),
                prom1_data=data1[len(data2):],
                prom2_data=b''
            ))
        else:
            diffs.append(DiffRegion(
                offset=len(data1),
                length=len(data2) - len(data1),
                prom1_data=b'',
                prom2_data=data2[len(data1):]
            ))

    return diffs


def format_diff(
    diffs: List[DiffRegion],
    prom1: str,
    prom2: str,
    max_bytes_shown: int = 32
) -> str:
    """
    Format diff results for display.

    Args:
        diffs: List of DiffRegion objects
        prom1: First PROM name
        prom2: Second PROM name
        max_bytes_shown: Maximum bytes to show per diff

    Returns:
        Formatted diff string
    """
    if not diffs:
        return f"No differences found between {prom1} and {prom2}"

    lines = [f"Differences between {prom1} and {prom2}:", ""]

    for i, diff in enumerate(diffs):
        lines.append(f"Diff #{i + 1} at 0x{diff.address:08x} (+0x{diff.offset:05x}), {diff.length} bytes:")

        # Show hex comparison
        bytes1 = diff.prom1_data[:max_bytes_shown]
        bytes2 = diff.prom2_data[:max_bytes_shown]

        hex1 = bytes1.hex() if bytes1 else "(empty)"
        hex2 = bytes2.hex() if bytes2 else "(empty)"

        if len(diff.prom1_data) > max_bytes_shown:
            hex1 += "..."
        if len(diff.prom2_data) > max_bytes_shown:
            hex2 += "..."

        lines.append(f"  < {hex1}")
        lines.append(f"  > {hex2}")
        lines.append("")

    return "\n".join(lines)


def find_common_code(
    prom_files: Optional[List[str]] = None,
    block_size: int = 64,
    min_occurrences: int = 2
) -> List[CommonCode]:
    """
    Find common code sequences across multiple PROMs.

    Uses suffix-based matching to find shared routines.

    Args:
        prom_files: List of PROM filenames (None = all available)
        block_size: Size of blocks to hash
        min_occurrences: Minimum occurrences to report

    Returns:
        List of CommonCode objects
    """
    if prom_files is None:
        prom_files = [p.name for p in list_prom_files()]

    # Hash all blocks in all PROMs
    block_hashes: Dict[str, List[Tuple[str, int]]] = {}

    for filename in prom_files:
        data = load_prom(filename)
        if data is None:
            continue

        meta = get_prom_metadata(filename)
        if meta and meta.endian != "big":
            data = normalize_data(data, meta.endian)

        # Hash blocks aligned to 4 bytes (instruction boundary)
        for offset in range(0, len(data) - block_size + 1, 4):
            block = data[offset:offset + block_size]
            block_hash = hashlib.md5(block).hexdigest()

            if block_hash not in block_hashes:
                block_hashes[block_hash] = []
            block_hashes[block_hash].append((filename, offset))

    # Filter to blocks that appear in multiple files
    common = []
    for block_hash, locations in block_hashes.items():
        # Check for multiple files (not just multiple locations in same file)
        files = set(loc[0] for loc in locations)
        if len(files) >= min_occurrences:
            common.append(CommonCode(
                hash=block_hash,
                length=block_size,
                locations=locations
            ))

    # Sort by number of occurrences (descending)
    common.sort(key=lambda c: len(c.locations), reverse=True)

    return common


def signature_search(
    pattern: bytes,
    prom_files: Optional[List[str]] = None,
    max_results_per_file: int = 100
) -> Dict[str, List[int]]:
    """
    Search for a byte pattern across all PROMs.

    Args:
        pattern: Byte pattern to search for
        prom_files: List of PROM filenames (None = all available)
        max_results_per_file: Maximum matches per file

    Returns:
        Dictionary mapping filename to list of offsets
    """
    if prom_files is None:
        prom_files = [p.name for p in list_prom_files()]

    results: Dict[str, List[int]] = {}

    for filename in prom_files:
        data = load_prom(filename)
        if data is None:
            continue

        matches = []
        start = 0
        while len(matches) < max_results_per_file:
            idx = data.find(pattern, start)
            if idx == -1:
                break
            matches.append(idx)
            start = idx + 1

        if matches:
            results[filename] = matches

    return results


def version_compare(prom1: str, prom2: str) -> Dict:
    """
    Compare two versions of the same PROM platform.

    Args:
        prom1: First PROM filename
        prom2: Second PROM filename

    Returns:
        Comparison summary dictionary
    """
    meta1 = get_prom_metadata(prom1)
    meta2 = get_prom_metadata(prom2)

    if meta1 is None:
        raise ValueError(f"Could not load metadata for {prom1}")
    if meta2 is None:
        raise ValueError(f"Could not load metadata for {prom2}")

    diffs = diff_binary(prom1, prom2)

    # Calculate similarity
    data1 = load_prom(prom1)
    data2 = load_prom(prom2)

    if meta1.endian != "big":
        data1 = normalize_data(data1, meta1.endian)
    if meta2.endian != "big":
        data2 = normalize_data(data2, meta2.endian)

    min_len = min(len(data1), len(data2))
    same_bytes = sum(1 for i in range(min_len) if data1[i] == data2[i])
    similarity = same_bytes / max(len(data1), len(data2)) * 100

    # Find changed regions by category
    total_diff_bytes = sum(d.length for d in diffs)

    return {
        "prom1": {
            "filename": prom1,
            "platform": meta1.platform,
            "part_number": meta1.part_number,
            "size": meta1.size,
            "sha256": meta1.sha256,
            "entry_point": f"0x{meta1.entry_point:08x}"
        },
        "prom2": {
            "filename": prom2,
            "platform": meta2.platform,
            "part_number": meta2.part_number,
            "size": meta2.size,
            "sha256": meta2.sha256,
            "entry_point": f"0x{meta2.entry_point:08x}"
        },
        "comparison": {
            "identical": meta1.sha256 == meta2.sha256,
            "similarity_percent": round(similarity, 2),
            "diff_regions": len(diffs),
            "total_diff_bytes": total_diff_bytes,
            "same_size": meta1.size == meta2.size,
            "same_platform": meta1.platform == meta2.platform,
            "same_entry_point": meta1.entry_point == meta2.entry_point
        }
    }


def find_unique_code(
    prom_file: str,
    other_proms: Optional[List[str]] = None,
    block_size: int = 32
) -> List[Tuple[int, int]]:
    """
    Find code regions unique to a specific PROM.

    Args:
        prom_file: PROM to analyze
        other_proms: PROMs to compare against (None = all others)
        block_size: Size of blocks to compare

    Returns:
        List of (offset, length) tuples for unique regions
    """
    if other_proms is None:
        all_proms = [p.name for p in list_prom_files()]
        other_proms = [p for p in all_proms if p != prom_file]

    target_data = load_prom(prom_file)
    if target_data is None:
        return []

    meta = get_prom_metadata(prom_file)
    if meta and meta.endian != "big":
        target_data = normalize_data(target_data, meta.endian)

    # Build set of all blocks in other PROMs
    other_blocks: Set[bytes] = set()

    for filename in other_proms:
        data = load_prom(filename)
        if data is None:
            continue

        file_meta = get_prom_metadata(filename)
        if file_meta and file_meta.endian != "big":
            data = normalize_data(data, file_meta.endian)

        for offset in range(0, len(data) - block_size + 1, 4):
            other_blocks.add(data[offset:offset + block_size])

    # Find unique blocks in target
    unique_offsets = []
    for offset in range(0, len(target_data) - block_size + 1, 4):
        block = target_data[offset:offset + block_size]
        if block not in other_blocks:
            unique_offsets.append(offset)

    # Merge adjacent unique blocks into regions
    if not unique_offsets:
        return []

    regions = []
    region_start = unique_offsets[0]
    region_end = region_start + block_size

    for offset in unique_offsets[1:]:
        if offset <= region_end:
            # Extend current region
            region_end = offset + block_size
        else:
            # Start new region
            regions.append((region_start, region_end - region_start))
            region_start = offset
            region_end = offset + block_size

    # Don't forget last region
    regions.append((region_start, region_end - region_start))

    return regions
