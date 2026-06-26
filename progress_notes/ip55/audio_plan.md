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

## Progress — Option A, step 1 DONE (2026-06-26): backend plumbing

The host-audio backend is now wired into `sgi_hpc3_virtuix.c` (`AudioBackend *audio_be` + `SWVoiceOut *voice` on the state; `#include "qemu/audio.h"`; `DEFINE_AUDIO_PROPERTIES(SGIHPC3VirtuixState, audio_be)`; in `realize`, an S16/stereo/44100 big-endian `AUD_open_out` voice). **Gated for safety:** the audio init runs ONLY when an audiodev is explicitly configured (`s->audio_be != NULL`); default boots pass no `-audiodev`, so audio is skipped entirely and **existing boots are provably unaffected**. Verified: `test_virtuix_boot.py` green with NO audiodev; and `-audiodev none,id=aud0 -global sgi-hpc3-virtuix.audiodev=aud0` boots to login with no realize crash. The voice is opened *inactive* and nothing feeds it yet (silent) — the callback `sgi_hpc3_virtuix_audio_out_cb` is a stub. Host wiring to use it: `-audiodev pa,id=aud0 -global sgi-hpc3-virtuix.audiodev=aud0`.

**Remaining steps (each builds on this, smallest-first):**
- **Step 2 — rate generator:** decode the HAL2 IAR/IDR indirect-register writes (`sgi_hpc3_virtuix.c` ~2905) into the Bresenham codec clock (MAME `hal2.cpp:333-363`) and re-open the voice at that rate.
- **Step 3 — PBUS audio-DMA walker:** in the PBUS DMA channel handler, when the audio channel is armed, read the descriptor ring + PCM from guest memory (`dma_memory_read`), `AUD_set_active_out(voice,1)`, feed frames via `AUD_write` in the out callback. This is the piece that makes sound.
- **Step 4 — interrupt:** raise the HAL2/HPC3 audio-DMA IRQ on buffer/descriptor completion so IRIX refills the ring (trace-tune against the real driver).
- **Validation:** to build steps 2-3 from real behavior, boot the desktop, play a sound (boot chord / `playaiff`), capture the existing `trace_sgi_hpc3_hal2_*` + the PBUS-audio-channel writes; final gate is *listening* via `-audiodev pa`.

## Progress — Option A, steps 2-4 DONE (2026-06-26): rate gen + DMA walker + IRQ implemented

The QEMU-side HAL2 audio path is now fully implemented in `sgi_hpc3_virtuix.c` (ported from MAME `sgi/hal2.cpp` + `sgi/hpc3.cpp` — port spec captured during the work). All gated on `s->voice != NULL` (NULL unless an audiodev is configured), so default boots are provably unaffected; builds clean; `tests/test_virtuix_boot.py` stays green.

- **Step 2 — rate generator (`hal2_iar_write` + `hal2_recompute_rate` + `hal2_maybe_reopen_voice`):** decodes the HAL2 IAR/IDR indirect-register protocol on the IAR write — Codec A control (channel/clock/channel_count, bits [1:0]/[4:3]/[9:8]) and the three Bresenham clock-gens (sel = 48k/44.1k base, inc, modctrl). Effective frame rate = `base*inc/mod`, `mod = 0x10000-((modctrl+1)-inc)`. The host voice is re-opened at the computed rate (S16/stereo/BE; mono duplicated L→R), clamped to [4000,50000] Hz defensively.
- **Step 3 — PBUS audio-DMA walker (`hal2_pbus_arm`/`hal2_pbus_next`/`hal2_fetch_sample` + the out-callback):** PBUS channel 0 = Codec A DAC out. On `pbus_ctrl` write with DMASTART|LOAD_EN and !RECV, load the 3-word descriptor (cur_ptr / flags[count|XIE|EOX] / next_ptr) from guest memory and activate the voice. The AUD out-callback walks the descriptor ring, reads big-endian S16 PCM (one sample per 4-byte slot, high half-word; swap gated on `dmacfg` bit19), assembles host stereo frames, and `AUD_write`s them — advancing/looping descriptors at end-of-buffer.
- **Step 4 — interrupt (`hal2_pbus_next` + `update_irq` whitelist + `pbus_ctrl` read ack):** on buffer completion with XIE, raise `INT3_LOCAL0_FIFO` (PBUS FIFO → IP2) so IRIX refills the ring; **the critical QEMU-specific fix was adding `INT3_LOCAL0_FIFO` to the `update_irq` whitelist mask** (it was being stripped, which would mask the refill IRQ). The `pbus_ctrl` read returns DMA status (bit0=IRQ pending, bit1=active) and acks/clears the IRQ on read.
- Trace events added (`hw/misc/trace-events`): `sgi_hpc3_hal2_rate`, `..._dma_arm`, `..._dma_write`, `..._dma_done` — the data-flow proof hooks.

