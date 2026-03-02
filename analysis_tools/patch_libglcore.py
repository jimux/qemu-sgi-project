#!/usr/bin/env python3
"""
patch_libglcore.py - IRIX libGLcore.so Binary Patching Tool

This tool analyzes and patches IRIX 6.5.22 libGLcore.so to redirect
OpenGL rendering calls to the custom GIO64 GL Accelerator device.

Usage:
    python3 patch_libglcore.py analyze <libGLcore.so>
    python3 patch_libglcore.py patch <input.so> <output.so>
    python3 patch_libglcore.py info <libGLcore.so>

The GL Accelerator is mapped at 0x1f400000 (GIO64 EXP0 slot).
"""

import argparse
import struct
import sys
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from pathlib import Path


# GL Accelerator register addresses (physical addresses)
GLACCEL_BASE = 0x1f400000

# Register offsets (from sgi_glaccel.h)
REG_ID              = 0x0000
REG_VERSION         = 0x0004
REG_ENABLE          = 0x0014
REG_MTX_MODE        = 0x0200
REG_MTX_PUSH        = 0x0204
REG_MTX_POP         = 0x0208
REG_MTX_LOAD_IDENT  = 0x020c
REG_MTX_DATA        = 0x0210
REG_MTX_MULT        = 0x0250
REG_VERTEX_X        = 0x0300
REG_VERTEX_Y        = 0x0304
REG_VERTEX_Z        = 0x0308
REG_VERTEX_W        = 0x030c
REG_VERTEX_EMIT     = 0x0310
REG_COLOR_R         = 0x0400
REG_COLOR_G         = 0x0404
REG_COLOR_B         = 0x0408
REG_COLOR_A         = 0x040c
REG_NORMAL_X        = 0x0500
REG_NORMAL_Y        = 0x0504
REG_NORMAL_Z        = 0x0508
REG_PRIM_BEGIN      = 0x0600
REG_PRIM_END        = 0x0604


# MIPS instruction encoding helpers
def encode_lui(rt: int, imm: int) -> int:
    """LUI rt, imm - Load Upper Immediate"""
    return (0x0f << 26) | (rt << 16) | (imm & 0xffff)

def encode_ori(rt: int, rs: int, imm: int) -> int:
    """ORI rt, rs, imm - OR Immediate"""
    return (0x0d << 26) | (rs << 21) | (rt << 16) | (imm & 0xffff)

def encode_sw(rt: int, offset: int, base: int) -> int:
    """SW rt, offset(base) - Store Word"""
    return (0x2b << 26) | (base << 21) | (rt << 16) | (offset & 0xffff)

def encode_lw(rt: int, offset: int, base: int) -> int:
    """LW rt, offset(base) - Load Word"""
    return (0x23 << 26) | (base << 21) | (rt << 16) | (offset & 0xffff)

def encode_jr(rs: int) -> int:
    """JR rs - Jump Register"""
    return (rs << 21) | 0x08

def encode_nop() -> int:
    """NOP (SLL $zero, $zero, 0)"""
    return 0x00000000

def encode_mtc1(rt: int, fs: int) -> int:
    """MTC1 rt, fs - Move To Coprocessor 1"""
    return (0x11 << 26) | (0x04 << 21) | (rt << 16) | (fs << 11)

def encode_mfc1(rt: int, fs: int) -> int:
    """MFC1 rt, fs - Move From Coprocessor 1"""
    return (0x11 << 26) | (0x00 << 21) | (rt << 16) | (fs << 11)

def encode_swc1(ft: int, offset: int, base: int) -> int:
    """SWC1 ft, offset(base) - Store Word from Coprocessor 1"""
    return (0x39 << 26) | (base << 21) | (ft << 16) | (offset & 0xffff)

def encode_lwc1(ft: int, offset: int, base: int) -> int:
    """LWC1 ft, offset(base) - Load Word to Coprocessor 1"""
    return (0x31 << 26) | (base << 21) | (ft << 16) | (offset & 0xffff)


@dataclass
class ElfHeader:
    """MIPS ELF32 header"""
    e_type: int
    e_machine: int
    e_version: int
    e_entry: int
    e_phoff: int
    e_shoff: int
    e_flags: int
    e_ehsize: int
    e_phentsize: int
    e_phnum: int
    e_shentsize: int
    e_shnum: int
    e_shstrndx: int


