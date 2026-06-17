# SGI-VIDEO-CONTROL (libXsgivc) — display mode + gamma (B5)

`libXsgivc.so` (staged `libxsgivc.bin`, PIC N32, 71 funcs) is the **client** of the
`SGI-VIDEO-CONTROL` X extension. Deps: libXext/libX11/libc — pure X protocol, no device
access. Built from `Xvc.c -DXSGIVC`. The **server** side is in Xsgi (`ProcSGIvc*` / the
SGIVC extension dispatch). Decompiles: `../decomp/sgivc.json`.

## What it controls (the X-extension request surface)
- **Display mode**: `XSGIvcLoadVideoFormat` / `LoadVideoFormatCombination`,
  `ListVideoFormats` / `ListVideoFormatCombinations`, `QueryVideoScreenInfo`,
  `QueryChannelInfo`, `DisableChannel`. (Video "formats" = the timing/resolution tables,
  e.g. 1280x1024_76 — same NG1 timing tables as `/var/X11/Xvc/NG1%d_TimingTable`.)
- **Gamma**: `SetChannelGammaMap` / `QueryChannelGammaMap` / `StoreGammaColors8/16`.
- **Output analog params**: `SetOutputGain/Pedestal/PhaseH/PhaseSCH/PhaseV/Blanking/Sync`.
- **Sync/genlock**: `SetScreenInputSyncSource`, `SetOutputSync`.
- **Platform**: `SetPlatformParameter` / `QueryPlatformParameter`, `QueryMonitorName`.

## The wire request (from `XSGIvcLoadVideoFormat` decompile)
Standard Xlib request builder: get the extension (`XQueryExtension("SGI-VIDEO-CONTROL")`,
cached), `GetReq`-style emit into the Display output buffer (`Display+0x6c`=bufptr,
`+0x70`=bufmax). `XSGIvcLoadVideoFormat` emits **minor opcode 5, request length 0x14 (20
bytes)** = SGIVC request #5 = LoadVideoFormat. (`XMissingExtension` if the server lacks
the extension.) Each `XSGIvc*` call = a distinct minor opcode on the SGIVC major.

## Path to working display-mode control on IP54
`XSGIvcLoadVideoFormat(minor 5)` → Xsgi SGIVC handler (`ProcSGIvc*`) → programs the VC2
video timing + the gfx pipe for the new format. To actually change modes on emulated IP54
we'd need: (a) Xsgi's SGIVC server handler functional, (b) pvrex3 to honor the VC2 timing
write (and the display surface to resize). RE deliverable (this doc) = the protocol + the
control surface; implementation is a follow-on (pairs with pvrex3 VC2 timing).
