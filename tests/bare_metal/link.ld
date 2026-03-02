/*
 * Linker script for SGI Indy bare-metal timing tests.
 * Places code at 0xBFC00000 (PROM base, kseg1-mapped).
 * Stack at 0x80100000 (1MB into kseg0 RAM).
 */

OUTPUT_FORMAT("elf32-bigmips")
OUTPUT_ARCH(mips)
ENTRY(_start)

SECTIONS
{
    . = 0xBFC00000;

    .text : {
        *(.text)
        *(.text.*)
    }

    .rodata : {
        *(.rodata)
        *(.rodata.*)
    }

    .data : {
        *(.data)
        *(.data.*)
    }

    .bss : {
        *(.bss)
        *(.bss.*)
        *(COMMON)
    }

    /DISCARD/ : {
        *(.MIPS.abiflags)
        *(.reginfo)
        *(.pdr)
        *(.comment)
        *(.note.*)
    }
}