@dataclass
class SectionHeader:
    """MIPS ELF32 section header"""
    sh_name: int
    sh_type: int
    sh_flags: int
    sh_addr: int
    sh_offset: int
    sh_size: int
    sh_link: int
    sh_info: int
    sh_addralign: int
    sh_entsize: int
    name: str = ""


@dataclass
class Symbol:
    """MIPS ELF32 symbol"""
    st_name: int
    st_value: int
    st_size: int
    st_info: int
    st_other: int
    st_shndx: int
    name: str = ""


class MipsElfParser:
    """Parser for MIPS ELF32 binaries (big-endian)"""

    def __init__(self, data: bytes):
        self.data = data
        self.header: Optional[ElfHeader] = None
        self.sections: List[SectionHeader] = []
        self.symbols: List[Symbol] = []
        self.string_tables: Dict[int, bytes] = {}

    def parse(self) -> bool:
        """Parse the ELF file"""
        if len(self.data) < 52:
            print("Error: File too small for ELF header")
            return False

        # Check ELF magic
        if self.data[:4] != b'\x7fELF':
            print("Error: Not an ELF file")
            return False

        # Check class (32-bit) and endianness (big-endian for MIPS)
        if self.data[4] != 1:  # ELFCLASS32
            print("Error: Not a 32-bit ELF")
            return False

        if self.data[5] != 2:  # ELFDATA2MSB (big-endian)
            print("Warning: Expected big-endian MIPS ELF")

        # Parse ELF header (big-endian)
        self.header = ElfHeader(
            e_type=struct.unpack('>H', self.data[16:18])[0],
            e_machine=struct.unpack('>H', self.data[18:20])[0],
            e_version=struct.unpack('>I', self.data[20:24])[0],
            e_entry=struct.unpack('>I', self.data[24:28])[0],
            e_phoff=struct.unpack('>I', self.data[28:32])[0],
            e_shoff=struct.unpack('>I', self.data[32:36])[0],
            e_flags=struct.unpack('>I', self.data[36:40])[0],
            e_ehsize=struct.unpack('>H', self.data[40:42])[0],
            e_phentsize=struct.unpack('>H', self.data[42:44])[0],
            e_phnum=struct.unpack('>H', self.data[44:46])[0],
            e_shentsize=struct.unpack('>H', self.data[46:48])[0],
            e_shnum=struct.unpack('>H', self.data[48:50])[0],
            e_shstrndx=struct.unpack('>H', self.data[50:52])[0],
        )

        # Check machine type (MIPS)
        if self.header.e_machine != 8:  # EM_MIPS
            print(f"Warning: Machine type {self.header.e_machine} is not MIPS (8)")

        # Parse section headers
        self._parse_sections()

        # Parse symbols
        self._parse_symbols()

        return True

    def _parse_sections(self):
        """Parse section headers"""
        if not self.header:
            return

        shoff = self.header.e_shoff
        shentsize = self.header.e_shentsize
        shnum = self.header.e_shnum

        for i in range(shnum):
            offset = shoff + i * shentsize
            sh = SectionHeader(
                sh_name=struct.unpack('>I', self.data[offset:offset+4])[0],
                sh_type=struct.unpack('>I', self.data[offset+4:offset+8])[0],
                sh_flags=struct.unpack('>I', self.data[offset+8:offset+12])[0],
                sh_addr=struct.unpack('>I', self.data[offset+12:offset+16])[0],
                sh_offset=struct.unpack('>I', self.data[offset+16:offset+20])[0],
                sh_size=struct.unpack('>I', self.data[offset+20:offset+24])[0],
                sh_link=struct.unpack('>I', self.data[offset+24:offset+28])[0],
                sh_info=struct.unpack('>I', self.data[offset+28:offset+32])[0],
                sh_addralign=struct.unpack('>I', self.data[offset+32:offset+36])[0],
                sh_entsize=struct.unpack('>I', self.data[offset+36:offset+40])[0],
            )
            self.sections.append(sh)

            # Save string tables
            if sh.sh_type == 3:  # SHT_STRTAB
                self.string_tables[i] = self.data[sh.sh_offset:sh.sh_offset+sh.sh_size]

        # Resolve section names
        if self.header.e_shstrndx in self.string_tables:
            strtab = self.string_tables[self.header.e_shstrndx]
            for sh in self.sections:
                sh.name = self._get_string(strtab, sh.sh_name)

    def _parse_symbols(self):
        """Parse symbol table"""
        symtab = None
        strtab_idx = 0

        for sh in self.sections:
            if sh.sh_type == 2:  # SHT_SYMTAB
                symtab = sh
                strtab_idx = sh.sh_link
                break
            elif sh.sh_type == 11:  # SHT_DYNSYM
                symtab = sh
                strtab_idx = sh.sh_link

        if not symtab:
            return

        strtab = self.string_tables.get(strtab_idx, b'')
        num_syms = symtab.sh_size // 16  # sizeof(Elf32_Sym) = 16

        for i in range(num_syms):
            offset = symtab.sh_offset + i * 16
            sym = Symbol(
                st_name=struct.unpack('>I', self.data[offset:offset+4])[0],
                st_value=struct.unpack('>I', self.data[offset+4:offset+8])[0],
                st_size=struct.unpack('>I', self.data[offset+8:offset+12])[0],
                st_info=self.data[offset+12],
                st_other=self.data[offset+13],
                st_shndx=struct.unpack('>H', self.data[offset+14:offset+16])[0],
            )
            sym.name = self._get_string(strtab, sym.st_name)
            self.symbols.append(sym)

    def _get_string(self, strtab: bytes, offset: int) -> str:
        """Get null-terminated string from string table"""
        if offset >= len(strtab):
            return ""
        end = strtab.find(b'\x00', offset)
        if end == -1:
            end = len(strtab)
        return strtab[offset:end].decode('ascii', errors='replace')

    def find_symbol(self, name: str) -> Optional[Symbol]:
        """Find symbol by name"""
        for sym in self.symbols:
            if sym.name == name:
                return sym
        return None

    def find_symbols_containing(self, substring: str) -> List[Symbol]:
        """Find all symbols containing substring"""
        return [sym for sym in self.symbols if substring in sym.name]

    def get_section_by_name(self, name: str) -> Optional[SectionHeader]:
        """Get section by name"""
        for sh in self.sections:
            if sh.name == name:
                return sh
        return None

    def addr_to_offset(self, addr: int) -> Optional[int]:
        """Convert virtual address to file offset"""
        for sh in self.sections:
            if sh.sh_addr <= addr < sh.sh_addr + sh.sh_size:
                return sh.sh_offset + (addr - sh.sh_addr)
        return None

    def read_word(self, offset: int) -> int:
        """Read big-endian 32-bit word at offset"""
        return struct.unpack('>I', self.data[offset:offset+4])[0]

    def disassemble_at(self, addr: int, count: int = 10) -> List[Tuple[int, int, str]]:
        """Disassemble instructions at address"""
        results = []
        offset = self.addr_to_offset(addr)
        if offset is None:
            return results

        for i in range(count):
            if offset + 4 > len(self.data):
                break
            insn = self.read_word(offset)
            disasm = self._disasm_insn(insn)
            results.append((addr, insn, disasm))
            addr += 4
            offset += 4

        return results

    def _disasm_insn(self, insn: int) -> str:
        """Simple MIPS disassembler"""
        op = (insn >> 26) & 0x3f
        rs = (insn >> 21) & 0x1f
        rt = (insn >> 16) & 0x1f
        rd = (insn >> 11) & 0x1f
        shamt = (insn >> 6) & 0x1f
        funct = insn & 0x3f
        imm = insn & 0xffff
        simm = imm if imm < 0x8000 else imm - 0x10000

        regs = ['zero', 'at', 'v0', 'v1', 'a0', 'a1', 'a2', 'a3',
                't0', 't1', 't2', 't3', 't4', 't5', 't6', 't7',
                's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7',
                't8', 't9', 'k0', 'k1', 'gp', 'sp', 'fp', 'ra']

        if op == 0:  # R-type
            if funct == 0:
                if rd == 0 and rt == 0 and shamt == 0:
                    return "nop"
                return f"sll ${regs[rd]}, ${regs[rt]}, {shamt}"
            elif funct == 8:
                return f"jr ${regs[rs]}"
            elif funct == 9:
                return f"jalr ${regs[rd]}, ${regs[rs]}"
            elif funct == 32:
                return f"add ${regs[rd]}, ${regs[rs]}, ${regs[rt]}"
            elif funct == 33:
                return f"addu ${regs[rd]}, ${regs[rs]}, ${regs[rt]}"
            elif funct == 36:
                return f"and ${regs[rd]}, ${regs[rs]}, ${regs[rt]}"
            elif funct == 37:
                return f"or ${regs[rd]}, ${regs[rs]}, ${regs[rt]}"
            else:
                return f"r-type funct={funct}"
        elif op == 2:
            target = (insn & 0x03ffffff) << 2
            return f"j 0x{target:08x}"
        elif op == 3:
            target = (insn & 0x03ffffff) << 2
            return f"jal 0x{target:08x}"
        elif op == 4:
            return f"beq ${regs[rs]}, ${regs[rt]}, {simm}"
        elif op == 5:
            return f"bne ${regs[rs]}, ${regs[rt]}, {simm}"
        elif op == 8:
            return f"addi ${regs[rt]}, ${regs[rs]}, {simm}"
        elif op == 9:
            return f"addiu ${regs[rt]}, ${regs[rs]}, {simm}"
        elif op == 12:
            return f"andi ${regs[rt]}, ${regs[rs]}, 0x{imm:04x}"
        elif op == 13:
            return f"ori ${regs[rt]}, ${regs[rs]}, 0x{imm:04x}"
        elif op == 15:
            return f"lui ${regs[rt]}, 0x{imm:04x}"
        elif op == 17:  # COP1
            return f"cop1 0x{insn:08x}"
        elif op == 35:
            return f"lw ${regs[rt]}, {simm}(${regs[rs]})"
        elif op == 43:
            return f"sw ${regs[rt]}, {simm}(${regs[rs]})"
        elif op == 49:
            return f"lwc1 $f{rt}, {simm}(${regs[rs]})"
        elif op == 57:
            return f"swc1 $f{rt}, {simm}(${regs[rs]})"
        else:
            return f"op={op} 0x{insn:08x}"


