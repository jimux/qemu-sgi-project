# IP54 Performance — M3A + M3B results

Date: 2026-06-09. Measured on ip54-test (256MB, sgi-ip54, build-linux QEMU).

## Changes under test

1. **pvnet TX immediate-completion** (`hw/misc/sgi_pvnet.c`): frame is
   copied into an 8-slot device ring and TX_DONE set synchronously in
   the CMD write handler; the BH only does `qemu_send_packet` on the
   private copy. Kills the guest's up-to-100k-MMIO-read TX spin.
2. **pvdisk cache=writeback** (`sgi_mcp/server.py` both mtd drive
   lines): was writethrough = fsync per 512-byte sector write.

## Numbers (vs Phase 11 baseline)

| Metric | Baseline (2026-03-18) | M3A (2026-06-09) | Change |
|---|---|---|---|
| ping RTT avg (10 pkts, 10.0.2.2) | ~18 ms (first reply) | **10.5 ms** (min 9.6 / max 18.4, 0% loss) | ~1.7× |
| TFTP get 460800 B | 17.3 s ≈ 26 KB/s | **8.8 s ≈ 52 KB/s** | 2.0× |
| dd 8 MB → /tmp (fs, buffered) | n/a | 0.01 s (cache speed, no stall) | — |

Remaining network ceiling is the kernel-side 2-tick (20 ms) RX poll
deferral and one-packet-per-poll processing — that's M3B (RX drain loop
+ POLL_TICKS 2→1, target ≥500 KB/s TCP). TFTP is inherently
RTT-lockstepped (512 B per round trip), so big bulk numbers need
ftp/rcp once TCP is fast.

Raw pvdisk read/sync timing not captured this round (bench harness
window-race; the later benches never ran). Capture before/after numbers
during M3B's pvdisk work — the kernel copy-loop change (8-bit → 32-bit
data-window reads) is expected to be ~4× on its own.

## M3B (2026-06-10) — kernel-side batch, ONE lboot rebuild

Changes (all five drivers recompiled on the ip54-test disk itself):
1. `if_pvnet.c`: `pvnet_poll` RX **drain loop** (budget 32; the RX_LEN
   re-arm makes QEMU flush its queued backlog synchronously, so the
   whole SLIRP queue empties in one poll) + `PVNET_POLL_TICKS` 2 → 1.
2. `pvdisk.c`: data window copies 8-bit → **32-bit** via an aligned
   staging buffer (128 MMIO exits per sector instead of 512, each
   direction). QEMU `sgi_bootdisk.c` already supported size-4 BE access.

| Metric | M3A | M3B | Note |
|---|---|---|---|
| ping RTT avg | 10.5 ms | **9.7 ms** (min 9.3/max 10.0) | floor = 10ms poll tick |
| TFTP 460800 B | 8.8 s | 8.6 s | protocol-bound: 512 B lockstep, 1 pkt in flight |
| TFTP 6471680 B | — | 121.8 s = 53 KB/s, zero drops over ~12.6k pkts | drain-loop robustness validated |

TFTP **cannot** go faster than ~52 KB/s with a 10ms poll (one 512-byte
block per RTT). The drain loop's payoff is TCP bulk transfer (window of
packets per poll) — needs a TCP endpoint to measure (e.g. host-side
listener via SLIRP hostfwd + guest `ftp`/`rcp`); not benchmarked yet.
Boot-time disk reads now use the 32-bit path (kernel + rc2 reads MBs);
no regression observed, subjectively similar boot wall-clock.

Kernel provenance: rebuilt 2026-06-10 via run_m1_kernel_rebuild.py
(now compiles all 5 PV drivers; if_pvnet needs `-I/tmp/khdrs` FIRST and
`-D_PAGESZ=16384`). Golden refreshed with this kernel.

## Benchmark harness lessons

- Serial sessions truncate long lines and mangle heredocs (`<<EOF`):
  every command must be one short line; drive tftp interactively.
- The `qemu_session_send` output window races command completion: a
  marker often lands in the SAME window as the send, or in odd later
  windows. Accumulate ALL windows (including the first) before
  searching — see run_until() in run_m1_kernel_rebuild.py for the
  correct pattern.
- `ps`-based assertions must not grep for a pattern that appears in
  the echoed command itself (false PASS).
- Shutdown after any session where X ran panics (post-X fragility,
  see boot_to_graphical_login.md) — disk survives, but benches should
  run BEFORE anything X-related when possible.
