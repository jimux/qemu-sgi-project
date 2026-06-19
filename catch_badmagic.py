#!/usr/bin/env python3
"""Boot IP54 with gdbstub enabled. Wait for multi-user. Attach gdb-multiarch,
break at the 'Bad magic' path inside xfs_inobp_bwcheck, continue. When the
breakpoint hits, dump the offending buffer + the surrounding context to
diagnose why the buffer has zeros in some inode slots.

The actual /unix.new on the IP54 disk has:
  xfs_inobp_bwcheck @ 0x88102024 (entry)
  bad-magic branch lands at 0x881020ac
  next_unlinked==0 branch lands at 0x881020f4
buf_t offsets observed in the disasm:
  +88   b_un.b_addr  (u32, the buffer data VA)
  +96   b_blkno      (s64, disk block number)
  +148  b_fsprivate3 (xfs_mount_t *)
"""
from __future__ import annotations
import os, subprocess, sys, time, socket, shlex
from pathlib import Path

ROOT = Path(__file__).parent
QEMU = ROOT / "qemu-sgi-repo/build-linux/qemu-system-mips64"
BIOS = ROOT / "PROM_library/bins/cpu/ip54/ip54.bin"
DISK = ROOT / "vm_instances/ip54-test/disk.qcow2"
GOLDEN = ROOT / "vm_instances/ip54-test/disk.qcow2.golden"
TFTP = ROOT / "ip54_tftp_staging"
RUN = Path("/tmp/qemu_ip54_gdb")
RUN.mkdir(exist_ok=True)
MON = RUN / "monitor.sock"
SER = RUN / "serial.sock"
LOG = RUN / "serial.log"

GDB_PORT = 1234

BAD_MAGIC_PC = 0x881020ac
BAD_NEXTUNL_PC = 0x881020f4


def sx(a):
    """Sign-extend KSEG0 VA to 64-bit for gdb."""
    return a if a < 0x80000000 else 0xffffffff00000000 | a


def cleanup():
    subprocess.run(["pkill", "-f", "qemu-system-mips64.*sgi-ip54"],
                   capture_output=True)
    time.sleep(2)
    for f in RUN.iterdir():
        if f.suffix in {".sock"} or f.name == "serial.log":
            try: f.unlink()
            except: pass


def boot():
    cleanup()
    print(f"[boot] restoring disk from {GOLDEN}", flush=True)
    subprocess.run(["cp", str(GOLDEN), str(DISK)], check=True)
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
        "-gdb", f"tcp::{GDB_PORT}",
    ]
    print(f"[boot] launching: {' '.join(shlex.quote(c) for c in cmd)}", flush=True)
    p = subprocess.Popen(cmd, stdout=open(LOG, "wb"), stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, env=env, cwd=str(ROOT))
    print(f"[boot] qemu pid={p.pid}", flush=True)
    return p


def wait_telnet(deadline_s=600):
    print(f"[wait] waiting up to {deadline_s}s for telnet login: prompt…",
          flush=True)
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            s = socket.create_connection(("127.0.0.1", 2324), timeout=2.0)
            s.settimeout(15.0)
            data = b""
            d_end = time.time() + 15
            while time.time() < d_end:
                try:
                    chunk = s.recv(2048)
                except OSError:
                    break
                if not chunk:
                    break
                data += chunk
                if b"login:" in data:
                    break
            s.close()
            if b"login:" in data:
                print(f"[wait] login: seen at t={int(time.time()-(end-deadline_s))}s",
                      flush=True)
                return True
        except OSError:
            pass
        time.sleep(20)
    return False


