"""
Input path integration tests.

Tests that keyboard input injected via the QEMU monitor sendkey command
flows through the full path: monitor → PS/2 → 8042 → INT3 → guest handler.

These tests are SLOW (require PROM boot, ~35s).
"""

import os
import time
import pytest

from helpers.qemu_runner import SGIQemuRunner


@pytest.mark.slow
class TestSendkeyPROM:

    def test_sendkey_selects_menu_option(self):
        """Send '5' to select Enter Command Monitor from the PROM menu.

        If sendkey works, the PROM should respond with the command monitor
        prompt '>>'. This tests the full input path: QEMU monitor sendkey →
        PS/2 keyboard → 8042 controller → INT3 interrupt → PROM handler →
        serial echo.
        """
        runner = SGIQemuRunner()
        try:
            runner.boot_prom_background(timeout=45)
            # PROM is at "Option?" menu. Send "5" (Enter Command Monitor)
            runner.sendkey('5')
            time.sleep(2)
            # Read any new serial output
            import select
            output = b""
            while True:
                ready, _, _ = select.select(
                    [runner._process.stdout], [], [], 0.5)
                if not ready:
                    break
                chunk = os.read(runner._process.stdout.fileno(), 4096)
                if not chunk:
                    break
                output += chunk
            text = output.decode("utf-8", errors="replace")
            assert '>>' in text or 'Command Monitor' in text, (
                f"Expected command monitor prompt, got: {text[:200]}"
            )
        finally:
            runner.cleanup()
