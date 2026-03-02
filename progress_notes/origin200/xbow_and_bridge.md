# Xbow and Bridge: XIO Bus Components

## Overview

The XIO (Crosstalk) bus is a packet-switched I/O fabric. In Origin 200 (SN00):
- **Xbow** is the switch/crossbar ASIC, sitting at widget 0
- **Bridge** is the PCI host bridge ASIC, at widget 8
- The Hub IIO section connects to Xbow via an LLP (Low Latency Protocol) link

```
Hub IIO (widget 1) ←─ LLP ─→ Xbow (widget 0) ←─ LLP ─→ Bridge (widget 8)
                                                                │
                                                            PCI bus
                                                           IOC3, QL
```

All XIO widgets share a common widget header structure (first 0x58 bytes).
The Hub IIO reads widget ID registers to identify connected devices.

---

## Xbow ASIC

### Widget Identification

| Field | Value |
|-------|-------|
| Part number | `XBOW_WIDGET_PART_NUM = 0x0000` |
| Manufacturer | SGI (0x036) |
| Widget ID register value | `(rev << 28) | (0x0000 << 12) | (0x036 << 1) | 1` |

For Xbow rev 1 (typical): `WID = (1 << 28) | (0 << 12) | (0x036 << 1) | 1 = 0x1000006d`

### Physical Address

Xbow is widget 0. Widget 0 is special — the SN0 address map macros handle it
via big window rather than small window:
```c
/* From addrs.h: */
#define NODE_SWIN_BASE(nasid, widget) \
    ((widget == 0) ? NODE_BWIN_BASE((nasid), SWIN0_BIGWIN) \
                   : RAW_NODE_SWIN_BASE(nasid, widget))
```

For QEMU with NASID 0: the Xbow is accessed through a big window. The Hub IIO
section internally dispatches accesses intended for widget 0 to Xbow. The
physical address derivation depends on QEMU's Hub IIO dispatch implementation.
**Conservative approach**: implement Xbow at a dedicated QEMU memory region
registered by the Hub MMIO handler — not as a standalone memory map entry.

### Xbow Register Layout

From `irix/kern/sys/xtalk/xbow.h` (`struct xbow_s`):

```
0x000000  widget_cfg (standard widget header, 0x58 bytes):
  +0x00  padding
  +0x04  w_id         (widget ID register)
  +0x08  w_status
  +0x0c  w_err_upper_addr
  +0x10  w_err_lower_addr
  +0x14  w_control
  +0x18  w_req_timeout
  +0x1c  w_intdest_upper_addr
  +0x20  w_intdest_lower_addr
  +0x24  w_err_cmd_word
  +0x28  w_llp_cfg
  +0x2c  w_tflush (stat_clr)
0x000058  xb_wid_arb_reload    (0x5c)
0x000064  xb_perf_ctr_a
0x00006c  xb_perf_ctr_b
0x000074  xb_nic               (NIC/Microlan)

0x000100  xb_link[0]   (port 8 link registers)
0x000140  xb_link[1]   (port 9)
...
0x000300  xb_link[7]   (port F = 15)
```

Each `xb_link[n]` (`xb_linkregs_t`) at offset 0x100 + n*0x40:
```
+0x04  link_ibf          (input buffer free)
+0x0c  link_control
+0x14  link_status       ← PROM reads this per port
+0x1c  link_arb_upper
+0x24  link_arb_lower
+0x2c  link_status_clr
+0x34  link_reset
+0x3c  link_aux_status
```

### PROM Probe Sequence

During `xtalk_init()` / `discover.c` topology discovery:

1. Read `xb_link[port].link_status` for each port (8–15)
2. A non-zero `link_status` with the "alive" bit indicates a connected widget
3. Read the connected widget's `w_id` register (at the widget's SWIN base +4)
4. Match `w_id` part number against known ASICs

For QEMU, the critical ports are:
- **Port 8** (`xb_link[0]`): Bridge connected
- **Port 1** (Hub connection): Not a numbered external port; the Hub's internal
  connection to Xbow is via the Hub IIO LLP, not an Xbow port register

**Minimum Xbow implementation for boot:**
- Widget ID register (`w_id` at +0x04) → Xbow part num 0x0000 with rev
- Per-port `link_status` for port 8 → set link-alive bit
- All other registers: R/W stubs returning 0

### Link Status Register

**Confirmed from `irix/kern/sys/xtalk/xbow.h`:**

```c
typedef union xbow_linkX_status_u {
    xbowreg_t   linkstatus;
    struct {
        __uint32_t  alive:1,     /* bit 31 — link-alive bit (MSB in big-endian) */
                    resvd1:12,
                    merror:1,    /* multiple error */
                    illdest:1,   /* illegal destination */
                    ioe:1,       /* input overallocation error */
                    bw_errport:8,
                    llp_rxovflow:1, llp_txovflow:1, llp_maxtxretry:1,
                    llp_rcverror:1, llp_xmitretry:1,
                    pkt_toutdest:1, pkt_toutconn:1, pkt_toutsrc:1;
    } xb_linkstatus;
} xbwX_stat_t;

#define link_alive  xb_linkstatus.alive
```

**Alive bit = bit 31 (MSB).** On big-endian MIPS, the first declared bitfield
occupies the most significant bit. A connected widget shows `link_status`
with bit 31 SET. QEMU must return `0x80000000` (or any value with bit 31 set)
for the Bridge port's link_status.

For a minimal boot: the PROM's topology discovery uses Xbow link status to
find the Bridge — this cannot be stubbed away if the natural PROM path is used.

---

## Bridge ASIC

### Widget Identification

