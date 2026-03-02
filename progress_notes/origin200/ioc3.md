# IOC3 I/O Controller

The IOC3 ("I/O Controller 3") is a PCI device on the IO6 card behind the
Bridge ASIC. It provides the console UART, Ethernet, keyboard/mouse, RTC, and
GPIO. Its UART A is the system console for Origin 200.

## PCI Identity

| Field | Value |
|-------|-------|
| Vendor ID | `0x10A9` (Silicon Graphics) |
| Device ID | `0x0003` |
| Class | Multifunction |
| BAR0 | Memory-mapped, 1 MB aligned |

PCI config space is probed by the PROM via the Bridge's PCI config window.
The PROM reads vendor/device ID to confirm IOC3 is present before calling
`ioc3uart_init()`.

## IOC3 MMIO Register Map

All IOC3 registers are 32-bit, accessed at 4-byte aligned offsets from the
IOC3 BAR0 base (memory-mapped base). From `irix/kern/sys/PCI/ioc3.h`:

### Control Registers

| Offset | Name | Description |
|--------|------|-------------|
| 0x01C | IOC3_SIO_IR | SuperIO Interrupt Register |
| 0x020 | IOC3_SIO_IES | SuperIO Interrupt Enable Set |
| 0x024 | IOC3_SIO_IEC | SuperIO Interrupt Enable Clear |
| 0x028 | IOC3_SIO_CR | SuperIO Control Register |
| 0x02C | IOC3_INT_OUT | INT_OUT (real-time interrupt) |
| 0x030 | IOC3_MCR | MicroLAN Control Register |
| 0x034 | IOC3_GPCR_S | GPIO Control Set |
| 0x038 | IOC3_GPCR_C | GPIO Control Clear |
| 0x03C | IOC3_GPDR | GPIO Data Register |
| 0x040 | IOC3_GPPR_0 | GPIO Pin Register 0 |

### Serial Port Registers (UART A and B — DMA mode)

The IOC3 has two serial ports that can operate in either "DMA" (ring buffer)
mode or "compatibility 16550" mode. The PROM uses the 16550-compatible mode
via the SIO_CR base address pointer.

| Offset | Name | Description |
|--------|------|-------------|
| 0x0B0 | IOC3_SBBR_H | Serial Ring Buffer Base High |
| 0x0B4 | IOC3_SBBR_L | Serial Ring Buffer Base Low |
| 0x0B8 | IOC3_SSCR_A | Serial Port A Control |
| 0x0BC | IOC3_STPIR_A | Serial A TX Produce |
| 0x0C0 | IOC3_STCIR_A | Serial A TX Consume |
| 0x0C4 | IOC3_SRPIR_A | Serial A RX Produce |
| 0x0C8 | IOC3_SRCIR_A | Serial A RX Consume |
| 0x0CC | IOC3_SRTR_A | Serial A Receive Timer |
| 0x0D0 | IOC3_SHADOW_A | Serial A 16550 Shadow (read-only status) |
| 0x0D4 | IOC3_SSCR_B | Serial Port B Control |
| ... | (similar B regs) | |

### 16550-Compatible UART Base Addresses

**This is the UART path the PROM uses** (`ioc3uart.c`). The SIO_CR register
configures where in the IOC3 address space the 16550 registers appear:

```c
/* From ioc3.h, in "prom" (standalone) mode: */
#define UARTA_BASE  0x178   /* UART A base offset from IOC3 BAR0 */
#define UARTB_BASE  0x170   /* UART B base offset from IOC3 BAR0 */
```

So UART A is at `IOC3_BAR0 + 0x178`. UART B is at `IOC3_BAR0 + 0x170`.

The 16550 register set within each UART (from `sys/ns16550.h`, registers are
4-byte aligned in IOC3):

| 16550 Reg | Offset from UART base | Description |
|-----------|----------------------|-------------|
| RBR/THR | +0x00 | Receive buffer / Transmit hold |
| IER | +0x04 | Interrupt enable |
| IIR/FCR | +0x08 | Interrupt ID / FIFO control |
| LCR | +0x0C | Line control (DLAB) |
| MCR | +0x10 | Modem control |
| LSR | +0x14 | Line status |
| MSR | +0x18 | Modem status |
| SCR | +0x1C | Scratch pad (IOC3: predivisor when DLAB set) |

For QEMU, UART A at `IOC3_BAR0 + 0x178` should be implemented as a standard
16550 UART (QEMU has `TYPE_SERIAL_MM` / `serial_mm_init()` which provides this).

### Keyboard and Mouse

| Offset | Name | Description |
|--------|------|-------------|
| 0x09C | IOC3_KM_CSR | Keyboard/Mouse Control/Status |
| 0x0A0 | IOC3_K_RD | Keyboard Read Data |
| 0x0A4 | IOC3_M_RD | Mouse Read Data |
| 0x0A8 | IOC3_K_WD | Keyboard Write Data |
| 0x0AC | IOC3_M_WD | Mouse Write Data |

