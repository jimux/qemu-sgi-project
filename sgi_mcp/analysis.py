# SGI PROM Comparative Analysis - Analysis Data Structures
"""
Core data structures for PROM analysis: functions, hardware accesses, call graphs.
"""

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Any
from enum import Enum

from .config import PROM_BASE, prom_offset_to_addr, addr_to_prom_offset
from .hardware_defs import annotate_address


class AccessType(Enum):
    """Type of hardware access."""
    READ = "read"
    WRITE = "write"
    UNKNOWN = "unknown"


@dataclass
class HardwareAccess:
    """A hardware register access detected in code."""
    code_address: int      # Address of the instruction performing the access
    device: str            # Device name (MC, HPC3, IOC2, REX3, etc.)
    register: str          # Register name or offset
    full_address: int      # Full MMIO address being accessed
    operation: AccessType  # Read or write
    description: str = ""  # Register description


@dataclass
class FunctionCall:
    """A function call (JAL instruction)."""
    caller_address: int    # Address of the JAL instruction
    target_address: int    # Target function address
    in_delay_slot: bool = False


@dataclass
class StringReference:
    """A reference to a string in the PROM."""
    code_address: int      # Address where string is referenced
    string_address: int    # Address of the string
    string_value: str      # The actual string content


@dataclass
class Function:
    """A function identified in the PROM."""
    address: int                              # Start address (prologue)
    end_address: int = 0                      # End address (after jr $ra)
    name: str = ""                            # Function name (auto or manual)
    stack_size: int = 0                       # Stack frame size from prologue
    callers: List[int] = field(default_factory=list)      # Addresses that call this function
    callees: List[int] = field(default_factory=list)      # Functions this one calls
    hardware_accesses: List[HardwareAccess] = field(default_factory=list)
    string_refs: List[StringReference] = field(default_factory=list)
    source: str = "auto"                      # How identified: "auto", "heuristic", "manual"
    is_leaf: bool = False                     # True if no JAL instructions
    returns: bool = True                      # True if has jr $ra

    def suggested_name(self) -> str:
        """Generate a suggested name based on function characteristics."""
        if self.name:
            return self.name

        # Name based on hardware access patterns
        devices = set(ha.device for ha in self.hardware_accesses)
        if len(devices) == 1:
            device = list(devices)[0]
            prefix_map = {
                "MC": "mc_",
                "Memory Controller": "mc_",
                "HPC3": "hpc3_",
                "IOC2 (Indy)": "ioc2_",
                "IOC2 (Indigo2)": "ioc2_",
                "Newport REX3": "rex3_",
                "GIO64_GFX": "gfx_",
                "GIO64_EXP0": "gio_exp0_",
                "GIO64_EXP1": "gio_exp1_",
            }
            prefix = prefix_map.get(device, "")
            if prefix:
                return f"{prefix}func_{self.address:x}"

        # Name based on string references
        if self.string_refs:
            first_str = self.string_refs[0].string_value
            # Extract a reasonable name from string
            words = first_str.split()
            if words:
                word = words[0].lower()
                # Clean up the word
                word = ''.join(c for c in word if c.isalnum() or c == '_')[:20]
                if word:
                    return f"{word}_func_{self.address:x}"

        return f"sub_{self.address:x}"


@dataclass
class BootSequenceStep:
    """A step in the boot sequence trace."""
    order: int                               # Execution order (0, 1, 2, ...)
    code_address: int                        # Address of the instruction
    function_address: int                    # Containing function address
    function_name: str = ""                  # Function name if known
    hardware_access: Optional[HardwareAccess] = None  # Hardware access if any
    is_call: bool = False                    # True if this is a function call
    call_target: int = 0                     # Target address if is_call


