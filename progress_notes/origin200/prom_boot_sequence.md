# IP27prom Boot Sequence

## Overview

The IP27prom is a two-stage architecture:
1. **IP27prom**: Runs from reset vector (0xBFC00000 = physical 0x1FC00000)
2. **IO6prom**: Loaded from Bridge flash into RAM at 0x01C00000

The IP27prom runs without any console output until IOC3 UART is initialized
(or Hub I2C UART for very early diagnostics on real hardware).

---

## Stage 1: CPU Reset and Early Assembly (`start.s`)

At power-on, R10000 starts executing at 0xBFC00000.

**Binary-confirmed**: The PROM has an exception/branch vector table at 0xBFC00000.
Table entry [0] is `J 0xBFC00800` — the actual boot code starts at **0xBFC00800**.

Binary trace of first ~10 instructions at 0xBFC00800:
```
0xBFC00800: addiu $k0, $zero, 0       ; clear $k0
0xBFC00804: addiu $k1, $zero, 0       ; clear $k1
0xBFC00808: mfc0  $k0, Status         ; read CP0 Status
0xBFC0080c: lui   $k1, 0x2440
0xBFC00810: ori   $k1, $k1, 0x80
0xBFC00814: mtc0  $k1, Status         ; Status = 0x24400080 (SR_KX|SR_BEV)
0xBFC00818: mtc0  $k0, EPC            ; save original Status in EPC
  ; Then construct Hub PI base (IO_BASE + 0x01000000 via 4-instruction dsll sequence)
0xBFC0081c: lui   $k0, 0x9200
0xBFC00820: dsll  $k0, $k0, 16        ; $k0 = 0xFFFF920000000000
0xBFC00824: ori   $k0, $k0, 0x100
0xBFC00828: dsll  $k0, $k0, 16        ; $k0 = 0x9200000001000000 (Hub PI SWIN)
0xBFC0082c: ld    $k1, 0x20($k0)      ; READ PI_CPU_NUM (Hub PI + 0x20)
0xBFC00830: dsll  $k1, $k1, 3         ; CPU_NUM × 8 (byte offset into compare regs)
  ; Then construct PI_RT_COMPARE_A address, add CPU offset, write PLED_LOCALARB=0x5e
```

```
Step 1: CPU reset
  - Exception vector table at 0xBFC00000; boot entry at 0xBFC00800
  - CPU in 32-bit COMPAT mode initially
  - Set CP0 Status = 0x24400080: SR_KX (bit 7) = 1 for 64-bit mode, SR_BEV set
  - Load ip27config from LBOOT_BASE + 0x60 (= flash byte 0x60):
      * Binary confirmed at PROM offset 0x60: mach_type=1 (SN00)
      * mach_type == 1 → SN00 (Origin 200)
  - Program R10000 config from ip27c_r10k_mode

Step 2: TLB flush
  - Invalidate all TLB entries
  - Set up WIRED entries for PROM regions

Step 3: Cache init
  - Init instruction and data caches (hand-coded MIPS assembly)
  - Cache must be valid before running C code

Step 4: Stack setup
  - CPU A stack: IP27PROM_STACK_A = physical 0x01BE0000
  - CPU B stack: IP27PROM_STACK_B = physical 0x01BF0000
  - PI_CPU_NUM determines which stack to use

Step 5: Branch to main()
  - Jump into C code (main.c)
```

**QEMU implications**: The R10000 CPU must handle all standard MIPS64 reset
sequences. QEMU's MIPS target handles TLB/cache init transparently.
The PROM writes to `MD_UREG0_0` early — must not bus-fault.

---

## Stage 2: Early Hub Init (`main.c`: beginning)

