#!/usr/bin/env python3
"""Probe userspace stability on the running IP54 guest.

Uses raw telnet (not IRIXTelnet's `run()` machinery) to send simple commands
and capture output, so we can SEE the segfault messages and shell responses
even when the channel is partially broken.
"""
import sys, time, socket, re, select

HOST, PORT = "127.0.0.1", 2324


def make_conn():
    s = socket.create_connection((HOST, PORT), timeout=30)
    s.settimeout(30)
    return s


IAC = 255
DO, DONT, WILL, WONT = 253, 254, 251, 252


def neg(sock, data):
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == IAC and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd in (DO, DONT):
                opt = data[i + 2] if i + 2 < len(data) else 0
                sock.sendall(bytes([IAC, WONT, opt])); i += 3; continue
            if cmd in (WILL, WONT):
                opt = data[i + 2] if i + 2 < len(data) else 0
                sock.sendall(bytes([IAC, DONT, opt])); i += 3; continue
            if cmd == 250:                 # SB
                j = data.find(bytes([IAC, 240]), i)
                i = (j + 2) if j >= 0 else len(data); continue
            i += 2; continue
        out.append(b); i += 1
    return bytes(out)


def recv_quiet(sock, secs):
    """Read everything that arrives in the next `secs` seconds; strip IAC."""
    deadline = time.time() + secs
    out = b""
    while time.time() < deadline:
        ready, _, _ = select.select([sock], [], [], min(0.5, deadline - time.time()))
        if not ready:
            continue
        try:
            chunk = sock.recv(8192)
        except OSError:
            break
        if not chunk:
            break
        out += neg(sock, chunk)
    return out


def login(sock):
    out = recv_quiet(sock, 5)
    print(f"banner: {out!r}", flush=True)
    if b"login:" not in out:
        # try wait a bit more
        out += recv_quiet(sock, 30)
        if b"login:" not in out:
            raise SystemExit("no login: prompt")
    sock.sendall(b"root\r")
    out2 = recv_quiet(sock, 8)
    print(f"after-root: {out2!r}", flush=True)
    if b"Password:" in out2:
        sock.sendall(b"\r")
        out3 = recv_quiet(sock, 8)
        print(f"after-pw: {out3!r}", flush=True)
    if b"TERM" in out2 or b"TERM" in (out3 if 'out3' in dir() else b""):
        sock.sendall(b"vt100\r")
        recv_quiet(sock, 5)


def cmd(sock, line, secs=5):
    print(f"\n>> {line}", flush=True)
    sock.sendall(line.encode() + b"\r")
    out = recv_quiet(sock, secs)
    text = out.decode("utf-8", errors="replace")
    print(text, flush=True)
    return text


def main():
    s = make_conn()
    login(s)

    # First, what shell are we in?
    cmd(s, "echo SHELL=$SHELL", 4)
    cmd(s, "ps -p $$", 4)

    # Get the basic state
    cmd(s, "uname -a", 5)
    cmd(s, "uptime", 4)
    cmd(s, "df -k /", 5)

    # Look for evidence of segfaults already in the logs
    cmd(s, "ls -lt /usr/adm/SYSLOG /var/adm/SYSLOG 2>&1 | head -5", 5)
    cmd(s, "grep -i 'sigsegv\\|memory fault\\|panic\\|kernel\\|except' /var/adm/SYSLOG 2>&1 | tail -30", 8)

    # Hunt for core files
    cmd(s, "find / -name core -type f 2>/dev/null | head -30", 15)

    # Run a chkconfig in a tight loop to see segfault rate
    print("\n--- chkconfig loop x20 ---", flush=True)
    cmd(s, "for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do /etc/chkconfig desktop; echo rc=$status; done", 20)

    # ps without pipe
    cmd(s, "ps -ef > /tmp/ps1.txt 2>&1; ls -la /tmp/ps1.txt; cat /tmp/ps1.txt | head -30", 12)

    # see /tmp/core or any newly-created cores
    cmd(s, "find /tmp /var/tmp /usr/tmp / -name 'core*' -mtime -1 2>/dev/null | head -20", 15)

    # Inspect a core if found (use 'file' to identify it)
    cmd(s, "for c in /tmp/core /var/tmp/core /usr/tmp/core /core; do test -e $c && file $c && ls -la $c; done", 8)

    # what does dbx look like?
    cmd(s, "which dbx; ls /usr/sbin/dbx /usr/bin/dbx 2>/dev/null", 4)

    s.close()


if __name__ == "__main__":
    main()
