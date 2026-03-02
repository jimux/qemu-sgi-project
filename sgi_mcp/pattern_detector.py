# SGI PROM Comparative Analysis - Pattern Detector
"""
Hardware and code pattern detection in PROM binaries.
Includes call graph extraction, boot sequence tracing, and function identification.
"""

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Set, Any

from .config import (
    PROM_BASE, MC_BASE, HPC3_BASE, REX3_BASE,
    GIO_GFX, GIO_EXP0, GIO_EXP1, prom_offset_to_addr, addr_to_prom_offset
)
from .hardware_defs import annotate_address
from .analysis import (
    CallGraph, Function, HardwareAccess, AccessType, FunctionDatabase,
    BootSequenceStep, StringReference, ARCS_CALLBACKS, get_arcs_callback_name,
    is_jal_instruction, is_jr_ra_instruction, is_addiu_sp_instruction,
    is_lui_instruction, is_load_instruction, is_store_instruction,
    is_ori_instruction, is_addiu_instruction,
    # MIPS64 support
    is_daddiu_sp_instruction, is_daddiu_instruction, detect_mips64_prom,
    # PROM classification
    classify_prom, PromClassification,
    # QEMU debugging dataclasses
    QemuLogEntry, QemuLogSummary, ExpectedAccess, RegisterValueAnalysis,
    ExecutionDivergence, ExecutionComparison,
)


@dataclass
class PatternMatch:
    """A detected pattern match."""
    offset: int
    address: int
    pattern_type: str
    description: str
    details: Dict = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


def read_u32_be(data: bytes, offset: int) -> int:
    """Read big-endian 32-bit unsigned integer."""
    if offset + 4 > len(data):
        return 0
    return struct.unpack(">I", data[offset:offset + 4])[0]


def find_hardware_probes(data: bytes, base_address: int = PROM_BASE) -> List[PatternMatch]:
    """
    Find hardware probe patterns (LUI with known device base addresses).

    Looks for LUI instructions loading device base addresses:
    - 0xbfa0 -> MC (Memory Controller)
    - 0xbfb8 -> HPC3
    - 0xbfbd -> IOC2
    - 0xbf0f -> REX3 (Newport)
    - 0xbf00 -> GIO64 Graphics
    - 0xbf40 -> GIO64 EXP0
    - 0xbf60 -> GIO64 EXP1

    Args:
        data: PROM binary data
        base_address: Base address for offset calculation

    Returns:
        List of PatternMatch objects
    """
    matches = []

    # LUI instruction: 0x3c00xxxx where xx is register, xxxx is immediate
    # Encoding: 001111 sssss ttttt iiii iiii iiii iiii
    # Opcode = 0x0f (bits 31-26), rt = bits 20-16, imm = bits 15-0

    known_bases = {
        0xbfa0: ("MC", "Memory Controller"),
        0xbfb8: ("HPC3", "HPC3 Peripheral Controller"),
        0xbfbd: ("IOC2", "I/O Controller 2"),
        0xbf0f: ("REX3", "Newport REX3 Graphics"),
        0xbf00: ("GIO_GFX", "GIO64 Graphics Slot"),
        0xbf04: ("GIO_GFX", "GIO64 Graphics Slot (REX3 area)"),
        0xbf40: ("GIO_EXP0", "GIO64 Expansion Slot 0"),
        0xbf60: ("GIO_EXP1", "GIO64 Expansion Slot 1"),
        0xbfc0: ("PROM", "Boot PROM"),
        0xa000: ("KSEG1", "KSEG1 uncached memory"),
        0x8000: ("KSEG0", "KSEG0 cached memory"),
    }

    for i in range(0, len(data) - 3, 4):
        word = read_u32_be(data, i)
        opcode = (word >> 26) & 0x3f

        if opcode == 0x0f:  # LUI
            imm = word & 0xffff
            rt = (word >> 16) & 0x1f

            if imm in known_bases:
                device, desc = known_bases[imm]
                matches.append(PatternMatch(
                    offset=i,
                    address=base_address + i,
                    pattern_type="hardware_probe",
                    description=f"LUI ${rt}, 0x{imm:04x} ; Load {device} base",
                    details={
                        "device": device,
                        "register": rt,
                        "immediate": imm,
                        "full_address": imm << 16
                    }
                ))

    return matches


def find_exception_vectors(data: bytes, base_address: int = PROM_BASE) -> List[PatternMatch]:
    """
    Find exception vector handlers in PROM.

    In BEV (Boot Exception Vector) mode, exceptions vector to:
    - 0xbfc00000 + 0x200: TLB Refill
    - 0xbfc00000 + 0x300: Cache Error
    - 0xbfc00000 + 0x380: General Exception
    - 0xbfc00000 + 0x400: Interrupt (R4000+)

    Args:
        data: PROM binary data
        base_address: Base address

    Returns:
        List of PatternMatch objects for exception handlers
    """
    matches = []

    vector_offsets = [
        (0x000, "Reset Vector", "CPU reset entry point"),
        (0x200, "TLB Refill", "TLB miss exception (BEV)"),
        (0x280, "XTLB Refill", "64-bit TLB miss (R4000+)"),
        (0x300, "Cache Error", "Cache error exception"),
        (0x380, "General Exception", "General exception handler"),
        (0x400, "Interrupt", "Interrupt handler (R4000+ Cause IV)"),
    ]

    for offset, name, desc in vector_offsets:
        if offset + 4 <= len(data):
            word = read_u32_be(data, offset)

            # Check if this looks like valid MIPS code
            opcode = (word >> 26) & 0x3f

            # Common valid opcodes at vector entry
            valid_opcodes = {
                0x00,  # SPECIAL (nop, sll, etc.)
                0x04,  # BEQ
                0x05,  # BNE
                0x08,  # J (in upper bits)
                0x09,  # JAL
                0x0f,  # LUI
                0x10,  # COP0
            }

            # Also check for J instruction (opcode 2)
            if opcode == 0x02 or opcode == 0x03:  # J or JAL
                target = (word & 0x03ffffff) << 2
                # Add PROM region bits
                target |= (base_address & 0xf0000000)

                matches.append(PatternMatch(
                    offset=offset,
                    address=base_address + offset,
                    pattern_type="exception_vector",
                    description=f"{name}: J 0x{target:08x}",
                    details={
                        "vector_name": name,
                        "jump_target": target,
                        "instruction": word
                    }
                ))
            elif opcode in valid_opcodes or word != 0:
                matches.append(PatternMatch(
                    offset=offset,
                    address=base_address + offset,
                    pattern_type="exception_vector",
                    description=f"{name}: {desc}",
                    details={
                        "vector_name": name,
                        "instruction": word
                    }
                ))

    return matches


def find_graphics_init(data: bytes, base_address: int = PROM_BASE) -> List[PatternMatch]:
    """
    Find Newport graphics initialization patterns.

    Looks for access patterns to REX3 registers (0xbf0f0xxx).

    Args:
        data: PROM binary data
        base_address: Base address

    Returns:
        List of PatternMatch objects
    """
    matches = []

    # Look for sequences of LUI 0xbf0f followed by register accesses
    in_gfx_sequence = False
    sequence_start = 0

    for i in range(0, len(data) - 3, 4):
        word = read_u32_be(data, i)
        opcode = (word >> 26) & 0x3f

        if opcode == 0x0f:  # LUI
            imm = word & 0xffff
            if imm in (0xbf0f, 0xbf04):  # REX3 area
                if not in_gfx_sequence:
                    in_gfx_sequence = True
                    sequence_start = i
        elif in_gfx_sequence:
            # Check for store instructions (graphics writes)
            if opcode in (0x2b, 0x29, 0x28):  # SW, SH, SB
                pass  # Still in sequence
            elif opcode in (0x23, 0x21, 0x20):  # LW, LH, LB
                pass  # Still in sequence
            else:
                # End of graphics sequence
                if i - sequence_start >= 16:  # At least 4 instructions
                    matches.append(PatternMatch(
                        offset=sequence_start,
                        address=base_address + sequence_start,
                        pattern_type="graphics_init",
                        description=f"Graphics init sequence ({(i - sequence_start) // 4} instructions)",
                        details={
                            "start_offset": sequence_start,
                            "end_offset": i,
                            "instruction_count": (i - sequence_start) // 4
                        }
                    ))
                in_gfx_sequence = False

    return matches