def generate_glVertex3f_trampoline() -> List[int]:
    """
    Generate MIPS code for glVertex3f trampoline

    glVertex3f(float x, float y, float z):
        - x in $f12, y in $f14, z in $f16 (MIPS O32 ABI)

    Trampoline stores x, y, z to accelerator and emits vertex.
    """
    code = []

    # Load accelerator base address into $t0
    # lui $t0, 0x1f40
    code.append(encode_lui(8, GLACCEL_BASE >> 16))

    # Store x (from $f12) to VERTEX_X
    # swc1 $f12, REG_VERTEX_X($t0)
    code.append(encode_swc1(12, REG_VERTEX_X, 8))

    # Store y (from $f14) to VERTEX_Y
    # swc1 $f14, REG_VERTEX_Y($t0)
    code.append(encode_swc1(14, REG_VERTEX_Y, 8))

    # Store z (from $f16) to VERTEX_Z
    # swc1 $f16, REG_VERTEX_Z($t0)
    code.append(encode_swc1(16, REG_VERTEX_Z, 8))

    # Store 1.0 to VERTEX_W (need to load constant)
    # lui $t1, 0x3f80  (1.0f = 0x3f800000)
    code.append(encode_lui(9, 0x3f80))
    # sw $t1, REG_VERTEX_W($t0)
    code.append(encode_sw(9, REG_VERTEX_W, 8))

    # Emit vertex by writing to VERTEX_EMIT
    # sw $zero, REG_VERTEX_EMIT($t0)
    code.append(encode_sw(0, REG_VERTEX_EMIT, 8))

    # Return
    # jr $ra
    code.append(encode_jr(31))
    # nop (delay slot)
    code.append(encode_nop())

    return code


