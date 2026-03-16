# Phase 1 Complete: IP54 Interactive Login (2026-03-12)

## Achievement

IRIX 6.5.5 boots to multi-user on the IP54 paravirtual machine and accepts interactive
root login via serial console. Full process tree running: init, cron, syslogd, sendmail,
inetd, httpd, lpsched, getty.

## What Was Fixed

### pvuart_cn.c — Three Changes

1. **RX Polling Timer**: Added `du_poll()` callback at 50Hz via `timeout()`. Calls
   `qenable(du_poll_rq)` when `pvuart_rxrdy()` returns true. Started in `du_open()`,
   cancelled in `du_close()`. Without this, `du_rsrv()` was never scheduled and reads
   blocked forever.

2. **M_IOCTL Handling**: Added `M_IOCTL` case to `du_wput()`. TCGETA returns a default
   `struct termio` (9600/CS8/OPOST|ONLCR/ICANON|ECHO|ISIG). TCSETA/TCSETAW/TCSETAF
   are ACK'd (ignored — pvuart has no configurable baud). Everything else is NAK'd.
   Without this, `stty_ld` hung waiting for ioctl responses.

3. **du_lateinit() hwgraph nodes**: Creates `/hw/ttys/ttyd1` and `/hw/ttys/ttyd2` via
   `hwgraph_path_add()` + `hwgraph_char_device_add()`. Does NOT update `cons_devs[]`
   (updating it caused init to block on console open). The on-disk symlink
   `/dev/ttyd1 → /hw/ttys/ttyd1` now resolves correctly.

### Compilation

pvuart_cn.c compiled on IRIX with MIPSpro 7.2.1:
```
/usr/cpu/sysgen/root/usr/bin/cc -c -n32 -mips3 -O2 -G 8 -non_shared \
  -TENV:kernel -DIP54 -D_KERNEL -I/usr/include pvuart_cn.c -o pvuart_cn.o
```

Required setup on irix655-dev:
- `ln -s /usr/cpu/sysgen/root/usr/bin/cc /usr/bin/cc`
- `cp /usr/cpu/sysgen/root/usr/lib32/cmplrs/* /usr/lib32/cmplrs/`
- `ln -s /usr/cpu/sysgen/root/usr/lib/ld /usr/lib/ld`
- Created minimal `/usr/include/stdarg.h`

### PROM Change

Changed `OSLoadFilename` from `/unix` to `/unix.new` in `prom-building/src/libsk/ml/env.c`
so the IP54 PROM loads the correct kernel.

## RAM Limitation

**64MB only.** With 256MB, the kernel PANICs in `bzero()` at KSEG2 addresses
(0xC0140000 range). TLB entries map to valid physical RAM (PFN=0x0FF2A → PA=0x0FF2A000)
but QEMU returns bus errors. Root cause: the IP54 machine's low-alias memory region
only covers the first 64MB of physical RAM (0x00000000-0x03FFFFFF → 0x08000000-0x0BFFFFFF).
Higher addresses (0x0C000000+) are not aliased and may not be properly wired in QEMU's
address space.

## System State at Login

```
IRIX IRIS 6.5 07151432 IP54
CPU: MIPS R10000 Processor Chip Revision: 0.0
Main memory size: 64 Mbytes
Filesystem: /dev/root xfs 4056512 kbytes, 24% used
Processes: ~25 daemons running
Console: ttyd1, root login successful
```

## Remaining Issues (Phase 2+)

- `kernel malloc: invalid size -- 1122342912` during early boot (doesn't prevent boot)
- No swap configured (`/dev/swap` doesn't exist)
- `configmon: namelist and corefile do not match!` (expected — /unix is IP22)
- lboot auto-reconfig triggers on multi-user if IP54 .o files have future timestamps
- 256MB RAM crash needs QEMU IP54 memory map fix
