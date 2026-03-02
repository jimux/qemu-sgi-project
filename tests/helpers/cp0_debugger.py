"""
CP0 timer and interrupt state debugging utilities.

Reusable helpers for interpreting MIPS CP0 Status, Cause, and IntCtl
registers, with IRIX-specific interrupt numbering (1-based).

Usage:
    from tests.helpers.cp0_debugger import (
        irix_to_hw_ip, hw_ip_to_irix, sr_ibit,
        decode_status_im, decode_cause_ip,
        deliverable_interrupts, format_cp0_irq_state
    )

    # Convert IRIX interrupt number to hardware IP
    hw = irix_to_hw_ip(8)  # IRIX IP8 = hardware IP7 (CP0 timer)

    # Decode which interrupts are enabled in Status register
    enabled = decode_status_im(0x0000e001)  # IE=1, IM[15:13]=1 → IP5,IP6,IP7

    # Full state summary
    print(format_cp0_irq_state(sr=0x0000ff01, cause=0x00008000, intctl=0xe0000000))
"""

# IRIX c0vec_tbl mapping (1-based IRIX IP# → handler name)
# From IRIX kernel intr.c: the c0vec_tbl[] array indexed by IP number
C0VEC_TABLE = {
    1: "timein",           # Software interrupt 0
    2: "pokesoftclk",      # Software interrupt 1
    3: "lcl0_intr",        # Local 0 (SCSI, ethernet, etc.)
    4: "lcl1_intr",        # Local 1 (panel, DMA, etc.)
    5: "clock",            # PIT Timer 0 (scheduling clock for non-IOC1)
    6: "ackkgclock",       # PIT Timer 1 (profiling clock)
    7: "buserror_intr",    # Bus error
    8: "r4kcount_intr",    # R4000 Count/Compare timer (scheduling for IOC1)
}

# IRIX SR_IBIT values (1-based) — each enables one interrupt pin
# SR_IBIT1 = 0x0100 (bit 8), SR_IBIT8 = 0x8000 (bit 15)
SR_IBITS = {
    1: 0x0100,
    2: 0x0200,
    3: 0x0400,
    4: 0x0800,
    5: 0x1000,
    6: 0x2000,
    7: 0x4000,
    8: 0x8000,
}

# IRIX SR_IMASK values (non-IP32 platforms like IP22/IP24/IP28)
# SR_IMASK0 enables all (IP1-IP8), SR_IMASK8 disables all
# Each level N enables only IP(N+1) through IP8
SR_IMASKS = {
    0: 0xff00,   # All enabled
    1: 0xfe00,   # IP2-IP8
    2: 0xfc00,   # IP3-IP8
    3: 0xf800,   # IP4-IP8
    4: 0xf000,   # IP5-IP8
    5: 0xe000,   # IP6-IP8 (splhi — masks scheduling clock IP5 and below)
    6: 0xc000,   # IP7-IP8
    7: 0x8000,   # IP8 only
    8: 0x0000,   # All disabled
}


def irix_to_hw_ip(irix_ip):
    """Convert 1-based IRIX IP number to 0-based hardware IP.

    IRIX uses 1-based interrupt numbering (IP1=software0, IP8=timer).
    Hardware uses 0-based (IP0=bit 8 of Status, IP7=bit 15).

    Args:
        irix_ip: IRIX interrupt number (1-8)

    Returns:
        Hardware IP number (0-7)
    """
    assert 1 <= irix_ip <= 8, f"IRIX IP must be 1-8, got {irix_ip}"
    return irix_ip - 1


def hw_ip_to_irix(hw_ip):
    """Convert 0-based hardware IP to 1-based IRIX IP number.

    Args:
        hw_ip: Hardware IP number (0-7)

    Returns:
        IRIX interrupt number (1-8)
    """
    assert 0 <= hw_ip <= 7, f"Hardware IP must be 0-7, got {hw_ip}"
    return hw_ip + 1


