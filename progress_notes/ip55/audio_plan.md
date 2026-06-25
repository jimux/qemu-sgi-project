# Virtuix audio — implementation plan (the one remaining large desktop feature)

Status as of 2026-06-24: virtuix is **silent**. `sgi_hpc3_virtuix.c` models the HAL2
registers (ISR/REV/IAR/IDR0-3/volume) and the 8 PBUS DMA channels as *state* — reads/writes
are accepted — but nothing is wired to a QEMU audio backend, so no samples ever leave the VM.
Graphics, input, networking, clock, SMP are all done + tested; audio is the last gap to the
CLAUDE.md "graphics, sound, networking" desktop goal.

This is deliberately **not** rushed into the codebase as a half-feature: it is a multi-component
hardware path that can only be truly validated by *listening*, so it warrants a dedicated effort.
This note captures the recon + the design so that effort is turnkey.

## How HAL2 audio actually works (from MAME `sgi/hal2.cpp` + `hpc3.cpp`)

1. **Rate generator (HAL2, Bresenham).** HAL2's IAR/IDR indirect registers program per-codec
   "clock generators": `m_bres_clock_freq` (master, e.g. 44.1/48 kHz crystal) and
   `m_bres_clock_inc/mod` (a Bresenham increment/modulus that divides it). `update_clock_freq()`
   → `get_rate(channel)` yields the effective sample period. (`hal2.cpp:333-363`.)
2. **The DMA itself is in HPC3, not HAL2.** The HPC3 PBUS DMA channel bound to the audio codec
   walks a descriptor ring in guest memory, and per sample calls `hal2->dma_write(channel,
   int16_t)` at the rate from step 1. (`hal2.cpp:365` `dma_write`.)
3. **Output.** MAME routes `dma_write` → two `dac_word_interface` DACs (`m_ldac/m_rdac`) →
   `speaker` (`hal2.cpp:385-388`). For QEMU we replace the DAC/speaker with a `SWVoiceOut`.

So three pieces are missing in `sgi_hpc3_virtuix.c`: the Bresenham rate, the PBUS audio-DMA
walker, and the AUD backend voice.

## Option A — faithful HAL2 (no guest kernel change). RECOMMENDED for compatibility.

The stock IRIX HAL2 audio driver in the IP55 kernel already drives these registers, so no
kernel rebuild is needed — all work is in `qemu-sgi-repo/hw/misc/sgi_hpc3_virtuix.c` (+ header).

1. **AUD backend wiring:** `#include "qemu/audio.h"`, add `SWVoiceOut *voice` and an
   `AudioBackend *audio` field, `DEFINE_AUDIO_PROPERTIES(SGIHPC3VirtuixState, audio)` in the
   property list, `AUD_register_card("sgi-hal2", ...)` in realize. (Pattern: `hw/misc/sgi_pvaudio.c`
   and any `hw/audio/*.c`.)
2. **Rate generator:** decode the IAR/IDR writes that program the codec clock (the indirect
   register protocol around `HAL2_REG_IAR`/`HAL2_REG_IDR0..3` already half-modelled), compute the
   Bresenham sample rate, and `AUD_open_out()` a `SWVoiceOut` at that rate (S16, stereo) when the
   codec is enabled; re-open on rate change.
3. **PBUS audio-DMA walker:** in the PBUS DMA channel handler (the `base_addr < 8 *
   HPC3_PBUS_STRIDE` block, ~`sgi_hpc3_virtuix.c:2290`), when the audio channel is armed, read the
   descriptor ring (`dma_p* / cbp / nbdp` regs) and the sample buffer from guest memory via
   `dma_memory_read`, and push frames to the voice. Pace it: either a periodic timer at the
   sample-buffer-drain interval, or feed on the AUD `out` callback (`AUD_set_active_out` +
   write in the callback) — the AUD-callback approach is cleaner and avoids a guest-rate timer.
4. **Interrupt:** raise the HAL2/HPC3 audio DMA interrupt (the ISR bits + the HPC3 IRQ) when a
   buffer/descriptor completes so IRIX refills the ring. This is the piece most likely to need
   trace-driven tuning against the real driver.

Effort: **large** (the DMA walker + descriptor format + interrupt timing are the hard parts).
Validation: trace `AUD_write` call counts + bytes; then listen (host `-audiodev pa`) to the
IRIX boot/login chord and `playaiff`/`soundscheme`. Iterate the interrupt timing until the
driver streams continuously without underrun/overrun.

## Option B — paravirtual audio (more virtualization-native, per the guiding principle)

Add the existing `hw/misc/sgi_pvaudio.c` device to the virtuix machine and a matching pvaudio
*driver* to the IP55 kernel (the IP54 kernel had `pvaudio`/`if_pvnet`/`pvfb`; the IP55 board
fork came off IP22 and lacks them). The device side is trivial (instantiate + wire an IRQ, like
`sgi_virtuix.c` does for MC/HPC3); the cost is the **guest driver + a kernel rebuild** (build
host, see `rebuild_ip55_kernel.md`) and a `master.d` entry. This drops the HAL2 DMA/rate/IRQ
complexity entirely (the guest hands PCM straight to the device) and is the cleaner long-term
"fast, clean" answer — but it needs the kernel-build loop.

Effort: **large** (kernel driver + rebuild), but simpler/more-robust device code than Option A.

## Recommendation

Start with **Option A** (no kernel rebuild → fastest path to *any* sound, and it keeps the stock
IRIX audio stack working). If the HAL2 DMA/interrupt timing proves too fiddly to stabilize, fall
back to **Option B** as the virtualization-native end-state. Either way, gate the work on a
listening test and a `AUD_write` byte-count trace, and add a register-level test that flips the
currently-global `xfail` in `tests/test_hal2_stub.py` to real passes against the *virtuix* HPC3
source for the parts that no longer stub (volume DAC, ISR, rate decode).

## Validation hooks already present

- `tests/test_hal2_stub.py` — 60 `xfail` tests (against the indy `sgi_hpc3.c`); split per-test +
  add a `hpc3_virtuix_source` fixture so the register-level ones pass against the virtuix copy.
- `-audiodev pa,id=aud0 -global sgi-hal2.audiodev=aud0` (or `sgi-pvaudio.audiodev` for Option B)
  is the host wiring; the IP54 launch line in CLAUDE.md already shows the `-audiodev` form.