@dataclass
class CallGraph:
    """Call graph for a PROM."""
    # Maps function address -> list of callee addresses
    callees: Dict[int, List[int]] = field(default_factory=dict)
    # Maps function address -> list of caller addresses
    callers: Dict[int, List[int]] = field(default_factory=dict)
    # All function addresses found
    functions: Set[int] = field(default_factory=set)
    # Entry points (reset vector, exception handlers)
    entry_points: Set[int] = field(default_factory=set)
    # Orphan functions (never called)
    orphans: Set[int] = field(default_factory=set)

    def add_call(self, caller: int, callee: int):
        """Add a call relationship."""
        if caller not in self.callees:
            self.callees[caller] = []
        if callee not in self.callees[caller]:
            self.callees[caller].append(callee)

        if callee not in self.callers:
            self.callers[callee] = []
        if caller not in self.callers[callee]:
            self.callers[callee].append(caller)

        self.functions.add(caller)
        self.functions.add(callee)

    def add_function(self, addr: int):
        """Add a function address."""
        self.functions.add(addr)

    def add_entry_point(self, addr: int):
        """Mark an address as an entry point."""
        self.entry_points.add(addr)
        self.functions.add(addr)

    def compute_orphans(self):
        """Compute set of orphan functions (never called, not entry points)."""
        called = set(self.callers.keys())
        self.orphans = self.functions - called - self.entry_points


class FunctionDatabase:
    """Database of identified functions in a PROM."""

    def __init__(self, prom_name: str = ""):
        self.prom_name = prom_name
        self.functions: Dict[int, Function] = {}  # address -> Function
        self.call_graph = CallGraph()
        self.boot_sequence: List[BootSequenceStep] = []
        self.strings: Dict[int, str] = {}  # address -> string value
        self.classification: Optional['PromClassification'] = None  # PROM type classification

    def add_function(self, func: Function):
        """Add or update a function."""
        self.functions[func.address] = func
        self.call_graph.add_function(func.address)

    def get_function(self, addr: int) -> Optional[Function]:
        """Get function at address."""
        return self.functions.get(addr)

    def get_function_containing(self, addr: int) -> Optional[Function]:
        """Get function containing an address."""
        # Find function with start <= addr < end
        for func in self.functions.values():
            if func.address <= addr < func.end_address:
                return func
        return None

    def add_string(self, addr: int, value: str):
        """Add a string at address."""
        self.strings[addr] = value

    def get_string(self, addr: int) -> Optional[str]:
        """Get string at address."""
        return self.strings.get(addr)

    def export_symbols(self) -> List[Tuple[int, str]]:
        """Export as list of (address, name) tuples."""
        symbols = []
        for addr, func in sorted(self.functions.items()):
            name = func.name if func.name else func.suggested_name()
            symbols.append((addr, name))
        return symbols

    def to_dict(self) -> Dict[str, Any]:
        """Export database as dictionary."""
        result = {
            "prom_name": self.prom_name,
            "function_count": len(self.functions),
        }

        # Include classification if present
        if self.classification:
            result["classification"] = {
                "type": self.classification.prom_type,
                "arch": self.classification.arch,
                "executable": self.classification.executable,
                "description": self.classification.description,
                "suggested_tools": self.classification.suggested_tools,
            }

        result["functions"] = [
            {
                "address": f"0x{func.address:08x}",
                "end_address": f"0x{func.end_address:08x}" if func.end_address else None,
                "name": func.name if func.name else func.suggested_name(),
                "stack_size": func.stack_size,
                "callers": [f"0x{a:08x}" for a in func.callers],
                "callees": [f"0x{a:08x}" for a in func.callees],
                "hardware_accesses": [
                    {
                        "address": f"0x{ha.code_address:08x}",
                        "device": ha.device,
                        "register": ha.register,
                        "operation": ha.operation.value,
                    }
                    for ha in func.hardware_accesses
                ],
                "string_refs": [
                    {
                        "code_address": f"0x{sr.code_address:08x}",
                        "string_address": f"0x{sr.string_address:08x}",
                        "value": sr.string_value[:50],
                    }
                    for sr in func.string_refs
                ],
                "source": func.source,
                "is_leaf": func.is_leaf,
            }
            for func in sorted(self.functions.values(), key=lambda f: f.address)
        ]

        result["call_graph"] = {
            "entry_points": [f"0x{a:08x}" for a in sorted(self.call_graph.entry_points)],
            "orphans": [f"0x{a:08x}" for a in sorted(self.call_graph.orphans)],
        }

        result["strings"] = {
            f"0x{addr:08x}": value
            for addr, value in sorted(self.strings.items())
        }

        return result


