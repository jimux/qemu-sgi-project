#!/usr/bin/env python3
"""Make a SINGLE telnet login and exercise many commands in that one
session — the most reliable way to test userspace stability without
inetd reconnect churn.

Outputs:
  - Did we get a shell prompt? (1st login should succeed on fresh boot)
  - chkconfig in a tight loop: how often does it segfault?
  - ps -ef and shell pipe behavior
  - find / for cores
  - /var/adm/SYSLOG tail
"""
import socket, time, select, sys

HOST, PORT = "127.0.0.1", 2324


def connect_one():
    s = socket.create_connection((HOST, PORT), timeout=20)
    s.settimeout(60)
    return s


def neg_iac(s, data):
    out = bytearray(); i = 0
    while i < len(data):
        b = data[i]
        if b == 255 and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd in (253, 254):
                s.sendall(bytes([255, 252, data[i + 2] if i + 2 < len(data) else 0]))
                i += 3; continue
            if cmd in (251, 252):
                s.sendall(bytes([255, 254, data[i + 2] if i + 2 < len(data) else 0]))
                i += 3; continue
            if cmd == 250:
                j = data.find(bytes([255, 240]), i)
                i = (j + 2) if j >= 0 else len(data); continue
            i += 2; continue
        out.append(b); i += 1
    return bytes(out)


def read_for(s, secs):
    out = b""; end = time.time() + secs
    while time.time() < end:
        r, _, _ = select.select([s], [], [], 0.3)
        if not r:
            continue
        try:
            c = s.recv(8192)
        except OSError:
            break
        if not c:
            break
        out += neg_iac(s, c)
    return out


def main():
    s = connect_one()
    # Wait for login
    out = b""
    end = time.time() + 20
    while time.time() < end:
        out += read_for(s, 2)
        if b"login:" in out:
            break
    if b"login:" not in out:
        print(f"FAIL: no login: prompt. Got: {out!r}")
        sys.exit(1)
    print(f"[banner] OK ({len(out)} bytes)")

    s.sendall(b"root\r")
    out2 = read_for(s, 8)
    print(f"[after-root] {out2[-200:]!r}")
    if b"# " not in out2 and b"% " not in out2:
        if b"Segmentation fault" in out2:
            print("PANIC: csh segfaulted on first login of fresh boot!")
            sys.exit(2)
        print("WARN: no prompt yet, waiting more…")
        out2 += read_for(s, 10)

    def cmd(c, secs=8):
        s.sendall(c.encode() + b"\r")
        r = read_for(s, secs).decode("utf-8", errors="replace")
        print(f"\n>>> {c}\n{r.rstrip()}")
        return r

    cmd("uname -a; uptime", 6)
    cmd("ps -ef", 12)
    cmd("ps -ef | wc -l", 8)

    # First the original error site: /etc/chkconfig
    print("\n=== chkconfig stress: 30x in one csh session ===")
    rr = cmd("set i=0; while ($i < 30); /etc/chkconfig desktop; echo r$status; "
             "@ i++; end; echo DONE", 30)
    n_segv = rr.count("Memory fault") + rr.count("Segmentation fault")
    n_rc1 = rr.count("r1")
    n_rc0 = rr.count("r0")
    print(f"  STATS: {n_segv} mem-faults, {n_rc1} rc=1 (off), {n_rc0} rc=0 (on)")

    # Now check ps with various pipes
    print("\n=== ps pipe tests ===")
    cmd("ps -e | head -5", 6)
    cmd("ps -ef >/tmp/p.txt 2>&1; wc -l /tmp/p.txt; head -3 /tmp/p.txt", 8)
    cmd("cat /tmp/p.txt | grep -c login", 6)

    # Inetd / network state
    print("\n=== inetd / network state ===")
    cmd("netstat -anv 2>&1 | head -10", 8)
    cmd("netstat -an | awk 'NR<=15 {print}'", 8)

    # Look for cores
    print("\n=== core / SYSLOG ===")
    cmd("find / -name 'core*' -type f 2>/dev/null | head -20", 20)
    cmd("ls -lat /var/adm/SYSLOG; wc -l /var/adm/SYSLOG", 6)
    cmd("tail -40 /var/adm/SYSLOG", 10)

    s.close()


if __name__ == "__main__":
    main()