### Remaining blocker is GUEST-SIDE, not the QEMU audio path: a2_dd doesn't attach under `-kernel` boot

End-to-end *listening* is blocked because the IRIX **A2 audio device driver `a2_dd` does not attach** on the virtuix desktop kernel. Root cause (diagnosed 2026-06-26): `a2_dd` is a **loadable VECTOR module** (`/var/sysgen/system/audio.sm`: `VECTOR: module=a2_dd ... base=0xBFBD8000 exprobe=(r,0xBFBD8020,2,0x0000,0x8000)`), NOT statically linked — and it is absent (0 symbols) from *both* `unix.ip55.g` and the reference `unix.g.smp-desktop`. Under `-M virtuix -kernel <k>` direct boot, the running kernel ≠ the disk's `/unix`, so **`configmon` rejects loadable modules on the "namelist and corefile do not match" check** → `a2_dd` never loads → `/hw/audio` absent, `hinv` shows no Audio, and `sfplay` reports *"failed to open audio port: unable to access audio driver."* At boot the kernel does exactly one HAL2 access (the exprobe reading REV=0x4010, which correctly matches MAME) and then stops — confirming attach never proceeds. (The "AES receiver not responding" SYSLOG lines are stale 1996 install-time entries, not this boot.) Our HAL2 register model is faithful (REV matches); the gap is purely that the guest never drives the codec.

