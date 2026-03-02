"""Heuristic analysis for unknown firmware types.

This module provides analysis capabilities for firmware that doesn't
match any known magic signatures. It attempts to detect:
- Processor architecture from instruction patterns
- Common metadata strings (version, author, date, copyright)
- Data entropy to identify compressed/encrypted sections
- Notable patterns and structures
"""

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..utils.hexdump import find_strings


@dataclass
class ArchitectureHint:
    """Detected processor architecture hints."""
    name: str               # e.g., "MIPS", "ARM", "x86", "68K", "PowerPC"
    confidence: float       # 0.0 to 1.0
    indicators: List[str]   # What patterns were found
    endian: str = "unknown"  # "big", "little", or "unknown"


@dataclass
class StringMatch:
    """A matched string with categorization."""
    offset: int
    text: str
    category: str   # "version", "author", "date", "copyright", "vendor", "filename"


@dataclass
class HeuristicResult:
    """Results from heuristic firmware analysis."""
    architecture: Optional[ArchitectureHint] = None
    entropy: float = 0.0
    entropy_description: str = ""
    strings: List[StringMatch] = field(default_factory=list)
    notable_patterns: List[Tuple[int, str]] = field(default_factory=list)
    magic_bytes: Optional[str] = None


# Architecture detection patterns
# Each tuple: (byte_sequence, architecture_name, endian, description)
ARCH_PATTERNS: List[Tuple[bytes, str, str, str]] = [
    # MIPS patterns
    (b'\x03\xe0\x00\x08', 'MIPS', 'big', 'jr $ra (function return)'),
    (b'\x27\xbd', 'MIPS', 'big', 'addiu $sp (stack frame setup)'),
    (b'\x3c\x1c', 'MIPS', 'big', 'lui $gp (global pointer setup)'),

    # ARM patterns (32-bit)
    (b'\xe1\xa0\x00\x00', 'ARM', 'little', 'MOV R0,R0 (NOP)'),
    (b'\xe5\x9f', 'ARM', 'little', 'LDR Rx,[PC] (PC-relative load)'),
    (b'\xe9\x2d', 'ARM', 'little', 'STMFD (push multiple)'),
    (b'\xe8\xbd', 'ARM', 'little', 'LDMFD (pop multiple)'),

    # x86 patterns
    (b'\x55\x8b\xec', 'x86', 'little', 'push ebp; mov ebp,esp (function prologue)'),
    (b'\x55\x89\xe5', 'x86', 'little', 'push ebp; mov ebp,esp (alternate)'),
    (b'\xfa', 'x86', 'little', 'CLI (disable interrupts)'),
    (b'\xfb', 'x86', 'little', 'STI (enable interrupts)'),
    (b'\xc3', 'x86', 'little', 'RET (return)'),
    (b'\x90', 'x86', 'little', 'NOP'),

    # Motorola 68K patterns
    (b'\x4e\x75', '68K', 'big', 'RTS (return from subroutine)'),
    (b'\x4e\x56', '68K', 'big', 'LINK (frame setup)'),
    (b'\x4e\x5e', '68K', 'big', 'UNLK (frame teardown)'),
    (b'\x48\xe7', '68K', 'big', 'MOVEM.L (push registers)'),
    (b'\x4c\xdf', '68K', 'big', 'MOVEM.L (pop registers)'),

    # PowerPC patterns
    (b'\x7c\x08\x02\xa6', 'PowerPC', 'big', 'mflr r0 (save link register)'),
    (b'\x7c\x08\x03\xa6', 'PowerPC', 'big', 'mtlr r0 (restore link register)'),
    (b'\x4e\x80\x00\x20', 'PowerPC', 'big', 'blr (branch to link register)'),
]

# Known magic bytes at offset 0
KNOWN_MAGIC: Dict[bytes, str] = {
    b'\x7fELF': 'ELF executable',
    b'\x55\xaa': 'x86 BIOS/boot sector',
    b'\x4d\x5a': 'DOS/Windows MZ executable',
    b'\x89PNG': 'PNG image',
    b'\x1f\x9d': 'Unix compress',
    b'\x1f\x8b': 'gzip compressed',
    b'\x42\x5a': 'bzip2 compressed',
    b'\x50\x4b': 'ZIP archive',
    b'\xca\xfe\xba\xbe': 'Mach-O fat binary',
    b'\xfe\xed\xfa\xce': 'Mach-O 32-bit',
    b'\xfe\xed\xfa\xcf': 'Mach-O 64-bit',
}