GDB_SCRIPT = f"""
set pagination off
set confirm off
set architecture mips:isa64
set mips abi n64
set endian big
target remote :{GDB_PORT}

echo \\n==== CONNECTED — planting bp at bad-magic branch ====\\n
hbreak *{sx(BAD_MAGIC_PC):#x}
hbreak *{sx(BAD_NEXTUNL_PC):#x}

continue

echo \\n==== STOPPED ====\\n
info registers
echo \\n
echo === bp (original buf_t *): from sp+0\\n
x/1xg $sp
echo \\n
echo === s0 (current dip pointer)\\n
p/x $s0
echo \\n
echo === s2 (xfs_mount_t *mp)\\n
p/x $s2
echo \\n
echo === s3 (ni — inodes per cluster)\\n
p/x $s3
echo \\n
echo === a2 (the offending magic value)\\n
p/x $a2

echo \\n==== Buffer header (offsets 0..160 of bp) ====\\n
x/40xw *(unsigned int *)$sp

echo \\n==== Offending dip (32 bytes) ====\\n
x/8xw $s0

echo \\n==== First 256 bytes of buffer (first inode) ====\\n
x/64xw $s0-(($s0 - *(unsigned int *)($sp+0+88)))

echo \\n==== Inode magic scan: first u16 of each 256B slot in buffer ====\\n
set $bp = *(unsigned int *)$sp
set $bufaddr = *(unsigned int *)($bp + 88)
set $bcount  = *(unsigned int *)($bp + 80)
echo bufaddr=
output/x $bufaddr
echo \\nbcount=
output/d $bcount
echo \\nblkno (low 32)=
output/x *(unsigned int *)($bp + 100)
echo \\nb_bvtype=
output/x *(unsigned int *)($bp + 152)
echo \\n

set $i = 0
while $i < 32
  set $slot = $bufaddr + ($i * 256)
  set $m = *(unsigned short *)$slot
  printf "  slot %2d @ 0x%x: magic=0x%04x\\n", $i, $slot, $m
  set $i = $i + 1
end

echo \\n==== Backtrace ====\\n
bt 20

detach
quit
"""


def run_gdb():
    script_path = RUN / "catch.gdb"
    script_path.write_text(GDB_SCRIPT)
    out_path = RUN / "catch.out"
    print(f"[gdb] running script {script_path}", flush=True)
    try:
        r = subprocess.run(["gdb-multiarch", "-nx", "-batch", "-x", str(script_path)],
                           capture_output=True, text=True, timeout=600)
        out_path.write_text(r.stdout + "\n[STDERR]\n" + r.stderr)
        print(f"[gdb] output saved to {out_path}", flush=True)
        print("=== gdb output ===", flush=True)
        print(r.stdout)
        if r.stderr.strip():
            print("=== gdb stderr ===")
            print(r.stderr)
    except subprocess.TimeoutExpired as e:
        print(f"[gdb] TIMEOUT — breakpoint never hit within 600s", flush=True)
        if e.stdout:
            print(e.stdout.decode() if isinstance(e.stdout, bytes) else e.stdout)


def main():
    p = boot()
    try:
        if not wait_telnet(600):
            print("[FAIL] no telnet login in 600s", flush=True)
            return 2
        # Login and trigger an inode-bwrite workload so the breakpoint fires.
        # Pull a fresh telnet session and run lots of file ops.
        print("[probe] kicking off file-IO workload to trigger inode bwrites…",
              flush=True)
        # quick driver — fire commands in a new telnet session, don't wait long
        drv = subprocess.Popen([
            "python3", "-c",
            "import sys; sys.path.insert(0, '/home/jimmy/qemu-sgi'); "
            "from pyirix_qemu.irix_telnet import IRIXTelnet; "
            "t=IRIXTelnet(port=2324, timeout=30); t.connect(retries=10,delay=2); "
            "t.login(user='root', password=''); "
            "print(t.run('for i in 1 2 3 4 5 6 7 8 9 10; do touch /tmp/x$i; done; sync', timeout=15)); "
            "print(t.run('rm -f /tmp/x*; sync', timeout=10)); "
            "print(t.run('cat /etc/passwd > /dev/null; ls -la /usr/bin/X11 > /dev/null; sync', timeout=15)); "
            "t.close()"
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        # Now attach gdb in parallel — it'll catch whatever the workload triggers
        run_gdb()
        try:
            drv.wait(timeout=60)
        except subprocess.TimeoutExpired:
            drv.kill()
    finally:
        # leave QEMU alone (user can inspect further); just print pid
        print(f"[final] qemu still pid={p.pid}, monitor at {MON}")


if __name__ == "__main__":
    sys.exit(main() or 0)