These are not needed for Milestone 1 (text console boot). Defer to later milestone.

### Ethernet (ef driver)

| Offset | Name | Description |
|--------|------|-------------|
| 0x0F0 | IOC3_EMCR | Ethernet MAC Control |
| 0x0F4 | IOC3_EISR | Ethernet Interrupt Status |
| 0x0F8 | IOC3_EIER | Ethernet Interrupt Enable |
| 0x0FC | IOC3_ERCSR | RX Control/Status |
| 0x100 | IOC3_ERBR_H | RX Base High |
| ... | (full Ethernet DMA engine) | |

Defer Ethernet to a later milestone. For boot: return 0 on reads.

### Parallel Port (ECPP)

Offsets 0x080–0x098. Defer to never (not present on Origin 200).

### RTC / NIC

- **IOC3_MCR (0x030)**: MicroLAN control for the NIC (Microlan 1-wire)
- IOC3 may have an integrated RTC; check Bridge/IO6 spec for exact RTC location
- Defer to later milestone

## PROM UART Initialization Sequence

From `stand/arcs/IP27prom/ioc3uart.c` (`ioc3uart_init()`):

```
1. Locate IOC3 PCI device via Xbow→Bridge→PCI probe
2. Get console_t struct with uart_base, memory_base, config_base
3. ioc3_chip_init():
   - PCI_OUTW to pci_scr: enable BUS_MASTER | MEM_SPACE
   - PCI_OUTW to pci_lat: 0xff00 (latency timer)
4. Write IOC3_SIO_CR:
   - Encode UARTA_BASE (0x178>>3 = 0x2f) in bits [17:11]
   - Encode UARTB_BASE (0x170>>3 = 0x2e) in bits [10:4]
   - SIO_CR_CMD_PULSE_SHIFT: set 4 in [3:2]
5. Write IOC3_GPDR = 0 (all GPIO as inputs)
6. Write IOC3_GPCR_S:
   - GPCR_INT_OUT_EN | GPCR_MLAN_EN
   - GPCR_DIR_SERA_XCVR | GPCR_DIR_SERB_XCVR
7. configure_port(cntrl = UART_A, baud = 9600):
   - Wait for LSR_XMIT_EMPTY
   - WR LCR = LCR_DLAB (0x80)
   - WR DLM = (divisor >> 8) & 0xff
   - WR DLL = divisor & 0xff
   - WR SCR = SER_PREDIVISOR * 2   (predivisor while DLAB set)
   - WR LCR = LCR_8_BITS_CHAR | LCR_1_STOP_BITS (0x03)
   - WR IER = 0x00 (no interrupts in PROM)
   - WR FCR = FCR_ENABLE_FIFO (enable FIFO)
   - WR FCR = FCR_ENABLE_FIFO | FCR_RCV_FIFO_RESET | FCR_XMIT_FIFO_RESET
   - WR MCR = MCR_DTR | MCR_RTS
```

**Clock divisor:**
```
SER_XIN_CLK = 22118400 Hz (22.1184 MHz — standard serial clock)
SER_PREDIVISOR = 3 (applied when DLAB set)
PROM_SER_CLK_SPEED = SER_XIN_CLK / SER_PREDIVISOR = 7372800 Hz
PROM_SER_DIVISOR(9600) = 7372800 / (9600 * 16) = 48 = 0x30
```

For QEMU `serial_mm_init()`, specify 22.118 MHz / 3 = 7.3728 MHz as the base
clock, or use a compatible baud divisor mapping.

## QEMU Implementation Priorities

### Milestone 1 (PROM text output)
- IOC3 PCI device with vendor=0x10A9, device=0x0003
- BAR0 responds to IOC3 register space
- **UART A at BAR0 + 0x178**: 16550-compatible, 8 bytes per register (stride=4)
- `IOC3_SIO_CR`, `IOC3_GPDR`, `IOC3_GPCR_S` must accept writes without error
- All other registers: return 0 / accept writes silently

### Later Milestones
- UART A/B RX interrupt (enable IRIX serial console input)
- Ethernet (MAC DMA engine, for SLIRP networking)
- Keyboard/Mouse (PS/2 protocol via IOC3_KM_CSR)
- RTC (time-of-day)

## Sources

- `irix/kern/sys/PCI/ioc3.h` — Complete register map, vendor/device ID
- `stand/arcs/IP27prom/ioc3uart.c` — UART init sequence
- `irix/kern/sys/ns16550.h` — 16550 register constants
- `gathered_documentation/techpubs/007-3439-002 *.pdf` — Theory of Operations