| Field | Value |
|-------|-------|
| Part number | `BRIDGE_WIDGET_PART_NUM = 0xc002` |
| Manufacturer | `BRIDGE_WIDGET_MFGR_NUM = 0x036` |
| Widget ID at SWIN base + 0x04 | `(rev << 28) | (0xc002 << 12) | (0x036 << 1) | 1` |

For Bridge rev 4 (spec 4.0): `WID = (4 << 28) | (0xc002 << 12) | (0x036 << 1) | 1`
= `0x4c002_06d`

### Physical Address (NASID 0, widget 8)

`NODE_SWIN_BASE(0, 8) = NODE_IO_BASE(0) + (8 << SWIN_SIZE_BITS)`
`SWIN_SIZE_BITS = 24`, so widget 8 base = `8 * 0x1000000 = 0x08000000`.

**Physical base: 0x08000000** (16 MB small window for widget 8)

### Bridge Register Layout

From `irix/kern/sys/PCI/bridge.h` and the Bridge ASIC spec:

```
0x08000000  Bridge local registers (widget header + Bridge-specific):
  +0x000004  Widget ID (w_id = BRIDGE part num encoded)
  +0x000008  Widget status
  +0x00000c  Widget err upper addr
  +0x000010  Widget err lower addr
  +0x000014  Widget control
  +0x000028  Widget LLP config
  +0x00002c  Widget target flush

  +0x000040  Bridge control register (br_control)
  +0x000048  Bridge status
  +0x000058  Bridge interrupt status
  +0x000060  Bridge interrupt enable
  +0x000068  Bridge interrupt reset
  +0x000070  Bridge int mode
  +0x000078  Bridge int device
  +0x000080  Bridge int host err
  +0x000090  Bridge xt err upper / xt err lower
  +0x0000a0  Bridge xt err command word

0x08020000  PCI config space (Bridge maps PCI config here)
0x08040000+  PCI device IO bars (16 PCI device slots × 1 MB)
0x08100000+  PCI memory BARs (IOC3 MMIO base here)
0x083C0000  Bridge INT8 (interrupt acknowledge)
0x08400000  Bridge FLASH PROM storage (IO6prom)
```

**Confirmed from `irix/kern/sys/PCI/bridge.h`:**
```c
#define BRIDGE_CONFIG_BASE   0x20000   /* PCI Type 0 config space base */
#define BRIDGE_CONFIG1_BASE  0x28000   /* PCI Type 1 config space base */
#define BRIDGE_CONFIG_END    0x30000
#define BRIDGE_CONFIG_SLOT_SIZE 0x1000 /* 4 KB per device slot */
```
PCI config space for device N at slot 0: Bridge SWIN + 0x20000 + N×0x1000.
IOC3 at device 0 → **physical 0x08020000** (Bridge SWIN 0x08000000 + 0x20000). ✓

### IO6prom Flash Storage

The IO6 PROM image (`io6prom.img`, 365 KB) is stored in the Bridge's flash
EEPROM. The IP27prom reads it from this flash during boot:

```
0x08400000  Bridge flash start (within Bridge window at 0x08000000+0x400000)
```

In QEMU, this should be implemented as a ROM region populated from `io6prom.img`.
The IP27prom reads from this region and decompresses the IO6prom to RAM at
`IO6PROM_BASE = 0x01C00000` using the segment loader (`segldr_load()`).

**QEMU strategy for IO6prom loading:**
- Option A: Pre-load `io6prom.img` at physical 0x01C00000 before PROM runs
  (bypasses IP27prom's flash read/decompress path)
- Option B: Implement Bridge flash as a ROM region at 0x08400000 with
  `io6prom.img` contents; let IP27prom discover and decompress it
- Option A is much simpler and sufficient for Milestone 1

### Interrupt Routing: Bridge → Hub

The Bridge generates XIO interrupts that travel to Hub IIO and arrive at
Hub PI as pending interrupt bits. The routing path:

```
PCI device interrupt → Bridge INT → XIO interrupt → Hub IIO IIDSR → PI_INT_PEND0
```

The `IIO_IIDSR` register (Hub IIO, offset 0x400138) configures interrupt
destination:
```
bits [28]   = IIDSR_SENT     (interrupt sent acknowledgment)
bits [24]   = IIDSR_ENB      (interrupt destination enable)
bits [15:8] = IIDSR_NODE     (target NASID)
bits [5:0]  = IIDSR_LVL      (interrupt level in PI_INT_PEND0/1)
```

For QEMU, this is needed only when IOC3 UART RX interrupts are required
for console input. For Milestone 1 (output only), interrupt routing can be
stubbed — the IOC3 UART is polled by the PROM.

### PCI Host Bridge Behavior

Bridge presents a standard PCI config space starting at 0x08020000.
IOC3 is at PCI slot 0 (device 0). QEMU should:
1. Register a PCI bus behind Bridge at base 0x08020000
2. Attach IOC3 as PCI device 0 on that bus
3. IOC3's BAR0 (memory) maps at a 4 KB address within the Bridge PCI memory window

## Sources

- `irix/kern/sys/xtalk/xbow.h` — Xbow struct layout
- `irix/kern/sys/PCI/bridge.h` — Bridge registers, BRIDGE_WIDGET_PART_NUM
- `gathered_documentation/octane origin/sgi crossbow spec.pdf` — Xbow link status bits
- `gathered_documentation/octane origin/SGI-Bridge_ASIC_Specification-*.pdf` — Bridge detail
- `stand/arcs/IP27prom/discover.c` — XIO topology discovery code
- `stand/arcs/IP27prom/segldr.c` — Segment loader (IO6prom decompress)
