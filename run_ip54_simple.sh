#!/bin/bash
# Simple IP54 boot test — uses expect-like approach
QEMU=/workspace/qemu/build-linux/qemu-system-mips64
PROM=/workspace/prom-building/build/ip54.bin
DISK=/workspace/vm_instances/ip54-test/disk.qcow2
LOG=/workspace/ip54_boot_v10.txt
ERRLOG=/workspace/ip54_stderr_v10.txt

# Use script(1) to force a PTY so QEMU doesn't buffer stdout
exec 2>"$ERRLOG"

# Start QEMU in background, with expect-style input
(
  sleep 5
  printf '5\r\n'
  sleep 2
  printf 'boot -f dksc(0,1,0)/unix.new\r\n'
  sleep 360
) | timeout 400 $QEMU \
    -M sgi-ip54 \
    -m 256 \
    -nographic \
    -bios "$PROM" \
    -serial mon:stdio \
    -drive "file=$DISK,format=qcow2,if=scsi,index=0" \
    | tee "$LOG"

echo ""
echo "=== QEMU exited with code $? ==="
