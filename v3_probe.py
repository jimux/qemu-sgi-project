#!/usr/bin/env python3
"""Re-test with PROPER csh-free syntax. The previous probe's csh while-loop
syntax was wrong (csh `while` needs newlines, not `;`); that produced csh-
internal `?` history errors that I misread as segfaults.

This probe uses /bin/sh -c "..." with `;` separators, which sh DOES accept.
"""
import socket, time, select, sys

HOST, PORT = "127.0.0.1", 2324


def neg(s, d):
    out = bytearray(); i = 0
    while i < len(d):
        b = d[i]
        if b == 255 and i+1 < len(d):
            cmd = d[i+1]
            if cmd in (253, 254):
                s.sendall(bytes([255, 252, d[i+2] if i+2<len(d) else 0])); i+=3; continue
            if cmd in (251, 252):
                s.sendall(bytes([255, 254, d[i+2] if i+2<len(d) else 0])); i+=3; continue
            if cmd == 250:
                j = d.find(bytes([255, 240]), i)
                i = (j+2) if j>=0 else len(d); continue
            i += 2; continue
        out.append(b); i += 1
    return bytes(out)


def read_for(s, secs):
    out = b""; end = time.time() + secs
    while time.time() < end:
        r,_,_ = select.select([s], [], [], 0.3)
        if not r: continue
        try: c = s.recv(8192)
        except OSError: break
        if not c: break
        out += neg(s, c)
    return out


s = socket.create_connection((HOST, PORT), timeout=20)
s.settimeout(60)
out = b""; end = time.time() + 25
while time.time() < end:
    out += read_for(s, 5)
    if b"login:" in out: break
if b"login:" not in out:
    print("FAIL no login"); sys.exit(1)
print("got login")
s.sendall(b"root\r")
read_for(s, 8)  # discard banner


def cmd(c, secs=15):
    s.sendall(c.encode() + b"\r")
    r = read_for(s, secs).decode("utf-8", errors="replace")
    print(f"\n>>> {c}")
    print(r.rstrip())
    return r


cmd("uptime")
# chkconfig stress via /bin/sh -c
print("\n=== Test B: 30x chkconfig in /bin/sh -c ===")
out = cmd("/bin/sh -c 'i=0; while [ $i -lt 30 ]; do /etc/chkconfig desktop; "
          "echo r=$?; i=`expr $i + 1`; done'", 30)
n_fault = out.count("Memory fault") + out.count("Segmentation fault")
n_rc1 = out.count("r=1")
n_rc0 = out.count("r=0")
print(f"  STATS: faults={n_fault}, r=1 (off)={n_rc1}, r=0 (on)={n_rc0}")

# What about other commands?
print("\n=== Test C: rapid /bin/sh -c stress ===")
out = cmd("/bin/sh -c 'i=0; while [ $i -lt 30 ]; do /bin/ls /etc/cshrc > /dev/null; "
          "echo r=$?; i=`expr $i + 1`; done'", 30)
n_fault = out.count("Memory fault") + out.count("Segmentation fault")
n_rc0 = out.count("r=0")
print(f"  ls: faults={n_fault}, r=0={n_rc0}")

print("\n=== Test D: /usr/bin/X11/xdpyinfo stress ===")
out = cmd("/bin/sh -c 'i=0; while [ $i -lt 5 ]; do "
          "/usr/bin/X11/xdpyinfo -display :0 2>&1 | head -2; "
          "echo r=$?; i=`expr $i + 1`; done'", 30)
n_fault = out.count("Memory fault") + out.count("Segmentation fault")
print(f"  xdpyinfo: faults={n_fault}")

print("\n=== Test E: ps via various paths ===")
cmd("/bin/ps -ef >/tmp/ps1.txt 2>&1; wc -l /tmp/ps1.txt", 10)
cmd("/sbin/ps -ef >/tmp/ps2.txt 2>&1; wc -l /tmp/ps2.txt", 10)
cmd("/usr/bin/ps -ef >/tmp/ps3.txt 2>&1; wc -l /tmp/ps3.txt", 10)

print("\n=== Test F: SYSLOG inspection ===")
cmd("tail -50 /var/adm/SYSLOG", 10)

s.close()
