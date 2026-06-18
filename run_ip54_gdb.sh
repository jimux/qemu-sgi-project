#!/bin/bash
# Run QEMU under gdb to catch the SIGSEGV

DISK=/workspace/vm_instances/ip54-test/disk.qcow2
PROM=/workspace/prom-building/build/ip54.bin
QEMU=/workspace/qemu/build-linux/qemu-system-mips64
PC_BIOS=/workspace/qemu/build-linux/pc-bios
TMPDIR=$(mktemp -d /tmp/ip54_gdb_XXXXXX)
SERIAL_SOCK=$TMPDIR/serial.sock
MONITOR_SOCK=$TMPDIR/monitor.sock

# GDB batch file
cat > $TMPDIR/gdb_cmds.txt << 'EOF'
set pagination off
set confirm off
run
bt
quit
EOF

CMD="$QEMU -M sgi-ip54 -bios $PROM -m 256M -L $PC_BIOS -display none -serial chardev:ser0 -monitor unix:$MONITOR_SOCK,server,nowait -chardev socket,id=ser0,path=$SERIAL_SOCK,server=on,wait=on -drive if=mtd,file=$DISK,format=qcow2,cache=writethrough,file.locking=off,snapshot=on"

echo "Running: gdb $QEMU"
echo "Args: $CMD"

gdb -batch -x $TMPDIR/gdb_cmds.txt --args $CMD 2>&1 | tee /workspace/ip54_gdb.txt &
GDB_PID=$!

# Connect serial socket once it appears
for i in $(seq 1 30); do
    if [ -S $SERIAL_SOCK ]; then
        break
    fi
    sleep 0.3
done

# Connect and send boot commands
if [ -S $SERIAL_SOCK ]; then
    echo "Connecting to serial..."
    (echo ""; sleep 2; echo "5"; sleep 1; echo "boot -f dksc(0,1,0)/unix.new"; sleep 600) | nc -U $SERIAL_SOCK &
    NC_PID=$!
fi

# Wait for gdb to finish
wait $GDB_PID

# Kill nc
kill $NC_PID 2>/dev/null

rm -rf $TMPDIR
echo "GDB output saved to /workspace/ip54_gdb.txt"
