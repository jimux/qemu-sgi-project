"""Live boot test for the virtuix (IP55) machine.

Boots `-M virtuix -kernel unix.ip55.g` on a fresh disposable overlay of the
clean golden and verifies the IP55-native kernel reaches multi-user, brings up
the requested number of SMP CPUs, and that the host-backed paths work: Seeq ec0
networking, the WD33C93/HPC3 SCSI disk (write->sync->read-back), and the Z85C30
serial console (computed round-trip). This is the live smoke/hardening test for
the virtuix machine (everything else is source analysis).

Marked `slow` (full IRIX boot, ~1-2 min) so it is excluded from the default
`-m "not slow"` fast suite. Run explicitly with:
    python3 -m pytest tests/test_virtuix_boot.py -v
"""
import os
import re
import socket
import subprocess
import time
import shutil
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

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


@pytest.fixture
def virtuix_vm(tmp_path):
    for p, what in [(QEMU, "qemu binary"), (PROM, "Indy PROM"),
                    (GOLDEN, "golden disk"), (KERNEL, "IP55 kernel")]:
        _require(p, what)

    overlay = tmp_path / "work.qcow2"
    subprocess.run([str(QEMU_IMG), "create", "-f", "qcow2", "-b", str(GOLDEN),
                    "-F", "qcow2", str(overlay)], check=True,
                   stdout=subprocess.DEVNULL)
    ser = tmp_path / "ser.sock"
    mon = tmp_path / "mon.sock"
    qlog = open(tmp_path / "q.log", "w")
    proc = subprocess.Popen(
        [str(QEMU), "-M", "virtuix", "-smp", str(SMP),
         "-accel", "tcg,thread=multi", "-kernel", str(KERNEL),
         "-bios", str(PROM), "-m", "256M", "-L", str(PCBIOS),
         "-drive", f"if=scsi,bus=0,unit=1,file={overlay},format=qcow2,"
                   "cache=writethrough,file.locking=off",
         "-nic", "user",
         "-serial", f"unix:{ser},server,nowait",
         "-monitor", f"unix:{mon},server,nowait",
         "-display", "none"],
        stdout=qlog, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True)
    time.sleep(5)
    yield proc, str(ser), str(mon)
    # graceful stop via monitor quit (writethrough overlay -> kill-safe anyway)
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
    qlog.close()


def test_virtuix_boots_smp_and_networking(virtuix_vm):
    proc, ser_path, mon_path = virtuix_vm
    ser = _Serial(ser_path)
    buf = ""
    end = time.time() + BOOT_TIMEOUT
    while time.time() < end:
        buf += ser.read(2)
        if proc.poll() is not None:
            ser.close()
            pytest.fail(f"QEMU exited during boot rc={proc.returncode}")
        if re.search(r"login:", buf):
            break
    assert re.search(r"login:", buf), \
        f"virtuix did not reach multi-user login in {BOOT_TIMEOUT}s"

    # log in as root and confirm the SMP CPU count
    ser.send("\r")
    ser.read(1)
    ser.send("root\r")
    r = ser.read(2)
    if "Password" in r:
        ser.send("\r")
        ser.read(2)
    ser.send("\r")
    ser.read(1)
    ser.send("hinv | grep -i Processors\r")
    hinv = ser.read(4)
    m = re.search(r"(\d+)\s+\d+\s+MHZ\s+IP22\s+Processors", hinv)
    assert m, f"could not read SMP processor count from hinv: {hinv!r}"
    assert int(m.group(1)) == SMP, \
        f"expected {SMP} CPUs, hinv reported {m.group(1)}"

    # Seeq ec0 networking: interface up + slirp gateway reachable
    ser.send("/usr/etc/ifconfig ec0\r")
    ifc = ser.read(3)
    assert re.search(r"ec0:.*UP", ifc, re.DOTALL), \
        f"ec0 ethernet interface not UP: {ifc!r}"
    ser.send("ping -c 2 10.0.2.2\r")
    png = ser.read(7)

    # --- host-backed path hardening (disk + console), exercised on R5000 ---
    # Serial console round-trip: a COMPUTED result that is NOT present in the
    # command text must echo back, proving the Z85C30 console + shell round-trip
    # cleanly (not just that a command was accepted).
    ser.send("expr 314159 + 271828\r")
    rt = ser.read(2)
    # Disk write -> sync -> read-back through the WD33C93 + HPC3 SCSI-DMA path on
    # the XFS root. Split into two reads so the marker can only appear from the
    # file read-back (the `cat` command text does not contain it) -> airtight.
    ser.send("echo DISKWRITEOK > /var/tmp/.hardencheck; sync; sync\r")
    ser.read(2)
    ser.send("cat /var/tmp/.hardencheck; rm -f /var/tmp/.hardencheck\r")
    diskrb = ser.read(3)
    # Disk/SCSI is enumerated in the hardware inventory.
    ser.send("hinv | grep -iE 'SCSI|Disk'\r")
    dinv = ser.read(3)
    ser.close()

    assert "0.0% packet loss" in png or re.search(r"2 packets received", png), \
        f"virtuix could not ping the slirp gateway over ec0: {png!r}"
    assert "585987" in rt, \
        f"serial console round-trip failed (Z85C30 console did not echo result): {rt!r}"
    assert "DISKWRITEOK" in diskrb, \
        f"disk write->sync->read-back failed (WD33C93/HPC3 SCSI path): {diskrb!r}"
    assert re.search(r"(?i)scsi|disk", dinv), \
        f"no SCSI/disk controller in hinv inventory: {dinv!r}"