**Fix options** (to actually exercise the now-implemented path):
1. **Make the disk `/unix` == the booted kernel** so configmon's namelist matches. Tried via TFTP onto `/unix` — too flaky (IRIX `tftp` batch-via-stdin doesn't transfer; interactive wedges the serial).
2. **Statically INCLUDE `a2_dd`** via a build-host relink — **TRIED, BLOCKED**: `a2_dd.o` references the DSP-microcode DATA symbol `kdsp_pro_audio_subcode`, which is in no object on the build host (it's a separate loadable resolved at load time). lboot ERROR 33. Commented out in the relink script.
3. **Boot stock `/unix` directly on `-M virtuix` via the PROM** (no `-kernel`, `-smp 1`) — **THE WINNER**: running kernel == disk `/unix` by construction, so configmon's namelist matches and `a2_dd` loads natively (resolving the subcode the loadable way). `tmp/cpu-r5000/audio_prom_boot.py`. This is how to exercise the audio path without any file transfer or relink.

## Progress — 2026-06-26 (cont.): PROM-boot makes a2_dd load; HAL2→host routing PROVEN; remaining blocker = AES-receiver attach probe

Booting stock `/unix` on `-M virtuix -smp 1` via the Indy PROM (no `-kernel`) makes `a2_dd` load (`hinv` shows *"Iris Audio Processor: version A2 revision 4.1.0"*). With that, the register-level attach sequence is now visible (full trace: `tmp/cpu-r5000/pb_trace.out`), and two things were fixed/proven:

**Timer-paced DMA completion (FIXED).** The first trace showed `a2_dd` arming PBUS audio DMA on **channels 1+2** (not 0) then **polling the channel ctrl register 6000× waiting for the `active` bit to clear**. My original callback-driven walker only drained ch0 and depended on the host-audio backend's pull timing (which doesn't advance during the guest's tight poll), so it never completed. Fix: drive DMA completion from **per-channel QEMU timers at the codec rate** (`hal2_dma_timer[4]`, `hal2_dma_tick`/`hal2_schedule_dma`/`hal2_drain_buffer_to_voice` in `sgi_hpc3_virtuix.c`) — the MAME model — independent of the host backend. Output is now push-driven (`AUD_write` from the timer; the out-callback is a no-op). All gated on `s->voice`; `test_virtuix_boot.py` stays green.

**HAL2 → host routing PROVEN.** With the timer, the guest's HAL2/PBUS DMA now streams real PCM to the host audiodev: `-audiodev wav` captured **368,600 bytes of NON-SILENT 44.1 kHz stereo PCM** (maxabs 32513, 172150/184300 samples non-zero — verified by reading `tmp/cpu-r5000/pb.wav`). So the core deliverable — *the guest's HAL2 audio actually reaches the host `-audiodev`* — works end to end. (The streamed content here is `a2_dd`'s codec-calibration DMA ring, not a user `sfplay`, but it is genuine HAL2-DMA'd PCM routed host-side.)

## ✅ DONE — 2026-06-26: a2_dd attaches, `sfplay` plays, audio works end-to-end

The full chain now works: **`sfplay <file>.aiff` returns RC=0, plays cleanly (no "unable to access audio driver"), and the PCM streams to the host `-audiodev`** (3.5 MB to a `wav` capture per test; the boot/login chimes + the played AIFF are audible, ~91% silence / 9% sound — clean, not noise). Proof artifact: `progress_notes/ip55/virtuix_audio_proof.wav` (44.1 kHz stereo, QEMU resamples the 48 kHz codec voice). Regression `tests/test_virtuix_boot.py` stays green; default (no-audiodev) boots and the PROM keyboard are unaffected (all audio gated on `s->voice`).

**How it was solved (RE of `a2_dd.o`, the binary HAL2 driver — non-stripped N32, decompiled with `~/cross/mips-elf/bin/mips-elf-objdump`):** I reverse-engineered the driver's attach-success contract (the registers it probes + the values it requires) and made the QEMU HAL2 model satisfy them — *not* by faithfully emulating the AES/codec silicon, but by returning the minimal values the probe needs (virtualization-native). The contract:
1. **HAL2 REV (0x20) low16 < 0x8000** — already (0x4010). [sole probe gate]
2. **HAL2 ISR (0x10) bit0 = 0** (transaction-not-busy) — already; else the indirect-register writes in `hal2_init` spin forever.
3. **AES register block (0x400–0x4ff) = write-readback** — `hal2_init_aesrx`/`hal2_init_aestx` write a byte then read it back; a mismatch returns -1 → "AES receiver/transmitter not responding" → `hal2_init` returns 0 (FATAL, no device node). Implemented as an 8-bit readback array `hal2_aes[]`.
4. **Keyboard/HAL2 address collision resolved**: the AES-TX registers (0x484/0x488/0x48c) coincide with our *PROM-only* keyboard PIO. The *OS* keyboard is at HPC3_KBD_MOUSE0/1 (0x59840/0x59844), so the AES block takes priority over the PROM keyboard PIO — **gated on `s->voice`** so non-audio boots keep the PROM keyboard byte-identical.
5. **Timer-paced PBUS DMA completion** makes `force_dma_frame`'s un-timeouted poll on the channel-ctrl "active" bit terminate (the buffer completes at the codec rate). [done earlier]

Net device-side change is small + self-contained in `sgi_hpc3_virtuix.c`/`.h` (HAL2 readback + the AES-priority + the timer DMA), all gated on a configured audiodev. The `a2_dd` RE workspace is `tmp/cpu-r5000/a2_re/` (the disassembly + `objdump` recipe).

### (historical) Remaining blocker (guest-side, well-characterized): the AES-receiver attach probe. `a2_dd`'s attach still does not *complete* — `/hw/audio` is never created and a user `sfplay` reports *"failed to open audio port: unable to access audio driver."* SYSLOG shows a **fresh** `audio: AES receiver not responding` this boot. So attach programs + probes the **AES digital-audio receiver** (the IAR `0x0200`/`0x0300` AES-In/Out writes seen in the trace) and the codec clock-calibration DMA, and aborts because my HAL2 model doesn't emulate the AES receiver's clock-recovery/response. Making `sfplay` open the device requires either **emulating the AES receiver + codec calibration** (deep SGI-silicon emulation — against the project's virtualization-native principle) or **trace-driven RE of the attach to fake the "AES present/calibration OK" status** so attach completes (the pragmatic, virtualization-native path; needs identifying the exact status bit `a2_dd` polls — `a2_dd` is binary-only, no source). This is a focused follow-on effort; the QEMU-side audio routing itself is done and proven.