# MIPS instruction parsing utilities

def read_u32_be(data: bytes, offset: int) -> int:
    """Read big-endian 32-bit value."""
    if offset + 4 > len(data):
        return 0
    return struct.unpack(">I", data[offset:offset + 4])[0]


def decode_mips_instruction(word: int) -> Dict[str, Any]:
    """Decode a MIPS instruction into its components."""
    opcode = (word >> 26) & 0x3f
    rs = (word >> 21) & 0x1f
    rt = (word >> 16) & 0x1f
    rd = (word >> 11) & 0x1f
    shamt = (word >> 6) & 0x1f
    funct = word & 0x3f
    imm = word & 0xffff
    target = word & 0x03ffffff

    # Sign-extend immediate
    if imm & 0x8000:
        imm_signed = imm - 0x10000
    else:
        imm_signed = imm

    return {
        "opcode": opcode,
        "rs": rs,
        "rt": rt,
        "rd": rd,
        "shamt": shamt,
        "funct": funct,
        "imm": imm,
        "imm_signed": imm_signed,
        "target": target,
        "word": word,
    }


def is_jal_instruction(word: int) -> Tuple[bool, int]:
    """Check if instruction is JAL and return target address."""
    opcode = (word >> 26) & 0x3f
    if opcode == 0x03:  # JAL
        target = (word & 0x03ffffff) << 2
        # JAL target is in the same 256MB segment as the instruction
        # For PROM, add the upper bits from PROM_BASE
        target |= (PROM_BASE & 0xf0000000)
        return True, target
    return False, 0


def is_jr_ra_instruction(word: int) -> bool:
    """Check if instruction is JR $RA (function return)."""
    # JR $ra: 0x03e00008
    # Encoding: 000000 11111 00000 00000 00000 001000
    opcode = (word >> 26) & 0x3f
    funct = word & 0x3f
    rs = (word >> 21) & 0x1f
    if opcode == 0x00 and funct == 0x08 and rs == 31:  # JR with $ra
        return True
    return False


def is_addiu_sp_instruction(word: int) -> Tuple[bool, int]:
    """Check if instruction is ADDIU $sp, $sp, imm and return stack adjustment."""
    opcode = (word >> 26) & 0x3f
    rs = (word >> 21) & 0x1f
    rt = (word >> 16) & 0x1f
    imm = word & 0xffff

    if opcode == 0x09 and rs == 29 and rt == 29:  # ADDIU $sp, $sp
        # Sign extend
        if imm & 0x8000:
            imm_signed = imm - 0x10000
        else:
            imm_signed = imm
        return True, imm_signed
    return False, 0


def is_lui_instruction(word: int) -> Tuple[bool, int, int]:
    """Check if instruction is LUI and return (is_lui, register, immediate)."""
    opcode = (word >> 26) & 0x3f
    if opcode == 0x0f:  # LUI
        rt = (word >> 16) & 0x1f
        imm = word & 0xffff
        return True, rt, imm
    return False, 0, 0


