# Indy desktop freeze FIXED — MC DMA_INT_CAUSE write semantics (2026-06-24)

The `-M indy` 4Dwm desktop hung hard while loading (one weave tile drawn, then a frozen busy-cursor; one vCPU pinned at ~94%, serial dead). `-M virtuix` was unaffected. Root-caused to the MC GIO-DMA interrupt-ack semantics and fixed in `hw/misc/sgi_mc.c` (+ the virtuix copy).

## Root cause
The IRIX gfx **GDMA interrupt handler** acks the DMA-complete interrupt by **writing `0x00000000` to `MC_DMA_INT_CAUSE`** (MC offset `0x0160`, KSEG1 `0xbfa00160`). The write handler used **write-1-to-clear**:

```c
s->dma_int_cause &= ~val;   /* val == 0 -> NO-OP: nothing cleared */
```

So the Complete bit was never cleared, the **level-followed** `LIO_GDMA -> IP2` line stayed asserted, and the ISR re-entered forever. Captured with an env-gated `MC_DMA_TRACE` (since removed) — the freeze was a 25-million-iteration loop of exactly:

```
MCrd 2048 = 00000008     # ISR reads DMA_RUN: COMPLETE set, RUNNING clear
MCwr 0160 = 00000000     # ISR writes DMA_INT_CAUSE = 0 to ACK -> no-op -> storm
```

**Why indy-only:** the freeze only happens when the guest enables the DMA IntMask (`dma_control` bit 4) so `perform_dma()` raises the IRQ. The stock IRIX 6.5.5 indy kernel uses **interrupt-driven** DMA completion (enables IntMask, ISR acks with write-0). The custom virtuix SMP-desktop kernel **polls** `DMA_RUN` and never enables IntMask, so it never raised the IRQ and never hit the bad ack path — which is exactly why the just-completed indy/virtuix device separation surfaced this as an indy desktop problem. It is **pre-existing** (the MC perform_dma + IRQ work was added in a prior session into the then-shared MC); it was never caught because indy's *desktop* wasn't re-tested since.

## Fix
Write-value semantics, so the guest's write-0 ack clears the cause and deasserts the line:

```c
case MC_DMA_INT_CAUSE:
    s->dma_int_cause = val;          /* was: &= ~val */
    sgi_mc_update_dma_irq(s);
    break;
```

Applied to **both** `hw/misc/sgi_mc.c` (indy — the active fix) and `hw/misc/sgi_mc_virtuix.c` (virtuix — correctness/consistency; its current kernel polls so it doesn't hit it yet).

## Verified
- `-M indy` boots through the PROM to login (indy gate) — fix did NOT break the PROM VDMA path.
- `-M indy` 4Dwm **desktop now loads fully and windows drag** (user-confirmed on the GTK window). The weave renders tile-by-tile via MC GIO DMA with no hang.
- `-M virtuix` still boots SMP to login; weave/desktop unaffected.
- `pytest -m "not slow"` green (989 passed, 0 failed).

## Still open (separate, parked)
Window-move leaves stale repaint artifacts on **both** machines — see `progress_notes/ip55/bug_artifacts/window_move_artifacts.md` (Newport expose/damage; not this bug).

## Diagnostic tooling left in place
- `hw/display/sgi_newport_virtuix.c`: env `NP_CURSOR=1` emits `cursor=(x,y)` for closed-loop cursor servo (`tmp/indy-virtuix-sep/servo.py`) — reliable virtuix desktop driving immune to X pointer accel. Virtuix-only, env-gated.
- Boot/gate scripts: `tmp/indy-virtuix-sep/{indy_gate.py,virtuix_gate.py,boot_desktop.py,servo.py}`.