def find_memory_detection(data: bytes, base_address: int = PROM_BASE) -> List[PatternMatch]:
    """
    Find memory detection/sizing patterns.

    Looks for:
    - Access to MEMCFG registers (MC + 0xc0, 0xc8)
    - Memory probing patterns (write, readback)

    Args:
        data: PROM binary data
        base_address: Base address

    Returns:
        List of PatternMatch objects
    """
    matches = []

    # Look for LUI 0xbfa0 (MC base) followed by access to MEMCFG offsets
    lui_mc_locations = []

    for i in range(0, len(data) - 3, 4):
        word = read_u32_be(data, i)
        opcode = (word >> 26) & 0x3f

        if opcode == 0x0f:  # LUI
            imm = word & 0xffff
            if imm == 0xbfa0:  # MC base
                lui_mc_locations.append(i)

    # Now look for SW/LW with MEMCFG offsets near these LUI instructions
    for lui_offset in lui_mc_locations:
        # Search within 64 instructions after LUI
        for j in range(lui_offset + 4, min(lui_offset + 256, len(data) - 3), 4):
            word = read_u32_be(data, j)
            opcode = (word >> 26) & 0x3f
            imm = word & 0xffff

            # Sign extend immediate
            if imm & 0x8000:
                imm_signed = imm - 0x10000
            else:
                imm_signed = imm

            # Check for access to MEMCFG0 (0xc0) or MEMCFG1 (0xc8)
            if opcode in (0x23, 0x2b) and imm_signed in (0xc0, 0xc8):  # LW or SW
                matches.append(PatternMatch(
                    offset=j,
                    address=base_address + j,
                    pattern_type="memory_detection",
                    description=f"MEMCFG{0 if imm_signed == 0xc0 else 1} access",
                    details={
                        "register_offset": imm_signed,
                        "operation": "write" if opcode == 0x2b else "read",
                        "lui_offset": lui_offset
                    }
                ))

    return matches


def find_device_detection(data: bytes, base_address: int = PROM_BASE) -> List[PatternMatch]:
    """
    Find GIO slot probing patterns.

    Looks for:
    - Access to GIO slot addresses
    - Badaddr-style probing (read with exception handler check)

    Args:
        data: PROM binary data
        base_address: Base address

    Returns:
        List of PatternMatch objects
    """
    matches = []

    # GIO slot base addresses (upper 16 bits)
    gio_bases = {
        0xbf00: "GIO_GFX",
        0xbf40: "GIO_EXP0",
        0xbf60: "GIO_EXP1",
    }

    for i in range(0, len(data) - 3, 4):
        word = read_u32_be(data, i)
        opcode = (word >> 26) & 0x3f

        if opcode == 0x0f:  # LUI
            imm = word & 0xffff
            if imm in gio_bases:
                slot = gio_bases[imm]

                # Look for subsequent load instruction (device probe)
                for j in range(i + 4, min(i + 32, len(data) - 3), 4):
                    next_word = read_u32_be(data, j)
                    next_opcode = (next_word >> 26) & 0x3f

                    if next_opcode in (0x20, 0x21, 0x23, 0x24, 0x25):  # Load instructions
                        matches.append(PatternMatch(
                            offset=i,
                            address=base_address + i,
                            pattern_type="device_detection",
                            description=f"{slot} device probe",
                            details={
                                "slot": slot,
                                "lui_offset": i,
                                "load_offset": j,
                                "base_address": imm << 16
                            }
                        ))
                        break

    return matches


def find_jump_tables(data: bytes, base_address: int = PROM_BASE) -> List[PatternMatch]:
    """
    Find jump tables (sequences of PROM-region addresses).

    Args:
        data: PROM binary data
        base_address: Base address

    Returns:
        List of PatternMatch objects
    """
    matches = []

    # Look for sequences of addresses in PROM range
    min_entries = 3  # Minimum entries for a jump table
    i = 0

    while i < len(data) - 3:
        # Check if this looks like a PROM address
        word = read_u32_be(data, i)

        if (0xbfc00000 <= word < 0xc0000000) or (0x9fc00000 <= word < 0xa0000000):
            # Found a potential jump table entry, count consecutive entries
            table_start = i
            entry_count = 0

            while i < len(data) - 3:
                word = read_u32_be(data, i)
                if (0xbfc00000 <= word < 0xc0000000) or (0x9fc00000 <= word < 0xa0000000):
                    entry_count += 1
                    i += 4
                else:
                    break

            if entry_count >= min_entries:
                matches.append(PatternMatch(
                    offset=table_start,
                    address=base_address + table_start,
                    pattern_type="jump_table",
                    description=f"Jump table with {entry_count} entries",
                    details={
                        "entry_count": entry_count,
                        "table_size": entry_count * 4,
                        "first_entry": read_u32_be(data, table_start)
                    }
                ))
        else:
            i += 4

    return matches


def find_all_patterns(data: bytes, base_address: int = PROM_BASE) -> Dict[str, List[PatternMatch]]:
    """
    Find all patterns in PROM data.

    Args:
        data: PROM binary data
        base_address: Base address

    Returns:
        Dictionary mapping pattern type to list of matches
    """
    return {
        "hardware_probes": find_hardware_probes(data, base_address),
        "exception_vectors": find_exception_vectors(data, base_address),
        "graphics_init": find_graphics_init(data, base_address),
        "memory_detection": find_memory_detection(data, base_address),
        "device_detection": find_device_detection(data, base_address),
        "jump_tables": find_jump_tables(data, base_address),
    }


def format_pattern_matches(matches: List[PatternMatch]) -> str:
    """Format pattern matches for display."""
    if not matches:
        return "No matches found."

    lines = []
    for m in matches:
        lines.append(f"0x{m.address:08x} (+0x{m.offset:05x}): {m.description}")

    return "\n".join(lines)


# =============================================================================
# MIPS Instruction Search
# =============================================================================

# MIPS GPR names
_GPR_NAMES = [
    "zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
    "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
    "t8", "t9", "k0", "k1", "gp", "sp", "s8", "ra",
]

# CP0 register names (register number -> name)
_CP0_NAMES = {
    0: "Index", 1: "Random", 2: "EntryLo0", 3: "EntryLo1",
    4: "Context", 5: "PageMask", 6: "Wired", 8: "BadVAddr",
    9: "Count", 10: "EntryHi", 11: "Compare", 12: "Status",
    13: "Cause", 14: "EPC", 15: "PRId", 16: "Config",
    17: "LLAddr", 18: "WatchLo", 19: "WatchHi", 20: "XContext",
    21: "FrameMask", 23: "Debug", 24: "DEPC", 25: "PerfCnt",
    26: "ErrCtl/ECC", 27: "CacheErr", 28: "TagLo", 29: "TagHi",
    30: "ErrorEPC",
}

# Known MIPS instruction types with their opcode and decoding
_INSN_TYPES = {
    # I-type: opcode in bits 31:26
    "cache":  {"opcode": 0x2F, "type": "cache"},
    "lw":     {"opcode": 0x23, "type": "itype"},
    "lh":     {"opcode": 0x21, "type": "itype"},
    "lb":     {"opcode": 0x20, "type": "itype"},
    "ld":     {"opcode": 0x37, "type": "itype"},
    "sw":     {"opcode": 0x2B, "type": "itype"},
    "sh":     {"opcode": 0x29, "type": "itype"},
    "sb":     {"opcode": 0x28, "type": "itype"},
    "sd":     {"opcode": 0x3F, "type": "itype"},
    "lui":    {"opcode": 0x0F, "type": "itype"},
    "ori":    {"opcode": 0x0D, "type": "itype"},
    "addiu":  {"opcode": 0x09, "type": "itype"},
    "daddiu": {"opcode": 0x19, "type": "itype"},
    "andi":   {"opcode": 0x0C, "type": "itype"},
    "beq":    {"opcode": 0x04, "type": "itype"},
    "bne":    {"opcode": 0x05, "type": "itype"},
    # J-type
    "j":      {"opcode": 0x02, "type": "jtype"},
    "jal":    {"opcode": 0x03, "type": "jtype"},
    # COP0: opcode=0x10, rs field selects sub-operation
    "mfc0":   {"opcode": 0x10, "type": "cop0", "rs": 0x00},
    "dmfc0":  {"opcode": 0x10, "type": "cop0", "rs": 0x01},
    "mtc0":   {"opcode": 0x10, "type": "cop0", "rs": 0x04},
    "dmtc0":  {"opcode": 0x10, "type": "cop0", "rs": 0x05},
    # COP0 TLB ops: opcode=0x10, rs=0x10 (CO bit), funct field
    "tlbwi":  {"opcode": 0x10, "type": "cop0_co", "funct": 0x02},
    "tlbwr":  {"opcode": 0x10, "type": "cop0_co", "funct": 0x06},
    "tlbp":   {"opcode": 0x10, "type": "cop0_co", "funct": 0x08},
    "tlbr":   {"opcode": 0x10, "type": "cop0_co", "funct": 0x01},
    "eret":   {"opcode": 0x10, "type": "cop0_co", "funct": 0x18},
    # SPECIAL: opcode=0x00, funct field
    "jr":     {"opcode": 0x00, "type": "special", "funct": 0x08},
    "jalr":   {"opcode": 0x00, "type": "special", "funct": 0x09},
    "sll":    {"opcode": 0x00, "type": "special", "funct": 0x00},
    "srl":    {"opcode": 0x00, "type": "special", "funct": 0x02},
    "sra":    {"opcode": 0x00, "type": "special", "funct": 0x03},
    "dsll":   {"opcode": 0x00, "type": "special", "funct": 0x38},
    "dsrl":   {"opcode": 0x00, "type": "special", "funct": 0x3A},
    "dsll32": {"opcode": 0x00, "type": "special", "funct": 0x3C},
    "dsrl32": {"opcode": 0x00, "type": "special", "funct": 0x3E},
    "addu":   {"opcode": 0x00, "type": "special", "funct": 0x21},
    "daddu":  {"opcode": 0x00, "type": "special", "funct": 0x2D},
    "subu":   {"opcode": 0x00, "type": "special", "funct": 0x23},
    "and":    {"opcode": 0x00, "type": "special", "funct": 0x24},
    "or":     {"opcode": 0x00, "type": "special", "funct": 0x25},
    "xor":    {"opcode": 0x00, "type": "special", "funct": 0x26},
    "nor":    {"opcode": 0x00, "type": "special", "funct": 0x27},
    "sltu":   {"opcode": 0x00, "type": "special", "funct": 0x2B},
}

