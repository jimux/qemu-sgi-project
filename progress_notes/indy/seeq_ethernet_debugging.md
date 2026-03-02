# Seeq Ethernet Debugging — Kernel Ping

## Problem

PROM-level BOOTP networking works (BOOTP rewrite fix captures ciaddr from TX,
rewrites broadcast replies to unicast per RFC 1542). But IRIX kernel-level
`ping -c 3 10.0.2.2` shows 100% packet loss.

## Status

**Diagnosing.** Root cause not yet confirmed.

## What Works (PROM Level)

- `boot -f bootp()kernel` → "Setting $netaddr to 10.0.2.15"
- Fix was BOOTP rewrite: broadcast IP dst → ciaddr in SLIRP BOOTP replies

## What Works at Kernel Level (But Doesn't Result in Ping Success)

1. **TX works** — ARP and ICMP echo requests sent via SLIRP correctly
2. **RX data arrives** — SLIRP delivers ICMP echo replies (98 bytes each)
3. **RX buffer written** — packet data + 2-byte pad + Seeq status byte in guest memory
4. **Descriptor BC word updated** — r_rown cleared, remaining count correct
5. **Interrupt raised** — XIE=1 in kernel descriptors, `sgi_hpc3_enet_raise_irq()` called
6. **Kernel uses NBDP** — `hio->nrbdp = K1_TO_PHYS(ei->ei_ract)` starts the chain

## Verified Correct

| Item | Details |
|------|---------|
| Descriptor format | IP22 layout: EOX(31), EOXP(30), XIE(29), unused(28-15), ROWN(14), COUNT(13-0) |
| Remaining byte count | 1485 for 98-byte ICMP packet (matches MAME) |
| Buffer alignment | 2-byte pad at start (matches MAME `enet_rx_bc_dec(2)`) |
| Seeq status byte | Written after packet data (GOOD|END = 0x30) |
| NBDP register | Correct register for descriptor chain head |
| DMA CA bit | Stays set during operation |
| enet_misc W1C model | Write-1-to-clear matches MAME |
| HPC_RSPACE values | PROM=3, Kernel=8, but kernel ISR uses `-3` for rlen computation |
| Packet length formula | `rlen = MAX_RPKT(1586) - remaining(1485) - 3 = 98` ✓ |

## Observations (from trace events, pre-diagnostic)

- Kernel descriptors: `bc=0x60004632` (XIE=1, EOXP=1, ROWN=1, count=1586)
- After RX: `bc=0x600005cd` (ROWN=0, count=1485)
- First ICMP reply: `local0_mask=0x83` (ethernet bit 0x08 NOT in mask)
- Second+ ICMP replies: `local0_mask=0x8b` (ethernet bit 0x08 IS in mask)
- Kernel ISR never writes to enet_misc (no MISC-W entries) — ISR never runs
- Multiple TX interrupt raise attempts (trace events with misc=0x0)

## Hypotheses

### H1: Synchronous RX inside TX blocks interrupt delivery (LEADING)

When kernel TX writes TX_CTRL CA → `sgi_hpc3_enet_tx()` → `qemu_send_packet()`
→ SLIRP generates reply synchronously → `sgi_hpc3_enet_receive()` runs during TX.

The RX handler calls `sgi_hpc3_enet_raise_irq()`:
```c
if (!(s->enet_misc & HPC3_ENET_MISC_INT)) {
    s->enet_misc |= HPC3_ENET_MISC_INT;
    s->int3_local0_stat |= INT3_LOCAL0_ETHERNET;
    sgi_hpc3_update_irq(s);
}
```

If mask doesn't include ethernet (0x83), the interrupt isn't delivered.
Then TX completes and tries `sgi_hpc3_enet_raise_irq()` → but `enet_misc`
already has INT set → **TX interrupt is also lost** (guard blocks it).

When mask later changes to 0x8b, `update_irq()` is called. At this point
`local0_stat` should have ETHERNET bit set → interrupt should fire. But
does this actually work? Need to verify with diagnostics.

### H2: ISR runs but descriptor not visible (memory model)

DMA writes use `&address_space_memory`. CPU reads through KSEG1 → physical.
If MC memory alias dispatch differs, writes go nowhere. **Unlikely** since
PROM BOOTP works (same write path).

### H3: Timing window where mask enable and RX interrupt miss each other

If `ec_init()` enables the mask BEFORE any RX arrives, and the RX arrives
later, the interrupt should work. If the mask is enabled AFTER the first
RX, the pending bit should be picked up when the mask is set. Either way
it should work in theory. Need diagnostic to confirm.

### H4: Kernel replenishment issue

`rd_chain->r_rown = 1` is `#ifdef NOTDEF` in kernel ISR — r_rown reset is
disabled. Replenishment happens separately. If replenishment fails,
descriptors stay consumed.

## Diagnostic Code (in sgi_hpc3.c)