def is_load_instruction(word: int) -> Tuple[bool, str, int, int, int]:
    """Check if instruction is a load and return (is_load, mnemonic, rt, rs, offset)."""
    opcode = (word >> 26) & 0x3f
    load_mnemonics = {
        0x20: "lb",
        0x21: "lh",
        0x23: "lw",
        0x24: "lbu",
        0x25: "lhu",
        0x27: "lwu",
        0x37: "ld",
    }
    if opcode in load_mnemonics:
        rt = (word >> 16) & 0x1f
        rs = (word >> 21) & 0x1f
        imm = word & 0xffff
        if imm & 0x8000:
            imm = imm - 0x10000
        return True, load_mnemonics[opcode], rt, rs, imm
    return False, "", 0, 0, 0


def is_store_instruction(word: int) -> Tuple[bool, str, int, int, int]:
    """Check if instruction is a store and return (is_store, mnemonic, rt, rs, offset)."""
    opcode = (word >> 26) & 0x3f
    store_mnemonics = {
        0x28: "sb",
        0x29: "sh",
        0x2b: "sw",
        0x3f: "sd",
    }
    if opcode in store_mnemonics:
        rt = (word >> 16) & 0x1f
        rs = (word >> 21) & 0x1f
        imm = word & 0xffff
        if imm & 0x8000:
            imm = imm - 0x10000
        return True, store_mnemonics[opcode], rt, rs, imm
    return False, "", 0, 0, 0


def is_ori_instruction(word: int) -> Tuple[bool, int, int, int]:
    """Check if instruction is ORI and return (is_ori, rt, rs, immediate)."""
    opcode = (word >> 26) & 0x3f
    if opcode == 0x0d:  # ORI
        rt = (word >> 16) & 0x1f
        rs = (word >> 21) & 0x1f
        imm = word & 0xffff
        return True, rt, rs, imm
    return False, 0, 0, 0


def is_addiu_instruction(word: int) -> Tuple[bool, int, int, int]:
    """Check if instruction is ADDIU and return (is_addiu, rt, rs, immediate)."""
    opcode = (word >> 26) & 0x3f
    if opcode == 0x09:  # ADDIU
        rt = (word >> 16) & 0x1f
        rs = (word >> 21) & 0x1f
        imm = word & 0xffff
        if imm & 0x8000:
            imm = imm - 0x10000
        return True, rt, rs, imm
    return False, 0, 0, 0


# =============================================================================
# MIPS64 Instruction Support (IP26/IP28/IP30)
# =============================================================================

def is_daddiu_instruction(word: int) -> Tuple[bool, int, int, int]:
    """
    Check if instruction is DADDIU (64-bit add immediate unsigned).

    DADDIU: opcode 0x19, rt = result, rs = source, imm = immediate
    Used for 64-bit address arithmetic on R8000/R10000.

    Args:
        word: 32-bit instruction word

    Returns:
        Tuple of (is_daddiu, rt, rs, sign_extended_immediate)
    """
    opcode = (word >> 26) & 0x3f
    if opcode == 0x19:  # DADDIU
        rt = (word >> 16) & 0x1f
        rs = (word >> 21) & 0x1f
        imm = word & 0xffff
        if imm & 0x8000:
            imm = imm - 0x10000
        return True, rt, rs, imm
    return False, 0, 0, 0


def is_daddiu_sp_instruction(word: int) -> Tuple[bool, int]:
    """
    Check if instruction is DADDIU $sp, $sp, imm (64-bit stack adjustment).

    This is the MIPS64 equivalent of ADDIU $sp, $sp, -N for function prologues.
    Used on R8000 (IP26) and R10000 (IP28/IP30) systems.

    Args:
        word: 32-bit instruction word

    Returns:
        Tuple of (is_sp_adjustment, stack_adjustment_value)
    """
    is_daddiu, rt, rs, imm = is_daddiu_instruction(word)
    if is_daddiu and rs == 29 and rt == 29:  # $sp = register 29
        return True, imm
    return False, 0


