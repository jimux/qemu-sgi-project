"""
QEMU launch helper for SGI Indy integration tests.

Launches QEMU with the SGI Indy configuration, captures serial output,
and provides access to trace log files written during boot.
"""

import os
import platform as _platform
import select
import signal
import socket
import subprocess
import tempfile
import time

# Derive all paths relative to the project root (two levels up from this file)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BUILD_SUBDIR = "build-mac" if _platform.system() == "Darwin" else "build"
# The project migrated from qemu/ to qemu-sgi-repo/build-linux; prefer the
# current build dir and fall back to the legacy layout for old checkouts.
_BUILD_DIR = os.path.join(_PROJECT_ROOT, "qemu-sgi-repo", "build-linux")
if not os.path.isdir(_BUILD_DIR):
    _BUILD_DIR = os.path.join(_PROJECT_ROOT, "qemu", _BUILD_SUBDIR)

# Find the QEMU binary (prefer unsigned on macOS where codesigning creates it)
def _find_qemu_bin():
    for name in ("qemu-system-mips64", "qemu-system-mips64-unsigned"):
        p = os.path.join(_BUILD_DIR, name)
        if os.path.exists(p):
            return p
    return os.path.join(_BUILD_DIR, "qemu-system-mips64")

DEFAULT_QEMU_BIN = _find_qemu_bin()
DEFAULT_PROM = None  # Will use QEMU's default PROM search
DEFAULT_NVRAM = os.path.join(_PROJECT_ROOT, "sgi_indy_nvram.bin")
PROM_DIR = os.path.join(_PROJECT_ROOT, "PROM_library", "bins", "cpu", "ip24")


def find_prom():
    """Find an IP24 PROM image."""
    if os.path.isdir(PROM_DIR):
        for f in sorted(os.listdir(PROM_DIR)):
            path = os.path.join(PROM_DIR, f)
            if os.path.isfile(path) and os.path.getsize(path) in (
                0x40000, 0x80000,  # 256KB or 512KB
            ):
                return path
    return None


