# libaudio HAL (the SGI Audio Library) — the /dev/hdsp model (B7 / M4)

`libaudio.so.1` (extracted from ip54-test → `libaudio.bin`, PIC N32, 185 funcs, deps **only
libc**) is the SGI Audio Library (AL). Apps use it directly — no audio daemon, no X. Built
from `dmedia/lib/libaudio2` (libaudio2). Decompiles: `al.json`, `al2.json`.

## API (two layers)
- Classic **AL**: `ALopenport`/`ALcloseport`, `ALwritesamps`/`ALreadsamps`,
  `ALnewconfig`/`ALsetconfig`, `ALsetsampfmt`/`ALsetchannels`/`ALsetqueuesize`,
  `ALgetfillable`/`ALgetfilled`/`ALsetfillpoint`, `ALsetparams`/`ALgetparams`.
- New **al** (the real impl): `alOpenPort`, `alConnect`, `alWriteFrames`, … `ALopenport`
  is a thin wrapper around `alOpenPort`.

## Device model — `/dev/hdsp/*` with an mmap'd ring (KEY)
NOT a simple `write()` /dev/audio. From `alConnect` + `alOpenPort` decompiles:
1. `open("/dev/hdsp/hdsp0master", O_RDWR)` — the master device.
2. `ioctl(master, 0x16/2, buf)` — query hardware/resource info.
3. `sprintf` a per-resource name `"/dev/hdsp/hdsp0r%d"`; `open` that resource device.
4. `ioctl(res, 0x1d, …)` config; `ioctl(res, 10, …)` / `ioctl(res, 8, …)` set params
   (rate/format/channels); `ioctl(res, 0x15/0x12/3, …)` start/control/query-fill.
5. **`mmap(res, …)`** — the audio **sample ring buffer** is memory-mapped; the app writes
   samples into the shared ring and the kernel/hardware DMAs them out (ALwritesamps copies
   into the mmap'd ring + updates the fill pointer via ioctl).

Also: `/dev/hdsp/hdsp0events` (event stream), `/dev/hdsp/hdsp0master` (control).

## Implication for IP54 sound (M4)
Real libaudio expects the **hdsp kernel driver**: `/dev/hdsp/hdsp0master` + per-resource
`/dev/hdsp/hdsp0r%d`, the ioctl set above, and **mmap'able ring buffers**. The current
IP54 pvaudio is a simpler write-based device → that's why sound currently needs a
libaudio *shim* (M4). Two paths forward:
- **Shim (current)**: keep intercepting AL at the lib level → pvaudio write. Lower fidelity
  but works for simple tone/wav.
- **Real hdsp**: implement a pv `hdsp` kernel driver presenting the master+resource nodes,
  the ioctl set, and an mmap'd ring backed by the QEMU pvaudio device. Higher effort; needed
  for apps that mmap the ring (most real audio apps). RE deliverable (this doc) = the exact
  device/ioctl/mmap contract to implement.
