"""
INT3 interrupt controller debugging utilities.

Reusable helpers for interpreting INT3 interrupt state during debugging.
Decodes local0/local1/map status registers and identifies spurious sources.

Usage:
    from tests.helpers.int3_debugger import (
        decode_bits, pending_sources, check_spurious, format_int3_state
    )

    # Decode a raw register value
    bits = decode_bits(0x86, LOCAL0_BITS)
    # {'FIFO': False, 'SCSI0': True, 'SCSI1': True, 'ETHERNET': False,
    #  'MC_DMA': False, 'PARALLEL': False, 'GRAPHICS': False, 'MAPPABLE0': True}

    # Check for spurious sources
    spurious = check_spurious(0xA6, EMULATED_LOCAL0_MASK, LOCAL0_BITS)
    # ['PARALLEL']  -- bit 0x20 is set but not emulated
"""

# INT3 LOCAL0 status/mask register bit definitions
LOCAL0_BITS = {
    0x01: "FIFO",
    0x02: "SCSI0",
    0x04: "SCSI1",
    0x08: "ETHERNET",
    0x10: "MC_DMA",
    0x20: "PARALLEL",
    0x40: "GRAPHICS",
    0x80: "MAPPABLE0",
}

# INT3 LOCAL1 status/mask register bit definitions
LOCAL1_BITS = {
    0x01: "GP0",
    0x02: "POWER",
    0x04: "GP2",
    0x08: "LCL0",
    0x10: "HPC_DMA",
    0x20: "AC_FAIL",
    0x40: "VIDEO",
    0x80: "RETRACE",
}

# INT3 MAP status/mask register bit definitions
MAP_BITS = {
    0x01: "VERT",
    0x02: "PASSWD",
    0x04: "ISDN_POWER",
    0x08: "EISA",
    0x10: "KBDMS",
    0x20: "DUART",
    0x40: "DRAIN0",
    0x80: "DRAIN1",
}

# Mask of sources we actually emulate in QEMU (from the bug fix)
# SCSI0 (0x02) | SCSI1 (0x04) | MAPPABLE0 (0x80)
EMULATED_LOCAL0_MASK = 0x86


def decode_bits(val, bit_names):
    """Decode a register value into a dict of {name: bool}.

    Args:
        val: Register value (uint8)
        bit_names: Dict mapping bit masks to names (e.g., LOCAL0_BITS)

    Returns:
        Dict of {name: True/False} for each defined bit
    """
    return {name: bool(val & mask) for mask, name in sorted(bit_names.items())}


def pending_sources(stat, mask, bit_names):
    """Return list of source names that are both active and enabled.

    Args:
        stat: Status register value
        mask: Mask (enable) register value
        bit_names: Dict mapping bit masks to names

    Returns:
        List of source names that are pending (stat & mask bit set)
    """
    pending = stat & mask
    return [name for bit_mask, name in sorted(bit_names.items())
            if pending & bit_mask]


def check_spurious(stat, emulated_mask, bit_names):
    """Return list of source names that are active but not emulated.

    These are "spurious" — the bit is set in the status register but
    we don't have hardware to generate or acknowledge it.

    Args:
        stat: Status register value
        emulated_mask: Bitmask of sources we actually emulate
        bit_names: Dict mapping bit masks to names

    Returns:
        List of source names that are set but not in the emulated mask
    """
    spurious = stat & ~emulated_mask
    return [name for bit_mask, name in sorted(bit_names.items())
            if spurious & bit_mask]


def format_int3_state(l0_stat, l0_mask, l1_stat, l1_mask,
                      map_stat, map_m0, map_m1):
    """Format a multi-line summary of INT3 interrupt state.

    Args:
        l0_stat: LOCAL0 status register
        l0_mask: LOCAL0 mask register
        l1_stat: LOCAL1 status register
        l1_mask: LOCAL1 mask register
        map_stat: MAP status register
        map_m0: MAP_MASK0 register
        map_m1: MAP_MASK1 register

    Returns:
        Multi-line string summarizing the interrupt state
    """
    lines = []
    lines.append(f"INT3 State:")
    lines.append(f"  LOCAL0: stat=0x{l0_stat:02x} mask=0x{l0_mask:02x}")

    l0_pending = pending_sources(l0_stat, l0_mask, LOCAL0_BITS)
    l0_spurious = check_spurious(l0_stat, EMULATED_LOCAL0_MASK, LOCAL0_BITS)
    if l0_pending:
        lines.append(f"    Pending: {', '.join(l0_pending)}")
    if l0_spurious:
        lines.append(f"    SPURIOUS: {', '.join(l0_spurious)}")

    lines.append(f"  LOCAL1: stat=0x{l1_stat:02x} mask=0x{l1_mask:02x}")
    l1_pending = pending_sources(l1_stat, l1_mask, LOCAL1_BITS)
    if l1_pending:
        lines.append(f"    Pending: {', '.join(l1_pending)}")

    lines.append(f"  MAP: stat=0x{map_stat:02x} mask0=0x{map_m0:02x} mask1=0x{map_m1:02x}")
    map_pending0 = pending_sources(map_stat, map_m0, MAP_BITS)
    map_pending1 = pending_sources(map_stat, map_m1, MAP_BITS)
    if map_pending0:
        lines.append(f"    → MAPPABLE0: {', '.join(map_pending0)}")
    if map_pending1:
        lines.append(f"    → MAPPABLE1: {', '.join(map_pending1)}")

    return "\n".join(lines)