# String patterns to search for
STRING_PATTERNS = [
    # Version patterns
    (re.compile(r'[Vv]ersion\s+[\d.]+[a-zA-Z]*'), 'version'),
    (re.compile(r'[Vv]er\.?\s*[\d.]+'), 'version'),
    (re.compile(r'[Rr]ev\.?\s*[\d.]+'), 'version'),
    (re.compile(r'[Rr]elease\s+[\d.]+'), 'version'),
    (re.compile(r'VER[\d.]+'), 'version'),

    # Author patterns
    (re.compile(r'[Aa]uthor[:\s]+[\w\s]+'), 'author'),
    (re.compile(r'<<\s*[\w\s]+\s*>>'), 'author'),
    (re.compile(r'[Ww]ritten by[\s:]+[\w\s]+'), 'author'),

    # Date patterns
    (re.compile(r'\d{4}/\d{2}/\d{2}'), 'date'),
    (re.compile(r'\d{2}/\d{2}/\d{4}'), 'date'),
    (re.compile(r'\d{4}-\d{2}-\d{2}'), 'date'),
    (re.compile(r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}'), 'date'),
    (re.compile(r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}'), 'date'),

    # Copyright patterns
    (re.compile(r'[Cc]opyright[\s(]+[\w\s,.-]+\d{4}'), 'copyright'),
    (re.compile(r'\([Cc]\)\s*\d{4}'), 'copyright'),
    (re.compile(r'\xc2\xa9\s*\d{4}'), 'copyright'),  # (C) symbol

    # Vendor patterns
    (re.compile(r'Silicon Graphics'), 'vendor'),
    (re.compile(r'\bSGI\b'), 'vendor'),
    (re.compile(r'ATI Technologies'), 'vendor'),
    (re.compile(r'Motorola'), 'vendor'),

    # Filename patterns
    (re.compile(r'[\w/]+\.[chsS]'), 'filename'),
    (re.compile(r'[\w/]+\.asm'), 'filename'),
]


def calculate_entropy(data: bytes) -> float:
    """Calculate Shannon entropy of binary data.

    Entropy indicates the randomness of data:
    - Low (< 5.0): Sparse data, padding, or mostly text
    - Medium (5.0-7.0): Typical compiled code
    - High (> 7.5): Compressed or encrypted data

    Args:
        data: Binary data to analyze

    Returns:
        Entropy value between 0.0 and 8.0
    """
    if len(data) == 0:
        return 0.0

    # Count byte frequencies
    byte_counts = [0] * 256
    for byte in data:
        byte_counts[byte] += 1

    # Calculate entropy
    entropy = 0.0
    data_len = len(data)
    for count in byte_counts:
        if count > 0:
            p = count / data_len
            entropy -= p * math.log2(p)

    return entropy


def get_entropy_description(entropy: float) -> str:
    """Get human-readable description of entropy value.

    Args:
        entropy: Entropy value (0-8)

    Returns:
        Description string
    """
    if entropy < 1.0:
        return "Very low (mostly zeros/padding)"
    elif entropy < 4.0:
        return "Low (sparse data or text)"
    elif entropy < 5.0:
        return "Below average (structured data)"
    elif entropy < 6.0:
        return "Medium (typical compiled code)"
    elif entropy < 7.0:
        return "Above average (dense code)"
    elif entropy < 7.5:
        return "High (possibly compressed)"
    else:
        return "Very high (compressed or encrypted)"


def detect_architecture(data: bytes) -> Optional[ArchitectureHint]:
    """Detect processor architecture from instruction patterns.

    Scans the data for known instruction patterns to identify
    the target processor architecture.

    Args:
        data: Binary firmware data

    Returns:
        ArchitectureHint or None if no architecture detected
    """
    if len(data) < 16:
        return None

    # Count pattern matches per architecture
    arch_scores: Dict[str, List[str]] = {}
    arch_endian: Dict[str, str] = {}

    # Sample regions: start, middle, common code offsets
    sample_regions = [
        (0, min(4096, len(data))),
        (len(data) // 4, min(len(data) // 4 + 2048, len(data))),
        (len(data) // 2, min(len(data) // 2 + 2048, len(data))),
    ]

    for start, end in sample_regions:
        region = data[start:end]

        for pattern, arch_name, endian, desc in ARCH_PATTERNS:
            if pattern in region:
                if arch_name not in arch_scores:
                    arch_scores[arch_name] = []
                    arch_endian[arch_name] = endian

                indicator = f"{desc} at region 0x{start:x}"
                if indicator not in arch_scores[arch_name]:
                    arch_scores[arch_name].append(indicator)

    if not arch_scores:
        return None

    # Find architecture with most matches
    best_arch = max(arch_scores.keys(), key=lambda k: len(arch_scores[k]))
    matches = arch_scores[best_arch]

    # Calculate confidence based on number of matches
    confidence = min(1.0, len(matches) / 5.0)

    return ArchitectureHint(
        name=best_arch,
        confidence=confidence,
        indicators=matches[:5],  # Limit to 5 indicators
        endian=arch_endian.get(best_arch, "unknown")
    )


def find_notable_strings(data: bytes, limit: int = 20) -> List[StringMatch]:
    """Find and categorize notable strings in firmware.

    Searches for version numbers, authors, dates, copyrights, etc.

    Args:
        data: Binary firmware data
        limit: Maximum strings to return

    Returns:
        List of categorized string matches
    """
    results: List[StringMatch] = []
    seen_texts: set = set()

    # Get all printable strings
    all_strings = find_strings(data, min_length=4)

    for offset, text in all_strings:
        if len(results) >= limit:
            break

        # Check against patterns
        for pattern, category in STRING_PATTERNS:
            match = pattern.search(text)
            if match:
                matched_text = match.group(0)
                if matched_text not in seen_texts:
                    seen_texts.add(matched_text)
                    results.append(StringMatch(
                        offset=offset + match.start(),
                        text=matched_text,
                        category=category
                    ))
                break

    return results


def check_magic_bytes(data: bytes) -> Optional[str]:
    """Check for known magic bytes at file start.

    Args:
        data: Binary firmware data

    Returns:
        Description of magic or None
    """
    for magic, description in KNOWN_MAGIC.items():
        if data.startswith(magic):
            return description
    return None


def analyze_unknown_firmware(data: bytes) -> HeuristicResult:
    """Perform heuristic analysis on unknown firmware.

    This is the main entry point for analyzing firmware that doesn't
    match any known format. It attempts to extract as much useful
    information as possible using heuristics.

    Args:
        data: Binary firmware data

    Returns:
        HeuristicResult with all detected information
    """
    result = HeuristicResult()

    # Check magic bytes
    result.magic_bytes = check_magic_bytes(data)

    # Calculate entropy
    result.entropy = calculate_entropy(data)
    result.entropy_description = get_entropy_description(result.entropy)

    # Detect architecture
    result.architecture = detect_architecture(data)

    # Find notable strings
    result.strings = find_notable_strings(data)

    # Look for notable patterns
    notable = []

    # Check for repeating pointer tables
    if len(data) >= 256:
        # Look for sequences of addresses (common in vector tables)
        for offset in range(0, min(512, len(data) - 16), 4):
            word = int.from_bytes(data[offset:offset+4], 'big')
            # SGI addresses often start with 0x8 or 0xa or 0xbf
            if word >> 28 in (0x8, 0xa, 0xb):
                next_word = int.from_bytes(data[offset+4:offset+8], 'big')
                if next_word >> 28 in (0x8, 0xa, 0xb):
                    notable.append((offset, "Possible address/vector table"))
                    break

    # Check for large zero regions (padding)
    zero_count = 0
    for i, byte in enumerate(data[:1024]):
        if byte == 0:
            zero_count += 1
    if zero_count > 256:
        notable.append((0, f"Header contains {zero_count}/1024 zero bytes (padded)"))

    result.notable_patterns = notable

    return result


def format_heuristic_report(result: HeuristicResult, indent: str = "  ") -> str:
    """Format heuristic analysis results as a human-readable report.

    Args:
        result: HeuristicResult from analysis
        indent: Indentation prefix

    Returns:
        Formatted report string
    """
    lines = []

    lines.append("Detected Characteristics:")

    # Architecture
    if result.architecture:
        arch = result.architecture
        conf_pct = int(arch.confidence * 100)
        lines.append(f"{indent}Architecture:    Possibly {arch.name} ({arch.endian}-endian, {conf_pct}% confidence)")
        for indicator in arch.indicators[:3]:
            lines.append(f"{indent}                 - {indicator}")
    else:
        lines.append(f"{indent}Architecture:    Unknown")

    # Entropy
    lines.append(f"{indent}Entropy:         {result.entropy:.2f} ({result.entropy_description})")

    # Magic bytes
    if result.magic_bytes:
        lines.append(f"{indent}Magic:           {result.magic_bytes}")

    # Notable patterns
    if result.notable_patterns:
        lines.append("")
        lines.append("Notable Patterns:")
        for offset, desc in result.notable_patterns:
            lines.append(f"{indent}0x{offset:04x}: {desc}")

    # Strings found
    if result.strings:
        lines.append("")
        lines.append("Strings Found:")

        # Group by category
        by_category: Dict[str, List[StringMatch]] = {}
        for sm in result.strings:
            if sm.category not in by_category:
                by_category[sm.category] = []
            by_category[sm.category].append(sm)

        category_order = ['version', 'author', 'date', 'copyright', 'vendor', 'filename']
        for cat in category_order:
            if cat in by_category:
                for sm in by_category[cat][:3]:  # Limit per category
                    lines.append(f"{indent}0x{sm.offset:04x}: {sm.text} [{cat}]")

    return "\n".join(lines)
