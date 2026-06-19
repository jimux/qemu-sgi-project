#!/usr/bin/env python3
"""Drive a live fix on the IP54 baseline disk:
  - Replace /var/X11/xdm/xdm-config with the canonical 2619B from x_eoe
  - chkconfig visuallogin on; chkconfig desktop on
  - init 6
  - Verify clogin face picker appears (screendump diff)
"""
from __future__ import annotations
import os, subprocess, sys, time, socket, shlex, threading
from pathlib import Path

ROOT = Path(__file__).parent
QEMU = ROOT / "qemu-sgi-repo/build-linux/qemu-system-mips64"
BIOS = ROOT / "PROM_library/bins/cpu/ip54/ip54.bin"
DISK = ROOT / "vm_instances/ip54-test/disk.qcow2"
TFTP = ROOT / "ip54_tftp_staging"
RUNDIR = Path("/tmp/qemu_ip54")
RUNDIR.mkdir(parents=True, exist_ok=True)
MON = RUNDIR / "monitor.sock"
SER = RUNDIR / "serial.sock"
LOG = RUNDIR / "serial.log"

for s in (MON, SER):
    if s.exists():
        s.unlink()

env = dict(os.environ)
env["IP54_CAUSE_IP5_COUNT_PA"] = "0x0829fee0"
env["QEMU_DISPLAY"] = "gtk"

cmd = [
    str(QEMU),
    "-M", "sgi-ip54",
    "-bios", str(BIOS),
    "-m", "256M",
    "-L", str(QEMU.parent / "pc-bios"),
    "-display", "gtk",
    "-chardev", f"socket,id=ser0,path={SER},server=on,wait=off",
    "-serial", "chardev:ser0",
    "-monitor", f"unix:{MON},server,nowait",
    "-drive", f"if=mtd,file={DISK},format=qcow2,cache=writeback,file.locking=off",
    "-nic", f"user,tftp={TFTP},hostfwd=tcp::2324-10.0.2.15:23",
    "-audiodev", "pa,id=aud0",
    "-global", "sgi-pvaudio.audiodev=aud0",
]
print("Launching:", " ".join(shlex.quote(c) for c in cmd), flush=True)

logf = open(LOG, "wb")
proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL, env=env, cwd=str(ROOT))
print(f"qemu pid={proc.pid}; serial log {LOG}", flush=True)


def mon(cmdline: str) -> str:
    for _ in range(20):
        if MON.exists():
            break
        time.sleep(0.5)
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(MON))
    s.settimeout(2.0)
    try:
        s.recv(8192)
    except socket.timeout:
        pass
    s.sendall(cmdline.encode() + b"\n")
    out = b""
    try:
        while True:
            chunk = s.recv(8192)
            if not chunk:
                break
            out += chunk
            if b"(qemu)" in out:
                break
    except socket.timeout:
        pass
    s.close()
    return out.decode("utf-8", errors="replace")


sys.path.insert(0, str(ROOT))
from pyirix_qemu.irix_telnet import IRIXTelnet

t = IRIXTelnet(port=2324, timeout=20)
print("Connecting via telnet (240s deadline)…", flush=True)
try:
    t.connect(retries=120, delay=2)
except OSError as e:
    print("Connect failed:", e, flush=True)
    sys.exit(2)
try:
    t.login(user="root", password="")
except Exception as e:
    print("Login failed:", e, flush=True)
    print("Serial tail:")
    try:
        with open(LOG, "rb") as f:
            sys.stdout.buffer.write(f.read()[-3000:])
    except Exception:
        pass
    proc.terminate()
    sys.exit(2)

def step(label, cmd, timeout=60):
    print(f"\n--- {label} ---\n$ {cmd}", flush=True)
    rc, out = t.run(cmd, timeout=timeout)
    print(f"[rc={rc}]\n{out}", flush=True)
    return rc, out

step("whoami / uname", "id; uname -a")
step("pre-fix xdm-config size", "ls -l /var/X11/xdm/xdm-config")
step("ifconfig + tftp client", "/usr/etc/ifconfig pvnet0; type tftp 2>&1 || /etc/chkconfig | head -2 ; ls -l /usr/bin/tftp /usr/etc/tftp 2>&1; which tftp 2>&1")

