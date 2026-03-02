# HAL2 Audio Architecture

## Current State

The HAL2 audio controller now has a proper register stub that returns the
correct revision (0x4010) and accepts all register writes silently. No audio
output is produced. The PROM startup chime code runs to completion without
hanging because:

- **HAL2_REV** = 0x4010: bit 15 clear, so the PROM thinks HAL2 exists
- **ISR** always returns TSTATUS=0: the PROM's SPIN macro exits immediately
- **PBUS DMA control** read format returns ch_act=0: `waitfordma()` exits

Previously, these values were accidental zeroes from the generic PBUS PIO
handler. Now they are intentional, with trace events for debugging.

## PROM Startup Chime

The PROM plays a startup chime via `play_hello_tune()` in
`stand/arcs/IP22prom/hello_tune.c` (IP22) / `plucktune.c` (IP24).

### IP24 (Indy) - Karplus-Strong Synthesis

The Indy PROM uses plucked-string synthesis (Karplus-Strong algorithm):
1. Programs HAL2 via indirect registers (IAR/IDR): codec rate, DMA enable
2. Sets up Bresenham clock dividers for 44.1 kHz sample rate
3. Generates samples in software using delay-line feedback
4. Writes samples to PBUS DMA descriptors on channels 1+2 (left/right DAC)
5. Kicks PBUS DMA to stream samples to HAL2 codec

### Three Tunes

- **Startup** (tune 0): Ascending A-C#-E arpeggio (major triad)
- **Shutdown** (tune 1): Descending version
- **Bad graphics** (tune 2): Dissonant "devil chords" (error indicator)

### Code Flow

```
play_hello_tune(0)
  -> hal2_configure()       # Program codec/BRES via IAR/IDR
  -> setup_pbus_dma()       # Build DMA descriptor ring
  -> pluck_string() / adpcm_decode()  # Generate samples
  -> kick DMA              # Write PBUS ctrl to start transfer
  -> waitfordma()          # Poll PBUS ctrl until ch_act clears
```

## IRIX Kernel Audio

The IRIX kernel audio driver:
1. Probes HAL2_REV to detect the chip
2. Configures codecs via HAL2 indirect registers
3. Checks AES receiver (CS8412 via HAL2 indirect register) - the
   "AES receiver not responding" message during boot is expected
   without a CS8412 chip emulated
4. Uses PBUS DMA for audio streaming

## Architecture for Full Audio

To produce actual audio output, five components are needed:

### 1. HAL2 Indirect Register Bank

HAL2 uses an indirect register scheme: write the register address to IAR,
then read/write data via IDR0-IDR3. Key indirect registers:

| Address | Name | Function |
|---------|------|----------|
| 0x0001  | RELAY_CONTROL | Codec mux routing |
| 0x1100  | CODECA_CTRL1 | Codec A control |
| 0x1101  | CODECA_CTRL2 | Codec A rate/format |
| 0x2000  | BRES1_CTRL | Bresenham clock 1 control |
| 0x2100  | BRES2_CTRL | Bresenham clock 2 control |
| 0x2200  | BRES3_CTRL | Bresenham clock 3 control |
| 0x3000  | DMA_ENABLE | Enable/disable DMA channels |
| 0x3100  | DMA_ENDIAN | DMA byte ordering |
| 0x3200  | DMA_DRIVE | DMA drive control |
| 0x9000  | UTIME | Microsecond timer |
| 0x9100  | UTIME_HI | Microsecond timer high bits |

### 2. PBUS DMA Engine

HPC3 PBUS DMA channels 1 and 2 drive audio output:
- Channel 1: DAC left/mono
- Channel 2: DAC right (stereo)

Each channel uses linked descriptor lists:
- `dp` = descriptor pointer (physical address of current descriptor)
- `bp` = buffer pointer (physical address of data buffer)
- `ctrl` = control register (start DMA, endian mode, etc.)

**Critical:** The PBUS DMA control register has different read vs write formats:
- Write: ch_act = bit 4 (start DMA), ch_le = bit 0 (little endian)
- Read: ch_act = bit 1, ch_le = bit 0

The PROM's `waitfordma()` reads bit 1. Currently this reads as 0 (DMA never
started), so it returns immediately.

### 3. Bresenham Sample Rate Computation

HAL2 uses Bresenham fractional dividers to generate audio sample rates from
the master clock (22.5792 MHz for 44.1k family, 24.576 MHz for 48k family):

```
output_rate = master_clock * inc / mod
```

Common configurations:
- 44.1 kHz: inc=1, mod=1 from 22.5792 MHz (or appropriate fraction)
- 48.0 kHz: inc=1, mod=1 from 24.576 MHz

### 4. QEMU Audio Backend

Integration with QEMU's audio subsystem:
```c
s->audio_stream = qemu_new_audio_stream(&s->card, "hal2",
                                         sample_rate, channels, fmt);
```

The DMA engine would periodically:
1. Fetch descriptor from `dp`
2. DMA audio data from `bp` into a local buffer
3. Feed samples to `qemu_audio_stream_write()`
4. Advance to next descriptor
5. Raise interrupt if XIE (interrupt enable) bit set

### 5. Volume DAC

Two 8-bit volume DAC registers control left/right output level:
- `0x58800` = right volume
- `0x58804` = left volume

These would attenuate the PCM samples before sending to the QEMU audio backend.

## MAME Reference

MAME implements HAL2 in `hal2.cpp` (~400 lines) and PBUS DMA in
`hpc3.cpp`. Key observations:

- HAL2 indirect register bank with full codec/BRES/DMA configuration
- PBUS DMA uses a QEMUTimer-style periodic callback to fetch descriptors
  and feed audio samples
- Volume DAC attenuation applied to output samples

## IRIX Source Reference

| File | Content |
|------|---------|
| `kern/sys/hal2.h` | Register definitions, indirect addresses |
| `kern/sys/pbus.h` | PBUS DMA descriptor format |
| `stand/arcs/IP22prom/hello_tune.c` | IP22 ADPCM chime |
| `stand/arcs/IP22prom/plucktune.c` | IP24 Karplus-Strong chime |
| `io/audio/hal2.c` | Kernel audio driver |

## Key Insight: PBUS DMA Control Read/Write Mismatch

The PBUS DMA control register has asymmetric read/write formats:

**Write format:**
- Bit 0: Little endian
- Bit 4: Channel active (start DMA)
- Other bits: FIFO thresholds, etc.

**Read format:**
- Bit 0: Little endian
- Bit 1: Channel active (DMA running)
- Other bits: FIFO status

The PROM's `waitfordma()` function reads the control register and checks
bit 1 (`ch_act` in read format). Since we never actually start DMA, the
control register value stays as written (with ch_act at bit 4, not bit 1),
so bit 1 reads as 0 (`little` endian bit from write format), and
`waitfordma()` returns immediately. This is correct behavior for a stub
that doesn't actually perform DMA.