# Cache operation names
_CACHE_TYPE = ["PI", "PD", "T", "SD"]
_CACHE_OP = [
    "Index_Invalidate", "Index_Load_Tag", "Index_Store_Tag",
    "Create_Dirty_Excl", "Hit_Invalidate", "Hit_WB_Invalidate",
    "Hit_Writeback", "Index_Load_Data" if False else "Fetch_Lock",
]
# More accurate for R10000
_CACHE_OP_NAMES = {
    0: "Index_Invalidate",
    1: "Index_Load_Tag",
    2: "Index_Store_Tag",
    3: "Create_Dirty_Excl",
    4: "Hit_Invalidate",
    5: "Hit_WB_Invalidate/Fill",
    6: "Index_Load_Data",
    7: "Index_Store_Data",
}


def _decode_instruction(word: int) -> Optional[Dict[str, Any]]:
    """
    Decode a MIPS instruction word into its fields.

    Returns dict with: mnemonic, opcode, rs, rt, rd, shamt, funct, imm,
                       and instruction-specific fields.
    Returns None if not a recognized instruction.
    """
    opcode = (word >> 26) & 0x3F
    rs = (word >> 21) & 0x1F
    rt = (word >> 16) & 0x1F
    rd = (word >> 11) & 0x1F
    shamt = (word >> 6) & 0x1F
    funct = word & 0x3F
    imm = word & 0xFFFF
    imm_signed = imm if imm < 0x8000 else imm - 0x10000
    target = word & 0x3FFFFFF

    result = {
        "word": word,
        "opcode": opcode,
        "rs": rs, "rt": rt, "rd": rd,
        "shamt": shamt, "funct": funct,
        "imm": imm, "imm_signed": imm_signed,
        "target26": target,
    }

    # Try to identify the mnemonic
    for name, info in _INSN_TYPES.items():
        if info["opcode"] != opcode:
            continue

        if info["type"] == "special":
            if funct == info["funct"]:
                result["mnemonic"] = name
                return result
        elif info["type"] == "cop0":
            if rs == info["rs"]:
                result["mnemonic"] = name
                result["cp0_reg"] = rd
                result["cp0_sel"] = funct & 0x7
                result["cp0_name"] = _CP0_NAMES.get(rd, f"${rd}")
                return result
        elif info["type"] == "cop0_co":
            if rs & 0x10 and funct == info["funct"]:
                result["mnemonic"] = name
                return result
        elif info["type"] == "cache":
            result["mnemonic"] = name
            result["cache_op"] = rt
            result["cache_type"] = rt & 0x3
            result["cache_operation"] = (rt >> 2) & 0x7
            result["cache_type_name"] = _CACHE_TYPE[rt & 0x3]
            result["cache_op_name"] = _CACHE_OP_NAMES.get(
                (rt >> 2) & 0x7, f"op{(rt >> 2) & 0x7}")
            result["base"] = rs
            result["offset"] = imm_signed
            return result
        elif info["type"] in ("itype", "jtype"):
            result["mnemonic"] = name
            return result

    # Unknown opcode - still return fields
    result["mnemonic"] = f"op{opcode:#04x}"
    return result


def _format_instruction(info: Dict[str, Any], address: int) -> str:
    """Format a decoded instruction as human-readable text."""
    m = info["mnemonic"]

    if m == "cache":
        op_name = f"{info['cache_op_name']}_{info['cache_type_name']}"
        base_name = _GPR_NAMES[info["base"]]
        return (f"cache 0x{info['cache_op']:02x}, "
                f"{info['offset']}(${base_name})  "
                f"; {op_name}")
    elif m in ("mfc0", "dmfc0"):
        gpr = _GPR_NAMES[info["rt"]]
        cp0 = info["cp0_name"]
        sel = info["cp0_sel"]
        sel_str = f", {sel}" if sel else ""
        return f"{m} ${gpr}, ${cp0}{sel_str}"
    elif m in ("mtc0", "dmtc0"):
        gpr = _GPR_NAMES[info["rt"]]
        cp0 = info["cp0_name"]
        sel = info["cp0_sel"]
        sel_str = f", {sel}" if sel else ""
        return f"{m} ${gpr}, ${cp0}{sel_str}"
    elif m == "lui":
        return f"lui ${_GPR_NAMES[info['rt']]}, 0x{info['imm']:04x}"
    elif m in ("jal", "j"):
        target = (address & 0xF0000000) | (info["target26"] << 2)
        return f"{m} 0x{target:08x}"
    elif m in ("jr", "jalr"):
        return f"{m} ${_GPR_NAMES[info['rs']]}"
    elif m in ("lw", "lh", "lb", "ld", "sw", "sh", "sb", "sd"):
        return (f"{m} ${_GPR_NAMES[info['rt']]}, "
                f"{info['imm_signed']}(${_GPR_NAMES[info['rs']]})")
    elif m in ("ori", "andi"):
        return (f"{m} ${_GPR_NAMES[info['rt']]}, "
                f"${_GPR_NAMES[info['rs']]}, 0x{info['imm']:04x}")
    elif m in ("addiu", "daddiu"):
        return (f"{m} ${_GPR_NAMES[info['rt']]}, "
                f"${_GPR_NAMES[info['rs']]}, {info['imm_signed']}")
    elif m in ("beq", "bne"):
        target = address + 4 + (info["imm_signed"] << 2)
        return (f"{m} ${_GPR_NAMES[info['rs']]}, "
                f"${_GPR_NAMES[info['rt']]}, 0x{target:08x}")
    else:
        return f"{m} (0x{info['word']:08x})"