```
Step 6: Hub PI basic init
  - Read PI_CPU_NUM → must return 0 (CPU A) or 1 (CPU B)
  - Read PI_CPU_PRESENT_A/B → determine SMP configuration
  - Set up PI_RT_COMPARE_A = PLED_LOCALARB (progress code)
  - Arbitrate for local master:
      * Wait for peer CPU or timeout
      * Disable non-responding peer via PI_CPU_ENABLE_B = 0

Step 7: Hub MD init (mdir_init)
  - Write MD_MEM_DIMM_INIT for each bank (16 times: 8 banks × 2 SIMMs)
  - Write MD_DIR_DIMM_INIT for each bank
  - Write MD_REFRESH_CONTROL = MRC_ENABLE | refresh_threshold
  - Write MD_MOQ_SIZE = MMS_RESET_DEFAULTS
  → QEMU: accept all these writes silently; no real DRAM timing needed

Step 8: Hub LED init
  - hub_led_set(PLED_MDIRINIT) → writes to MD_LED0 (SN0) or MD_UREG1_0 (SN00)
  → QEMU: write-only, ignore

Step 9: Memory sizing (mdir_config)
  - Write MD_MEMORY_CONFIG = MMC_BANK_ALL_MASK | MMC_DIR_PREMIUM (probe config)
  - For each bank (0..7): probe back-door space to detect SIMM size
  - Write final MD_MEMORY_CONFIG with detected sizes
  → QEMU: MD_MEMORY_CONFIG is pre-set by machine init; PROM will read it back
    after this step. The probe writes are ignored; reads return the QEMU value.
    IMPORTANT: PROM writes to MD_MEMORY_CONFIG twice — once for probing, once
    for final config. The final value must match what QEMU pre-computed.

Step 10: Hub IIO init
  - Read IIO_ILCSR → must have IIO_LLP_CSR_IS_UP (bit 13) set
  - If link not up → PROM may hang or enter POD mode
  → QEMU: IIO_ILCSR reads must return 0x00002000 (link up)

Step 11: NI status check (single-node detection)
  - Read NI_STATUS_REV_ID → check NSRI_LINKUP bit (29)
  - If link down → single node (SN00 path)
  - Write NI_SCRATCH_REG1 |= ADVERT_SN00_MASK (bit 50) if mach_type==1
  - Write NI_SCRATCH_REG0 with Hub NIC and partition info
  → QEMU: NI_STATUS_REV_ID must have bit 29 CLEAR (link down for SN00)
```

**Critical boot gate**: If `IIO_ILCSR` does not show link-up, the PROM aborts
XIO initialization. If `NI_STATUS_REV_ID` shows link-up (wrong for SN00), the
PROM may try to discover remote nodes and hang.

---

## Stage 3: XIO Discovery and IOC3 UART Init

```
Step 12: XIO topology discovery (xtalk_init / discover.c)
  - Read Xbow widget ID at widget 0 base → verify XBOW_WIDGET_PART_NUM = 0x0
  - For each Xbow port (8-15):
      * Read xb_link[port].link_status
      * If alive bit set: read widget ID at SWIN base +4
      * BRIDGE_WIDGET_PART_NUM = 0xc002 → found Bridge
  → QEMU: Xbow must respond with correct widget ID; Bridge port (port 8)
    must show link-alive; Bridge SWIN must respond with Bridge widget ID

Step 13: Bridge initialization
  - Write Bridge control register (clear errors)
  - Set Bridge PCI config access parameters
  - Probe PCI bus for IOC3 (PCI config read at device 0)
  → QEMU: Bridge PCI config space must return IOC3 vendor=0x10A9, dev=0x0003

Step 14: IOC3 chip init and UART setup (ioc3uart_init)
  - PCI write: enable BUS_MASTER | MEM_SPACE in pci_scr
  - Write IOC3_SIO_CR to configure UART base addresses
  - Write IOC3_GPDR = 0
  - Write IOC3_GPCR_S = GPCR_INT_OUT_EN | GPCR_MLAN_EN | ...
  - configure_port(UART A, 9600 baud):
      * Write LCR = 0x80 (DLAB)
      * Write DLM = 0x00, DLL = 0x30 (divisor = 0x30 = 48 for 9600 baud)
      * Write SCR = SER_PREDIVISOR*2 = 6
      * Write LCR = 0x03 (8N1, DLAB=0)
      * Write IER = 0x00 (no interrupts)
      * Write FCR = 0x01 (enable FIFO)
      * Write FCR = 0x07 (enable + reset FIFOs)
      * Write MCR = 0x03 (DTR + RTS)
  → AFTER THIS STEP: UART A is ready for output

Step 15: Console output begins
  - "Starting PROM Boot process" (or similar) printed to IOC3 UART A
  - All subsequent PROM output goes to UART A at 9600 baud
```

**QEMU milestone**: After step 14, if IOC3 UART A is implemented, PROM output
appears on the serial console.

---

## Stage 4: IO6prom Load and Jump

