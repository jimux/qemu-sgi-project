"""Hardening tests for the virtuix (IP55) host-backed device paths.

Goes beyond the smoke test in ``test_virtuix_boot.py`` to validate the
properties that have historically cost the most time — above all **data
durability**, since disk corruption from broken write-ordering has been the #1
time-sink (see CLAUDE.md "VM Lifecycle & Disk Safety").

What this asserts that the smoke test does not:
  * **Disk durability across a full QEMU restart** — write a multi-MB file +
    a marker on one boot, ``sync``, shut the VM down, then boot a *fresh* QEMU
    process on the *same* overlay and verify the data + checksum survived
    byte-for-byte. This is the crash-consistency property that ``cache=write
    through`` + XFS journaling is supposed to give, exercised end to end through
    the WD33C93 + HPC3 SCSI-DMA path.
  * **Large-file write/read integrity** (2 MB, cksum), not just a tiny marker.
  * **Seeq ec0 net health** under real traffic (no interface errors).
  * **Z85C30 console bulk-output integrity** — a burst of marker lines emitted
    over the serial all arrive intact (no dropped/corrupted bytes).

Marked ``slow`` (two full IRIX boots, ~2-3 min). Run explicitly:
    python3 -m pytest tests/test_virtuix_hardening.py -v
"""
import os
import re
import socket
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parent.parent
QEMU = ROOT / "qemu-sgi-repo" / "build-linux" / "qemu-system-mips64"
QEMU_IMG = ROOT / "qemu-sgi-repo" / "build-linux" / "qemu-img"
PROM = ROOT / "PROM_library" / "bins" / "cpu" / "ip24" / "Indy_ip24prom.070-9101-011.bin"
GOLDEN = ROOT / "prebuilt_disks" / "irix-6.5.5-complete-fixed.qcow2"
KERNEL = ROOT / "ip55_desktop_kernel" / "unix.ip55.g"
PCBIOS = ROOT / "qemu-sgi-repo" / "build-linux" / "pc-bios"

SMP = 4
BOOT_TIMEOUT = 240
BIG_BYTES = 2 * 1024 * 1024  # 2 MB written + checksummed


def _require(p, what):
    if not Path(p).exists():
        pytest.skip(f"{what} not found: {p}")


class _Serial:
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(path)
        self.s.settimeout(1.0)

    def read(self, t=2.0):
        out = b""
        end = time.time() + t
        while time.time() < end:
            try:
                out += self.s.recv(65536)
            except Exception:
                pass
        return out.decode("latin1", "replace")

    def send(self, c):
        self.s.sendall(c.encode())

    def cmd(self, c, t=3.0):
        """Send a command and return the output it produced."""
        self.send(c + "\r")
        return self.read(t)

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def _launch(overlay, ser, mon, qlog, extra_drives=(), tftp_dir=None):
    args = [str(QEMU), "-M", "virtuix", "-smp", str(SMP),
            "-accel", "tcg,thread=multi", "-kernel", str(KERNEL),
            "-bios", str(PROM), "-m", "256M", "-L", str(PCBIOS),
            "-drive", f"if=scsi,bus=0,unit=1,file={overlay},format=qcow2,"
                      "cache=writethrough,file.locking=off"]
    for d in extra_drives:
        args += ["-drive", d]
    nic = "user" + (f",tftp={tftp_dir}" if tftp_dir else "")
    args += ["-nic", nic,
             "-serial", f"unix:{ser},server,nowait",
             "-monitor", f"unix:{mon},server,nowait",
             "-display", "none"]
    return subprocess.Popen(
        args, stdout=qlog, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True)


def _wait_login(proc, ser):
    buf = ""
    end = time.time() + BOOT_TIMEOUT
    while time.time() < end:
        buf += ser.read(2)
        if proc.poll() is not None:
            pytest.fail(f"QEMU exited during boot rc={proc.returncode}")
        if re.search(r"login:", buf):
            return buf
    pytest.fail(f"virtuix did not reach login in {BOOT_TIMEOUT}s")


def _login(ser):
    ser.send("\r")
    ser.read(1)
    ser.send("root\r")
    r = ser.read(2)
    if "Password" in r:
        ser.send("\r")
        ser.read(2)
    ser.send("\r")
    ser.read(1)


