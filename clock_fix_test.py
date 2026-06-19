#!/usr/bin/env python3
"""Hypothesis: IRIX guest clock is in 2004, host files are dated 2026, nsd
complains about future-dated /etc/passwd, getpwnam fails intermittently,
chkconfig (and others) segfault on NULL pwent dereference.

This script:
  1) connects via the SINGLE first telnet session (most reliable)
  2) sets the IRIX clock to a sensible date past the file mtimes
  3) touches /etc/passwd /etc/services /etc/group /etc/hosts /etc/rpc
  4) sends SIGHUP to nsd so it re-reads
  5) runs /etc/chkconfig 30x and counts faults
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
read_for(s, 8)

def cmd(c, secs=15, label=None):
    s.sendall(c.encode() + b"\r")
    r = read_for(s, secs).decode("utf-8", errors="replace")
    print(f"\n>>> {label or c}")
    print(r.rstrip())
    return r

# BEFORE: chkconfig stress, get baseline fault count
print("\n=== BEFORE clock fix: 30x chkconfig stress ===")
out_before = cmd(
    "/bin/sh -c 'i=0; while [ $i -lt 30 ]; do "
    "/etc/chkconfig desktop; echo rc=$?; i=`expr $i + 1`; done'", 60)
faults_before = (out_before.count("Memory fault")
                 + out_before.count("Segmentation fault"))
rc_before = out_before.count("rc=")
print(f"BEFORE: {faults_before} faults / {rc_before} attempts")

# Apply clock fix: set IRIX clock to current real time
print("\n=== Setting IRIX clock ===")
cmd("date")
import datetime
host_now = datetime.datetime.now()
# IRIX `date` format: MMDDhhmm or MMDDhhmmYY for full
dstr = host_now.strftime("%m%d%H%M%Y")
print(f"\n>>> /sbin/date {dstr}")
cmd(f"/sbin/date {dstr}", 6)
cmd("date")

# Touch critical files so their mtime <= now (in case anyone cares)
print("\n=== Touching critical config files ===")
cmd("touch /etc/passwd /etc/group /etc/services /etc/hosts /etc/rpc /etc/inetd.conf")
cmd("ls -lt /etc/passwd /etc/group /etc/hosts | head -6")

# Restart nsd to pick up the changes
print("\n=== Restarting nsd ===")
cmd("/etc/init.d/network status 2>&1 | head -5; echo -- ; ps -ef | grep nsd | grep -v grep", 8)
cmd("kill -HUP `ps -ef | awk '/[n]sd/{print $2}'` 2>&1; echo HUP_RC=$?", 6)
cmd("sleep 2; ps -ef | grep nsd | grep -v grep", 8)

# AFTER: chkconfig stress
print("\n=== AFTER clock fix: 30x chkconfig stress ===")
out_after = cmd(
    "/bin/sh -c 'i=0; while [ $i -lt 30 ]; do "
    "/etc/chkconfig desktop; echo rc=$?; i=`expr $i + 1`; done'", 60)
faults_after = (out_after.count("Memory fault")
                + out_after.count("Segmentation fault"))
rc_after = out_after.count("rc=")
print(f"AFTER: {faults_after} faults / {rc_after} attempts")

# Summary
print("\n========================================")
print(f" BEFORE: {faults_before} faults in {rc_before} runs "
      f"({100.0*faults_before/(rc_before or 1):.1f}%)")
print(f" AFTER:  {faults_after} faults in {rc_after} runs "
      f"({100.0*faults_after/(rc_after or 1):.1f}%)")
print("========================================")

s.close()