def generate_glColor3f_trampoline() -> List[int]:
    """
    Generate MIPS code for glColor3f trampoline

    glColor3f(float r, float g, float b):
        - r in $f12, g in $f14, b in $f16
    """
    code = []

    # Load accelerator base address into $t0
    code.append(encode_lui(8, GLACCEL_BASE >> 16))

    # Store r to COLOR_R
    code.append(encode_swc1(12, REG_COLOR_R, 8))

    # Store g to COLOR_G
    code.append(encode_swc1(14, REG_COLOR_G, 8))

    # Store b to COLOR_B
    code.append(encode_swc1(16, REG_COLOR_B, 8))

    # Store 1.0 to COLOR_A
    code.append(encode_lui(9, 0x3f80))
    code.append(encode_sw(9, REG_COLOR_A, 8))

    # Return
    code.append(encode_jr(31))
    code.append(encode_nop())

    return code


def generate_glBegin_trampoline() -> List[int]:
    """
    Generate MIPS code for glBegin trampoline

    glBegin(GLenum mode):
        - mode in $a0
    """
    code = []

    # Load accelerator base address into $t0
    code.append(encode_lui(8, GLACCEL_BASE >> 16))

    # Store mode to PRIM_BEGIN
    code.append(encode_sw(4, REG_PRIM_BEGIN, 8))  # $a0 = 4

    # Return
    code.append(encode_jr(31))
    code.append(encode_nop())

    return code