```
Step 16: Load IO6prom from Bridge flash
  - Access Bridge flash region (physical 0x08400000)
  - Read promhdr_t magic number to verify IO6prom image
  - Decompress (gzip) IO6prom segments to IO6PROM_BASE = physical 0x01C00000
  - Jump to IO6prom entry point

Step 17: IO6prom begins execution
  - IO6prom runs at 0x01C00000
  - Re-initializes IOC3 UART (possibly changes baud rate)
  - Provides ARCS firmware interface
  - Prints version banner: "SGI Version X.XX ... Origin200 IP27"
  - Loads IRIX from SCSI or network via ARCS

Step 18: IRIX kernel loaded
  - IO6prom reads IRIX kernel from disk via QL SCSI + PCI
  - Passes ARCS memory descriptors to kernel
  - Jumps to kernel entry point
```

**QEMU simplification**: For Milestone 1, Option A is to pre-load io6prom.img
at physical 0x01C00000 before IP27prom starts, so Step 16 can be bypassed by
having a stub that detects the pre-loaded IO6prom and jumps to it directly.
Or implement Bridge flash at 0x08400000 with io6prom.img contents so the
natural PROM path works without modification.

---

## Register Access Sequence Summary

The following table lists every hardware register the PROM reads during POST,
in approximate order, with the QEMU return value needed to proceed:

| Order | Register | Physical | QEMU must return |
|-------|----------|----------|------------------|
| 1 | IP27prom at reset | 0x1FC00000 | (PROM binary; boot jumps to 0xBFC00800) |
| 1a | ip27config (LBOOT+0x60) | 0x10000060 **and** 0x1FC00060 | mach_type=1 (SN00) — binary confirmed |
| 2 | PI_CPU_NUM | 0x01000020 | 0 (CPU A) — **first hardware register read** (binary confirmed) |
| 2a | PI_RT_COMPARE_A | 0x01000108 | write 0x5e (PLED_LOCALARB) — binary confirmed |
| 3 | MD_UREG0_0 | 0x01220000 | 0x00 (I2C idle) |
| 4 | PI_CPU_PRESENT_A | 0x01000040 | 1 (present) |
| 5 | PI_CPU_PRESENT_B | 0x01000048 | 0 (1-CPU config) |
| 6 | PI_RT_COMPARE_A | 0x01000108 | read-back write (init 0) |
| 7 | MD_MEMORY_CONFIG | 0x01200018 | encoded from `-m` |
| 8 | IIO_ILCSR | 0x01400128 | 0x00002000 (link up) |
| 9 | NI_STATUS_REV_ID | 0x01600000 | 0x00010061 (link down, NASID=0) |
| 10 | Xbow w_id | (via Hub IIO) | 0x1000006d (Xbow part=0x0) |
| 11 | xb_link[0].link_status | (via Hub IIO) | non-zero (link alive) |
| 12 | Bridge w_id at 0x08000004 | 0x08000004 | 0x4c002_06d (Bridge part=0xc002) |
| 13 | IOC3 PCI vendor+dev | 0x08020000 | 0x000310A9 |
| 14 | IOC3 UART A LSR | 0x08100178+0x14 | 0x60 (TX empty) |
| 15+ | Normal UART I/O | 0x08100178 | (serial data) |

---

## Known Branch Points Where QEMU Must Respond Correctly

1. **PI_CPU_NUM = 0**: If non-zero, PROM thinks it's CPU B and waits for CPU A
   (dead loop unless CPU A is also running)

2. **IIO_ILCSR link-up**: If `IIO_LLP_CSR_IS_UP` is clear, PROM may enter POD
   mode or spin

3. **NI_STATUS_REV_ID link-down**: If link-up bit is SET, PROM tries to
   discover remote nodes via NI network interface → hangs (no network)

4. **MD_MEMORY_CONFIG non-zero**: If all banks show empty (0), PROM prints
   "No memory found" and enters POD mode

5. **Xbow widget ID correct**: If widget ID mismatch, PROM may not find Bridge
   and skip IOC3 init → no serial output

6. **IOC3 PCI vendor/device match**: PROM calls `ioc3uart_init()` only if the
   PCI probe returns `vendor=0x10A9, device=0x0003`

## Sources

- `stand/arcs/IP27prom/main.c` — Boot orchestration
- `stand/arcs/IP27prom/mdir.c` — mdir_init(), mdir_config()
- `stand/arcs/IP27prom/ioc3uart.c` — ioc3uart_init(), configure_port()
- `stand/arcs/IP27prom/discover.c` — XIO topology discovery
- `stand/arcs/IP27prom/segldr.c` — IO6prom segment loader
- MCP tool `build_function_database` / `find_hardware_probes` against `ip27prom.img`
  can cross-check this trace against the actual binary
