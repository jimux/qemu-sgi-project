# IP54 Phase 10: Networking, Graphics, Audio (2026-03-16)

## Summary

All five IP54 paravirtual devices now work end-to-end on IRIX 6.5.5:

| Device | QEMU Type | Kernel Driver | Tested |
|--------|-----------|---------------|--------|
| pvdisk | sgi-bootdisk | pvdisk.c | Phase 8 |
| pvuart_cn | sgi-pvuart | pvuart_cn.c | Phase 9 |
| pvnet | sgi-pvnet | if_pvnet.c | **Phase 10** - ping 0% loss |
| pvfb | sgi-glaccel | pvfb.c | **Phase 10** - gradient rendered |
| pvaudio | sgi-pvaudio | pvaudio.c | **Phase 10** - PCM write works |

## Networking (pvnet)

### Bug 1: PROM io_init table incomplete
The PROM only appended `pvdiskedtinit` to the io_init[] function pointer array.
`if_pvnetedtinit`, `pvfbedtinit`, and `pvaudioedtinit` were missing. Fixed in
`prom-building/src/fw/ip54_stubs.c` to append all four PV device edtinit functions.

### Bug 2: QEMU reentrancy_guard NULL pointer
`qemu_new_nic()` was passed NULL for the `reentrancy_guard` parameter. When SLIRP
processed an ARP reply and tried to deliver it back to the NIC,
`qemu_deliver_packet_iov()` dereferenced `reentrancy_guard->engaged_in_io` = SEGFAULT.

**Root cause**: Line 851 in `qemu/net/net.c`:
```c
qemu_get_nic(nc)->reentrancy_guard->engaged_in_io
```

**Fix**: Added `MemReentrancyGuard reentrancy_guard` to `SGIPVNetState` struct and
passed `&s->reentrancy_guard` to `qemu_new_nic()`.

### Bug 3: TX from MMIO handler
`qemu_send_packet()` called from within MMIO write handler could cause issues.
Deferred TX to a QEMU bottom-half (`qemu_bh_new`/`qemu_bh_schedule`).

### Verification
```
IRIS# ping -c 5 10.0.2.2
5 packets transmitted, 5 packets received, 0.0% packet loss
```

## Graphics (pvfb / glaccel)

### Issue 1: IRIX hwgraph device model
IRIX 6.5 routes char device opens through hwgraph, not raw cdevsw. `mknod` with a
cdevsw major number returns ENXIO. Drivers must register via
`hwgraph_char_device_add()` to create `/hw/<name>` entries (all use major 0).

**Fix**: Added `pvfbedtinit()` function that calls
`hwgraph_char_device_add(hwgraph_root, "pvfb", "pvfb", &vhdl)`.

### Issue 2: Kernel memory allocation failure
`kvpalloc()` and `kmem_alloc()` both hang or fail when allocating 1-5MB contiguous
physical memory on 64MB systems. `VM_NOSLEEP` returns NULL; without it, the call
blocks forever waiting for contiguous pages that will never free.

**Fix**: Static BSS buffer `pvfb_static_fb[640*480*4]` in kernel data segment. BSS is
in KSEG0 (direct-mapped), so `kvtophys()` gives the correct physical address for DMA.

### Issue 3: glaccel IRQ hang
The glaccel QEMU device raised an interrupt on EXEC_PROCESS. Since the kernel has no
interrupt handler for IP5 (glaccel), the unhandled interrupt caused a kernel hang.

**Fix**: Removed `qemu_irq_raise(s->irq)` from the EXEC_PROCESS path in
`sgi_glaccel.c`. The pvfb driver polls `STATUS_DONE` instead.

### Verification
```
IRIS# ./fbtest
opened ok
setmode ok
mmap ok
done
```
Screendump confirms 640x480 RGBA8888 gradient: R varies with x, G with y, B=128.

## Audio (pvaudio)

### Same fixes as pvfb
- hwgraph registration via `pvaudioedtinit()`
- Static BSS ring buffer `pvaudio_static_buf[64*1024]`

### Verification
```
IRIS# ./audiotest 2>&1
audio done
```

## Compilation Notes (no /usr/include on ip54-test)

The ip54-test instance has MIPSpro cc but no userland headers. Test programs use:
- `extern` declarations instead of `#include`
- `__start()` entry point instead of `main()` (no crt1.o)
- Link: `/usr/lib32/cmplrs/ld32 -n32 -o prog prog.o -lc`

Kernel drivers compile with kernel headers from `/var/sysgen`:
```
cc -c -n32 -mips3 -O2 -G 8 -non_shared -TENV:kernel -DIP54 -D_KERNEL -D_PAGESZ=16384 -I/var/sysgen
```

## Files Modified
- `qemu/hw/misc/sgi_pvnet.c` - TX BH, reentrancy_guard, can_receive
- `qemu/include/hw/misc/sgi_pvnet.h` - tx_bh, tx_pending, reentrancy_guard
- `qemu/hw/display/sgi_glaccel.c` - removed IRQ from EXEC_PROCESS
- `prom-building/src/fw/ip54_stubs.c` - io_init[] appends all 4 PV edtinit
- `ip54_tftp_staging/pvfb.c` - hwgraph reg, static BSS fb, includes
- `ip54_tftp_staging/pvaudio.c` - hwgraph reg, static BSS ring buffer, includes
- `ip54_tftp_staging/fbtest.c` - userland framebuffer test
- `ip54_tftp_staging/audiotest.c` - userland audio test
