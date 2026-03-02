"""SGI Firmware type detection and classification.

This module provides an enum of all supported firmware types and
functions to detect the type from binary data based on magic signatures
and header patterns.
"""

from enum import Enum, auto
from typing import Optional, Tuple


class FirmwareType(Enum):
    """Enumeration of all recognized SGI firmware types."""

    # CPU PROMs
    MIPS_EXCEPTION_VECTOR = auto()  # IP4-IP30: Traditional MIPS PROM
    SHDR = auto()                    # IP32/O2: SHDR header format
    SN0_CONTAINER = auto()           # IP27, IO6: Origin 2000
    SN1_CONTAINER = auto()           # IP35: Origin 3000

    # Graphics Microcode
    KONA_ARM = auto()                # InfiniteReality transport processor (ARM)
    MIPS_ELF = auto()                # Venice VS2 (MIPS ELF)
    IMPACT_MICROCODE = auto()        # MGRAS GE11/HQ3 (raw microcode)
    VPRO_BUZZ = auto()               # Odyssey transform engine
    VOYAGER_X86 = auto()             # O2 GBE (ATI x86 BIOS)

    # System Controllers
    SYSCO_68K = auto()               # L1/L2 controllers (68K, ESTFBINR)
    PBAY_MCU = auto()                # Power bay microcontroller
    MMSC_X86 = auto()                # MMSC controller (x86)

    UNKNOWN = auto()


# Display names for firmware types
FIRMWARE_TYPE_NAMES = {
    FirmwareType.MIPS_EXCEPTION_VECTOR: "MIPS Exception Vector Table",
    FirmwareType.SHDR: "SHDR Header (O2/IP32)",
    FirmwareType.SN0_CONTAINER: "SN0 Container (Origin 2000)",
    FirmwareType.SN1_CONTAINER: "SN1 Container (Origin 3000)",
    FirmwareType.KONA_ARM: "KONA Transport Processor (ARM)",
    FirmwareType.MIPS_ELF: "MIPS ELF Executable",
    FirmwareType.IMPACT_MICROCODE: "Impact Graphics Microcode",
    FirmwareType.VPRO_BUZZ: "VPro Buzz Transform Engine",
    FirmwareType.VOYAGER_X86: "Voyager/ATI x86 VGA BIOS",
    FirmwareType.SYSCO_68K: "System Controller (68K)",
    FirmwareType.PBAY_MCU: "Power Bay Controller (MCU)",
    FirmwareType.MMSC_X86: "MMSC Controller (x86)",
    FirmwareType.UNKNOWN: "Unknown",
}

# Short descriptions for firmware types
FIRMWARE_TYPE_DESCRIPTIONS = {
    FirmwareType.MIPS_EXCEPTION_VECTOR: "Traditional SGI CPU PROM (IP4-IP30)",
    FirmwareType.SHDR: "SGI O2 CPU PROM",
    FirmwareType.SN0_CONTAINER: "Origin 2000/Onyx2 CPU or I/O PROM",
    FirmwareType.SN1_CONTAINER: "Origin 3000/Onyx 3 CPU PROM",
    FirmwareType.KONA_ARM: "InfiniteReality graphics transport processor",
    FirmwareType.MIPS_ELF: "Venice/VGX graphics processor",
    FirmwareType.IMPACT_MICROCODE: "MGRAS/Impact graphics microcode",
    FirmwareType.VPRO_BUZZ: "Odyssey/VPro graphics transform engine",
    FirmwareType.VOYAGER_X86: "O2 Voyager graphics (ATI Fire GL)",
    FirmwareType.SYSCO_68K: "Origin L1/L2 system controller",
    FirmwareType.PBAY_MCU: "Origin power bay controller",
    FirmwareType.MMSC_X86: "Multi-Module System Controller",
    FirmwareType.UNKNOWN: "Unrecognized firmware format",
}


# Magic signature bytes and their corresponding firmware types
# Format: (offset, bytes, firmware_type)
MAGIC_SIGNATURES: list[Tuple[int, bytes, FirmwareType]] = [
    # SN0/SN1 container: "JFKSWCSM" at offset 0x40
    (0x40, b'JFKSWCSM', FirmwareType.SN0_CONTAINER),

    # SHDR header: "SHDR" at offset 0x08
    (0x08, b'SHDR', FirmwareType.SHDR),

    # ELF magic at offset 0
    (0x00, b'\x7fELF', FirmwareType.MIPS_ELF),

    # KONA ARM: 0xbadc0ffe at offset 0 (big-endian)
    (0x00, b'\xba\xdc\x0f\xfe', FirmwareType.KONA_ARM),

    # x86 VGA BIOS signature at offset 0
    (0x00, b'\x55\xaa', FirmwareType.VOYAGER_X86),

    # SYSCO 68K: "ESTFBINR" at offset 0
    (0x00, b'ESTFBINR', FirmwareType.SYSCO_68K),
]

# MMSC magic at offset 0x18: 0x5aa5a55a
MMSC_MAGIC_OFFSET = 0x18
MMSC_MAGIC = b'\x5a\xa5\xa5\x5a'