# Use tftp to fetch canonical xdm-config
rc, out = step(
    "tftp fetch xdm-config.canonical",
    "cd /tmp; rm -f xdm-config.canonical; "
    "(echo binary; echo get xdm-config.canonical; echo quit) | tftp 10.0.2.2 2>&1; "
    "ls -l xdm-config.canonical 2>&1",
    timeout=30,
)
if "xdm-config.canonical" not in out or "No such file" in out:
    print("!!! tftp fetch failed; trying alternative syntax", flush=True)
    rc, out = step(
        "tftp fetch (binary mode + connect syntax)",
        "cd /tmp; rm -f xdm-config.canonical; "
        "tftp 10.0.2.2 <<EOF\nbinary\nget xdm-config.canonical\nquit\nEOF\n"
        "ls -l xdm-config.canonical 2>&1",
        timeout=30,
    )

# Check size matches expected 2619
step("verify xdm-config.canonical size", "wc -c /tmp/xdm-config.canonical")

step("backup current xdm-config", "cp /var/X11/xdm/xdm-config /var/X11/xdm/xdm-config.pre_canonical")
step("apply canonical xdm-config", "cp /tmp/xdm-config.canonical /var/X11/xdm/xdm-config; chmod 644 /var/X11/xdm/xdm-config; ls -l /var/X11/xdm/xdm-config*")
step("chkconfig visuallogin on", "/etc/chkconfig visuallogin on")
step("chkconfig desktop on", "/etc/chkconfig desktop on")
step("verify chkconfig state", "/etc/chkconfig | grep -E 'visuallogin|^desktop|xdm '")

print("\n--- sync + init 6 (reboot) ---", flush=True)
try:
    t.send("sync; sync; init 6\r")
except Exception:
    pass
time.sleep(3)
t.close()

# Wait until telnet refuses (guest is going down)
print("Waiting for guest to disappear…", flush=True)
for _ in range(60):
    try:
        s = socket.create_connection(("127.0.0.1", 2324), timeout=1.5)
        s.close()
        time.sleep(2)
    except OSError:
        print("(guest down)", flush=True)
        break

# Reconnect
print("Waiting for guest to reach multi-user again…", flush=True)
t2 = IRIXTelnet(port=2324, timeout=20)
try:
    t2.connect(retries=180, delay=2)
    t2.login(user="root", password="")
except Exception as e:
    print("FAIL second login:", e)
    print("serial tail:")
    with open(LOG, "rb") as f:
        sys.stdout.buffer.write(f.read()[-3000:])
    proc.terminate()
    sys.exit(3)

step2 = lambda label, c, to=60: (print(f"\n--- {label} ---\n$ {c}", flush=True),
                                  print(t2.run(c, timeout=to)[1], flush=True))
step2("post-reboot xdm-config size", "ls -l /var/X11/xdm/xdm-config")
step2("xdm processes", "ps -ef | grep -E 'xdm|Xsgi|4Dwm|clogin|toolchest' | grep -v grep")
step2("xdm-errors tail", "tail -40 /var/X11/xdm/xdm-errors 2>&1 || true")

# Give clogin some time to render
print("Sleeping 20s for clogin to render…", flush=True)
time.sleep(20)
shot = RUNDIR / "post_fix_clogin.ppm"
print(mon(f"screendump {shot}"))
print(f"Screenshot: {shot}", flush=True)

# Another after 30s
time.sleep(30)
shot2 = RUNDIR / "post_fix_clogin_50s.ppm"
print(mon(f"screendump {shot2}"))
print(f"Second screenshot: {shot2}", flush=True)

step2("late xdm processes", "ps -ef | grep -E 'xdm|Xsgi|4Dwm|clogin|toolchest' | grep -v grep")
step2("xdm-errors late tail", "tail -60 /var/X11/xdm/xdm-errors 2>&1 || true")

print("\nQEMU still running, pid={}. Screenshots:".format(proc.pid))
print(f"  {shot}")
print(f"  {shot2}")
print("Telnet still on 127.0.0.1:2324, monitor at", MON)