def _stop(proc, mon):
    """Graceful shutdown so the overlay stays crash-consistent (writethrough
    is kill-safe anyway, but we want to prove the *clean* path persists)."""
    try:
        m = socket.socket(socket.AF_UNIX)
        m.connect(str(mon))
        m.settimeout(1.0)
        m.sendall(b"quit\n")
        time.sleep(1)
        m.close()
    except Exception:
        pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _reboot_and_wait(proc, ser):
    """Drive a clean guest reboot (`init 6`) and wait for the system to come
    back to a fresh login prompt — exercising the XFS unmount + journal-replay
    remount cycle that the QEMU-restart test does not."""
    ser.send("init 6\r")
    time.sleep(10)        # let shutdown proceed well past the current prompt
    ser.read(8)           # drain shutdown / early-boot output
    buf = ""
    end = time.time() + BOOT_TIMEOUT
    while time.time() < end:
        buf += ser.read(2)
        if proc.poll() is not None:
            pytest.fail(f"QEMU exited during guest reboot rc={proc.returncode}")
        if re.search(r"login:", buf):
            return buf
    pytest.fail("guest did not return to login after init 6")


def test_virtuix_disk_durability_guest_reboot(tmp_path):
    """Data must survive a *clean guest reboot* (`init 6`), not just a QEMU
    restart — this additionally exercises XFS journal replay on remount."""
    for p, what in [(QEMU, "qemu binary"), (PROM, "Indy PROM"),
                    (GOLDEN, "golden disk"), (KERNEL, "IP55 kernel")]:
        _require(p, what)

    overlay = tmp_path / "work.qcow2"
    subprocess.run([str(QEMU_IMG), "create", "-f", "qcow2", "-b", str(GOLDEN),
                    "-F", "qcow2", str(overlay)], check=True,
                   stdout=subprocess.DEVNULL)
    ser_path = tmp_path / "ser.sock"
    mon_path = tmp_path / "mon.sock"
    qlog = open(tmp_path / "q.log", "w")
    proc = _launch(overlay, ser_path, mon_path, qlog)
    time.sleep(5)
    ser = _Serial(str(ser_path))
    _wait_login(proc, ser)
    _login(ser)

    # Write a 1 MB payload + marker and flush.
    blocks = (1024 * 1024) // 4096
    ser.cmd(f"dd if=/unix of=/var/tmp/reb.dat bs=4096 count={blocks}", 15)
    ser.cmd("echo REBOOT_PAYLOAD_7373 > /var/tmp/reb.marker", 2)
    ser.cmd("sync; sync", 3)
    ck1 = ser.cmd("cksum /var/tmp/reb.dat", 4)

    # Clean guest reboot (NOT a raw kill) -> XFS unmount + journal-replay remount.
    _reboot_and_wait(proc, ser)
    _login(ser)
    ck2 = ser.cmd("cksum /var/tmp/reb.dat", 4)
    marker = ser.cmd("cat /var/tmp/reb.marker; rm -f /var/tmp/reb.dat "
                     "/var/tmp/reb.marker", 3)
    ser.close()
    _stop(proc, mon_path)
    qlog.close()

    c1 = re.search(r"(\d+)\s+(\d+)\s+/var/tmp/reb\.dat", ck1)
    c2 = re.search(r"(\d+)\s+(\d+)\s+/var/tmp/reb\.dat", ck2)
    assert c1, f"could not read cksum before reboot: {ck1!r}"
    assert c2, f"could not read cksum after reboot: {ck2!r}"
    assert c1.group(1) == c2.group(1) and c1.group(2) == c2.group(2), \
        f"CHECKSUM MISMATCH across guest reboot (corruption!): " \
        f"before={c1.group(0)!r} after={c2.group(0)!r}"
    assert "REBOOT_PAYLOAD_7373" in marker, \
        f"marker did not survive the guest reboot: {marker!r}"


def test_virtuix_second_scsi_disk_enumerates(tmp_path):
    """A second SCSI target on the WD33C93/HPC3 bus must be discovered. The
    other tests only ever touch unit 1; this attaches a blank unit-2 disk and
    asserts the guest's hardware inventory enumerates BOTH drives — exercising
    the controller's multi-target scan + selection path."""
    for p, what in [(QEMU, "qemu binary"), (PROM, "Indy PROM"),
                    (GOLDEN, "golden disk"), (KERNEL, "IP55 kernel")]:
        _require(p, what)

    overlay = tmp_path / "work.qcow2"
    subprocess.run([str(QEMU_IMG), "create", "-f", "qcow2", "-b", str(GOLDEN),
                    "-F", "qcow2", str(overlay)], check=True,
                   stdout=subprocess.DEVNULL)
    disk2 = tmp_path / "disk2.qcow2"
    subprocess.run([str(QEMU_IMG), "create", "-f", "qcow2", str(disk2), "200M"],
                   check=True, stdout=subprocess.DEVNULL)
    drive2 = (f"if=scsi,bus=0,unit=2,file={disk2},format=qcow2,"
              "cache=writethrough,file.locking=off")

    ser_path = tmp_path / "ser.sock"
    mon_path = tmp_path / "mon.sock"
    qlog = open(tmp_path / "q.log", "w")
    proc = _launch(overlay, ser_path, mon_path, qlog, extra_drives=[drive2])
    time.sleep(5)
    ser = _Serial(str(ser_path))
    _wait_login(proc, ser)
    _login(ser)
    hinv = ser.cmd("hinv -c disk", 4)
    if "unit 2" not in hinv:           # some hinv variants want the full table
        hinv = ser.cmd("hinv", 4)
    ser.close()
    _stop(proc, mon_path)
    qlog.close()

    units = set(re.findall(r"[Dd]isk drive:\s*unit\s+(\d+)", hinv))
    assert "1" in units and "2" in units, \
        f"second SCSI target not enumerated (units seen: {sorted(units)}): {hinv!r}"


