#!/usr/bin/env python3
"""Probe userspace stability with a robust shell channel.

Uses raw telnet and waits for actual prompts (rather than sentinels) so we
can detect a stalled/crashed shell. Tries a sequence of probes to estimate
the segfault rate of basic commands.
"""
import sys, time, socket, re, select, argparse

HOST, PORT = "127.0.0.1", 2324


class Tel:
    def __init__(self):
        s = socket.create_connection((HOST, PORT), timeout=30)
        s.settimeout(30)
        self.s = s
        self.buf = b""

    def neg(self, data):
        out = bytearray(); i = 0
        while i < len(data):
            b = data[i]
            if b == 255 and i + 1 < len(data):
                cmd = data[i + 1]
                if cmd in (253, 254):
                    opt = data[i + 2] if i + 2 < len(data) else 0
                    self.s.sendall(bytes([255, 252, opt])); i += 3; continue
                if cmd in (251, 252):
                    opt = data[i + 2] if i + 2 < len(data) else 0
                    self.s.sendall(bytes([255, 254, opt])); i += 3; continue
                if cmd == 250:
                    j = data.find(bytes([255, 240]), i)
                    i = (j + 2) if j >= 0 else len(data); continue
                i += 2; continue
            out.append(b); i += 1
        return bytes(out)

    def read_for(self, secs):
        out = b""
        end = time.time() + secs
        while time.time() < end:
            r, _, _ = select.select([self.s], [], [], min(0.3, end - time.time()))
            if not r:
                continue
            try:
                c = self.s.recv(8192)
            except OSError:
                break
            if not c:
                break
            out += self.neg(c)
        return out

    def read_until(self, pat, secs=20):
        rx = re.compile(pat)
        end = time.time() + secs
        out = b""
        text = self.buf.decode("latin-1", "replace")
        if rx.search(text):
            m = rx.search(text); self.buf = text[m.end():].encode("latin-1")
            return text[:m.end()]
        while time.time() < end:
            r, _, _ = select.select([self.s], [], [], min(0.5, end - time.time()))
            if not r:
                continue
            try:
                c = self.s.recv(8192)
            except OSError:
                break
            if not c:
                break
            text += self.neg(c).decode("latin-1", "replace")
            m = rx.search(text)
            if m:
                self.buf = text[m.end():].encode("latin-1")
                return text[:m.end()]
        self.buf = text.encode("latin-1")
        return text

    def send(self, s):
        self.s.sendall(s.encode("latin-1", "replace"))

    def close(self):
        try: self.s.close()
        except OSError: pass


def login_and_setup(t):
    # Drain banner and wait for login: prompt
    out = t.read_until(r"login:", secs=60)
    print(f"[banner] {out[-150:]!r}", flush=True)
    t.send("root\r")
    # Either Password: or directly TERM or prompt
    out = t.read_until(r"Password:|TERM|#|\$|%|>", secs=15)
    print(f"[after-root] {out[-150:]!r}", flush=True)
    if "Password:" in out:
        t.send("\r")
        out = t.read_until(r"TERM|#|\$|%|>", secs=15)
        print(f"[after-pw] {out[-150:]!r}", flush=True)
    if "TERM" in out:
        t.send("vt100\r")
        out = t.read_until(r"#|\$|%|>", secs=15)
        print(f"[after-term] {out[-150:]!r}", flush=True)
    # Now in csh. Set a stable prompt.
    t.send("set prompt='RX> '\r")
    t.read_until(r"RX> ", secs=10)
    return True


def run(t, cmd, secs=10, marker=None):
    """Run with a unique end-of-output marker we echo ourselves."""
    if marker is None:
        marker = f"END_{time.time_ns()}"
    full = f"{cmd}; echo {marker}\r"
    t.send(full)
    raw = t.read_until(re.escape(marker), secs=secs)
    # Try to also read trailing prompt
    try:
        t.read_until(r"RX> ", secs=2)
    except Exception:
        pass
    # Strip out the echoed command and trailing marker line
    lines = raw.replace("\r", "").splitlines()
    # find marker line
    body_lines = []
    for line in lines:
        if marker in line:
            break
        if line.strip().startswith(cmd[:30]) and cmd[:30] in line:
            continue  # echoed command
        body_lines.append(line)
    return "\n".join(body_lines).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="probe")
    ap.add_argument("--loops", type=int, default=30,
                    help="how many times to run chkconfig in a row")
    args = ap.parse_args()

    t = Tel()
    login_and_setup(t)
    print(f"\n=== {args.label}: basic state ===", flush=True)
    print(run(t, "uname -a", 10), flush=True)
    print(run(t, "uptime", 6), flush=True)
    print(run(t, "echo SHELL=$shell", 5), flush=True)
    print(run(t, "/bin/sh -c 'echo IT_RUNS'", 6), flush=True)

    print(f"\n=== {args.label}: visuallogin/desktop chkconfig ===", flush=True)
    print(run(t, "/etc/chkconfig visuallogin; echo rc=$status", 5), flush=True)
    print(run(t, "/etc/chkconfig desktop; echo rc=$status", 5), flush=True)

    print(f"\n=== {args.label}: chkconfig stress loop ({args.loops}x) ===",
          flush=True)
    # /bin/sh -c oneliner — csh's multiline syntax is fragile over telnet
    loop = ("/bin/sh -c 'i=0; while [ $i -lt %d ]; do "
            "/etc/chkconfig desktop; echo rc=$?; "
            "i=`expr $i + 1`; done'" % args.loops)
    out = run(t, loop, secs=60, marker="STRESSDONE_X")
    # Tally segfaults
    n_fault = out.count("Memory fault")
    n_ok = out.count("rc=0")
    n_err = out.count("rc=") - n_ok - n_fault
    print(out[-800:], flush=True)
    print(f"\nSTATS: {n_fault} mem-faults / {n_ok} ok / {n_err} other / "
          f"{args.loops} runs", flush=True)

    print(f"\n=== {args.label}: ps / shell pipe behavior ===", flush=True)
    print(run(t, "ps -ef", 12), flush=True)
    print("---", flush=True)
    print(run(t, "ps -ef >/tmp/p.txt; wc -l /tmp/p.txt", 8), flush=True)
    print("---", flush=True)
    print(run(t, "cat /tmp/p.txt | wc -l", 6), flush=True)

    print(f"\n=== {args.label}: kernel log / core hunt ===", flush=True)
    print(run(t, "ls -lat /var/adm/SYSLOG", 6), flush=True)
    print(run(t,
              "grep -i 'sigsegv\\|fault\\|except\\|panic\\|killed' "
              "/var/adm/SYSLOG 2>/dev/null | tail -40", 12), flush=True)
    print(run(t, "find / -name 'core*' -mtime -1 2>/dev/null | head -20",
              20), flush=True)
    print(run(t, "ls /tmp/core /var/tmp/core /core 2>/dev/null", 6), flush=True)

    t.close()


if __name__ == "__main__":
    main()
