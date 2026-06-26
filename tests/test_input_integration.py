"""
Input path integration test.

Validates the Z85C30 **serial console input** path end-to-end: with no boot disk
the PROM auto-boots, fails, and blocks at 'press any key to continue:'. Writing a
byte to the serial line must unblock the PROM and advance it to the System
Maintenance menu — proving received serial characters reach the PROM's input
handler. (This is the headless input path; PS/2 keyboard input drives the
*graphical* console and is exercised by the desktop-eyes tooling instead — a
serial-console PROM does not read PS/2.)

SLOW (PROM boot, ~35-45s).
"""

import os
import select
import time

import pytest

from helpers.qemu_runner import SGIQemuRunner


def _drain(proc, seconds):
    """Read whatever serial output is available within `seconds`."""
    out = b""
    end = time.time() + seconds
    while time.time() < end:
        ready, _, _ = select.select([proc.stdout], [], [], 0.3)
        if not ready:
            continue
        chunk = os.read(proc.stdout.fileno(), 4096)
        if not chunk:
            break
        out += chunk
    return out.decode("utf-8", errors="replace")


@pytest.mark.slow
class TestSendkeyPROM:

    def test_serial_input_advances_prom_at_keypress_prompt(self):
        """The PROM blocks at 'press any key to continue:'; a byte on the serial
        line must unblock it and reach the System Maintenance menu — proving the
        Z85C30 RX → PROM input path works."""
        runner = SGIQemuRunner()
        try:
            runner.boot_prom_background(
                timeout=60,
                wait_for=r"press any key to continue|System Maintenance|Option\?")
            text = ""
            # PROM is blocked on serial input; feed it bytes and read the result.
            for _ in range(6):
                try:
                    runner._process.stdin.write(b"\r")
                    runner._process.stdin.flush()
                except Exception:
                    pass
                text += _drain(runner._process, 1.5)
                if "System Maintenance" in text or "Option?" in text:
                    break
            text += _drain(runner._process, 4.0)
            assert "System Maintenance" in text or "Option?" in text, (
                "serial input did not advance the PROM past 'press any key' "
                f"(Z85C30 RX path broken?); got: {text[-400:]!r}")
        finally:
            runner.cleanup()