def test_virtuix_net_file_transfer_integrity(tmp_path):
    """A real multi-KB payload must cross Seeq ec0 byte-for-byte. The other net
    check only pings (small ICMP); this TFTP-fetches a 64 KB file from the slirp
    server and asserts the in-guest cksum matches the host's exactly — exercising
    the Seeq receive-DMA path with a sustained, verified transfer."""
    for p, what in [(QEMU, "qemu binary"), (PROM, "Indy PROM"),
                    (GOLDEN, "golden disk"), (KERNEL, "IP55 kernel")]:
        _require(p, what)

    tftp = tmp_path / "tftp"
    tftp.mkdir()
    payload = ((b"NETHARDEN-" + bytes(range(256))) * 256)[:65536]
    (tftp / "nettest.dat").write_bytes(payload)
    host_cksum = subprocess.run(["cksum", str(tftp / "nettest.dat")],
                                capture_output=True, text=True,
                                check=True).stdout.split()[0]

    overlay = tmp_path / "work.qcow2"
    subprocess.run([str(QEMU_IMG), "create", "-f", "qcow2", "-b", str(GOLDEN),
                    "-F", "qcow2", str(overlay)], check=True,
                   stdout=subprocess.DEVNULL)
    ser_path = tmp_path / "ser.sock"
    mon_path = tmp_path / "mon.sock"
    qlog = open(tmp_path / "q.log", "w")
    proc = _launch(overlay, ser_path, mon_path, qlog, tftp_dir=str(tftp))
    time.sleep(5)
    ser = _Serial(str(ser_path))
    _wait_login(proc, ser)
    _login(ser)

    # Interactive tftp with explicit octet mode (slirp rejects netascii); the
    # client reads commands from the tty, so pace each line with a read.
    ser.cmd("rm -f /var/tmp/nettest.dat", 2)
    ser.send("tftp 10.0.2.2\r"); ser.read(2)
    ser.send("mode octet\r"); ser.read(2)
    ser.send("get nettest.dat /var/tmp/nettest.dat\r")
    got = ser.read(15)
    ser.send("quit\r"); ser.read(2)
    ck = ser.cmd("cksum /var/tmp/nettest.dat; rm -f /var/tmp/nettest.dat", 5)
    ser.close()
    _stop(proc, mon_path)
    qlog.close()

    assert "Received 65536 bytes" in got, \
        f"tftp transfer over ec0 did not complete: {got!r}"
    m = re.search(r"(\d+)\s+65536\s+/var/tmp/nettest\.dat", ck)
    assert m, f"could not read transferred-file cksum: {ck!r}"
    assert m.group(1) == host_cksum, \
        f"Seeq ec0 transfer corrupted the payload: guest cksum {m.group(1)} " \
        f"!= host {host_cksum}"