def is_dmtc0_instruction(word: int) -> Tuple[bool, int, int]:
    """
    Check if instruction is DMTC0 (64-bit move to CP0).

    DMTC0: Used for 64-bit CP0 register writes.
    Opcode 0x10 (COP0), function 0x05 (DMTC0).

    Args:
        word: 32-bit instruction word

    Returns:
        Tuple of (is_dmtc0, rt (source), rd (CP0 reg))
    """
    opcode = (word >> 26) & 0x3f
    rs = (word >> 21) & 0x1f
    if opcode == 0x10 and rs == 0x05:  # COP0, MT (DMTC0)
        rt = (word >> 16) & 0x1f
        rd = (word >> 11) & 0x1f
        return True, rt, rd
    return False, 0, 0


def is_dmfc0_instruction(word: int) -> Tuple[bool, int, int]:
    """
    Check if instruction is DMFC0 (64-bit move from CP0).

    DMFC0: Used for 64-bit CP0 register reads.
    Opcode 0x10 (COP0), function 0x01 (DMFC0).

    Args:
        word: 32-bit instruction word

    Returns:
        Tuple of (is_dmfc0, rt (dest), rd (CP0 reg))
    """
    opcode = (word >> 26) & 0x3f
    rs = (word >> 21) & 0x1f
    if opcode == 0x10 and rs == 0x01:  # COP0, MF (DMFC0)
        rt = (word >> 16) & 0x1f
        rd = (word >> 11) & 0x1f
        return True, rt, rd
    return False, 0, 0


def is_ld_instruction(word: int) -> Tuple[bool, int, int, int]:
    """
    Check if instruction is LD (load doubleword, 64-bit).

    LD: opcode 0x37

    Args:
        word: 32-bit instruction word

    Returns:
        Tuple of (is_ld, rt, rs, offset)
    """
    opcode = (word >> 26) & 0x3f
    if opcode == 0x37:  # LD
        rt = (word >> 16) & 0x1f
        rs = (word >> 21) & 0x1f
        imm = word & 0xffff
        if imm & 0x8000:
            imm = imm - 0x10000
        return True, rt, rs, imm
    return False, 0, 0, 0


def is_sd_instruction(word: int) -> Tuple[bool, int, int, int]:
    """
    Check if instruction is SD (store doubleword, 64-bit).

    SD: opcode 0x3f

    Args:
        word: 32-bit instruction word

    Returns:
        Tuple of (is_sd, rt, rs, offset)
    """
    opcode = (word >> 26) & 0x3f
    if opcode == 0x3f:  # SD
        rt = (word >> 16) & 0x1f
        rs = (word >> 21) & 0x1f
        imm = word & 0xffff
        if imm & 0x8000:
            imm = imm - 0x10000
        return True, rt, rs, imm
    return False, 0, 0, 0


def detect_mips64_prom(data: bytes) -> bool:
    """
    Detect if a PROM uses MIPS64 instructions.

    Checks the first few instructions for MIPS64-specific opcodes
    like DMTC0, DADDIU, LD, SD.

    Args:
        data: PROM binary data

    Returns:
        True if MIPS64 instructions detected
    """
    if len(data) < 64:
        return False

    for i in range(0, min(64, len(data) - 3), 4):
        word = read_u32_be(data, i)

        # Check for DMTC0/DMFC0 (64-bit CP0 moves)
        is_dmtc0, _, _ = is_dmtc0_instruction(word)
        is_dmfc0, _, _ = is_dmfc0_instruction(word)
        if is_dmtc0 or is_dmfc0:
            return True

        # Check for DADDIU with $sp
        is_daddiu_sp, _ = is_daddiu_sp_instruction(word)
        if is_daddiu_sp:
            return True

    return False


# =============================================================================
# PROM Classification
# =============================================================================

@dataclass
class PromClassification:
    """Classification result for a PROM file."""
    prom_type: str          # "cpu_prom", "graphics_microcode", "embedded_firmware", etc.
    arch: Optional[str]     # "mips32", "mips64", "ge_custom", None
    executable: bool        # Whether this is executable MIPS code
    description: str        # Human-readable description
    suggested_tools: List[str] = field(default_factory=list)  # Recommended analysis tools


