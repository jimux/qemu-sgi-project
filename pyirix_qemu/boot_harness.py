"""Smart serial interaction engine for QEMU SGI emulation.

Provides QEMUSession with idle-timeout-based waiting, repeat detection,
auto-bail on fatal patterns, and snapshot support via HMP monitor.
"""

import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def _is_native_binary(path: Path) -> bool:
    """Return True if the binary is executable on the current OS (ELF on Linux, Mach-O on macOS)."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
        if sys.platform == "darwin":
            return magic[:4] in (b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
                                  b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
                                  b"\xca\xfe\xba\xbe")
        else:
            return magic == b"\x7fELF"
    except OSError:
        return False


def _find_qemu_bin():
    """Find the QEMU binary, checking all known build directories."""
    for subdir in ("build-mac", "build-linux", "build"):
        for name in ("qemu-system-mips64", "qemu-system-mips64-unsigned"):
            p = PROJECT_ROOT / "qemu" / subdir / name
            if p.exists() and _is_native_binary(p):
                return str(p)
    return "qemu-system-mips64"  # fall back to PATH

QEMU_BIN = _find_qemu_bin()
DEFAULT_PROM = str(PROJECT_ROOT / "PROM_library" / "bins" / "cpu" / "ip24"
                   / "Indy_ip24prom.070-9101-011.bin")

MACHINE_PROM_MAP = {
    "indy": ("ip24", "Indy_ip24prom.070-9101-011.bin"),
    "indigo2": ("ip22", None),
    "indigo2-r10k": ("ip28", None),
    "indigo2-r8k": ("ip26", None),
    "indigo": ("ip20", None),
}

DEFAULT_BAIL_PATTERNS = [
    r"PANIC",
    r"panic:",
    r"bus error",
    r"Bus Error",
    r"Unable to boot",
    r"not syncing",
    r"Kernel panic",
]

# Result namedtuple-like return from wait_for
class WaitResult:
    """Result from QEMUSession.wait_for()."""
    __slots__ = ("matched", "output", "bail_reason")

    def __init__(self, matched, output, bail_reason=None):
        self.matched = matched
        self.output = output
        self.bail_reason = bail_reason

    def __iter__(self):
        return iter((self.matched, self.output, self.bail_reason))

    def __repr__(self):
        return (f"WaitResult(matched={self.matched!r}, "
                f"output=<{len(self.output)} chars>, "
                f"bail_reason={self.bail_reason!r})")


class QEMUSession:
    """Launch and interact with a QEMU SGI machine via Unix sockets.

    Usage::

        with QEMUSession(scsi_drives=["disk.qcow2", "cd.img:cdrom"]) as q:
            q.wait_for("Option")
            q.send("2\\r")
            matched, output, bail = q.wait_for("Inst>", timeout=5, max_wait=300)
    """

    def __init__(self, machine="indy", ram_mb=64, prom=None,
                 scsi_drives=None, debug_flags=None, snapshot=None,
                 extra_args=None, bail_patterns=None, repeat_threshold=3,
                 debug_log_path=None, serial_log_path=None):
        self.machine = machine
        self.ram_mb = ram_mb
        self.prom = prom or self._resolve_prom(machine)
        self.scsi_drives = scsi_drives or []
        self.debug_flags = debug_flags
        self.snapshot = snapshot
        self.extra_args = extra_args or []
        self.bail_patterns = DEFAULT_BAIL_PATTERNS + (bail_patterns or [])
        self.repeat_threshold = repeat_threshold
        self.debug_log_path = debug_log_path
        self.serial_log_path = serial_log_path

        self.transcript = ""
        self.lines = []
        self.debug_log_content = ""  # Populated from debug_log_path after close
        self._proc = None
        self._serial_sock = None
        self._serial_log_file = None
        self._tmpdir = None
        self._serial_path = None
        self._monitor_path = None
        self._closed = False

    def __enter__(self):
        self._launch()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    @staticmethod
    def _resolve_prom(machine):
        """Find the default PROM for a given machine type."""
        if machine not in MACHINE_PROM_MAP:
            return DEFAULT_PROM
        subdir, filename = MACHINE_PROM_MAP[machine]
        if filename is None:
            # Find first .bin in the subdir
            prom_dir = PROJECT_ROOT / "PROM_library" / "bins" / "cpu" / subdir
            bins = sorted(prom_dir.glob("*.bin"))
            if bins:
                return str(bins[-1])
            return DEFAULT_PROM
        return str(PROJECT_ROOT / "PROM_library" / "bins" / "cpu" / subdir / filename)

    def _build_cmd(self):
        """Build QEMU command-line arguments."""
        cmd = [
            QEMU_BIN, "-M", self.machine,
            "-m", f"{self.ram_mb}M",
            "-bios", self.prom,
            "-display", "none",
            "-chardev", f"socket,id=ser0,path={self._serial_path},server=on,wait=on",
            "-serial", "chardev:ser0",
            "-monitor", f"unix:{self._monitor_path},server,nowait",
        ]

        if self.machine not in ("sgi-o2",):
            cmd.extend(["-global", "sgi-hpc3.autoload=false"])

        if self.debug_flags:
            cmd.extend(["-d", self.debug_flags])

        if self.debug_log_path:
            cmd.extend(["-D", self.debug_log_path])

        if self.snapshot:
            cmd.extend(["-loadvm", self.snapshot])

        # SCSI drives
        # Spec suffixes: ":cdrom" for CD-ROM media, ":ro" for read-only disk
        next_disk_id = 1
        next_cdrom_id = 4
        for drive_spec in self.scsi_drives:
            # Parse suffixes
            is_cdrom = False
            is_readonly = False
            drive_path = drive_spec
            for suffix in (":cdrom", ":ro"):
                if drive_path.endswith(suffix):
                    drive_path = drive_path[:-len(suffix)]
                    if suffix == ":cdrom":
                        is_cdrom = True
                        is_readonly = True  # CD-ROMs are always read-only
                    elif suffix == ":ro":
                        is_readonly = True

            if is_cdrom:
                scsi_id = next_cdrom_id
                next_cdrom_id += 1
            else:
                scsi_id = next_disk_id
                next_disk_id += 1

            if scsi_id > 7:
                raise ValueError(f"Too many SCSI drives (max 7 targets)")

            drive_file = Path(drive_path)
            if not drive_file.is_absolute():
                drive_file = Path("/workspace") / drive_path
            if not drive_file.exists():
                raise FileNotFoundError(f"SCSI drive image not found: {drive_path}")

            fmt = "qcow2" if drive_file.suffix == ".qcow2" else "raw"
            opts = f"if=scsi,bus=0,unit={scsi_id},file={drive_file},format={fmt}"
            if is_cdrom:
                opts += ",media=cdrom"
            if is_readonly:
                opts += ",readonly=on"
            opts += ",cache=writethrough,file.locking=off"
            cmd.extend(["-drive", opts])

        cmd.extend(self.extra_args)
        return cmd

    def _launch(self):
        """Start QEMU and connect sockets."""
        self._tmpdir = tempfile.mkdtemp(prefix="qemu_harness_")
        self._serial_path = os.path.join(self._tmpdir, "serial.sock")
        self._monitor_path = os.path.join(self._tmpdir, "monitor.sock")

        cmd = self._build_cmd()
        self._stderr_path = os.path.join(self._tmpdir, "stderr.log")
        self._stderr_file = open(self._stderr_path, "w")
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=self._stderr_file
        )

        # Connect serial socket with retry
        self._serial_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connected = False
        for _ in range(20):
            try:
                self._serial_sock.connect(self._serial_path)
                connected = True
                break
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(0.5)
        if not connected:
            self.close()
            raise ConnectionError("Could not connect to QEMU serial socket")

        if self.serial_log_path:
            self._serial_log_file = open(self.serial_log_path, "w", buffering=1)

        # Verify monitor socket exists (with retry).  We don't keep a
        # persistent connection — send_monitor() opens a fresh socket each
        # time so there's no readline echo state to deal with.
        mon_available = False
        for _ in range(20):
            if os.path.exists(self._monitor_path):
                mon_available = True
                break
            time.sleep(0.5)
        if not mon_available:
            self._monitor_path = None

    def wait_for(self, pattern, timeout=3, max_wait=120, bail_on=None):
        """Wait for regex pattern in serial output.

        Args:
            pattern: Regex pattern to match.
            timeout: Idle timeout — seconds since last byte received.
            max_wait: Absolute maximum wait time (safety net).
            bail_on: Extra patterns that trigger immediate abort.

        Returns:
            WaitResult(matched, output, bail_reason) — also unpacks as tuple.
        """
        if self._serial_sock is None:
            return WaitResult(False, "", "no serial connection")

        compiled = re.compile(pattern)
        bail_patterns = [re.compile(p) for p in self.bail_patterns]
        if bail_on:
            bail_patterns.extend(re.compile(p) for p in bail_on)

        accumulated = ""
        recent_lines = []
        deadline = time.time() + max_wait
        last_data_time = time.time()

        while True:
            now = time.time()
            # Check absolute deadline
            if now >= deadline:
                return WaitResult(False, accumulated, "max_wait exceeded")
            # Check idle timeout
            if now - last_data_time >= timeout:
                return WaitResult(False, accumulated, "idle timeout")

            # Calculate socket timeout: min of remaining idle, remaining max_wait, 0.5s
            sock_timeout = min(timeout - (now - last_data_time),
                               deadline - now, 0.5)
            if sock_timeout <= 0:
                continue

            self._serial_sock.settimeout(sock_timeout)
            try:
                data = self._serial_sock.recv(4096)
                if not data:
                    return WaitResult(False, accumulated, "connection closed")
                last_data_time = time.time()
                text = data.decode("latin-1", errors="replace")
                accumulated += text
                self.transcript += text
                if self._serial_log_file:
                    self._serial_log_file.write(text)

                # Update lines for repeat detection
                new_lines = text.split("\n")
                if new_lines:
                    # Merge partial line with previous
                    if recent_lines and not accumulated[:-len(text)].endswith("\n"):
                        recent_lines[-1] += new_lines[0]
                        new_lines = new_lines[1:]
                    for line in new_lines:
                        stripped = line.strip()
                        if stripped:
                            recent_lines.append(stripped)
                            self.lines.append(stripped)

                # Check for pattern match
                if compiled.search(accumulated):
                    return WaitResult(True, accumulated, None)

                # Check bail patterns
                for bp in bail_patterns:
                    if bp.search(accumulated):
                        return WaitResult(False, accumulated, f"bail: {bp.pattern}")

                # Check repeat detection
                if self.repeat_threshold > 0 and len(recent_lines) >= self.repeat_threshold:
                    tail = recent_lines[-self.repeat_threshold:]
                    if len(set(tail)) == 1 and tail[0]:
                        return WaitResult(False, accumulated,
                                          f"repeat ({self.repeat_threshold}x): {tail[0][:80]}")

            except socket.timeout:
                continue
            except OSError:
                return WaitResult(False, accumulated, "socket error")

    def send(self, text):
        """Send text to serial console. Escape sequences (\\r, \\n) are processed."""
        if self._serial_sock is None:
            return
        send_bytes = text.encode("latin-1").decode("unicode_escape").encode("latin-1")
        self._serial_sock.sendall(send_bytes)
        self.transcript += f"\n[SENT: {text!r}]\n"
        if self._serial_log_file:
            self._serial_log_file.write(f"\n[SENT: {text!r}]\n")

    def send_monitor(self, cmd, timeout=10):
        """Send HMP monitor command and return response.

        Opens a fresh socket connection for each command to avoid
        readline echo state accumulation on persistent connections.
        Waits for the ``(qemu)`` prompt to confirm command completion.
        """
        if self._monitor_path is None:
            raise ConnectionError("Monitor socket path not set")

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect(self._monitor_path)
        except (FileNotFoundError, ConnectionRefusedError) as e:
            sock.close()
            raise ConnectionError(f"Cannot connect to monitor: {e}")

        # Drain the banner/prompt from the fresh connection
        try:
            sock.recv(4096)
        except socket.timeout:
            pass

        sock.sendall(f"{cmd}\n".encode())

        # Read until we see the (qemu) prompt
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            sock.settimeout(max(0.1, min(remaining, 2)))
            try:
                data = sock.recv(65536)
                if not data:
                    break
                buf += data
                if b"(qemu)" in buf:
                    break
            except socket.timeout:
                continue
            except OSError:
                break

        sock.close()

        resp = buf.decode("latin-1", errors="replace")
        # Strip (qemu) prompts
        resp = resp.replace("(qemu)", "").strip()
        return resp

    def change_media(self, scsi_unit, image_path):
        """Swap CD-ROM media on a SCSI unit via QEMU monitor.

        Args:
            scsi_unit: SCSI target ID (e.g. 5 for the second CD-ROM).
            image_path: Path to the new disc image file.

        Returns:
            Monitor response text.

        Raises:
            RuntimeError: If the change command fails.
        """
        image_path = str(Path(image_path).resolve())
        # Quote the path — HMP splits on spaces without quotes
        resp = self.send_monitor(f'change scsi0-cd{scsi_unit} "{image_path}"')
        if "error" in resp.lower() or "could not open" in resp.lower():
            raise RuntimeError(f"CD swap failed: {resp.strip()}")
        return resp

    def save_snapshot(self, name):
        """Save VM snapshot. Requires at least one qcow2 SCSI drive."""
        if self._monitor_path is None:
            raise ConnectionError("Monitor socket not available")
        # savevm can take a while for large RAM — use generous timeout
        resp = self.send_monitor(f"savevm {name}", timeout=60)
        if "Error" in resp or "error" in resp:
            raise RuntimeError(f"savevm failed: {resp}")
        # Verify with retries — the monitor's readline echoes the "info snapshots"
        # command before outputting the table, which can cause the (qemu) prompt
        # to appear early and truncate the response.  Retry until we see the
        # snapshot name or the output is clearly a real table (> 80 chars).
        for attempt in range(3):
            time.sleep(1.0 + attempt)
            info = self.send_monitor("info snapshots", timeout=15)
            # Strip all ANSI escape sequences and control characters
            clean_info = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', info)
            clean_info = re.sub(r'[\x00-\x1f]', '', clean_info)
            if name in clean_info:
                return info
        raise RuntimeError(f"Snapshot '{name}' not found after savevm. "
                           f"Ensure at least one qcow2 drive is attached.\n"
                           f"info snapshots output: {info}")
        return info

    def collect(self, duration=3):
        """Collect serial output for a fixed duration. Returns collected text."""
        if self._serial_sock is None:
            return ""
        collected = ""
        end_time = time.time() + duration
        while time.time() < end_time:
            remaining = end_time - time.time()
            if remaining <= 0:
                break
            self._serial_sock.settimeout(min(remaining, 0.5))
            try:
                data = self._serial_sock.recv(4096)
                if data:
                    text = data.decode("latin-1", errors="replace")
                    collected += text
                    self.transcript += text
                    if self._serial_log_file:
                        self._serial_log_file.write(text)
                    self.lines.extend(
                        l.strip() for l in text.split("\n") if l.strip()
                    )
            except socket.timeout:
                continue
            except OSError:
                break
        return collected

    def close(self):
        """Shut down QEMU and clean up."""
        if self._closed:
            return
        self._closed = True

        # Read debug log before cleanup
        if self.debug_log_path:
            try:
                with open(self.debug_log_path, 'r', errors='replace') as f:
                    self.debug_log_content = f.read()
            except (OSError, IOError):
                self.debug_log_content = ""

        # Quit via monitor (fresh connection)
        if self._monitor_path:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(3)
                sock.connect(self._monitor_path)
                sock.sendall(b"quit\n")
                sock.close()
            except Exception:
                pass

            # Wait for QEMU to exit gracefully (flush qcow2 metadata)
            if self._proc:
                try:
                    self._proc.wait(timeout=10)
                    self._proc = None  # Exited cleanly, skip kill
                except subprocess.TimeoutExpired:
                    pass  # Fall through to kill

        # Close serial log file
        if self._serial_log_file:
            try:
                self._serial_log_file.close()
            except Exception:
                pass
            self._serial_log_file = None

        # Close serial
        if self._serial_sock:
            try:
                self._serial_sock.close()
            except Exception:
                pass
            self._serial_sock = None

        # Read stderr before killing
        if hasattr(self, '_stderr_file') and self._stderr_file:
            try:
                self._stderr_file.close()
            except Exception:
                pass
        if hasattr(self, '_stderr_path') and self._stderr_path:
            try:
                with open(self._stderr_path, 'r', errors='replace') as f:
                    self.stderr_content = f.read()
                if self.stderr_content.strip():
                    print(f"[QEMU stderr] {self.stderr_content[:1000]}",
                          flush=True)
            except (OSError, IOError):
                pass

        # Kill process (if quit didn't work)
        if self._proc:
            try:
                self._proc.kill()
                self._proc.wait(timeout=5)
            except Exception:
                pass
            self._proc = None

        # Clean up socket files
        if self._tmpdir:
            for p in [self._serial_path, self._monitor_path]:
                if p:
                    try:
                        os.unlink(p)
                    except Exception:
                        pass
            try:
                os.rmdir(self._tmpdir)
            except Exception:
                pass
            self._tmpdir = None
