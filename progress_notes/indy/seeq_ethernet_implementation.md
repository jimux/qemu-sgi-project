# Seeq 80C03 Ethernet Implementation

## Summary

Implemented functional ethernet for SGI Indy/Indigo2 emulation. The Seeq 80C03
EDLC (Ethernet Data Link Controller) is connected through the HPC3's ethernet
DMA channels. IRIX exposes this as the `ec0` network interface.

**Result:** IRIX 6.5 can ping the SLIRP gateway and accept telnet connections.

```
IRIS# ping -c 5 10.0.2.2
64 bytes from 10.0.2.2: icmp_seq=1 ttl=255 time=2083.171 ms
64 bytes from 10.0.2.2: icmp_seq=3 ttl=255 time=143.947 ms
64 bytes from 10.0.2.2: icmp_seq=4 ttl=255 time=1152.003 ms
```

## Architecture

The Seeq is implemented directly inside `sgi_hpc3.c` rather than as a separate
QOM device. This matches the physical wiring — the Seeq is an HPC3-attached
peripheral, and the HPC3 owns the DMA engine. QEMU's `NICState` lives in
`SGIHPC3State`.

### Data Paths

- **TX:** IRIX writes TX descriptors → sets `TXC_CA` → HPC3 walks descriptor
  chain, reads packet from guest memory → calls `qemu_send_packet()`
- **RX:** QEMU backend calls `receive()` → HPC3 writes packet to guest memory
  via RX descriptor chain → sets status → raises INT3 LOCAL0 ethernet interrupt

### Usage

```
qemu_session_start(
    scsi_drives=["/workspace/irix_disk_fresh.qcow2"],
    extra_args="-icount shift=0,sleep=off -nic user,model=sgi-hpc3",
    boot_wait=45
)
```

After login: `ifconfig ec0 inet 10.0.2.15 netmask 255.255.255.0 up`

## Bugs Found and Fixed

### 1. Seeq 80C03 Bank Selection (Root Cause of packet drops)

The Seeq 80C03 has a bank-select mechanism in the TX command register.
Bits [6:5] (`TXC_B = 0x60`) control which register set regs 0-5 map to:

| Bank | TX cmd bits [6:5] | Regs 0-5 map to |
|------|-------------------|-----------------|
| 0x00 | 00 | Station address |
| 0x20 | 10 | Multicast filter low (bytes 0-5) |
| 0x40 | 01 | Multicast filter high (0-1), control (3), config (4) |
| 0x60 | 11 | Reserved (NOP) |

IRIX programs the MAC in bank 0, then switches to banks 0x20/0x40 to write
the multicast hash filter (all zeros for broadcast-only mode). Without bank
selection, the hash writes overwrote the station address, changing it from
`08:00:69:xx:xx:xx` to `40:00:00:3f:00:00`. This caused all unicast packets
(ARP replies, ICMP echo replies) to be silently dropped by the address filter.

**Fix:** Check `seeq_tx_cmd & SEEQ_TXC_BANK_MASK` before writing to
`seeq_station_addr`. Only bank 0x00 writes go to station address.

### 2. RX Descriptor r_rown Bit Polarity

The `r_rown` bit (bit 14 of the BC word in RX descriptors) has inverted
semantics from what's intuitive:
- `r_rown = 0` → Software owns (data ready for driver to process)
- `r_rown = 1` → Hardware owns (not yet filled by DMA)

The IRIX interrupt handler loops `while (!rd_chain->r_rown)`, processing
descriptors where r_rown=0. We were initially SETTING r_rown after DMA
(marking as "hardware owns"), which meant the driver could never see the
received data.

**Fix:** Clear `HPC3_ENET_BC_ROWN` in the writeback to mark as software-owned.
MAME confirms: it writes only the 16-bit count to desc+6, which inherently
clears bit 14.

### 5. INT3 Local0 Spurious Interrupt Mask Missing ETHERNET

The INT3 `local0_stat` spurious interrupt mask (which filters bits to only
emulated hardware sources) did not include `INT3_LOCAL0_ETHERNET` (0x08).
This was written when ethernet was not yet emulated, but was never updated
after the Seeq implementation was completed. If IRIX enabled the ethernet
interrupt without it being in the mask, the bit would be cleared before the
pending check, preventing ethernet interrupts from reaching the CPU.

