#!/usr/bin/env python3
"""Reliable telnet command channel to a running IRIX guest (over slirp hostfwd).

Why telnet, not the serial console: IRIX telnetd gives a real login PTY with line
discipline, so commands and output are clean — no PROM-menu / boot-banner / typematic
quirks, no fragile inter-character timing. The guest's port 23 is exposed on the
container via the instance manifest's `hostfwd_port` (auto-injected as
`hostfwd=tcp::PORT-10.0.2.15:23`), so we just connect to 127.0.0.1:PORT inside the
container.

Command execution uses a sentinel: each `run(cmd)` sends `cmd; echo __EOC__$?` and reads
until the marker, so we know exactly when the command finished and its exit status —
far more robust than waiting on a shell prompt regex.

    tn = IRIXTelnet(port=2323); tn.login()
    rc, out = tn.run("hinv -c processor")
    tn.run("cc -n32 -O -o /tmp/x /tmp/x.c -lX11", timeout=120)
    tn.close()
"""
import re
import select
import socket
import time

IAC = 255
DO, DONT, WILL, WONT, SB, SE = 253, 254, 251, 252, 250, 240


class IRIXTelnet:
    def __init__(self, host="127.0.0.1", port=2323, timeout=15):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self.buf = b""

    # ---- connection / telnet option negotiation ----
    def connect(self, retries=60, delay=5):
        """Connect, retrying until telnetd accepts (guest reaches multi-user)."""
        last = None
        for _ in range(retries):
            try:
                s = socket.create_connection((self.host, self.port), timeout=self.timeout)
                s.settimeout(self.timeout)
                self.sock = s
                return True
            except OSError as e:
                last = e
                time.sleep(delay)
        raise OSError("telnet connect failed after retries: %s" % last)

    def _negotiate(self, data):
        """Strip IAC option negotiation, refusing every option (clean byte stream)."""
        out = bytearray()
        i = 0
        while i < len(data):
            b = data[i]
            if b == IAC and i + 1 < len(data):
                cmd = data[i + 1]
                if cmd in (DO, DONT):
                    opt = data[i + 2] if i + 2 < len(data) else 0
                    self.sock.sendall(bytes([IAC, WONT, opt]))
                    i += 3
                    continue
                if cmd in (WILL, WONT):
                    opt = data[i + 2] if i + 2 < len(data) else 0
                    self.sock.sendall(bytes([IAC, DONT, opt]))
                    i += 3
                    continue
                if cmd == SB:                       # subnegotiation: skip to SE
                    j = data.find(bytes([IAC, SE]), i)
                    i = (j + 2) if j >= 0 else len(data)
                    continue
                i += 2
                continue
            out.append(b)
            i += 1
        return bytes(out)

    def _recv_until(self, pattern, timeout=30):
        rx = re.compile(pattern)
        deadline = time.time() + timeout
        text = self.buf.decode("latin-1", "replace")
        if rx.search(text):
            m = rx.search(text)
            self.buf = text[m.end():].encode("latin-1")
            return text[:m.end()]
        while time.time() < deadline:
            ready, _, _ = select.select([self.sock], [], [], min(1.0, deadline - time.time()))
            if not ready:
                continue
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            clean = self._negotiate(chunk)
            text += clean.decode("latin-1", "replace")
            m = rx.search(text)
            if m:
                self.buf = text[m.end():].encode("latin-1")
                return text[:m.end()]
        self.buf = b""
        return text

    def send(self, text):
        self.sock.sendall(text.encode("ascii", "replace"))

    # ---- login + command execution ----
    def wait_for_login(self, deadline_s=360):
        """Connect+reconnect until the guest telnetd shows a login: prompt.

        slirp accepts the forwarded port immediately, but until the guest reaches
        multi-user the connection yields nothing or is reset — so we retry the whole
        connect/await-prompt cycle until login: appears or the deadline passes.
        """
        end = time.time() + deadline_s
        while time.time() < end:
            try:
                if self.sock is None:
                    self.connect(retries=1, delay=1)
                out = self._recv_until(r"login:", timeout=20)
                if "login:" in out:
                    return True
            except OSError:
                pass
            self.close()
            self.buf = b""
            time.sleep(5)
        raise OSError("no login: prompt before deadline (guest not multi-user?)")

    def login(self, user="root", password=None):
        self.wait_for_login()
        self.send(user + "\r")
        out = self._recv_until(r"Password:|TERM|#|\$", timeout=20)
        if "Password:" in out:
            self.send((password or "") + "\r")
            out = self._recv_until(r"TERM|#|\$", timeout=15)
        if "TERM" in out:
            self.send("\r")
            out = self._recv_until(r"#|\$", timeout=15)
        # IRIX root's login shell is csh ("IRIS N# "); drop to a Bourne shell so our
        # `cmd; echo __EOC__$?` sentinel (and PS1) work — csh has $status, not $?.
        self.send("exec /bin/sh\r")
        time.sleep(1)
        self.buf = b""
        self.send('PS1="IRIXSH> "; echo READY$?\r')
        self._recv_until(r"READY0", timeout=15)
        return True

    def _drain(self):
        """Discard any pending/stale bytes so a prior timed-out command can't corrupt the
        next one's captured output (resync the channel)."""
        self.buf = b""
        while True:
            ready, _, _ = select.select([self.sock], [], [], 0.2)
            if not ready:
                break
            try:
                if not self.sock.recv(65536):
                    break
            except OSError:
                break

    def run(self, cmd, timeout=60):
        """Run a command, return (exit_code, output). Sentinel-delimited.

        The marker uses lowercase ('zz...zz') so it can never appear inside uuencode
        output (uuencode emits only chars 0x20-0x5F — no lowercase) — important for get_file().
        """
        self._drain()
        marker = "zzEOCzz"
        self.send("%s; echo %s$?\r" % (cmd, marker))
        out = self._recv_until(marker + r"(\d+)", timeout=timeout)
        m = re.search(marker + r"(\d+)", out)
        rc = int(m.group(1)) if m else -1
        # strip the echoed command line and the trailing marker line
        body = out[:m.start()] if m else out
        lines = body.splitlines()
        if lines and cmd.split()[0] in lines[0]:
            lines = lines[1:]
        return rc, "\n".join(lines).strip()

    def get_file(self, remote_path, timeout=120):
        """Pull a (binary) file out of the guest via uuencode, return its bytes.

        Frames the uuencode stream with lowercase markers that cannot occur in uuencode
        output, captures it over telnet, and decodes with binascii.a2b_uu. Reliable for
        the small compiled binaries we move between build host (irix-devel) and target.
        """
        import binascii
        beg, end = "zzBEGINzz", "zzENDzz"
        self.send("echo %s; uuencode %s f; echo %s\r" % (beg, remote_path, end))
        out = self._recv_until(re.escape(end), timeout=timeout)
        try:
            seg = out.split(beg, 1)[1].split(end, 1)[0]
        except IndexError:
            raise OSError("get_file: markers not found for %s" % remote_path)
        data = bytearray()
        started = False
        for line in seg.splitlines():
            if line.startswith("begin "):
                started = True
                continue
            if not started:
                continue
            if line.strip() in ("end", "`") or line.startswith("end"):
                break
            if not line or line == "`":
                continue
            try:
                data += binascii.a2b_uu(line + "\n")
            except (binascii.Error, ValueError):
                # last partial line / length byte ' ' (0x60) edge — best-effort
                try:
                    data += binascii.a2b_uu((" " + line[1:] + "\n"))
                except Exception:
                    pass
        return bytes(data)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