Three fprintf blocks writing to `/tmp/enet_diag.log`:
1. **RX-DMA** (line ~1800): After RX DMA descriptor write — logs desc addr,
   BC values, readback, mask/stat/misc, first 20 packet bytes
2. **MISC-W** (line ~2928): In enet_misc WRITE handler — logs ISR acknowledgment,
   descriptor state at NBDP/CBDP
3. **MASK** (line ~3203): In local0_mask WRITE handler — logs when ethernet
   interrupt mask bit changes

Need to add: **IRQ** diagnostic in `update_irq()` — log when ethernet
pending state transitions (ETHERNET in both stat and mask).

## Approaches Already Tried (DO NOT RE-TRY)

### PROM BOOTP (SOLVED)
| Attempt | Result |
|---------|--------|
| Always raise interrupt after RX | No change — PROM polls, not interrupt-driven |
| Update enet_rx_cbp/enet_rx_bc registers | No change — PROM doesn't read these during polling |
| **BOOTP rewrite (ciaddr)** | **FIXED** — root cause was broadcast replies |

### Kernel Ping (IN PROGRESS)
| Attempt | Result |
|---------|--------|
| Verified interrupt mechanism matches MAME | Confirmed W1C model correct |
| Verified descriptor format matches kernel | IP22 layout, offsets correct |
| Verified remaining byte count matches MAME | 1485 for 98-byte packet |
| Verified NBDP is correct register | Kernel writes ei_ract to hio->nrbdp |
| Verified DMA CA bit stays set | No spurious DMA stop |
| Verified local0_mask write calls update_irq | Line ~3214 |
| Noted mask=0x83 for first reply | Ethernet bit not in mask |
| Noted mask=0x8b for 2nd+ replies | Ethernet bit IS in mask |
| Added RX-DMA/MISC-W/MASK diagnostics | In code, not yet tested |

## IRIX Kernel ISR Reference

```c
if_ecintr() ISR:
  // Acknowledge interrupt
  if (hio->ctl & HPC_INTPEND)           // enet_misc bit 1
      hio->ctl = HPC_MODNORM | HPC_INTPEND;  // write 0x03 then 0x00

  // Walk RX descriptor chain (software pointer, not hardware reg)
  rd_chain = PHYS_TO_K1(KDM_TO_PHYS(ei->ei_ract));
  while (!rd_chain->r_rown && bcnt) {
      rem = rd_chain->r_rbcnt;
      rlen = MAX_RPKT - rem - 3;
      rstat = *(caddr_t)(eh + rlen);   // Seeq status byte
      if (!(rstat & SEQ_RS_GOOD)) { error; goto next; }
      m_adj(m, rlen - MAX_RPKT);
      ether_input(&ei->ei_eif, snoopflags, m);
  next:
      bcnt--;
      rd_chain = PHYS_TO_K1(rd_chain->r_nrdesc);
  }

  // Check if DMA stopped
  if (!(hio->rcvstat & HPC_STRCVDMA))
      ec_init();   // reinitialize
```

Key constants:
- MAX_RPKT = 1586 (1514 + 8 + 64)
- HPC_RSPACE = 8 (but rlen uses -3, not -HPC_RSPACE)
- HPC_INTPEND = 0x02
- HPC_STRCVDMA = 0x200 (CA bit)

## Diagnostic Results

### Run 1: MCP Test Harness Investigation (2026-02-17)

**Attempted:** `qemu_session_start(instance="irix65-desktop", snapshot="irix655_booted", extra_args="-icount shift=0,sleep=off -nic user,model=sgi-hpc3,...")`
**Result:** "Error: QEMU exited with code 1 before serial connected"

**Attempted:** `qemu_run_sgi(instance="irix65-desktop", timeout=8, extra_args="-icount shift=0,sleep=off")`
**Result:** WORKS — IRIX boots to multi-user (serial shows "network: WARNING: IRIS's Internet address is the default")

**Root cause identified:**
- `qemu_run_sgi` uses `-nographic` + `stderr=PIPE` → works, can see error output
- `qemu_session_start` / `qemu_serial_interact` use socket serial + `stderr=DEVNULL` → QEMU crashes, no error visible
- The crash reason was hidden because stderr was discarded

**Fix applied to `sgi_prom_mcp/server.py`:**
1. Added `_popen_qemu(cmd, tmpdir)` helper that redirects stderr to `{tmpdir}/qemu_stderr.txt` (avoids pipe overflow for long sessions)
2. Modified `_connect_serial_retry()` to accept `stderr_log_path` and `cmd` params — includes actual QEMU error message and command in failures
3. Updated all 8 callers of `_connect_serial_retry` to use `_popen_qemu` and pass the log path
4. Fixed `qemu_session_start` boot_wait error path to read from the stderr log file
5. Fixed VNC port detection to read from stderr log file (was reading `proc.stderr` which is now None)

**Next step:** Restart MCP server and re-run `qemu_session_start` to see the actual QEMU error message, then fix the root cause of the crash.
