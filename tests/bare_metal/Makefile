# MIPS cross-toolchain prefix. Defaults to the workstation location
# (~/cross/mips-elf); override with `make CROSS=/path/to/mips-elf-`.
CROSS ?= $(HOME)/cross/mips-elf/bin/mips-elf-
CC = $(CROSS)gcc
LD = $(CROSS)ld
OBJCOPY = $(CROSS)objcopy

# Use GCC to assemble .S files (handles #define and C preprocessor)
CFLAGS = -mips3 -mabi=32 -EB -nostdlib -nostartfiles -ffreestanding -c
LDFLAGS = -T link.ld -EB

all: timing_test.bin scsi_bench.bin

timing_test.bin: timing_test.elf
	$(OBJCOPY) -O binary $< $@

timing_test.elf: timing_test.o link.ld
	$(LD) $(LDFLAGS) -o $@ timing_test.o

timing_test.o: timing_test.S
	$(CC) $(CFLAGS) -o $@ $<

scsi_bench.bin: scsi_bench.elf
	$(OBJCOPY) -O binary $< $@

scsi_bench.elf: scsi_bench.o link.ld
	$(LD) $(LDFLAGS) -o $@ scsi_bench.o

scsi_bench.o: scsi_bench.S
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f *.o *.elf *.bin

.PHONY: all clean