def generate_glEnd_trampoline() -> List[int]:
    """
    Generate MIPS code for glEnd trampoline
    """
    code = []

    # Load accelerator base address into $t0
    code.append(encode_lui(8, GLACCEL_BASE >> 16))

    # Write to PRIM_END
    code.append(encode_sw(0, REG_PRIM_END, 8))

    # Return
    code.append(encode_jr(31))
    code.append(encode_nop())

    return code


def cmd_analyze(args):
    """Analyze libGLcore.so and find patchable functions"""
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        return 1

    data = input_path.read_bytes()
    elf = MipsElfParser(data)

    if not elf.parse():
        return 1

    print(f"Analyzing: {input_path}")
    print(f"File size: {len(data)} bytes")
    print()

    # Look for Newport-specific rendering functions
    newport_funcs = [
        "__glNptFillOffsetTriangle",
        "__glNptPickPixelProcs",
        "__glNptRenderBitmap",
        "__glSpanRenderRGBA",
        "__glSpanRenderDepth",
    ]

    print("Newport Rendering Functions:")
    print("-" * 60)
    for name in newport_funcs:
        sym = elf.find_symbol(name)
        if sym:
            print(f"  {name}:")
            print(f"    Address: 0x{sym.st_value:08x}")
            print(f"    Size: {sym.st_size} bytes")

            # Disassemble first few instructions
            disasm = elf.disassemble_at(sym.st_value, 5)
            for addr, insn, text in disasm:
                print(f"      0x{addr:08x}: {insn:08x}  {text}")
        else:
            print(f"  {name}: NOT FOUND")
        print()

    # Look for GL functions we want to accelerate
    gl_funcs = [
        "glVertex3f",
        "glVertex3fv",
        "glColor3f",
        "glColor4f",
        "glNormal3f",
        "glBegin",
        "glEnd",
        "glMatrixMode",
        "glLoadIdentity",
        "glPushMatrix",
        "glPopMatrix",
    ]

    print("GL API Functions:")
    print("-" * 60)
    for name in gl_funcs:
        matches = elf.find_symbols_containing(name)
        for sym in matches:
            if sym.name == name or sym.name.startswith(name + "_"):
                print(f"  {sym.name}:")
                print(f"    Address: 0x{sym.st_value:08x}")
                print(f"    Size: {sym.st_size} bytes")

    # Find other interesting symbols
    print()
    print("Other Interesting Symbols:")
    print("-" * 60)
    interesting = ["Matrix", "Viewport", "Light", "Material", "Texture"]
    for substr in interesting:
        matches = elf.find_symbols_containing(substr)
        if matches:
            print(f"  Containing '{substr}': {len(matches)} symbols")
            for sym in matches[:3]:
                print(f"    {sym.name} @ 0x{sym.st_value:08x}")
            if len(matches) > 3:
                print(f"    ... and {len(matches) - 3} more")

    return 0


