# IP54 Audio — wav-capture gate PASSED (M4 step 1)

Date: 2026-06-10

## Result

Full audio chain verified end-to-end:
guest userland write → `/hw/pvaudio` (pvaudio.c kernel driver, 64KB
ring) → QEMU `sgi-pvaudio` (AudioBackend) → `-audiodev wav` capture.

- `/usr/local/bin/audiotest` (440Hz, 2s, s16 stereo 44.1kHz) ran on
  sgi-ip54: `audio done`, exit 0.
- Host capture `/workspace/pvaudio_out.wav`: 1.6s of audio, peak
  15488/32767, zero-crossing estimate **≈444 Hz** — PASS.
  (1.6s < 2s because the backend drains at real-time rate until guest
  shutdown; header unfinalized if QEMU is killed — parse data chunk
  with canonical 44-byte offset if `wave` rejects it.)

## How the test binary was built — THE recipe for guest userland

The ip54-test/irix655-dev install has **no crt1.o anywhere** (neither
/usr/lib32/crt1.o nor nonshared) — the MIPSpro install is the
kernel-build subset. Normal `cc -o` linking is impossible. Working
recipe (freestanding, audiotest.c defines `__start` and externs libc
syscalls):

    cc -c -n32 -mips3 -o audiotest.o audiotest.c
    /usr/lib/ld -n32 -e __start -o audiotest audiotest.o -lc

Built in an **Indy-machine session** (run_m4_buildtest.py): in-guest cc
on the sgi-ip54 machine fails intermittently (cc exit 32 etc. — see the
fragility investigation), while the SAME DISK on Indy compiled 5 kernel
drivers + this binary flawlessly across several sessions. **Build
userland binaries on Indy; run them on sgi-ip54.** That contrast is
also the strongest clue yet that the fragility is sgi-ip54-machine-
specific, not disk/toolchain state.

Pitfall that burned a boot: `cc ... | head -5 ; echo RC=$?` reports
head's status. Never pipe a compile when checking `$?`.

## Audiodev wiring

`vm_instances/ip54-test/manifest.json` default_extra_args:
`-audiodev wav,id=aud0,path=/workspace/pvaudio_out.wav -global
sgi-pvaudio.audiodev=aud0`. The wav backend is always compiled in; the
Docker QEMU build has no audible backend (OSS only, no /dev/dsp), so
wav capture is the acceptance gate. Live host audio would need
PulseAudio-over-TCP (stretch, see plan).

## Remaining M4 work (next session)

1. Stage `dmedia/audio.h` from "IRIX 6.5 Development Libraries February
   2002" CD (`dmedia_dev.sw`) — ALpv ABI must match shipped binaries.
2. `libaudio_shim.c`: the ~20 AL entry points actually imported by
   soundscheme/sfplay (alOpenPort, alWriteFrames, alSetParams,
   alGetFilled, ...) → format-convert → write() to pvaudio. Build on
   Indy (cc -n32 -shared... note: shared link needs investigation given
   the crt situation; may need `ld -shared -soname libaudio.so.1`).
3. Back up + replace `/usr/lib32/libaudio.so.1`, test ladder:
   `sfplay <aiff>` → wav contains sample → `soundscheme` under desktop.

## Update 2026-06-14 — concrete state for the shim sub-project (M4 step 2)

Verified blockers + resources so the next session can execute the shim directly:

- **`audio.h` is NOT on the run disk** (ip54-test `/usr/include/dmedia` absent — kernel-build
  subset). LOCATED for extraction: `software_library/prepackaged_combo_discs/
  IRIX_6.5.5_full_extracted/IRIX_6.5_Development_Libraries_February_2002_-_812-0766-003/
  dmedia_dev.idb` lists `usr/include/dmedia/audio.h` (data lives compressed in the matching
  `.sw` inst image at the manifest's `off(N)/cmpsize(M)` — needs the inst unpacker, not a
  plain copy).
- **Exact AL ABI constants** (AL_OUTPUT_RATE token value, AL_SAMPFMT_*, AL_SAMPLE_16,
  ALconfig/ALport struct layout) can be read straight from the shipped-lib decompiles
  `progress_notes/audio_re/al.json` + `al2.json` — preferable to audio.h since it's what the
  on-disk binaries actually use.
- **pvaudio write contract (verified, from audiotest.c)**: `open("/hw/pvaudio", 1)` →
  `ioctl(fd,0x6000,&rate)` `ioctl(fd,0x6001,&channels)` `ioctl(fd,0x6002,&bits)` →
  `write(fd, s16_interleaved, nbytes)`. The shim's AL playback path lowers onto exactly this.
- **Risk still open**: replacing `libaudio.so.1` means satisfying *every* symbol sfplay/
  soundscheme import (97 AL/al exports in `libaudio_symbols.json`) or rld fails the link — a
  partial shim breaks the app. And `cc -shared`/`ld -shared -soname libaudio.so.1` on the
  crt-less MIPSpro subset is unproven (build on Indy, as with the kernel objects).

**Status: sound capability DONE (wav gate, real 440Hz tone end-to-end). Shim = scoped,
resourced, remaining integration sub-project (extract audio.h → author full-surface shim →
shared-link on Indy → install → sfplay/soundscheme ladder).**