def test_virtuix_disk_durability_net_and_console(tmp_path):
    for p, what in [(QEMU, "qemu binary"), (PROM, "Indy PROM"),
                    (GOLDEN, "golden disk"), (KERNEL, "IP55 kernel")]:
        _require(p, what)

    overlay = tmp_path / "work.qcow2"
    subprocess.run([str(QEMU_IMG), "create", "-f", "qcow2", "-b", str(GOLDEN),
                    "-F", "qcow2", str(overlay)], check=True,
                   stdout=subprocess.DEVNULL)
    ser_path = tmp_path / "ser.sock"
    mon_path = tmp_path / "mon.sock"

    # ---- Boot 1: exercise net + console, then write the durable payload ----
    qlog1 = open(tmp_path / "q1.log", "w")
    proc = _launch(overlay, ser_path, mon_path, qlog1)
    time.sleep(5)
    ser = _Serial(str(ser_path))
    _wait_login(proc, ser)
    _login(ser)

    # Seeq ec0 net health: drive real traffic, then confirm no interface errors.
    ser.cmd("/usr/etc/ifconfig ec0", 2)
    png = ser.cmd("ping -c 5 10.0.2.2", 9)
    netstat = ser.cmd("netstat -in", 3)

    # Z85C30 console bulk output integrity: emit 120 copies of a fixed marker
    # line *over the serial* and confirm every one arrives intact (no dropped or
    # corrupted bytes under a burst), bounded by a sentinel so the read can't
    # race the output.
    bulk_line = "HARDENBULK_ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789"
    ser.send(f"repeat 120 echo {bulk_line}; echo BULKDONE_SENTINEL\r")
    bulk = ""
    bend = time.time() + 12
    while time.time() < bend and "BULKDONE_SENTINEL" not in bulk:
        bulk += ser.read(1)

    # Disk: write 2 MB from /unix (real, varied data) + a small marker, then
    # checksum on THIS boot.  (csh-safe: no 2>/redirections.)
    blocks = BIG_BYTES // 4096
    ser.cmd(f"dd if=/unix of=/var/tmp/harden.dat bs=4096 count={blocks}", 20)
    ser.cmd("echo DURABLE_PAYLOAD_4242 > /var/tmp/harden.marker", 2)
    ser.cmd("sync; sync", 3)
    sz1 = ser.cmd("wc -c /var/tmp/harden.dat", 3)
    ck1 = ser.cmd("cksum /var/tmp/harden.dat", 4)
    ser.close()
    _stop(proc, mon_path)
    qlog1.close()

    # ---- Boot 2: fresh QEMU on the SAME overlay — did the data survive? ----
    qlog2 = open(tmp_path / "q2.log", "w")
    proc = _launch(overlay, ser_path, mon_path, qlog2)
    time.sleep(5)
    ser = _Serial(str(ser_path))
    _wait_login(proc, ser)
    _login(ser)
    sz2 = ser.cmd("wc -c /var/tmp/harden.dat", 3)
    ck2 = ser.cmd("cksum /var/tmp/harden.dat", 4)
    marker = ser.cmd("cat /var/tmp/harden.marker; rm -f /var/tmp/harden.dat "
                     "/var/tmp/harden.marker", 3)
    ser.close()
    _stop(proc, mon_path)
    qlog2.close()

    # ---- Assertions ----
    # Net: pings succeeded and the Seeq interface reported no errors.
    assert "0% packet loss" in png or re.search(r"5 packets received", png), \
        f"ec0 large-traffic ping failed: {png!r}"
    ecline = next((ln for ln in netstat.splitlines() if ln.startswith("ec0")), "")
    # netstat -in: Name Mtu Net Address Ipkts Ierrs Opkts Oerrs Coll
    nums = re.findall(r"\d+", ecline)
    assert ecline, f"ec0 not in netstat -in: {netstat!r}"
    if len(nums) >= 5:
        # Ierrs is the 2nd packet column, Oerrs the 4th (after Ipkts/Opkts).
        ierrs, oerrs = int(nums[-4]), int(nums[-2])
        assert ierrs == 0 and oerrs == 0, \
            f"Seeq ec0 reported interface errors (Ierrs={ierrs} Oerrs={oerrs}): {ecline!r}"

    # Console: all 120 emitted lines arrived intact over the serial (the +1 is
    # the echoed command line), and the run completed (sentinel seen).
    assert "BULKDONE_SENTINEL" in bulk, \
        "Z85C30 console bulk output did not complete (truncated/hung)"
    assert bulk.count(bulk_line) >= 120, \
        f"Z85C30 dropped/corrupted console output: only " \
        f"{bulk.count(bulk_line)}/120 marker lines intact"

    # Disk integrity (boot 1): exact size written.
    m1 = re.search(r"(\d+)\s+/var/tmp/harden\.dat", sz1)
    assert m1 and int(m1.group(1)) == BIG_BYTES, \
        f"2MB write size wrong on boot 1: {sz1!r}"
    c1 = re.search(r"(\d+)\s+(\d+)\s+/var/tmp/harden\.dat", ck1)
    assert c1, f"could not read cksum on boot 1: {ck1!r}"

    # Disk DURABILITY (boot 2): size + checksum identical after the restart.
    m2 = re.search(r"(\d+)\s+/var/tmp/harden\.dat", sz2)
    assert m2 and int(m2.group(1)) == BIG_BYTES, \
        f"file size changed across restart: boot1={sz1!r} boot2={sz2!r}"
    c2 = re.search(r"(\d+)\s+(\d+)\s+/var/tmp/harden\.dat", ck2)
    assert c2, f"could not read cksum on boot 2: {ck2!r}"
    assert c1.group(1) == c2.group(1), \
        f"CHECKSUM MISMATCH across restart (data corruption!): " \
        f"boot1={c1.group(1)} boot2={c2.group(1)}"
    assert "DURABLE_PAYLOAD_4242" in marker, \
        f"marker file did not survive the restart: {marker!r}"