def cmd_patch(args):
    """Patch libGLcore.so to use GL accelerator"""
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        return 1

    data = bytearray(input_path.read_bytes())
    elf = MipsElfParser(bytes(data))

    if not elf.parse():
        return 1

    print(f"Patching: {input_path}")
    print(f"Output: {output_path}")
    print()

    patches_applied = 0

    # For each function we want to patch, find it and replace with trampoline
    patch_targets = [
        ("glVertex3f", generate_glVertex3f_trampoline),
        ("glColor3f", generate_glColor3f_trampoline),
        ("glBegin", generate_glBegin_trampoline),
        ("glEnd", generate_glEnd_trampoline),
    ]

    for func_name, trampoline_gen in patch_targets:
        sym = elf.find_symbol(func_name)
        if not sym:
            print(f"  {func_name}: NOT FOUND (skipping)")
            continue

        trampoline = trampoline_gen()
        trampoline_size = len(trampoline) * 4

        if sym.st_size < trampoline_size:
            print(f"  {func_name}: Function too small ({sym.st_size} < {trampoline_size})")
            continue

        offset = elf.addr_to_offset(sym.st_value)
        if offset is None:
            print(f"  {func_name}: Could not map address to offset")
            continue

        print(f"  {func_name}:")
        print(f"    Address: 0x{sym.st_value:08x}")
        print(f"    Offset: 0x{offset:08x}")
        print(f"    Trampoline size: {trampoline_size} bytes")

        # Write trampoline code
        for i, insn in enumerate(trampoline):
            insn_bytes = struct.pack('>I', insn)
            data[offset + i*4:offset + i*4 + 4] = insn_bytes

        # Fill remaining space with NOPs
        remaining = sym.st_size - trampoline_size
        for i in range(remaining // 4):
            nop_offset = offset + trampoline_size + i * 4
            data[nop_offset:nop_offset+4] = struct.pack('>I', encode_nop())

        print(f"    Patched successfully")
        patches_applied += 1

    print()
    print(f"Total patches applied: {patches_applied}")

    if patches_applied > 0:
        output_path.write_bytes(data)
        print(f"Wrote patched binary to: {output_path}")
    else:
        print("No patches applied, output file not written")
        return 1

    return 0


def cmd_info(args):
    """Show ELF file information"""
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        return 1

    data = input_path.read_bytes()
    elf = MipsElfParser(data)

    if not elf.parse():
        return 1

    print(f"ELF Information: {input_path}")
    print("=" * 60)

    h = elf.header
    print(f"Type: {['NONE', 'REL', 'EXEC', 'DYN', 'CORE'][h.e_type] if h.e_type < 5 else h.e_type}")
    print(f"Machine: {'MIPS' if h.e_machine == 8 else h.e_machine}")
    print(f"Entry: 0x{h.e_entry:08x}")
    print(f"Flags: 0x{h.e_flags:08x}")
    print()

    print("Sections:")
    print("-" * 60)
    for i, sh in enumerate(elf.sections):
        if sh.sh_size > 0:
            print(f"  [{i:2d}] {sh.name:20s} addr=0x{sh.sh_addr:08x} size=0x{sh.sh_size:08x}")

    print()
    print(f"Total symbols: {len(elf.symbols)}")

    # Count function symbols
    func_count = sum(1 for s in elf.symbols if (s.st_info & 0xf) == 2)  # STT_FUNC
    print(f"Function symbols: {func_count}")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="IRIX libGLcore.so Binary Patching Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # analyze command
    analyze_parser = subparsers.add_parser('analyze', help='Analyze library for patchable functions')
    analyze_parser.add_argument('input', help='Input libGLcore.so file')

    # patch command
    patch_parser = subparsers.add_parser('patch', help='Patch library to use GL accelerator')
    patch_parser.add_argument('input', help='Input libGLcore.so file')
    patch_parser.add_argument('output', help='Output patched file')

    # info command
    info_parser = subparsers.add_parser('info', help='Show ELF file information')
    info_parser.add_argument('input', help='Input ELF file')

    args = parser.parse_args()

    if args.command == 'analyze':
        return cmd_analyze(args)
    elif args.command == 'patch':
        return cmd_patch(args)
    elif args.command == 'info':
        return cmd_info(args)
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())