def classify_prom(data: bytes, filename: str) -> PromClassification:
    """
    Classify PROM type based on header/content analysis.

    Identifies:
    - CPU PROMs (MIPS32 or MIPS64)
    - Graphics microcode (GE5, GE7, HQ, RE)
    - System controller firmware (L1, L2, sysco)
    - Adjustment/data tables (adjntsc, adjpal)
    - IO board firmware (io4prom)

    Args:
        data: PROM binary data
        filename: Filename for pattern matching

    Returns:
        PromClassification with type, architecture, and analysis recommendations
    """
    filename_lower = filename.lower()

    # Check for known microcode patterns by filename first
    # Graphics microcode - GE (Geometry Engine)
    if "ge7" in filename_lower or "ge_7" in filename_lower:
        return PromClassification(
            prom_type="graphics_microcode",
            arch="ge7_custom",
            executable=False,
            description="GE7 Geometry Engine microcode (GR2 graphics)",
            suggested_tools=["strings", "hexdump", "xxd"]
        )

    if "ge5" in filename_lower or "ge_5" in filename_lower:
        return PromClassification(
            prom_type="graphics_microcode",
            arch="ge5_custom",
            executable=False,
            description="GE5 Geometry Engine microcode (GTX/Personal IRIS)",
            suggested_tools=["strings", "hexdump", "xxd"]
        )

    if "ge11" in filename_lower or "ge_11" in filename_lower:
        return PromClassification(
            prom_type="graphics_microcode",
            arch="ge11_custom",
            executable=False,
            description="GE11 Geometry Engine microcode (Impact graphics)",
            suggested_tools=["strings", "hexdump", "xxd"]
        )

    # Host Queue microcode
    if "hq2" in filename_lower or "hq3" in filename_lower or "hq_" in filename_lower:
        return PromClassification(
            prom_type="graphics_microcode",
            arch="hq_custom",
            executable=False,
            description="Host Queue processor microcode",
            suggested_tools=["strings", "hexdump", "xxd"]
        )

    # Raster Engine microcode
    if any(x in filename_lower for x in ["re1", "re2", "re3", "re_1", "re_2", "re_3"]):
        return PromClassification(
            prom_type="graphics_microcode",
            arch="re_custom",
            executable=False,
            description="Raster Engine microcode",
            suggested_tools=["strings", "hexdump", "xxd"]
        )

    # Video adjustment tables
    if "adjntsc" in filename_lower or "adjpal" in filename_lower or "adj_" in filename_lower:
        return PromClassification(
            prom_type="adjustment_data",
            arch=None,
            executable=False,
            description="Video timing adjustment tables",
            suggested_tools=["hexdump", "xxd"]
        )

    # System controllers (Origin 3000 L1/L2/L3)
    if "l1" in filename_lower and ("sysco" in filename_lower or "origin" in filename_lower):
        return PromClassification(
            prom_type="embedded_firmware",
            arch="embedded",
            executable=False,
            description="Origin 3000 L1 brick controller firmware (non-MIPS)",
            suggested_tools=["strings", "hexdump"]
        )

    if "l2" in filename_lower and ("sysco" in filename_lower or "origin" in filename_lower):
        return PromClassification(
            prom_type="embedded_firmware",
            arch="embedded",
            executable=False,
            description="Origin 3000 L2 rack controller firmware (non-MIPS)",
            suggested_tools=["strings", "hexdump"]
        )

    if "sysco" in filename_lower:
        return PromClassification(
            prom_type="embedded_firmware",
            arch="embedded",
            executable=False,
            description="System controller firmware (non-MIPS)",
            suggested_tools=["strings", "hexdump"]
        )

    # MSC (Module System Controller for Origin 200/2000)
    if "msc" in filename_lower or "mmsc" in filename_lower:
        return PromClassification(
            prom_type="embedded_firmware",
            arch="embedded",
            executable=False,
            description="Module System Controller firmware (Origin 200/2000)",
            suggested_tools=["strings", "hexdump"]
        )

    # VPro/Buzz graphics
    if "vpro" in filename_lower or "buzz" in filename_lower:
        return PromClassification(
            prom_type="graphics_firmware",
            arch="unknown",
            executable=False,
            description="VPro/Buzz graphics firmware",
            suggested_tools=["strings", "hexdump", "xxd"]
        )

    # InfiniteReality graphics
    if "kona" in filename_lower or "tport" in filename_lower:
        return PromClassification(
            prom_type="graphics_firmware",
            arch="unknown",
            executable=False,
            description="InfiniteReality graphics firmware",
            suggested_tools=["strings", "hexdump", "xxd"]
        )

    # Now analyze the binary content for MIPS code
    if len(data) >= 4:
        first_word = read_u32_be(data, 0)
        opcode = (first_word >> 26) & 0x3f

        # Check for J instruction at reset vector (typical MIPS PROM)
        if opcode == 0x02:  # J instruction
            # Detect MIPS64 vs MIPS32
            if detect_mips64_prom(data):
                return PromClassification(
                    prom_type="cpu_prom",
                    arch="mips64",
                    executable=True,
                    description="CPU boot PROM (MIPS64)",
                    suggested_tools=["disassemble", "find_function_prologues", "build_function_database"]
                )
            else:
                return PromClassification(
                    prom_type="cpu_prom",
                    arch="mips32",
                    executable=True,
                    description="CPU boot PROM (MIPS32)",
                    suggested_tools=["disassemble", "find_function_prologues", "build_function_database"]
                )

        # Check for COP0 instruction (common in MIPS64 boot code - DMTC0/MTC0)
        if opcode == 0x10:  # COP0
            rs = (first_word >> 21) & 0x1f
            if rs in (0x04, 0x05):  # MTC0, DMTC0
                return PromClassification(
                    prom_type="cpu_prom",
                    arch="mips64",
                    executable=True,
                    description="CPU boot PROM (MIPS64, starts with CP0 access)",
                    suggested_tools=["disassemble", "find_function_prologues", "build_function_database"]
                )

        # Check for other valid MIPS startup sequences
        if opcode in (0x00, 0x0f, 0x09, 0x03):  # SPECIAL, LUI, ADDIU, JAL
            return PromClassification(
                prom_type="cpu_prom",
                arch="mips32",
                executable=True,
                description="CPU boot PROM (MIPS32)",
                suggested_tools=["disassemble", "find_function_prologues", "build_function_database"]
            )

    # IO4 PROM (might be MIPS or embedded)
    if "io4prom" in filename_lower:
        return PromClassification(
            prom_type="io_firmware",
            arch="unknown",
            executable=False,
            description="IO4 board firmware",
            suggested_tools=["strings", "hexdump"]
        )

    # Network controller PROMs
    if "enp" in filename_lower:
        return PromClassification(
            prom_type="network_firmware",
            arch="unknown",
            executable=False,
            description="Ethernet controller firmware",
            suggested_tools=["strings", "hexdump"]
        )

    # Default: unknown PROM type
    return PromClassification(
        prom_type="unknown",
        arch=None,
        executable=False,
        description="Unknown PROM type",
        suggested_tools=["strings", "hexdump", "xxd"]
    )


