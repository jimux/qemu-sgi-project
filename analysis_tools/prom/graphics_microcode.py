"""Analyzers for SGI graphics subsystem firmware.

This module provides parsers for various graphics firmware types:
- Voyager/ATI x86 VGA BIOS (O2 graphics upgrade)
- KONA Transport Processor (InfiniteReality, ARM)
- Venice VS2 (VGX/VGXT, MIPS ELF)
- Impact GE11/HQ3 (MGRAS, raw microcode)
- VPro Buzz (Odyssey, transform engine microcode)
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..utils.endian import read_u16_be, read_u32_be, read_cstring
from ..utils.hexdump import find_strings


# ============================================================================
# Voyager / ATI x86 BIOS
# ============================================================================

# x86 VGA BIOS signature
X86_BIOS_MAGIC = b'\x55\xaa'


@dataclass
class VoyagerBIOSInfo:
    """Parsed Voyager/ATI VGA BIOS information."""
    magic: str = "55aa"
    bios_size: int = 0              # Size in 512-byte blocks
    bios_size_bytes: int = 0        # Actual size in bytes
    card_name: str = ""             # e.g., "SGI-ATI Fire GL X1 256 MB"
    chip_name: str = ""             # e.g., "FGL 9700"
    part_number: str = ""           # e.g., "113-99002-109"
    version: str = ""               # e.g., "VER001.109.008.000"
    build_date: str = ""            # e.g., "2003/05/21 15:48"
    copyright: str = ""             # Copyright notice
    pci_vendor_id: int = 0
    pci_device_id: int = 0
    subsystem_info: str = ""


def parse_voyager_bios(data: bytes) -> Optional[VoyagerBIOSInfo]:
    """Parse Voyager/ATI VGA BIOS.

    The ATI BIOS follows the standard x86 VGA BIOS format with
    0x55aa magic at offset 0, followed by size and init code.

    Args:
        data: Binary firmware data

    Returns:
        VoyagerBIOSInfo or None if not a valid VGA BIOS
    """
    if len(data) < 512 or data[0:2] != X86_BIOS_MAGIC:
        return None

    info = VoyagerBIOSInfo()

    # BIOS size in 512-byte blocks at offset 2
    info.bios_size = data[2]
    info.bios_size_bytes = info.bios_size * 512

    # Find build date (typically at offset 0x50)
    if len(data) >= 0x60:
        try:
            date_match = re.search(rb'\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}', data[0x40:0x70])
            if date_match:
                info.build_date = date_match.group(0).decode('ascii', errors='ignore')
        except Exception:
            pass

    # Search for card information strings
    all_strings = find_strings(data, min_length=6)

    for offset, s in all_strings:
        s_upper = s.upper()

        # Card name: "SGI-ATI Fire GL X1 256 MB ..."
        if 'SGI-ATI' in s_upper or 'FIRE GL' in s_upper:
            if 'BIOS' in s_upper:
                # This is the full info string
                info.card_name = _extract_card_name(s)
                info.chip_name = _extract_chip_name(s)
                info.part_number = _extract_part_number(s)
            elif not info.card_name:
                info.card_name = s.strip()

        # Version string: "VER001.109.008.000"
        if s_upper.startswith('VER') and '.' in s:
            version_match = re.match(r'VER[\d.]+', s)
            if version_match:
                info.version = version_match.group(0)

        # Copyright
        if '(C)' in s or 'COPYRIGHT' in s_upper:
            if 'ATI' in s_upper:
                info.copyright = s.strip()

    # Parse PCI information if PCIR header is present
    pcir_offset = data.find(b'PCIR')
    if pcir_offset > 0 and pcir_offset + 24 <= len(data):
        info.pci_vendor_id = int.from_bytes(data[pcir_offset+4:pcir_offset+6], 'little')
        info.pci_device_id = int.from_bytes(data[pcir_offset+6:pcir_offset+8], 'little')

    return info


def _extract_card_name(s: str) -> str:
    """Extract card name from BIOS info string."""
    # "SGI-ATI Fire GL X1 256 MB  FGL 9700 113-99002-109 BIOS"
    match = re.search(r'(SGI-ATI\s+Fire\s+GL\s+\w+\s+\d+\s*MB)', s, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # Try simpler pattern
    match = re.search(r'(ATI\s+Fire\s+GL\s+\w+)', s, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _extract_chip_name(s: str) -> str:
    """Extract chip name from BIOS info string."""
    # "FGL 9700"
    match = re.search(r'(FGL\s+\d+)', s, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _extract_part_number(s: str) -> str:
    """Extract part number from BIOS info string."""
    # "113-99002-109"
    match = re.search(r'(\d{3}-\d{5}-\d{3})', s)
    if match:
        return match.group(1)
    return ""


def format_voyager_report(info: VoyagerBIOSInfo) -> str:
    """Format Voyager BIOS info as a report."""
    lines = []
    lines.append("=== SGI Firmware Analysis ===")
    lines.append("")
    lines.append("Type: x86 VGA BIOS (ATI)")
    lines.append("")

    lines.append("Hardware:")
    if info.card_name:
        lines.append(f"  Card:            {info.card_name}")
    if info.chip_name:
        lines.append(f"  Chip:            {info.chip_name}")
    if info.part_number:
        lines.append(f"  Part Number:     {info.part_number}")
    if info.pci_vendor_id or info.pci_device_id:
        lines.append(f"  PCI ID:          {info.pci_vendor_id:04x}:{info.pci_device_id:04x}")

    lines.append("")
    lines.append("Version:")
    if info.version:
        lines.append(f"  BIOS:            {info.version}")
    if info.build_date:
        lines.append(f"  Build Date:      {info.build_date}")
    if info.copyright:
        lines.append(f"  Copyright:       {info.copyright}")

    return "\n".join(lines)


# ============================================================================
# KONA Transport Processor (InfiniteReality, ARM)
# ============================================================================

# KONA magic at offset 0: 0xbadc0ffe (big-endian)
KONA_MAGIC = b'\xba\xdc\x0f\xfe'


@dataclass
class KonaInfo:
    """Parsed KONA transport processor firmware information."""
    magic: str = "badc0ffe"
    size: int = 0
    architecture: str = "ARM"
    entry_point: int = 0
    notable_functions: List[str] = field(default_factory=list)


def parse_kona_firmware(data: bytes) -> Optional[KonaInfo]:
    """Parse KONA ARM transport processor firmware.

    KONA is an ARM-based processor used in InfiniteReality graphics
    for handling data transport between the host and graphics hardware.

    Args:
        data: Binary firmware data

    Returns:
        KonaInfo or None if not valid KONA firmware
    """
    if len(data) < 256 or data[0:4] != KONA_MAGIC:
        return None

    info = KonaInfo()
    info.size = len(data)

    # Read potential entry point from offset 4-7
    info.entry_point = read_u32_be(data, 4)

    # Search for notable function references
    notable_patterns = [
        'eeprom', 'video', 'clock', 'dma', 'fifo', 'combo',
        'interrupt', 'init', 'reset', 'config'
    ]

    strings = find_strings(data, min_length=4)
    for offset, s in strings:
        s_lower = s.lower()
        for pattern in notable_patterns:
            if pattern in s_lower:
                info.notable_functions.append(s)
                break

    return info


def format_kona_report(info: KonaInfo) -> str:
    """Format KONA firmware info as a report."""
    lines = []
    lines.append("=== SGI Firmware Analysis ===")
    lines.append("")
    lines.append("Type: KONA Transport Processor (ARM)")
    lines.append("")

    lines.append("Header:")
    lines.append(f"  Magic:           0x{info.magic}")
    lines.append(f"  Size:            {info.size} bytes ({info.size // 1024} KB)")
    lines.append(f"  Architecture:    {info.architecture}")
    if info.entry_point:
        lines.append(f"  Entry Point:     0x{info.entry_point:08x}")

    if info.notable_functions:
        lines.append("")
        lines.append("Notable Strings:")
        for func in info.notable_functions[:10]:
            lines.append(f"  - {func}")

    return "\n".join(lines)


# ============================================================================
# Venice VS2 (MIPS ELF)
# ============================================================================

ELF_MAGIC = b'\x7fELF'


@dataclass
class ELFInfo:
    """Parsed ELF header information."""
    magic: str = "7f454c46"
    elf_class: int = 0          # 1=32-bit, 2=64-bit
    endian: str = ""            # "big" or "little"
    elf_version: int = 0
    os_abi: int = 0
    elf_type: int = 0           # 1=relocatable, 2=executable, 3=shared
    machine: int = 0            # Machine type (8=MIPS)
    machine_name: str = ""
    entry_point: int = 0
    program_header_offset: int = 0
    section_header_offset: int = 0
    flags: int = 0
    interpreter: str = ""       # Dynamic linker path if present


# ELF machine types
ELF_MACHINES: Dict[int, str] = {
    0: "None",
    3: "Intel 386",
    8: "MIPS",
    20: "PowerPC",
    40: "ARM",
    62: "x86-64",
    183: "AArch64",
}


def parse_elf_header(data: bytes) -> Optional[ELFInfo]:
    """Parse ELF header.

    Args:
        data: Binary firmware data

    Returns:
        ELFInfo or None if not a valid ELF file
    """
    if len(data) < 64 or data[0:4] != ELF_MAGIC:
        return None

    info = ELFInfo()

    # ELF class (32 or 64 bit) at offset 4
    info.elf_class = data[4]

    # Endianness at offset 5 (1=little, 2=big)
    info.endian = "little" if data[5] == 1 else "big"

    # ELF version at offset 6
    info.elf_version = data[6]

    # OS/ABI at offset 7
    info.os_abi = data[7]

    # For the rest, we need to respect endianness
    is_big = info.endian == "big"

    def read_u16(offset: int) -> int:
        if is_big:
            return int.from_bytes(data[offset:offset+2], 'big')
        return int.from_bytes(data[offset:offset+2], 'little')

    def read_u32(offset: int) -> int:
        if is_big:
            return int.from_bytes(data[offset:offset+4], 'big')
        return int.from_bytes(data[offset:offset+4], 'little')

    # ELF type at offset 16
    info.elf_type = read_u16(16)

    # Machine at offset 18
    info.machine = read_u16(18)
    info.machine_name = ELF_MACHINES.get(info.machine, f"Unknown ({info.machine})")

    # Entry point at offset 24 (32-bit) or different for 64-bit
    if info.elf_class == 1:  # 32-bit
        info.entry_point = read_u32(24)
        info.program_header_offset = read_u32(28)
        info.section_header_offset = read_u32(32)
        info.flags = read_u32(36)
    else:  # 64-bit
        info.entry_point = int.from_bytes(data[24:32], 'big' if is_big else 'little')

    # Try to find interpreter string
    info.interpreter = _find_elf_interpreter(data)

    return info


def _find_elf_interpreter(data: bytes) -> str:
    """Find the dynamic linker/interpreter path in ELF."""
    # Look for common interpreter paths
    interp_patterns = [
        b'/usr/lib32/libc.so',
        b'/lib/ld-linux',
        b'/lib64/ld-linux',
        b'/usr/lib/libc.so',
    ]

    for pattern in interp_patterns:
        idx = data.find(pattern)
        if idx >= 0:
            # Read null-terminated string
            end = data.find(b'\x00', idx)
            if end > idx:
                return data[idx:end].decode('ascii', errors='ignore')

    return ""


def format_elf_report(info: ELFInfo) -> str:
    """Format ELF info as a report."""
    lines = []
    lines.append("=== SGI Firmware Analysis ===")
    lines.append("")
    lines.append("Type: MIPS ELF Executable")
    lines.append("")

    elf_class_str = "32-bit" if info.elf_class == 1 else "64-bit"
    elf_type_map = {1: "Relocatable", 2: "Executable", 3: "Shared Object"}
    elf_type_str = elf_type_map.get(info.elf_type, f"Unknown ({info.elf_type})")

    lines.append("ELF Header:")
    lines.append(f"  Class:           {elf_class_str}")
    lines.append(f"  Endianness:      {info.endian.capitalize()}")
    lines.append(f"  Type:            {elf_type_str}")
    lines.append(f"  Machine:         {info.machine_name}")
    lines.append(f"  Entry Point:     0x{info.entry_point:08x}")
    if info.flags:
        lines.append(f"  Flags:           0x{info.flags:08x}")
    if info.interpreter:
        lines.append(f"  Interpreter:     {info.interpreter}")

    return "\n".join(lines)


# ============================================================================
# Impact GE11/HQ3 Microcode
# ============================================================================

@dataclass
class ImpactMicrocodeInfo:
    """Information about Impact graphics microcode."""
    microcode_type: str = ""    # "GE11" or "HQ3"
    size: int = 0
    word_count: int = 0         # Number of microcode words
    word_size: int = 0          # Bits per word (estimated)
    notable_patterns: List[str] = field(default_factory=list)


def parse_impact_microcode(data: bytes, filename: str = "") -> ImpactMicrocodeInfo:
    """Analyze Impact/MGRAS microcode.

    Impact microcode is raw DSP microcode without headers.
    We can identify GE11 vs HQ3 by size and patterns.

    Args:
        data: Binary microcode data
        filename: Filename for hints

    Returns:
        ImpactMicrocodeInfo
    """
    info = ImpactMicrocodeInfo()
    info.size = len(data)

    # Determine type from filename or size
    fname_lower = filename.lower()
    if 'ge11' in fname_lower:
        info.microcode_type = "GE11"
        info.word_size = 128  # GE11 has 128-bit microinstructions
    elif 'hq3' in fname_lower:
        info.microcode_type = "HQ3"
        info.word_size = 64   # HQ3 command processor
    else:
        # Guess from size
        if len(data) > 500000:
            info.microcode_type = "GE11"
            info.word_size = 128
        else:
            info.microcode_type = "HQ3"
            info.word_size = 64

    # Calculate word count
    word_bytes = info.word_size // 8
    if word_bytes > 0:
        info.word_count = len(data) // word_bytes

    return info


def format_impact_report(info: ImpactMicrocodeInfo) -> str:
    """Format Impact microcode info as a report."""
    lines = []
    lines.append("=== SGI Firmware Analysis ===")
    lines.append("")
    lines.append(f"Type: Impact {info.microcode_type} Microcode")
    lines.append("")

    lines.append("Microcode:")
    lines.append(f"  Processor:       {info.microcode_type}")
    lines.append(f"  Size:            {info.size} bytes ({info.size // 1024} KB)")
    lines.append(f"  Word Size:       {info.word_size} bits")
    lines.append(f"  Word Count:      {info.word_count}")

    return "\n".join(lines)


# ============================================================================
# VPro Buzz Transform Engine
# ============================================================================

@dataclass
class VProBuzzInfo:
    """Information about VPro Buzz transform engine microcode."""
    size: int = 0
    word_count: int = 0


def parse_vpro_buzz(data: bytes) -> VProBuzzInfo:
    """Analyze VPro Buzz transform engine microcode.

    Args:
        data: Binary microcode data

    Returns:
        VProBuzzInfo
    """
    info = VProBuzzInfo()
    info.size = len(data)
    # Buzz uses 128-bit words
    info.word_count = len(data) // 16

    return info


def format_vpro_report(info: VProBuzzInfo) -> str:
    """Format VPro Buzz info as a report."""
    lines = []
    lines.append("=== SGI Firmware Analysis ===")
    lines.append("")
    lines.append("Type: VPro Buzz Transform Engine Microcode")
    lines.append("")

    lines.append("Microcode:")
    lines.append(f"  Size:            {info.size} bytes ({info.size // 1024} KB)")
    lines.append(f"  Word Count:      {info.word_count} (128-bit words)")

    return "\n".join(lines)


# ============================================================================
# Unified graphics analysis
# ============================================================================

@dataclass
class GraphicsAnalysis:
    """Unified graphics firmware analysis result."""
    firmware_type: str = ""
    specific_info: object = None  # Type-specific info dataclass
    notable_strings: List[Tuple[int, str]] = field(default_factory=list)


def analyze_graphics_firmware(data: bytes, filename: str = "") -> Optional[GraphicsAnalysis]:
    """Analyze graphics firmware and return unified result.

    Args:
        data: Binary firmware data
        filename: Filename for hints

    Returns:
        GraphicsAnalysis or None
    """
    # Try Voyager/ATI
    if data[0:2] == X86_BIOS_MAGIC:
        voyager_info = parse_voyager_bios(data)
        if voyager_info:
            return GraphicsAnalysis(
                firmware_type="voyager_x86",
                specific_info=voyager_info,
                notable_strings=find_strings(data, min_length=10)[:20],
            )

    # Try KONA
    if data[0:4] == KONA_MAGIC:
        kona_info = parse_kona_firmware(data)
        if kona_info:
            return GraphicsAnalysis(
                firmware_type="kona_arm",
                specific_info=kona_info,
                notable_strings=find_strings(data, min_length=6)[:20],
            )

    # Try ELF
    if data[0:4] == ELF_MAGIC:
        elf_info = parse_elf_header(data)
        if elf_info:
            return GraphicsAnalysis(
                firmware_type="mips_elf",
                specific_info=elf_info,
                notable_strings=find_strings(data, min_length=6)[:20],
            )

    # Try Impact microcode
    fname_lower = filename.lower()
    if 'ge11' in fname_lower or 'hq3' in fname_lower or 'impact' in fname_lower:
        impact_info = parse_impact_microcode(data, filename)
        return GraphicsAnalysis(
            firmware_type="impact_microcode",
            specific_info=impact_info,
            notable_strings=[],
        )

    # Try VPro Buzz
    if 'buzz' in fname_lower or 'vpro' in fname_lower:
        buzz_info = parse_vpro_buzz(data)
        return GraphicsAnalysis(
            firmware_type="vpro_buzz",
            specific_info=buzz_info,
            notable_strings=[],
        )

    return None


def graphics_to_dict(analysis: GraphicsAnalysis) -> dict:
    """Convert graphics analysis to dictionary.

    Args:
        analysis: GraphicsAnalysis result

    Returns:
        Dictionary for JSON serialization
    """
    result = {
        'firmware_type': analysis.firmware_type,
        'notable_strings': [
            {'offset': o, 'string': s}
            for o, s in analysis.notable_strings[:15]
        ],
    }

    info = analysis.specific_info

    if isinstance(info, VoyagerBIOSInfo):
        result['voyager'] = {
            'card_name': info.card_name,
            'chip_name': info.chip_name,
            'part_number': info.part_number,
            'version': info.version,
            'build_date': info.build_date,
            'copyright': info.copyright,
            'pci_vendor_id': info.pci_vendor_id,
            'pci_device_id': info.pci_device_id,
        }
    elif isinstance(info, KonaInfo):
        result['kona'] = {
            'size': info.size,
            'architecture': info.architecture,
            'entry_point': info.entry_point,
            'notable_functions': info.notable_functions[:10],
        }
    elif isinstance(info, ELFInfo):
        result['elf'] = {
            'class': info.elf_class,
            'endian': info.endian,
            'type': info.elf_type,
            'machine': info.machine_name,
            'entry_point': info.entry_point,
            'interpreter': info.interpreter,
        }
    elif isinstance(info, ImpactMicrocodeInfo):
        result['impact'] = {
            'type': info.microcode_type,
            'size': info.size,
            'word_size': info.word_size,
            'word_count': info.word_count,
        }
    elif isinstance(info, VProBuzzInfo):
        result['vpro'] = {
            'size': info.size,
            'word_count': info.word_count,
        }

    return result
