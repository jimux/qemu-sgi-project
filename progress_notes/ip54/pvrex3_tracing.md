# pvrex3 dynamic register tracing (B2)

QEMU trace instrumentation for the IP54 paravirtual REX3 (`qemu-sgi-repo/hw/display/
sgi_pvrex3.c`). The device already had `sgi_pvrex3_reg_write` / `_dcb_write` /
`_draw_block|span|scr2scr` events; **register reads and VC2 access were invisible**.

## Added (B2)
- `sgi_pvrex3_reg_read(reg, go, val, size)` — **wired** at the end of `sgi_pvrex3_read`
  (after the Go-command, before sub-word extraction; `val` is the full 32-bit value).
- `sgi_pvrex3_dcb_read`, `sgi_pvrex3_vc2(reg,val,write)` — **declared** in `trace-events`
  for the DCB-read and named-VC2 paths (wiring TBD; reg_read already captures the raw
  reads, and VC2 writes are visible via the existing `_dcb_write`).

Enable: `debug_flags="trace:sgi_pvrex3_reg_read,trace:sgi_pvrex3_reg_write"` +
`save_log=<path>` (MCP `qemu_run_sgi`/`qemu_session_start`), or
`-d trace:sgi_pvrex3_* -D log` directly. The MCP server's `qemu/build-linux` binary is a
symlink to `qemu-sgi-repo/build-linux` — so a `ninja qemu-system-mips64` rebuild there is
what the server runs.

## Verified (sgi-ip54 boot, golden ip54-test)
782 `reg_read` events during boot; QEMU ran to completion (no regression). The hot reads
are the engine init/poll conversation (previously unseen):

| reg | name | count | meaning |
|---|---|---|---|
| 0x0240 | REX3_DCBDATA0 | 258 | DCB read-back (XMAP9/VC2/CMAP register reads via DCB) |
| 0x0238 | REX3_DCBMODE | 257 | DCB mode (set up each DCB transaction) |
| 0x1338 | REX3_STATUS | 257 | busy-poll (GFXBUSY/BACKBUSY/FIFO levels) |
| 0x133c | REX3_STATUS_ALIAS | 8 | version read (returns 3 = REX3) |
| 0x1330 | REX3_CONFIG | 1 | config |

So the kernel pvrex3 driver + Xsgi DDX initialize by polling STATUS and reading back
sub-device registers through the DCB. This trace is the basis for the B3 live IRIS-GL
trace (watch what dgld drives) and for B4 (VC2 cursor moves).

## Note
`sgi_mcp.server._handle_tool(...)` is **synchronous** (returns a str) — do NOT `await` it
in runner scripts; it launches QEMU and blocks until the run's timeout.