def find_instructions(
    data: bytes,
    mnemonic: str,
    base_address: int = PROM_BASE,
    rs: Optional[int] = None,
    rt: Optional[int] = None,
    rd: Optional[int] = None,
    imm: Optional[int] = None,
    cp0_reg: Optional[int] = None,
    cache_op: Optional[int] = None,
    cache_type: Optional[int] = None,
    cache_operation: Optional[int] = None,
    max_results: int = 200,
    context: int = 0,
) -> str:
    """
    Search for MIPS instructions by mnemonic and optional field filters.

    Args:
        data: PROM binary data
        mnemonic: Instruction mnemonic (e.g., "cache", "dmtc0", "lui")
        base_address: PROM base address
        rs: Filter by rs field value
        rt: Filter by rt field value (for CACHE, this is the full 5-bit op)
        rd: Filter by rd field value
        imm: Filter by immediate value (unsigned 16-bit)
        cp0_reg: Filter by CP0 register number (for mtc0/mfc0/dmtc0/dmfc0)
        cache_op: Filter by full 5-bit CACHE op (alias for rt)
        cache_type: Filter by 2-bit cache type (bits 1:0 of CACHE op)
        cache_operation: Filter by 3-bit cache operation (bits 4:2 of CACHE op)
        max_results: Maximum results to return
        context: Number of surrounding instructions to show
    """
    mnemonic = mnemonic.lower()
    matches = []

    for i in range(0, len(data) - 3, 4):
        word = read_u32_be(data, i)
        info = _decode_instruction(word)
        if info is None:
            continue
        if info["mnemonic"] != mnemonic:
            continue

        # Apply filters
        if rs is not None and info["rs"] != rs:
            continue
        if rt is not None and info["rt"] != rt:
            continue
        if rd is not None and info["rd"] != rd:
            continue
        if imm is not None and info["imm"] != imm:
            continue
        if cp0_reg is not None and info.get("cp0_reg") != cp0_reg:
            continue
        if cache_op is not None and info.get("cache_op") != cache_op:
            continue
        if cache_type is not None and info.get("cache_type") != cache_type:
            continue
        if cache_operation is not None and info.get("cache_operation") != cache_operation:
            continue

        addr = base_address + i
        matches.append((i, addr, info))

        if len(matches) >= max_results:
            break

    if not matches:
        return "No matching instructions found."

    lines = [f"Found {len(matches)} `{mnemonic}` instruction(s):", ""]
    lines.append("```")
    for offset, addr, info in matches:
        desc = _format_instruction(info, addr)
        # Show context instructions before and after
        if context > 0:
            for ci in range(-context, 0):
                ctx_off = offset + ci * 4
                if ctx_off >= 0:
                    ctx_word = read_u32_be(data, ctx_off)
                    ctx_info = _decode_instruction(ctx_word)
                    ctx_addr = base_address + ctx_off
                    ctx_desc = _format_instruction(ctx_info, ctx_addr) if ctx_info else f"0x{ctx_word:08x}"
                    lines.append(f"  0x{ctx_addr:08x}:  {ctx_desc}")

        lines.append(f"  0x{addr:08x}:  {desc}")

        if context > 0:
            for ci in range(1, context + 1):
                ctx_off = offset + ci * 4
                if ctx_off + 4 <= len(data):
                    ctx_word = read_u32_be(data, ctx_off)
                    ctx_info = _decode_instruction(ctx_word)
                    ctx_addr = base_address + ctx_off
                    ctx_desc = _format_instruction(ctx_info, ctx_addr) if ctx_info else f"0x{ctx_word:08x}"
                    lines.append(f"  0x{ctx_addr:08x}:  {ctx_desc}")
            lines.append("")  # blank separator between context groups
    lines.append("```")

    if len(matches) >= max_results:
        lines.append(f"\n(Truncated at {max_results} results)")

    return "\n".join(lines)


# =============================================================================
# Call Graph and Function Analysis
# =============================================================================

def build_call_graph(data: bytes, base_address: int = PROM_BASE) -> CallGraph:
    """
    Build call graph from PROM binary.

    Scans all JAL instructions to extract call targets and builds
    caller/callee relationships.

    Args:
        data: PROM binary data
        base_address: Base address for offset calculation

    Returns:
        CallGraph object with all relationships
    """
    call_graph = CallGraph()

    # First, find all function prologues to identify function starts
    prologues = find_function_prologues_enhanced(data, base_address)
    for addr, stack_size in prologues:
        call_graph.add_function(addr)

    # Find exception vectors as entry points
    vectors = find_exception_vectors(data, base_address)
    for v in vectors:
        if "jump_target" in v.details:
            call_graph.add_entry_point(v.details["jump_target"])
        else:
            call_graph.add_entry_point(v.address)

    # Special case: typical reset vector jump location
    # Many SGI PROMs jump to 0xbfc003c0 from reset
    reset_entry = 0xbfc003c0
    if addr_to_prom_offset(reset_entry) is not None:
        offset = addr_to_prom_offset(reset_entry)
        if offset < len(data):
            call_graph.add_entry_point(reset_entry)

    # Scan for all JAL instructions
    for i in range(0, len(data) - 3, 4):
        word = read_u32_be(data, i)
        is_jal, target = is_jal_instruction(word)

        if is_jal:
            caller_addr = base_address + i

            # Find containing function for caller
            caller_func = None
            for func_addr in sorted(call_graph.functions, reverse=True):
                if func_addr <= caller_addr:
                    caller_func = func_addr
                    break

            if caller_func is None:
                caller_func = caller_addr  # Use instruction address as function

            # Validate target is within PROM
            target_offset = addr_to_prom_offset(target)
            if target_offset is not None and 0 <= target_offset < len(data):
                call_graph.add_call(caller_func, target)

    # Compute orphan functions
    call_graph.compute_orphans()

    return call_graph


def find_function_prologues_enhanced(data: bytes, base_address: int = PROM_BASE) -> List[Tuple[int, int]]:
    """
    Find function prologues with enhanced detection.

    Looks for:
    - addiu $sp, $sp, -N (MIPS32 standard prologue)
    - daddiu $sp, $sp, -N (MIPS64 prologue for R8000/R10000)
    - jr $ra patterns to find function boundaries

    Args:
        data: PROM binary data
        base_address: Base address

    Returns:
        List of (address, stack_frame_size) tuples
    """
    prologues = []

    for i in range(0, len(data) - 3, 4):
        word = read_u32_be(data, i)

        # Check for MIPS32 ADDIU $sp, $sp, -N
        is_sp32, stack_adj32 = is_addiu_sp_instruction(word)
        if is_sp32 and stack_adj32 < 0:
            addr = base_address + i
            prologues.append((addr, -stack_adj32))
            continue

        # Check for MIPS64 DADDIU $sp, $sp, -N (for IP26/IP28/IP30)
        is_sp64, stack_adj64 = is_daddiu_sp_instruction(word)
        if is_sp64 and stack_adj64 < 0:
            addr = base_address + i
            prologues.append((addr, -stack_adj64))

    return prologues


def find_function_epilogues(data: bytes, base_address: int = PROM_BASE) -> List[int]:
    """
    Find function epilogues (jr $ra instructions).

    Args:
        data: PROM binary data
        base_address: Base address

    Returns:
        List of addresses containing jr $ra
    """
    epilogues = []

    for i in range(0, len(data) - 3, 4):
        word = read_u32_be(data, i)
        if is_jr_ra_instruction(word):
            addr = base_address + i
            epilogues.append(addr)

    return epilogues


def find_function_boundaries(data: bytes, base_address: int = PROM_BASE) -> List[Function]:
    """
    Find function boundaries by matching prologues with epilogues.

    Args:
        data: PROM binary data
        base_address: Base address

    Returns:
        List of Function objects with address and end_address set
    """
    prologues = find_function_prologues_enhanced(data, base_address)
    epilogues = find_function_epilogues(data, base_address)

    functions = []
    prologue_addrs = sorted([addr for addr, _ in prologues])

    for addr, stack_size in prologues:
        # Find the jr $ra that ends this function
        # It should be before the next function prologue
        next_prologue = None
        for pa in prologue_addrs:
            if pa > addr:
                next_prologue = pa
                break

        # Find epilogue in range
        end_addr = next_prologue if next_prologue else base_address + len(data)
        for epi in epilogues:
            if addr < epi < end_addr:
                # jr $ra has a delay slot, so function ends 8 bytes after jr
                func_end = epi + 8
                func = Function(
                    address=addr,
                    end_address=func_end,
                    stack_size=stack_size,
                )
                functions.append(func)
                break
        else:
            # No epilogue found, estimate end at next prologue or limit
            func = Function(
                address=addr,
                end_address=end_addr,
                stack_size=stack_size,
                returns=False,
            )
            functions.append(func)

    return functions


# =============================================================================
# Enhanced Hardware Access Tracking
# =============================================================================