def detect_firmware_type(data: bytes, filename: str = "") -> FirmwareType:
    """Detect firmware type from binary data.

    Uses magic signatures and heuristics to identify the firmware type.

    Args:
        data: Binary firmware data
        filename: Optional filename for hints

    Returns:
        FirmwareType enum value
    """
    if len(data) < 16:
        return FirmwareType.UNKNOWN

    # Check all magic signatures first (these are definitive)
    for offset, magic, fw_type in MAGIC_SIGNATURES:
        if len(data) >= offset + len(magic):
            if data[offset:offset + len(magic)] == magic:
                # Distinguish SN0 vs SN1 for JFKSWCSM containers
                if fw_type == FirmwareType.SN0_CONTAINER:
                    return _classify_sn_container(data, filename)
                return fw_type

    # Check for MMSC magic at offset 0x18
    if len(data) >= MMSC_MAGIC_OFFSET + len(MMSC_MAGIC):
        if data[MMSC_MAGIC_OFFSET:MMSC_MAGIC_OFFSET + len(MMSC_MAGIC)] == MMSC_MAGIC:
            return FirmwareType.MMSC_X86

    # Check filename-based heuristics BEFORE falling back to generic MIPS detection
    # This ensures graphics microcode isn't misidentified as CPU PROM

    # Check for pbay firmware (small MCU firmware with "Owen Yah" author)
    if _is_pbay_firmware(data, filename):
        return FirmwareType.PBAY_MCU

    # Check for Impact microcode based on filename hints
    if _is_impact_microcode(data, filename):
        return FirmwareType.IMPACT_MICROCODE

    # Check for VPro Buzz based on filename hints
    if _is_vpro_buzz(data, filename):
        return FirmwareType.VPRO_BUZZ

    # Now check for MIPS exception vector format (generic CPU PROM)
    if _is_mips_exception_vector(data):
        return FirmwareType.MIPS_EXCEPTION_VECTOR

    return FirmwareType.UNKNOWN


def _classify_sn_container(data: bytes, filename: str) -> FirmwareType:
    """Classify SN0 vs SN1 container based on content.

    Args:
        data: Binary data with JFKSWCSM header
        filename: Filename for hints

    Returns:
        SN0_CONTAINER or SN1_CONTAINER
    """
    # Check module name at offset 0x80
    if len(data) >= 0x90:
        try:
            module_name = data[0x80:0x90].split(b'\x00')[0].decode('ascii', errors='ignore')
            if 'ip35' in module_name.lower():
                return FirmwareType.SN1_CONTAINER
        except Exception:
            pass

    # Check filename hints
    fname_lower = filename.lower()
    if 'ip35' in fname_lower:
        return FirmwareType.SN1_CONTAINER

    # Search for SN1/IP35 markers in strings
    try:
        data_str = data[:0x2000].decode('ascii', errors='ignore')
        if 'SN1' in data_str or 'IP35' in data_str:
            return FirmwareType.SN1_CONTAINER
    except Exception:
        pass

    return FirmwareType.SN0_CONTAINER


def _is_mips_exception_vector(data: bytes) -> bool:
    """Check if data starts with MIPS exception vector table.

    MIPS PROMs typically start with a J (jump) or B (branch) instruction
    at offset 0 (reset vector). Some PROMs (like IP28) have zeros at offset 0
    with the actual instruction at offset 4 or 8.

    Args:
        data: Binary data to check

    Returns:
        True if this looks like a MIPS exception vector PROM
    """
    if len(data) < 8:
        return False

    # Valid reset vector opcodes:
    # 0x02 = J (jump) - most common
    # 0x04 = BEQ (branch if equal, often used as unconditional)
    # 0x10 = COP0 (coprocessor 0, used for cache init on some)
    # 0x01 = REGIMM (branch variants)
    valid_reset_opcodes = {0x02, 0x04, 0x10, 0x01}

    # Check first word at offset 0
    first_word = int.from_bytes(data[0:4], 'big')
    opcode = (first_word >> 26) & 0x3F

    if opcode in valid_reset_opcodes:
        return True

    # Some PROMs (IP28) have zeros at offset 0, instruction at offset 4
    if first_word == 0 and len(data) >= 8:
        second_word = int.from_bytes(data[4:8], 'big')
        second_opcode = (second_word >> 26) & 0x3F
        if second_opcode in valid_reset_opcodes:
            return True

    return False


def _is_pbay_firmware(data: bytes, filename: str) -> bool:
    """Check if this is power bay controller firmware.

    pbay firmware is small MCU code with "Owen Yah" author string.

    Args:
        data: Binary data to check
        filename: Filename for hints

    Returns:
        True if this looks like pbay firmware
    """
    # Size hint: pbay is typically around 5-6KB
    if len(data) > 16384:
        return False

    # Filename hint
    if 'pbay' in filename.lower():
        return True

    # Look for author signature
    if b'Owen Yah' in data[:256]:
        return True

    return False


def _is_impact_microcode(data: bytes, filename: str) -> bool:
    """Check if this is Impact/MGRAS graphics microcode.

    Args:
        data: Binary data to check
        filename: Filename for hints

    Returns:
        True if this looks like Impact microcode
    """
    fname_lower = filename.lower()
    if 'ge11' in fname_lower or 'hq3' in fname_lower or 'impact' in fname_lower:
        return True
    return False


def _is_vpro_buzz(data: bytes, filename: str) -> bool:
    """Check if this is VPro Buzz transform engine microcode.

    Args:
        data: Binary data to check
        filename: Filename for hints

    Returns:
        True if this looks like VPro Buzz microcode
    """
    fname_lower = filename.lower()
    if 'buzz' in fname_lower or 'vpro' in fname_lower:
        return True
    return False


def get_type_name(fw_type: FirmwareType) -> str:
    """Get display name for a firmware type.

    Args:
        fw_type: Firmware type enum value

    Returns:
        Human-readable name
    """
    return FIRMWARE_TYPE_NAMES.get(fw_type, "Unknown")


def get_type_description(fw_type: FirmwareType) -> str:
    """Get description for a firmware type.

    Args:
        fw_type: Firmware type enum value

    Returns:
        Short description
    """
    return FIRMWARE_TYPE_DESCRIPTIONS.get(fw_type, "Unknown firmware type")