# ARCS callback names based on ARC specification
ARCS_CALLBACKS = [
    "Load",              # 0
    "Invoke",            # 1
    "Execute",           # 2
    "Halt",              # 3
    "PowerDown",         # 4
    "Restart",           # 5
    "Reboot",            # 6
    "EnterInteractiveMode",  # 7
    "reserved_8",        # 8
    "GetPeer",           # 9
    "GetChild",          # 10
    "GetParent",         # 11
    "GetConfigurationData",  # 12
    "AddChild",          # 13
    "DeleteComponent",   # 14
    "GetComponent",      # 15
    "SaveConfiguration", # 16
    "GetSystemId",       # 17
    "GetMemoryDescriptor",  # 18
    "reserved_19",       # 19
    "GetTime",           # 20
    "GetRelativeTime",   # 21
    "GetDirectoryEntry", # 22
    "Open",              # 23
    "Close",             # 24
    "Read",              # 25
    "GetReadStatus",     # 26
    "Write",             # 27
    "Seek",              # 28
    "Mount",             # 29
    "GetEnvironmentVariable",  # 30
    "SetEnvironmentVariable",  # 31
    "GetFileInformation",  # 32
    "SetFileInformation",  # 33
    "FlushAllCaches",    # 34
    "TestUnicodeCharacter",  # 35
    "GetDisplayStatus",  # 36
    "reserved_37",       # 37
    "reserved_38",       # 38
    "reserved_39",       # 39
    # SGI extensions start at 40
    "sgi_unused_40",     # 40
    "sgi_unused_41",     # 41
    "sgi_unused_42",     # 42
    "sgi_FlushAllCaches",  # 43
    "sgi_unused_44",     # 44
]