def track_hardware_accesses(data: bytes, base_address: int = PROM_BASE) -> List[HardwareAccess]:
    """
    Track hardware register accesses with full address reconstruction.

    Follows LUI + ORI/ADDIU + LW/SW patterns to determine exact
    MMIO addresses being accessed.

    Args:
        data: PROM binary data
        base_address: Base address

    Returns:
        List of HardwareAccess objects
    """
    accesses = []

    # Track LUI values per register
    lui_values: Dict[int, Tuple[int, int]] = {}  # reg -> (upper_value, address)

    for i in range(0, len(data) - 3, 4):
        word = read_u32_be(data, i)
        addr = base_address + i

        # Track LUI instructions
        is_lui, lui_rt, lui_imm = is_lui_instruction(word)
        if is_lui:
            lui_values[lui_rt] = (lui_imm << 16, addr)
            continue

        # Track ORI that completes address formation
        is_ori, ori_rt, ori_rs, ori_imm = is_ori_instruction(word)
        if is_ori and ori_rs in lui_values:
            full_addr = lui_values[ori_rs][0] | ori_imm
            lui_values[ori_rt] = (full_addr, addr)
            continue

        # Track ADDIU that completes address formation
        is_add, add_rt, add_rs, add_imm = is_addiu_instruction(word)
        if is_add and add_rs in lui_values:
            full_addr = lui_values[add_rs][0] + add_imm
            if full_addr < 0:
                full_addr += 0x100000000
            lui_values[add_rt] = (full_addr, addr)
            continue

        # Check for load instructions
        is_ld, ld_mnem, ld_rt, ld_rs, ld_offset = is_load_instruction(word)
        if is_ld and ld_rs in lui_values:
            full_addr = lui_values[ld_rs][0] + ld_offset
            if full_addr < 0:
                full_addr += 0x100000000

            annotation = annotate_address(full_addr)
            if annotation:
                device, register, desc = annotation
                accesses.append(HardwareAccess(
                    code_address=addr,
                    device=device,
                    register=register,
                    full_address=full_addr,
                    operation=AccessType.READ,
                    description=desc,
                ))
            continue

        # Check for store instructions
        is_st, st_mnem, st_rt, st_rs, st_offset = is_store_instruction(word)
        if is_st and st_rs in lui_values:
            full_addr = lui_values[st_rs][0] + st_offset
            if full_addr < 0:
                full_addr += 0x100000000

            annotation = annotate_address(full_addr)
            if annotation:
                device, register, desc = annotation
                accesses.append(HardwareAccess(
                    code_address=addr,
                    device=device,
                    register=register,
                    full_address=full_addr,
                    operation=AccessType.WRITE,
                    description=desc,
                ))

    return accesses


def track_hardware_accesses_in_function(
    data: bytes,
    func_start: int,
    func_end: int,
    base_address: int = PROM_BASE
) -> List[HardwareAccess]:
    """
    Track hardware accesses within a specific function.

    Args:
        data: PROM binary data
        func_start: Function start address
        func_end: Function end address
        base_address: Base address

    Returns:
        List of HardwareAccess objects within the function
    """
    start_offset = addr_to_prom_offset(func_start)
    end_offset = addr_to_prom_offset(func_end)

    if start_offset is None or end_offset is None:
        return []

    # Extract function data
    func_data = data[start_offset:end_offset]
    accesses = track_hardware_accesses(func_data, func_start)

    return accesses


# =============================================================================
# Boot Sequence Tracing
# =============================================================================

def trace_boot_sequence(
    data: bytes,
    base_address: int = PROM_BASE,
    start_address: int = 0xbfc003c0,
    max_steps: int = 1000,
    max_call_depth: int = 5
) -> List[BootSequenceStep]:
    """
    Trace boot sequence from reset vector.

    Follows JAL calls in execution order and records hardware
    accesses chronologically.

    Args:
        data: PROM binary data
        base_address: Base address
        start_address: Starting address (default: typical reset handler)
        max_steps: Maximum trace steps
        max_call_depth: Maximum call depth to follow

    Returns:
        List of BootSequenceStep objects in execution order
    """
    steps = []
    visited = set()
    step_order = [0]  # Use list to allow mutation in nested function

    # Track LUI values for hardware access detection
    lui_values: Dict[int, int] = {}

    def trace_function(addr: int, depth: int, func_name: str = ""):
        """Recursively trace a function."""
        if depth > max_call_depth:
            return
        if step_order[0] >= max_steps:
            return

        offset = addr_to_prom_offset(addr)
        if offset is None or offset < 0 or offset >= len(data):
            return

        # Trace instructions until jr $ra or call
        current = offset
        while current < len(data) - 3 and step_order[0] < max_steps:
            inst_addr = base_address + current
            if inst_addr in visited:
                break
            visited.add(inst_addr)

            word = read_u32_be(data, current)

            # Check for jr $ra (function return)
            if is_jr_ra_instruction(word):
                steps.append(BootSequenceStep(
                    order=step_order[0],
                    code_address=inst_addr,
                    function_address=addr,
                    function_name=func_name,
                ))
                step_order[0] += 1
                return

            # Check for JAL (function call)
            is_jal, target = is_jal_instruction(word)
            if is_jal:
                target_offset = addr_to_prom_offset(target)
                if target_offset is not None and 0 <= target_offset < len(data):
                    steps.append(BootSequenceStep(
                        order=step_order[0],
                        code_address=inst_addr,
                        function_address=addr,
                        function_name=func_name,
                        is_call=True,
                        call_target=target,
                    ))
                    step_order[0] += 1

                    # Recursively trace called function
                    trace_function(target, depth + 1, f"sub_{target:x}")

                current += 8  # Skip delay slot
                continue

            # Track LUI for hardware access detection
            is_lui, lui_rt, lui_imm = is_lui_instruction(word)
            if is_lui:
                lui_values[lui_rt] = lui_imm << 16

            # Check for hardware access
            hw_access = None

            is_ld, _, _, ld_rs, ld_off = is_load_instruction(word)
            if is_ld and ld_rs in lui_values:
                full_addr = lui_values[ld_rs] + ld_off
                annotation = annotate_address(full_addr)
                if annotation:
                    device, register, desc = annotation
                    hw_access = HardwareAccess(
                        code_address=inst_addr,
                        device=device,
                        register=register,
                        full_address=full_addr,
                        operation=AccessType.READ,
                        description=desc,
                    )

            is_st, _, _, st_rs, st_off = is_store_instruction(word)
            if is_st and st_rs in lui_values:
                full_addr = lui_values[st_rs] + st_off
                annotation = annotate_address(full_addr)
                if annotation:
                    device, register, desc = annotation
                    hw_access = HardwareAccess(
                        code_address=inst_addr,
                        device=device,
                        register=register,
                        full_address=full_addr,
                        operation=AccessType.WRITE,
                        description=desc,
                    )

            if hw_access:
                steps.append(BootSequenceStep(
                    order=step_order[0],
                    code_address=inst_addr,
                    function_address=addr,
                    function_name=func_name,
                    hardware_access=hw_access,
                ))
                step_order[0] += 1

            current += 4

    # Start tracing from entry point
    trace_function(start_address, 0, "boot_reset_handler")

    return steps


# =============================================================================
# String Reference Analysis
# =============================================================================

def find_string_references(
    data: bytes,
    strings: List[Tuple[int, str]],
    base_address: int = PROM_BASE
) -> List[StringReference]:
    """
    Find code that references strings.

    Args:
        data: PROM binary data
        strings: List of (offset, string) tuples from extract_strings
        base_address: Base address

    Returns:
        List of StringReference objects
    """
    references = []

    # Build a set of string addresses for quick lookup
    string_addrs = {base_address + offset: (offset, s) for offset, s in strings}

    # Track LUI values
    lui_values: Dict[int, int] = {}

    for i in range(0, len(data) - 3, 4):
        word = read_u32_be(data, i)
        addr = base_address + i

        # Track LUI
        is_lui, lui_rt, lui_imm = is_lui_instruction(word)
        if is_lui:
            lui_values[lui_rt] = lui_imm << 16
            continue

        # Check ORI for string reference
        is_ori, ori_rt, ori_rs, ori_imm = is_ori_instruction(word)
        if is_ori and ori_rs in lui_values:
            full_addr = lui_values[ori_rs] | ori_imm
            if full_addr in string_addrs:
                offset, string_val = string_addrs[full_addr]
                references.append(StringReference(
                    code_address=addr,
                    string_address=full_addr,
                    string_value=string_val,
                ))
            continue

        # Check ADDIU for string reference
        is_add, add_rt, add_rs, add_imm = is_addiu_instruction(word)
        if is_add and add_rs in lui_values:
            full_addr = lui_values[add_rs] + add_imm
            if full_addr in string_addrs:
                offset, string_val = string_addrs[full_addr]
                references.append(StringReference(
                    code_address=addr,
                    string_address=full_addr,
                    string_value=string_val,
                ))

    return references


# =============================================================================
# ARCS Callback Identification
# =============================================================================