class SGIQemuRunner:
    """Launch QEMU with SGI Indy configuration, capture serial output."""

    def __init__(self, qemu_bin=None, prom_path=None, nvram_path=None):
        self.qemu_bin = qemu_bin or DEFAULT_QEMU_BIN
        self.prom_path = prom_path or find_prom()
        self.nvram_path = nvram_path or DEFAULT_NVRAM
        self._process = None
        self._serial_output = ""

    def _base_args(self, ram_mb=64):
        """Build base QEMU command line arguments.

        Note: 64MB is the minimum working RAM. 32MB causes PROM to hang.
        """
        args = [
            self.qemu_bin,
            "-M", "indy",
            "-m", str(ram_mb),
            "-nographic",
            "-serial", "stdio",
            "-monitor", "none",
        ]
        if self.prom_path:
            args.extend(["-bios", self.prom_path])
        return args

    def boot_prom(self, timeout=45, ram_mb=64, extra_args=None):
        """Boot to PROM menu, return serial output.

        PROM boot takes ~30.5s minimum (escape countdown) with no SCSI
        devices, ~90s with disk, ~120s with disk + CD-ROM. The default
        timeout of 45s covers the no-device case.

        Note: -icount shift=0,sleep=off has no effect on PROM boot
        (PROM polls, never WAITs).

        Args:
            timeout: Maximum seconds to wait for boot (default 45)
            ram_mb: RAM size in MB (minimum 64; 32MB hangs)
            extra_args: Additional QEMU arguments

        Returns:
            Serial output as string
        """
        args = self._base_args(ram_mb=ram_mb)
        if extra_args:
            args.extend(extra_args)

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=_BUILD_DIR,
            )
            self._serial_output = result.stdout + result.stderr
        except subprocess.TimeoutExpired as e:
            self._serial_output = ""
            if e.stdout:
                self._serial_output += e.stdout.decode("utf-8", errors="replace")
            if e.stderr:
                self._serial_output += e.stderr.decode("utf-8", errors="replace")

        return self._serial_output

    def boot_miniroot(self, disk_img, cdrom_img=None, timeout=300,
                      ram_mb=256, extra_args=None):
        """Boot miniroot kernel, return serial output.

        Args:
            disk_img: Path to SCSI disk image
            cdrom_img: Path to CD-ROM image (optional)
            timeout: Maximum seconds to wait
            ram_mb: RAM size in MB
            extra_args: Additional QEMU arguments

        Returns:
            Serial output as string
        """
        args = self._base_args(ram_mb=ram_mb)

        # Add SCSI drives using -drive if=scsi syntax.
        # Note: -device scsi-hd fails with "No 'SCSI' bus found" because
        # the WD33C93's bus is not QOM-discoverable. Legacy -drive if=scsi
        # uses scsi_bus_legacy_handle_cmdline() which finds it.
        args.extend([
            "-drive", f"if=scsi,file={disk_img},format=raw",
        ])
        if cdrom_img:
            args.extend([
                "-drive", f"if=scsi,file={cdrom_img},format=raw,media=cdrom,readonly=on",
            ])

        if extra_args:
            args.extend(extra_args)

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=_BUILD_DIR,
            )
            self._serial_output = result.stdout + result.stderr
        except subprocess.TimeoutExpired as e:
            self._serial_output = ""
            if e.stdout:
                self._serial_output += e.stdout.decode("utf-8", errors="replace")
            if e.stderr:
                self._serial_output += e.stderr.decode("utf-8", errors="replace")

        return self._serial_output

    def run_bare_metal(self, binary_path, timeout=10, extra_args=None,
                       ram_mb=64):
        """Run a bare-metal binary as PROM replacement, capture serial output.

        Args:
            binary_path: Path to raw binary (loaded at 0xBFC00000)
            timeout: Maximum seconds to wait
            extra_args: Additional QEMU arguments
            ram_mb: RAM size in MB

        Returns:
            Serial output as string
        """
        args = [
            self.qemu_bin,
            "-M", "indy",
            "-m", str(ram_mb),
            "-nographic",
            "-serial", "stdio",
            "-monitor", "none",
            "-bios", binary_path,
        ]
        if extra_args:
            args.extend(extra_args)

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=_BUILD_DIR,
            )
            self._serial_output = result.stdout + result.stderr
        except subprocess.TimeoutExpired as e:
            self._serial_output = ""
            if e.stdout:
                self._serial_output += e.stdout.decode("utf-8", errors="replace")
            if e.stderr:
                self._serial_output += e.stderr.decode("utf-8", errors="replace")

        return self._serial_output

    @property
    def serial_output(self):
        """Last captured serial output."""
        return self._serial_output

    @staticmethod
    def get_trace_file(name):
        """Return path to a trace log file."""
        return f"/tmp/{name}"

    @staticmethod
    def trace_exists(name):
        """Check if a trace log file exists."""
        return os.path.exists(f"/tmp/{name}")

    @staticmethod
    def clean_traces():
        """Remove all trace log files from /tmp."""
        for name in [
            "cp0_timer_trace.log",
            "map_mask_raw.log",
            "scc_tx_timer_trace.log",
            "scc_wr1_trace.log",
        ]:
            path = f"/tmp/{name}"
            if os.path.exists(path):
                os.unlink(path)

    def boot_prom_background(self, timeout=45, ram_mb=64, extra_args=None,
                             wait_for="Option\\?|System Maintenance"):
        """Boot PROM in background, wait for menu, leave QEMU running.

        Unlike boot_prom(), this launches QEMU with Popen so it stays
        running while we interact via monitor commands (e.g., fb-dump).

        Args:
            timeout: Maximum seconds to wait for PROM menu
            ram_mb: RAM size in MB (minimum 64)
            extra_args: Additional QEMU arguments
            wait_for: Regex pattern to wait for in serial output

        Raises:
            TimeoutError: If PROM menu not reached within timeout
            FileNotFoundError: If QEMU binary or PROM not found
        """
        import re

        self._monitor_sock = tempfile.mktemp(suffix='.sock')

        args = [
            self.qemu_bin,
            "-M", "indy",
            "-m", str(ram_mb),
            "-nographic",
            "-serial", "stdio",
            "-monitor", f"unix:{self._monitor_sock},server,nowait",
        ]
        if self.prom_path:
            args.extend(["-bios", self.prom_path])
        if extra_args:
            args.extend(extra_args)

        self._process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=_BUILD_DIR,
        )

        # Read stdout until we see the PROM menu or timeout
        deadline = time.monotonic() + timeout
        buf = b""
        pattern = re.compile(wait_for.encode())

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select(
                [self._process.stdout], [], [], min(remaining, 1.0)
            )
            if ready:
                chunk = os.read(self._process.stdout.fileno(), 4096)
                if not chunk:
                    break
                buf += chunk
                if pattern.search(buf):
                    self._serial_output = buf.decode("utf-8", errors="replace")
                    return self._serial_output

        self._serial_output = buf.decode("utf-8", errors="replace")
        self.cleanup()
        raise TimeoutError(
            f"PROM menu not reached within {timeout}s. "
            f"Output so far ({len(buf)} bytes): {self._serial_output[:500]}"
        )

    def monitor_command(self, cmd):
        """Send command to QEMU monitor via Unix socket, return response.

        Args:
            cmd: Monitor command string (e.g., 'info version')

        Returns:
            Response string from QEMU monitor
        """
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(self._monitor_sock)

        # Read initial prompt/banner
        banner = b""
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                banner += chunk
                if b"(qemu)" in banner:
                    break
            except socket.timeout:
                break

        # Send command
        s.sendall((cmd + "\n").encode())

        # Read response until next prompt
        response = b""
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                response += chunk
                if b"(qemu)" in response:
                    break
            except socket.timeout:
                break

        s.close()
        return response.decode("utf-8", errors="replace")

    def query_newport(self, subsystem="all"):
        """Query Newport diagnostic property via QOM.

        Args:
            subsystem: One of 'all', 'cmap', 'xmap', 'vc2', 'rex3', 'dcb'

        Returns:
            Diagnostic text string
        """
        return self.monitor_command(
            f'qom-get /machine/newport diag-{subsystem}')

    def sendkey(self, keys):
        """Send keyboard input via QEMU monitor.

        Args:
            keys: Key specification (e.g., 'a', 'ret', 'ctrl-alt-delete')

        Returns:
            Monitor response string
        """
        return self.monitor_command(f'sendkey {keys}')

    def capture_framebuffer(self, output_path=None):
        """Trigger fb-dump via QOM property and return PIL Image.

        Args:
            output_path: Path for PPM output file (auto-generated if None)

        Returns:
            PIL.Image.Image of the framebuffer
        """
        from PIL import Image

        if output_path is None:
            output_path = tempfile.mktemp(suffix='.ppm')

        self.monitor_command(
            f'qom-set /machine/newport fb-dump {output_path}'
        )

        # Give QEMU a moment to flush the file
        time.sleep(0.5)

        img = Image.open(output_path)
        img.load()  # Force read before file might be deleted
        return img

    def cleanup(self):
        """Kill the background QEMU process and clean up."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                self._process.kill()
                self._process.wait(timeout=5)
            self._process = None

        if hasattr(self, '_monitor_sock') and self._monitor_sock:
            try:
                os.unlink(self._monitor_sock)
            except OSError:
                pass
            self._monitor_sock = None