def get_arcs_callback_name(index: int) -> str:
    """Get ARCS callback name by index."""
    if 0 <= index < len(ARCS_CALLBACKS):
        return f"ARCS_{ARCS_CALLBACKS[index]}"
    return f"ARCS_unknown_{index}"


# =============================================================================
# QEMU Debugging Tools - Data Structures
# =============================================================================

@dataclass
class QemuLogEntry:
    """A parsed entry from QEMU -d unimp output."""
    line_number: int           # Original line number in log
    device: str                # Device name (sgi_mc, sgi_hpc3, etc.)
    register_offset: int       # Register offset within device
    full_address: int          # Full MMIO address
    operation: AccessType      # READ or WRITE
    value: Optional[int]       # Value for writes, None for reads
    raw_line: str              # Original log line
    annotation: Optional[str]  # Hardware annotation if available


@dataclass
class QemuLogSummary:
    """Summary of parsed QEMU log."""
    total_lines: int
    hardware_accesses: int
    entries: List[QemuLogEntry]
    device_counts: Dict[str, int]
    register_counts: Dict[str, int]
    unrecognized_lines: List[str]


@dataclass
class ExpectedAccess:
    """An expected hardware access from PROM analysis."""
    order: int                 # Execution order
    code_address: int          # Address of instruction in PROM
    device: str                # Device name
    register: str              # Register name
    full_address: int          # Full MMIO address
    operation: AccessType      # READ or WRITE
    expected_value: Optional[int]  # Expected value if known
    value_source: str          # "immediate", "register", "unknown"
    description: str           # Register description


@dataclass
class RegisterValueAnalysis:
    """Analysis of a register value being written."""
    code_address: int          # Address of the store instruction
    device: str                # Device name
    register: str              # Register name
    full_address: int          # Full MMIO address
    operation: AccessType      # READ or WRITE
    value: Optional[int]       # Computed value if known
    value_confidence: str      # "exact", "partial", "unknown"
    instruction_sequence: List[str]  # Instructions that built the value
    is_polling_loop: bool      # True if this is part of a polling loop


@dataclass
class ExecutionDivergence:
    """A divergence between expected and actual execution."""
    divergence_type: str       # "missing", "unexpected", "wrong_order", "wrong_value"
    expected: Optional[ExpectedAccess]
    actual: Optional[QemuLogEntry]
    severity: str              # "critical", "warning", "info"
    suggestion: str            # Suggested fix or action


@dataclass
class ExecutionComparison:
    """Result of comparing QEMU trace vs expected PROM sequence."""
    prom_name: str
    log_file: str
    expected_count: int
    actual_count: int
    match_count: int
    divergences: List[ExecutionDivergence]
    summary: str
    recommendations: List[str]