def identify_arcs_callbacks(
    data: bytes,
    base_address: int = PROM_BASE,
    callback_table_address: int = 0
) -> List[Tuple[int, str, int]]:
    """
    Identify ARCS callback functions.

    ARCS (Advanced RISC Computing Specification) defines a standard
    set of firmware callbacks. SGI PROMs implement these.

    Args:
        data: PROM binary data
        base_address: Base address
        callback_table_address: Known callback table address (0 = auto-detect)

    Returns:
        List of (index, name, address) tuples
    """
    callbacks = []

    # Try to find the callback table if not specified
    if callback_table_address == 0:
        # Look for a jump table that could be ARCS callbacks
        jump_tables = find_jump_tables(data, base_address)

        # ARCS has ~40 callbacks, look for appropriate sized tables
        for jt in jump_tables:
            entry_count = jt.details.get("entry_count", 0)
            if 30 <= entry_count <= 60:
                callback_table_address = jt.address
                break

    if callback_table_address == 0:
        return callbacks

    # Read callback table
    table_offset = addr_to_prom_offset(callback_table_address)
    if table_offset is None:
        return callbacks

    # Read entries
    for i in range(min(50, (len(data) - table_offset) // 4)):
        entry_offset = table_offset + (i * 4)
        if entry_offset + 4 > len(data):
            break

        entry_addr = read_u32_be(data, entry_offset)

        # Validate entry is a PROM address
        if (0xbfc00000 <= entry_addr < 0xc0000000) or \
           (0x9fc00000 <= entry_addr < 0xa0000000):
            name = get_arcs_callback_name(i)
            callbacks.append((i, name, entry_addr))
        else:
            # End of table
            break

    return callbacks


# =============================================================================
# Function Analysis
# =============================================================================

def analyze_function(
    data: bytes,
    func_addr: int,
    base_address: int = PROM_BASE,
    strings: Optional[List[Tuple[int, str]]] = None
) -> Function:
    """
    Perform detailed analysis of a single function.

    Args:
        data: PROM binary data
        func_addr: Function start address
        base_address: Base address
        strings: Optional pre-extracted strings

    Returns:
        Function object with full analysis
    """
    # Find function boundaries
    functions = find_function_boundaries(data, base_address)

    # Find our function
    func = None
    for f in functions:
        if f.address == func_addr:
            func = f
            break

    if func is None:
        # Create minimal function object
        func = Function(address=func_addr)

    # Analyze hardware accesses
    if func.end_address > func.address:
        func.hardware_accesses = track_hardware_accesses_in_function(
            data, func.address, func.end_address, base_address
        )

    # Find function calls (callees)
    func_offset = addr_to_prom_offset(func_addr)
    if func_offset is not None:
        end_offset = addr_to_prom_offset(func.end_address) if func.end_address else len(data)
        if end_offset is None:
            end_offset = len(data)

        has_jal = False
        for i in range(func_offset, min(end_offset, len(data) - 3), 4):
            word = read_u32_be(data, i)
            is_jal, target = is_jal_instruction(word)
            if is_jal:
                has_jal = True
                if target not in func.callees:
                    func.callees.append(target)

        func.is_leaf = not has_jal

    # Find string references if strings provided
    if strings:
        refs = find_string_references(data, strings, base_address)
        for ref in refs:
            if func.address <= ref.code_address < func.end_address:
                func.string_refs.append(ref)

    return func


# =============================================================================
# QEMU Debugging Tools
# =============================================================================

import re

# QEMU log patterns from sgi_mc.c, sgi_hpc3.c, wd33c93.c, scsi-disk.c
QEMU_LOG_PATTERNS = [
    # sgi_mc: unimplemented read at 0x00c0
    re.compile(r'^(\w+): unimplemented read at 0x([0-9a-fA-F]+)$'),
    # sgi_mc: unimplemented write at 0x00c0 value 0x12345678
    re.compile(r'^(\w+): unimplemented write at 0x([0-9a-fA-F]+) value 0x([0-9a-fA-F]+)$'),
    # sgi_mc: MEMCFG0 read: 0x12345678
    re.compile(r'^(\w+): (\w+) read: 0x([0-9a-fA-F]+)$'),
    # sgi_mc: MEMCFG0 write: 0x12345678
    re.compile(r'^(\w+): (\w+) write: 0x([0-9a-fA-F]+)$'),
    # sgi_hpc3: read reg 0x1234 (value 0x5678)
    re.compile(r'^(\w+): read reg 0x([0-9a-fA-F]+)(?: \(value 0x([0-9a-fA-F]+)\))?$'),
    # sgi_hpc3: write reg 0x1234 = 0x5678
    re.compile(r'^(\w+): write reg 0x([0-9a-fA-F]+)(?: = 0x([0-9a-fA-F]+))?$'),
    # SCSI patterns (idx 6-9) — matched but categorized separately
    # wd33c93: SELECT_XFER target=1 cmd=MODE_SENSE(0x1a) CDB[6]={1a 00 3f 00 fc 00 }tc=252
    re.compile(r'^(wd33c93): SELECT_XFER target=(\d+)\s+(?:cmd=\w+\(0x[0-9a-fA-F]+\)\s+)?CDB\[(\d+)\]=\{([0-9a-fA-F ]+)\}tc=(\d+)$'),
    # wd33c93: SCSI response datalen=36
    re.compile(r'^(wd33c93): SCSI response datalen=(-?\d+)$'),
    # scsi-disk: check_condition cmd=MODE_SENSE (0x1a) sense=5/36/0
    re.compile(r'^(scsi-disk): check_condition cmd=(\S+)\s+\(0x([0-9a-fA-F]+)\)\s+sense=(\d+)/(\d+)/(\d+)$'),
    # scsi-disk: MODE_SENSE unsupported page 0x3f (page_control=0, dbd=0, dev_type=0)
    re.compile(r'^(scsi-disk): MODE_SENSE unsupported page 0x([0-9a-fA-F]+)\s+\(page_control=(\d+),\s*dbd=(\d+),\s*dev_type=(\d+)\)$'),
]

# Device base addresses for reconstructing full addresses
DEVICE_BASES = {
    'sgi_mc': 0x1fa00000,
    'sgi_hpc3': 0x1fb80000,
    'sgi_ioc2': 0x1fbd9800,
    'newport': 0x1f0f0000,
}


def parse_qemu_log(log_content: str, max_entries: int = 500) -> QemuLogSummary:
    """
    Parse QEMU -d unimp output and extract device accesses.

    Parses log lines from QEMU's debug output to extract hardware
    register accesses, mapping them to SGI hardware annotations.

    Args:
        log_content: Raw QEMU log content
        max_entries: Maximum entries to return

    Returns:
        QemuLogSummary with parsed entries and statistics
    """
    entries = []
    device_counts: Dict[str, int] = {}
    register_counts: Dict[str, int] = {}
    unrecognized = []

    lines = log_content.strip().split('\n')
    total_lines = len(lines)

    for line_num, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue

        parsed = False
        for idx, pattern in enumerate(QEMU_LOG_PATTERNS):
            match = pattern.match(line)
            if match:
                groups = match.groups()
                device = groups[0]

                # Parse based on pattern index (more reliable than string matching)
                # Pattern order: 0=unimpl read, 1=unimpl write, 2=named read, 3=named write, 4=reg read, 5=reg write
                if idx == 0:  # unimplemented read
                    offset = int(groups[1], 16)
                    operation = AccessType.READ
                    value = None
                    register_name = f"0x{offset:04x}"
                elif idx == 1:  # unimplemented write
                    offset = int(groups[1], 16)
                    operation = AccessType.WRITE
                    value = int(groups[2], 16)
                    register_name = f"0x{offset:04x}"
                elif idx == 2:  # named register read (MEMCFG0 read: 0x...)
                    register_name = groups[1]
                    offset = 0  # Unknown offset for named registers
                    value = int(groups[2], 16) if groups[2] else None
                    operation = AccessType.READ
                elif idx == 3:  # named register write
                    register_name = groups[1]
                    offset = 0
                    value = int(groups[2], 16) if groups[2] else None
                    operation = AccessType.WRITE
                elif idx == 4:  # offset-based read (read reg 0x1234)
                    offset = int(groups[1], 16)
                    register_name = f"0x{offset:04x}"
                    value = int(groups[2], 16) if len(groups) > 2 and groups[2] else None
                    operation = AccessType.READ
                elif idx == 5:  # offset-based write
                    offset = int(groups[1], 16)
                    register_name = f"0x{offset:04x}"
                    value = int(groups[2], 16) if len(groups) > 2 and groups[2] else None
                    operation = AccessType.WRITE
                elif idx >= 6:
                    # SCSI patterns (6-9) — count but don't create MMIO entries
                    device = groups[0]
                    device_counts[device] = device_counts.get(device, 0) + 1
                    parsed = True
                    break

                # Calculate full address
                base = DEVICE_BASES.get(device, 0)
                full_address = base + offset if base else offset

                # Get hardware annotation
                annotation_result = annotate_address(full_address)
                annotation = None
                if annotation_result:
                    dev_name, reg_name, desc = annotation_result
                    annotation = f"{dev_name}.{reg_name}: {desc}"

                entry = QemuLogEntry(
                    line_number=line_num,
                    device=device,
                    register_offset=offset,
                    full_address=full_address,
                    operation=operation,
                    value=value,
                    raw_line=line,
                    annotation=annotation,
                )
                entries.append(entry)

                # Update counts
                device_counts[device] = device_counts.get(device, 0) + 1
                reg_key = f"{device}.{register_name}"
                register_counts[reg_key] = register_counts.get(reg_key, 0) + 1

                parsed = True
                break

        if not parsed and line:
            # Check if this looks like a device access we didn't parse
            if any(dev in line.lower() for dev in ['sgi_', 'mc:', 'hpc3:', 'ioc2:']):
                unrecognized.append(line)

        if len(entries) >= max_entries:
            break

    return QemuLogSummary(
        total_lines=total_lines,
        hardware_accesses=len(entries),
        entries=entries[:max_entries],
        device_counts=device_counts,
        register_counts=register_counts,
        unrecognized_lines=unrecognized[:20],
    )


def generate_expected_sequence(
    data: bytes,
    base_address: int = PROM_BASE,
    start_address: int = 0xbfc003c0,
    max_steps: int = 1000,
    max_call_depth: int = 5,
    include_values: bool = True
) -> List[ExpectedAccess]:
    """
    Generate expected hardware access sequence with register values.

    Extends trace_boot_sequence() with value tracking. Follows LUI+ORI
    sequences to determine what values will be written to registers.

    Args:
        data: PROM binary data
        base_address: Base address for PROM
        start_address: Starting address (reset vector handler)
        max_steps: Maximum trace steps
        max_call_depth: Maximum call depth to follow
        include_values: Whether to track and include expected values

    Returns:
        List of ExpectedAccess objects in execution order
    """
    expected = []
    visited = set()
    order = [0]

    # Track register values for value prediction
    reg_values: Dict[int, Tuple[int, str, List[str]]] = {}  # reg -> (value, source, instructions)

    def trace_function(addr: int, depth: int, func_name: str = ""):
        if depth > max_call_depth or order[0] >= max_steps:
            return

        offset = addr_to_prom_offset(addr)
        if offset is None or offset < 0 or offset >= len(data):
            return

        current = offset
        while current < len(data) - 3 and order[0] < max_steps:
            inst_addr = base_address + current
            if inst_addr in visited:
                break
            visited.add(inst_addr)

            word = read_u32_be(data, current)

            # Check for jr $ra (return)
            if is_jr_ra_instruction(word):
                return

            # Check for JAL (function call)
            is_jal, target = is_jal_instruction(word)
            if is_jal:
                target_offset = addr_to_prom_offset(target)
                if target_offset is not None and 0 <= target_offset < len(data):
                    trace_function(target, depth + 1, f"sub_{target:x}")
                current += 8  # Skip delay slot
                continue

            # Track LUI for address formation
            is_lui, lui_rt, lui_imm = is_lui_instruction(word)
            if is_lui:
                value = lui_imm << 16
                reg_values[lui_rt] = (value, "immediate", [f"lui ${lui_rt}, 0x{lui_imm:04x}"])
                current += 4
                continue

            # Track ORI for value completion
            is_ori, ori_rt, ori_rs, ori_imm = is_ori_instruction(word)
            if is_ori and ori_rs in reg_values:
                base_val, source, instrs = reg_values[ori_rs]
                new_val = base_val | ori_imm
                new_instrs = instrs + [f"ori ${ori_rt}, ${ori_rs}, 0x{ori_imm:04x}"]
                reg_values[ori_rt] = (new_val, "immediate", new_instrs)
                current += 4
                continue

            # Track ADDIU for address/value completion
            is_add, add_rt, add_rs, add_imm = is_addiu_instruction(word)
            if is_add and add_rs in reg_values:
                base_val, source, instrs = reg_values[add_rs]
                new_val = (base_val + add_imm) & 0xffffffff
                new_instrs = instrs + [f"addiu ${add_rt}, ${add_rs}, {add_imm}"]
                reg_values[add_rt] = (new_val, "immediate", new_instrs)
                current += 4
                continue

            # Check for load instructions (hardware read)
            is_ld, ld_mnem, ld_rt, ld_rs, ld_off = is_load_instruction(word)
            if is_ld and ld_rs in reg_values:
                base_val, _, _ = reg_values[ld_rs]
                full_addr = (base_val + ld_off) & 0xffffffff

                annotation = annotate_address(full_addr)
                if annotation:
                    device, register, desc = annotation
                    expected.append(ExpectedAccess(
                        order=order[0],
                        code_address=inst_addr,
                        device=device,
                        register=register,
                        full_address=full_addr,
                        operation=AccessType.READ,
                        expected_value=None,
                        value_source="unknown",
                        description=desc,
                    ))
                    order[0] += 1

            # Check for store instructions (hardware write)
            is_st, st_mnem, st_rt, st_rs, st_off = is_store_instruction(word)
            if is_st and st_rs in reg_values:
                base_val, _, _ = reg_values[st_rs]
                full_addr = (base_val + st_off) & 0xffffffff

                annotation = annotate_address(full_addr)
                if annotation:
                    device, register, desc = annotation

                    # Determine the value being written
                    if include_values and st_rt in reg_values:
                        write_val, val_source, instrs = reg_values[st_rt]
                    else:
                        write_val = None
                        val_source = "unknown"

                    expected.append(ExpectedAccess(
                        order=order[0],
                        code_address=inst_addr,
                        device=device,
                        register=register,
                        full_address=full_addr,
                        operation=AccessType.WRITE,
                        expected_value=write_val,
                        value_source=val_source,
                        description=desc,
                    ))
                    order[0] += 1

            current += 4

    trace_function(start_address, 0, "boot_reset_handler")
    return expected


def analyze_register_values(
    data: bytes,
    base_address: int = PROM_BASE,
    max_results: int = 500,
    device_filter: Optional[str] = None
) -> List[RegisterValueAnalysis]:
    """
    Analyze register values from PROM code.

    Tracks LUI+ORI/ADDIU sequences to determine written values,
    and detects polling loops (repeated reads of same register).

    Args:
        data: PROM binary data
        base_address: Base address for PROM
        max_results: Maximum results to return
        device_filter: Filter to specific device (e.g., "MC", "HPC3")

    Returns:
        List of RegisterValueAnalysis objects
    """
    results = []

    # Track register values
    reg_values: Dict[int, Tuple[int, str, List[str]]] = {}

    # Track polling patterns (repeated reads)
    read_addresses: Dict[int, List[int]] = {}  # mmio_addr -> [code_addrs]

    for i in range(0, len(data) - 3, 4):
        word = read_u32_be(data, i)
        addr = base_address + i

        # Track LUI
        is_lui, lui_rt, lui_imm = is_lui_instruction(word)
        if is_lui:
            reg_values[lui_rt] = (lui_imm << 16, "exact", [f"lui ${lui_rt}, 0x{lui_imm:04x}"])
            continue

        # Track ORI
        is_ori, ori_rt, ori_rs, ori_imm = is_ori_instruction(word)
        if is_ori and ori_rs in reg_values:
            base_val, conf, instrs = reg_values[ori_rs]
            new_val = base_val | ori_imm
            new_instrs = instrs + [f"ori ${ori_rt}, ${ori_rs}, 0x{ori_imm:04x}"]
            reg_values[ori_rt] = (new_val, "exact", new_instrs)
            continue

        # Track ADDIU
        is_add, add_rt, add_rs, add_imm = is_addiu_instruction(word)
        if is_add and add_rs in reg_values:
            base_val, conf, instrs = reg_values[add_rs]
            new_val = (base_val + add_imm) & 0xffffffff
            new_instrs = instrs + [f"addiu ${add_rt}, ${add_rs}, {add_imm}"]
            reg_values[add_rt] = (new_val, "exact", new_instrs)
            continue

        # Check for hardware access
        is_ld, _, ld_rt, ld_rs, ld_off = is_load_instruction(word)
        is_st, _, st_rt, st_rs, st_off = is_store_instruction(word)

        if is_ld and ld_rs in reg_values:
            base_val, _, _ = reg_values[ld_rs]
            full_addr = (base_val + ld_off) & 0xffffffff

            annotation = annotate_address(full_addr)
            if annotation:
                device, register, desc = annotation

                # Apply device filter
                if device_filter and device_filter.upper() not in device.upper():
                    continue

                # Track for polling detection
                if full_addr not in read_addresses:
                    read_addresses[full_addr] = []
                read_addresses[full_addr].append(addr)

                is_polling = len(read_addresses[full_addr]) > 1

                results.append(RegisterValueAnalysis(
                    code_address=addr,
                    device=device,
                    register=register,
                    full_address=full_addr,
                    operation=AccessType.READ,
                    value=None,
                    value_confidence="unknown",
                    instruction_sequence=[],
                    is_polling_loop=is_polling,
                ))

        elif is_st and st_rs in reg_values:
            base_val, _, _ = reg_values[st_rs]
            full_addr = (base_val + st_off) & 0xffffffff

            annotation = annotate_address(full_addr)
            if annotation:
                device, register, desc = annotation

                # Apply device filter
                if device_filter and device_filter.upper() not in device.upper():
                    continue

                # Get value being written
                if st_rt in reg_values:
                    write_val, confidence, instrs = reg_values[st_rt]
                else:
                    write_val = None
                    confidence = "unknown"
                    instrs = []

                results.append(RegisterValueAnalysis(
                    code_address=addr,
                    device=device,
                    register=register,
                    full_address=full_addr,
                    operation=AccessType.WRITE,
                    value=write_val,
                    value_confidence=confidence,
                    instruction_sequence=instrs,
                    is_polling_loop=False,
                ))

        if len(results) >= max_results:
            break

    return results


def compare_execution(
    expected: List[ExpectedAccess],
    actual: QemuLogSummary,
    strict_order: bool = False,
    max_divergences: int = 50
) -> ExecutionComparison:
    """
    Compare QEMU trace vs expected PROM sequence.

    Identifies divergences between what the PROM should do and
    what QEMU actually logged, generating actionable recommendations.

    Args:
        expected: Expected hardware accesses from PROM analysis
        actual: Parsed QEMU log
        strict_order: If True, order matters for matching
        max_divergences: Maximum divergences to report

    Returns:
        ExecutionComparison with divergences and recommendations
    """
    divergences = []
    recommendations = []
    match_count = 0

    # Build lookup for actual accesses
    actual_by_addr: Dict[int, List[QemuLogEntry]] = {}
    for entry in actual.entries:
        if entry.full_address not in actual_by_addr:
            actual_by_addr[entry.full_address] = []
        actual_by_addr[entry.full_address].append(entry)

    # Build set of expected addresses
    expected_addrs = set(e.full_address for e in expected)
    actual_addrs = set(a.full_address for a in actual.entries)

    # Track what we've matched
    matched_actual = set()

    # Check each expected access
    for exp in expected:
        if exp.full_address in actual_by_addr:
            # Found matching address
            matches = actual_by_addr[exp.full_address]
            found_match = False

            for act in matches:
                if id(act) in matched_actual:
                    continue

                # Check operation matches
                if act.operation == exp.operation:
                    # Check value if we have both
                    if exp.expected_value is not None and act.value is not None:
                        if exp.expected_value != act.value:
                            divergences.append(ExecutionDivergence(
                                divergence_type="wrong_value",
                                expected=exp,
                                actual=act,
                                severity="warning",
                                suggestion=f"Expected 0x{exp.expected_value:08x} but got 0x{act.value:08x}",
                            ))
                        else:
                            match_count += 1
                            matched_actual.add(id(act))
                            found_match = True
                            break
                    else:
                        match_count += 1
                        matched_actual.add(id(act))
                        found_match = True
                        break

            if not found_match and len(divergences) < max_divergences:
                divergences.append(ExecutionDivergence(
                    divergence_type="missing",
                    expected=exp,
                    actual=None,
                    severity="critical" if exp.operation == AccessType.WRITE else "warning",
                    suggestion=f"Expected {exp.operation.value} to {exp.device}.{exp.register} not found in QEMU log",
                ))
        else:
            # Expected access not in QEMU log at all
            if len(divergences) < max_divergences:
                divergences.append(ExecutionDivergence(
                    divergence_type="missing",
                    expected=exp,
                    actual=None,
                    severity="critical",
                    suggestion=f"No QEMU log entry for {exp.device}.{exp.register} (0x{exp.full_address:08x})",
                ))

    # Check for unexpected accesses in QEMU log
    unexpected_addrs = actual_addrs - expected_addrs
    for addr in list(unexpected_addrs)[:10]:
        entries = actual_by_addr.get(addr, [])
        for entry in entries[:1]:
            if len(divergences) < max_divergences:
                divergences.append(ExecutionDivergence(
                    divergence_type="unexpected",
                    expected=None,
                    actual=entry,
                    severity="info",
                    suggestion=f"QEMU accessed {entry.device} at 0x{addr:08x} but PROM analysis didn't predict this",
                ))

    # Generate recommendations
    if divergences:
        # Count by type
        missing = sum(1 for d in divergences if d.divergence_type == "missing")
        unexpected = sum(1 for d in divergences if d.divergence_type == "unexpected")
        wrong_val = sum(1 for d in divergences if d.divergence_type == "wrong_value")

        if missing > 0:
            # Check which devices are missing
            missing_devices = set()
            for d in divergences:
                if d.divergence_type == "missing" and d.expected:
                    missing_devices.add(d.expected.device)

            for dev in missing_devices:
                if "MC" in dev or "Memory Controller" in dev:
                    recommendations.append("MC device may have incomplete register handling - check sgi_mc.c")
                elif "HPC3" in dev:
                    recommendations.append("HPC3 device may have incomplete register handling - check sgi_hpc3.c")
                elif "IOC2" in dev:
                    recommendations.append("IOC2 device not implemented - create sgi_ioc2.c")

        if unexpected > 0:
            recommendations.append("Some QEMU accesses were unexpected - PROM analysis may need deeper tracing")

        if wrong_val > 0:
            recommendations.append("Value mismatches detected - check register read return values")

    # Build summary
    summary_parts = [
        f"Matched {match_count}/{len(expected)} expected accesses",
        f"Found {len(divergences)} divergences",
    ]

    return ExecutionComparison(
        prom_name="",  # Will be set by caller
        log_file="",   # Will be set by caller
        expected_count=len(expected),
        actual_count=len(actual.entries),
        match_count=match_count,
        divergences=divergences,
        summary=". ".join(summary_parts),
        recommendations=recommendations,
    )


def build_function_database(
    data: bytes,
    base_address: int = PROM_BASE,
    prom_name: str = ""
) -> FunctionDatabase:
    """
    Build complete function database for a PROM.

    Args:
        data: PROM binary data
        base_address: Base address
        prom_name: Name for the database

    Returns:
        FunctionDatabase with all analysis
    """
    db = FunctionDatabase(prom_name)

    # Check PROM classification first
    classification = classify_prom(data, prom_name)

    if not classification.executable:
        # Non-executable PROM - return minimal database with classification info
        db.classification = classification
        # Still extract strings for reference
        from .prom_loader import extract_strings
        strings = extract_strings(data, min_length=4)
        for offset, s in strings:
            db.add_string(base_address + offset, s)
        return db

    # Store classification for reference
    db.classification = classification

    # Extract strings first
    from .prom_loader import extract_strings
    strings = extract_strings(data, min_length=4)
    for offset, s in strings:
        db.add_string(base_address + offset, s)

    # Build call graph
    db.call_graph = build_call_graph(data, base_address)

    # Find function boundaries
    functions = find_function_boundaries(data, base_address)

    # Analyze each function
    for func in functions:
        func = analyze_function(data, func.address, base_address, strings)

        # Add callers from call graph
        if func.address in db.call_graph.callers:
            func.callers = db.call_graph.callers[func.address]

        db.add_function(func)

    # Identify ARCS callbacks and name those functions
    # ARCS callbacks may be at KSEG0 (0x9fc...) or KSEG1 (0xbfc...) addresses
    # which map to the same physical PROM location
    arcs_callbacks = identify_arcs_callbacks(data, base_address)
    for index, name, addr in arcs_callbacks:
        # Try direct match first
        if addr in db.functions:
            db.functions[addr].name = name
            db.functions[addr].source = "arcs"
        else:
            # Try converting between KSEG0 and KSEG1
            if addr >= 0x9fc00000 and addr < 0xa0000000:
                # KSEG0 -> KSEG1
                kseg1_addr = addr + 0x20000000
                if kseg1_addr in db.functions:
                    db.functions[kseg1_addr].name = name
                    db.functions[kseg1_addr].source = "arcs"
            elif addr >= 0xbfc00000 and addr < 0xc0000000:
                # KSEG1 -> KSEG0
                kseg0_addr = addr - 0x20000000
                if kseg0_addr in db.functions:
                    db.functions[kseg0_addr].name = name
                    db.functions[kseg0_addr].source = "arcs"

    return db