def sr_ibit(irix_ip):
    """Get the Status Register bit mask for an IRIX interrupt number.

    Args:
        irix_ip: IRIX interrupt number (1-8)

    Returns:
        Bitmask in the SR IM field (e.g., SR_IBIT8 = 0x8000)
    """
    return SR_IBITS[irix_ip]


def decode_status_im(sr):
    """Decode the Status register IM field to list of enabled hardware IPs.

    Args:
        sr: Full Status register value (32-bit)

    Returns:
        List of enabled hardware IP numbers (0-7), sorted
    """
    im = (sr >> 8) & 0xff
    return [i for i in range(8) if im & (1 << i)]


def decode_cause_ip(cause):
    """Decode the Cause register IP field to list of pending hardware IPs.

    Args:
        cause: Full Cause register value (32-bit)

    Returns:
        List of pending hardware IP numbers (0-7), sorted
    """
    ip = (cause >> 8) & 0xff
    return [i for i in range(8) if ip & (1 << i)]


def deliverable_interrupts(sr, cause):
    """Return list of hardware IPs that are both pending and enabled.

    An interrupt is deliverable when:
    1. Its IP bit is set in Cause
    2. Its IM bit is set in Status
    3. IE bit (bit 0) is set in Status

    Args:
        sr: Status register value
        cause: Cause register value

    Returns:
        List of deliverable hardware IP numbers (0-7), or empty if IE=0
    """
    if not (sr & 0x01):  # IE not set
        return []
    enabled = set(decode_status_im(sr))
    pending = set(decode_cause_ip(cause))
    return sorted(enabled & pending)


def decode_intctl_ipti(intctl):
    """Extract the IPTI field from CP0_IntCtl register.

    IPTI (IP Timer Interrupt) is bits [31:29], specifying which
    hardware IP the CP0 timer fires on.

    Args:
        intctl: CP0_IntCtl register value (32-bit)

    Returns:
        Hardware IP number for timer (0-7)
    """
    return (intctl >> 29) & 0x7


def format_cp0_irq_state(sr, cause, intctl):
    """Format a multi-line summary of CP0 interrupt state.

    Args:
        sr: Status register value
        cause: Cause register value
        intctl: IntCtl register value

    Returns:
        Multi-line string summarizing interrupt state
    """
    lines = []
    ie = bool(sr & 0x01)
    lines.append(f"CP0 IRQ State:")
    lines.append(f"  Status: 0x{sr:08x}  IE={'Y' if ie else 'N'}")

    enabled = decode_status_im(sr)
    if enabled:
        irix_names = []
        for hw in enabled:
            irix = hw_ip_to_irix(hw)
            handler = C0VEC_TABLE.get(irix, "?")
            irix_names.append(f"IP{irix}({handler})")
        lines.append(f"    Enabled: {', '.join(irix_names)}")

    pending = decode_cause_ip(cause)
    lines.append(f"  Cause:  0x{cause:08x}")
    if pending:
        irix_names = []
        for hw in pending:
            irix = hw_ip_to_irix(hw)
            handler = C0VEC_TABLE.get(irix, "?")
            irix_names.append(f"IP{irix}({handler})")
        lines.append(f"    Pending: {', '.join(irix_names)}")

    ipti = decode_intctl_ipti(intctl)
    lines.append(f"  IntCtl: 0x{intctl:08x}  IPTI={ipti} (hw IP{ipti})")

    deliverable = deliverable_interrupts(sr, cause)
    if deliverable:
        irix_names = []
        for hw in deliverable:
            irix = hw_ip_to_irix(hw)
            handler = C0VEC_TABLE.get(irix, "?")
            irix_names.append(f"IP{irix}({handler})")
        lines.append(f"  DELIVERABLE: {', '.join(irix_names)}")
    elif pending and ie:
        lines.append(f"  DELIVERABLE: none (pending but masked)")
    elif pending:
        lines.append(f"  DELIVERABLE: none (IE=0)")

    return "\n".join(lines)