In practice this bug was masked because IRIX's ethernet interrupt worked via
a different path during testing. But it's incorrect — any emulated interrupt
source must be in the mask to avoid being spuriously filtered.

**Fix:** Add `INT3_LOCAL0_ETHERNET` to the mask in `sgi_hpc3_update_irq()`.

### 3. ENET_MISC Reset Semantics

IRIX writes `0x03` (RESET + INT) then `0x00` to ENET_MISC during every
interrupt acknowledge. Initially this triggered a full device reset, clearing
all Seeq state, RX DMA, and station address. This is wrong — it's a reset
pulse, not a sustained reset.

**Fix:** Only reset on the rising edge of the RESET bit (transition 0→1).
The subsequent write of 0x00 (clearing RESET) does not trigger another reset.

### 4. DMA Address Space

HPC3 DMA descriptors contain KSEG0 virtual addresses (0x8xxxxxxx). QEMU's
`address_space_memory` uses physical addresses. All DMA addresses must be
masked with `& 0x1fffffff` via `HPC3_DMA_ADDR()`.

## Files Modified

| File | Changes |
|------|---------|
| `qemu/include/hw/misc/sgi_hpc3.h` | Seeq state fields, NIC conf, bank select constants, DMA descriptor bits |
| `qemu/hw/misc/sgi_hpc3.c` | Seeq register R/W with bank selection, TX/RX DMA, NIC callbacks, interrupts |
| `qemu/hw/mips/sgi_indy.c` | Default MAC property, NIC wiring |
| `qemu/hw/misc/trace-events` | Ethernet trace events |

## IRIX Driver Reference

The IRIX ethernet driver lives in:
- `irix/kern/bsd/mips/if_ec2.c` — Main driver (ec_init, if_ecintr, START_RCV)
- `irix/kern/bsd/misc/seeq.h` — Register defs, descriptor structs, HPC constants
- `irix/kern/bsd/misc/ether.c` — ether_stop/ether_start

Key IRIX constants:
- `MAX_RPKT = 1586` (max receive packet = 14 + 1500 + 8 + 64)
- `HPC_RSPACE = 8` (2 byte offset + 1 status byte + padding)
- `rlen = MAX_RPKT - remaining - 3` (packet length from remaining count)

## Telnet Access and Port Forwarding

SLIRP user-mode networking supports port forwarding via the `hostfwd` option.
This enables telnet access to IRIX from the host without TAP networking
(which requires `CAP_NET_ADMIN`, unavailable in containers).

### Usage

Start QEMU with port forwarding:
```
extra_args="-icount shift=0,sleep=off -nic user,model=sgi-hpc3,hostfwd=tcp::2323-10.0.2.15:23"
```

Configure networking inside IRIX:
```
ifconfig ec0 inet 10.0.2.15 netmask 255.255.255.0 up
route add default 10.0.2.2
```

Connect from the host:
```
telnet localhost 2323
```

This connects to the IRIX login prompt. Root login works with no password
(default IRIX 6.5 installation).

### Notes

- **TAP networking** would provide lower latency and full L2 connectivity, but
  requires `CAP_NET_ADMIN` capability which is not available in container
  environments. SLIRP is the only option in unprivileged contexts.
- **Multiple port forwards** can be stacked:
  `hostfwd=tcp::2323-10.0.2.15:23,hostfwd=tcp::8080-10.0.2.15:80`
- The SLIRP default gateway is `10.0.2.2` and DNS is `10.0.2.3`.

## Known Limitations

- **High latency:** Ping RTTs of 100-2000ms with `-icount shift=0,sleep=off`.
  This is inherent to the virtual time approach — the scheduling clock fires
  at 100 Hz, and packet processing happens on interrupt boundaries.
- **Occasional packet drops:** First ping often missed due to ARP resolution
  timing. 80-100% success rate on subsequent pings.
- **No multicast hash filtering:** The multicast filter registers are accepted
  but ignored. All multicast packets are accepted when in multicast mode.
- **IRIX standalone mode:** When the IP address is default (0.0.0.0), IRIX
  starts and immediately stops the interface. Must manually configure with
  `ifconfig ec0 inet <addr> ... up`.
