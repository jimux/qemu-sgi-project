# Multi-Platform Status

Status of all SGI machine types implemented in QEMU, PROM boot results,
and platform-specific hardware differences.

---

## Machine Types

| Machine | Name | CPU | Chipset | Board | Status |
|---------|------|-----|---------|-------|--------|
| `indy` (IP24) | SGI Indy | R4600 | MC/HPC3/IOC2 | Guinness | **IRIX 6.5 desktop** (4Dwm, xdm login, networking, input) |
| `indigo2` (IP22) | SGI Indigo2 | R4000 | MC/HPC3/IOC2 | Full House | IRIX 5.3 + 6.2 boot to login |
| `indigo2-r8k` (IP26) | SGI Indigo2 Power | R4000* | MC/HPC3/IOC2 | Full House | Stalls (EISA scan) |
| `indigo2-r10k` (IP28) | SGI Indigo2 Impact | R10000 | MC/HPC3/IOC2 | Full House | Boots to menu |
| `indigo` (IP20) | SGI Indigo | R4000 | MC/HPC3** | — | Stalls (needs HPC1/INT2) |

\* R8000 not available in QEMU; R4000 used as substitute.
\** IP20 uses HPC1/INT2, not HPC3/IOC2. HPC3 used as stub.

---

## PROM Test Matrix

| PROM | Platform | Machine | Result |
|------|----------|---------|--------|
| `Indy_ip24prom.070-9101-011.bin` | IP24 | `indy` | Boots to menu |
| `Indy_ip24prom.070-9101-007.bin` | IP24 | `indy` | Boots to menu |
| `Indigo_2_ip22prom.070-8127-002.bin` | IP22 | `indigo2` | Boots to menu |
| `Indigo_2_ip26prom.070-1361-003.bin` | IP26 | `indigo2-r8k` | Stalls: EISA I/O scan loop |
| `Indigo_2_ip26prom.070-1361-004.bin` | IP26 | `indigo2-r8k` | Stalls: EISA I/O scan loop |
| `Indigo_2_ip26prom.070-1371-007.bin` | IP26 | `indigo2-r8k` | Stalls: EISA I/O scan loop |
| `Indigo_2_ip28prom.070-1477-001.bin` | IP28 | `indigo2-r10k` | Boots to menu |
| `Indigo_2_ip28prom.070-1477-002.bin` | IP28 | `indigo2-r10k` | Boots to menu |
| `Indigo_ip20prom.070-8116-004.BE.bin` | IP20 | `indigo` | Stalls: needs HPC1/INT2 |

IP24 and IP22 PROMs complete POST, detect 64MB RAM, probe GIO slots, and
present the System Maintenance Menu. Both IP24 PROMs are validated. Indy
boots IRIX 6.5 to full 4Dwm desktop with graphics, networking, audio stub,
and keyboard/mouse input. Indigo2 boots IRIX 5.3 and 6.2 to login prompt.

IP26 PROMs enter an EISA bus scan loop (sequentially reading 0x00080000+)
before any CPU PRId check. The unimplemented device returns 0 instead of the
expected "no device" signature, so the loop never terminates.

IP28 PROMs now boot to menu after R10000 CPU emulation support was added.
They show 2 non-fatal MRU bit warnings from L2 cache diagnostics (MRU
tracking not simulated).

---

## IP22 vs IP28 Hardware Differences

### CPU

| Feature | IP22 (R4000) | IP28 (R10000) |
|---------|-------------|---------------|
| CP0_PRid | 0x00000400 | 0x00000900 |
| ISA | MIPS III | MIPS IV + partial R1 |
| L2 cache | None | 1 MB, 128-byte lines, 2-way |
| TLB entries | 48 | 64 |

The R10000 Config0 encodes L2 parameters: SC=0 (present), SS=1 (1MB),
SB=3 (128-byte blocks).

### Memory Controller (MC)

| Feature | IP22 | IP28 |
|---------|------|------|
| MC revision | 3 | 5 |
| SYSID register | 0x13 (rev 3 + EISA) | 0x15 (rev 5 + EISA) |
| Memory granularity | 4 MB (shift 22) | 16 MB (shift 24) |
| SEG0 base | 0x08000000 | 0x20000000 |
| Wrap aliases | No | Yes |

IP22 PROM probes memory by write-read-compare (mismatch = no RAM).
IP28 PROM probes by configuring progressively larger sizes and checking
for address wrap-around.

### Shared (Full House Board)

Both IP22 and IP28 share: EISA I/O space at 0x00080000, HPC3 board
type = `BOARD_IP22` (1), INT3 at PIO4 offset (0x59000), same Newport/GIO/
interrupt wiring, 64 MB default RAM.

### Platform-Specific Implementation Notes

**Full House INT3:** Added case labels at PIO4 base (0x59000) in addition
to Guinness INT3 at PIO6+0x80 (0x59880). Both map to the same INT3 state.

**EISA stub:** Unimplemented device at 0x00080000 (512KB) for Full House
machines. IP26 PROMs get stuck scanning this sequentially.

**MC EISA bit:** Full House machines set bit 4 in MC SYSID (EISA present).

**IP28 secondary cache diagnostics:** The PROM runs `pon_scache` testing
tag SRAM, data SRAM, and ECC. All pass except MRU bit test (not simulated —
non-blocking, 2 warnings printed).

**IP28 memory probing via XKPHYS:** Uses 0x9000_0000_xxxx_xxxx for physical
access, bypassing TLB. Can reach up to 0xFF000000.
