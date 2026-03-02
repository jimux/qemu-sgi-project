# Keyboard/Mouse Input — Implementation Notes

## Summary

PS/2 keyboard and mouse input is functional in the SGI Indy emulator.
The entire input stack was already implemented — it just needed testing
and MCP tool enhancements for convenient use.

## Architecture

### Data Path (keyboard)

```
newport_sendkey (MCP) → HMP "sendkey" → QEMU input core
→ ps2_keyboard_event() → scancode queued (set 3)
→ PS2_DEVICE_IRQ asserted
→ sgi_hpc3_ps2_kbd_irq() → INT3_MAP_KBDMS (0x10) in int3_map_status
→ cascade: map_status & map_mask0 → INT3_LOCAL0_MAPPABLE0 (0x80)
→ CPU interrupt (IP2)
→ IRIX pckm_intr() → read data from 0x1fbd9843 → process scancode
→ shmiq → X server → xdm login field
```

### Data Path (mouse)

```
newport_mouse (MCP) → HMP "mouse_move"/"mouse_button" → QEMU input core
→ ps2_mouse_event() → 3-byte packet queued
→ PS2_DEVICE_IRQ asserted
→ sgi_hpc3_ps2_mouse_irq() → INT3_MAP_KBDMS (0x10)
→ same cascade as keyboard
→ IRIX pckm_intr() → read data from 0x1fbd9843 (with MOUSE_OBF bit set)
→ shmiq → X server → cursor movement / button events
```

### Key Components

| Component | File | Lines |
|-----------|------|-------|
| PS/2 keyboard/mouse | `qemu/hw/input/ps2.c` | QEMU core |
| 8042 controller | `qemu/hw/misc/sgi_hpc3.c` | 573-860 |
| IRQ routing | `qemu/hw/misc/sgi_hpc3.c` | 685-700 |
| Status register | `qemu/hw/misc/sgi_hpc3.c` | 650-674 |
| newport_sendkey | `sgi_prom_mcp/server.py` | ~4199 |
| newport_mouse | `sgi_prom_mcp/server.py` | ~4290 |

## 8042 Controller Details

The 8042 PS/2 controller is embedded in the IOC2 ASIC. IRIX accesses it via
HPC3 peripheral space:

- **0x1fbd9843** — Data port (read/write)
- **0x1fbd9847** — Status/command port (read status, write commands)

### Status Register (read from 0x1fbd9847)

The IRIX pckm interrupt handler checks `(status & (SR_MSFULL|SR_OBF))`:
- `0x21` → mouse data pending → read from ps2mouse queue
- `0x01` → keyboard data pending → read from ps2kbd queue

### IRQ Gating

The 8042 command byte controls interrupt delivery:
- Bit 0 (`KBD_MODE_KBD_INT`): Keyboard data generates IRQ
- Bit 1 (`KBD_MODE_MOUSE_INT`): Mouse data generates IRQ

IRIX's `pckm_reinit_lock()` clears these bits during polled initialization
while holding `pckm_mutex`. Without gating, an interrupt during init would
cause `pckm_intr()` to deadlock on the mutex.

## MCP Tool Enhancements

### `newport_sendkey` — text parameter

The `text` parameter converts a string into a sequence of `sendkey` HMP
commands, eliminating the need to call the tool once per character.

Supported characters:
- `a-z` → `sendkey {letter}`
- `A-Z` → `sendkey shift-{letter}`
- `0-9` → `sendkey {digit}`
- Space → `sendkey spc`
- Enter/newline → `sendkey ret`
- Tab → `sendkey tab`
- Common punctuation: `.`→`dot`, `,`→`comma`, `/`→`slash`, etc.
- Shifted punctuation: `!`→`shift-1`, `@`→`shift-2`, etc.

Example: `newport_sendkey(session_id="s1", text="root\n")` types "root"
followed by Enter.

The `delay_ms` parameter (default 100) controls inter-keystroke delay.

### `newport_mouse`

Sends relative mouse movement and/or button state:
- `dx`, `dy` — relative pixel movement
- `buttons` — button bitmask (1=left, 2=middle, 4=right)

## IRIX Driver Details

The `pckm` driver (PC Keyboard/Mouse) handles both PS/2 devices:
- Detected at boot: `pckm0: keyboard (id=83)`
- Interrupt handler: `pckm_intr()` reads status to determine device,
  reads data byte, dispatches through shmiq (shared memory input queue)
- The shmiq subsystem delivers events to `/dev/input/keyboard` and
  `/dev/input/mouse`, which the X server reads via `/dev/shmiq`

## Trace Events

For debugging input issues:
```
debug_flags="trace:sgi_hpc3_kbd_*,trace:sgi_hpc3_int3*"
```

Key events to look for:
- `sgi_hpc3_kbd_cmd` — 8042 controller commands
- `sgi_hpc3_kbd_data_read` — data reads (value + source device)
- `sgi_hpc3_kbd_irq` — IRQ level changes and map status
- `sgi_hpc3_kbd_status` — status register reads
