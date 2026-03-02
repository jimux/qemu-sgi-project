# IP30/IP54 Paravirtual Driver Stack — Implementation Notes

## Status: Phases 1-3 Complete (QEMU side)

Date: 2026-03-01

## What Was Done

### Phase 1a: Wired All 5 HEART CPU IRQs

Previously only cpu_irq[1] (HEART timer -> CPU IP6) was wired. Now all 5 are
connected to CPU0 interrupt pins:

  cpu_irq[0] -> cpus[0]->env.irq[7]  /* IP7: errors/widget (level 4) */
  cpu_irq[1] -> cpus[0]->env.irq[6]  /* IP6: timer (level 3) */
  cpu_irq[2] -> cpus[0]->env.irq[5]  /* IP5: IPI/local (level 2) */
  cpu_irq[3] -> cpus[0]->env.irq[4]  /* IP4: local (level 1) */
  cpu_irq[4] -> cpus[0]->env.irq[3]  /* IP3: local (level 0) */

### Phase 1b: Instantiated All PV Devices

PV device bank at 0x1F480000-0x1F4807FF:

  0x1F480000  sgi-smp       -- IPI + secondary CPU boot
  0x1F480100  sgi-pvmem     -- RAM layout info (read-only)
  0x1F480200  sgi-pvnet     -- NIC, IRQ -> HEART ISR bit 20 (IP4)
  0x1F480300  sgi-glaccel   -- framebuffer, IRQ -> HEART ISR bit 21 (IP4)
  0x1F480400  sgi-pvaudio   -- audio, IRQ -> HEART ISR bit 22 (IP4)
  0x1F480500+ pv-expansion  -- unimplemented stub

PV devices connect to HEART via qdev_get_gpio_in(heart_dev, bit_N).
GPIO input N sets/clears ISR bit N and calls sgi_heart_update_irq().

### Phase 1c: HEART COMPARE Timer

Added QEMUTimer *compare_timer to SGIHEARTState. On HEART_COMPARE write,
sgi_heart_rearm_compare() computes the delta from current HEART_COUNT to
the compare value (handles 52-bit rollover) and arms the timer. Callback
fires HEART_INT_TIMER (ISR bit 50, level 3/IP6) and calls update_irq().

HEART also gains HEART_NUM_IRQS=64 GPIO input lines (one per ISR bit).
VMState bumped to version 2 with VMSTATE_TIMER_PTR_V and post_load hook.

### Phase 2: Glaccel Dumb Framebuffer

Replaced stub with functional dumb framebuffer:
- gfx_update callback DMAs from FB_BASE, converts pixel format at 60Hz
- FORMAT register (0x1C): 0=RGBA8888 (default), 1=RGB565
- STRIDE register (0x20): bytes per scanline (0 = width*bpp)
- GLACCEL_CMD_PROCESS triggers immediate update + IRQ
- VNC confirmed working (VNC server opens on port 5900)

### Phase 3: sgi-pvaudio Device

New sgi_pvaudio.c/h at 0x1F480400, IRQ -> HEART ISR bit 22.
Ring-buffer PCM audio registers: CTRL, STATUS, INTR, BUF_BASE/SIZE/HEAD/TAIL,
SAMPLE_RATE, CHANNELS, BITS.
Uses QEMU AudioBackend API (AUD_open_out/AUD_write), -audiodev configurable.

### Bonus: sgi_smp.c DEVICE_NATIVE_ENDIAN -> DEVICE_BIG_ENDIAN

### Bonus: sgi_hpc1.c/h Stub

The last commit introduced TYPE_SGI_HPC1 in sgi_indy.c without the
implementation files. Created stubs to allow compilation. IP20 (Indigo)
won't work at runtime but the build succeeds.

## Build Result

  cd qemu-sgi-repo/build-new
  ninja -j4  # Success - no errors
  ./qemu-system-mips64 -M octane -m 64
  # -> VNC server on :5900 (glaccel framebuffer active)

## Key Discoveries

1. HEART GPIO inputs: Added 64 GPIO input lines to HEART (one per ISR bit).
   sgi_heart_set_irq(n, level) sets/clears ISR bit n and updates CPU IRQ state.
   PV devices connect: sysbus_connect_irq(pvdev, 0, qdev_get_gpio_in(heart, bit))

2. pvaudio AudioBackend: QEMU 10.x uses AudioBackend* (not QEMUSoundCard).
   Include "qemu/audio.h", use DEFINE_AUDIO_PROPERTIES, AUD_backend_check.

3. sgi_hpc1.h: Added to sgi_indy.c in last commit but never created.
   Build required creating stubs.

## Next Steps (IRIX Drivers -- Phases 4-6)

Phase 4: pvnet IRIX driver
  - Probe MAC at 0xBF480240/0xBF480248
  - ifnet-based driver (ecif.c pattern)
  - Compile with MIPSpro on irix655-kern VM

Phase 5: pvfb IRIX driver
  - /dev/pvfb character device, mmap framebuffer
  - Program WIDTH/HEIGHT/FORMAT/FB_BASE, EXEC=PROCESS

Phase 6: pvaudio IRIX driver
  - /dev/pvaudio ring-buffer driver, /dev/audio wrapper
