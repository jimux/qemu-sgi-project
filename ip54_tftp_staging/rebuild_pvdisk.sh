#!/bin/sh
# rebuild_pvdisk.sh - Recompile pvdisk.c and run lboot
# Run this on the IRIX ip54-test VM
# Fetch with: tftp 10.0.2.2; binary; get rebuild_pvdisk.sh /tmp/rebuild_pvdisk.sh; quit
# Run with: sh /tmp/rebuild_pvdisk.sh

set -e

echo "=== rebuild_pvdisk: starting ==="

# Step 1: Ensure /tmp/cc wrapper exists (needed by IP54.sm: CC: /tmp/cc)
if [ ! -x /tmp/cc ]; then
    echo "=== Creating /tmp/cc wrapper ==="
    cat > /tmp/cc << 'CCEOF'
#!/bin/sh
for a in "$@"; do
  case "$a" in *master.c)
    ed "$a" << 'ED'
/^struct edt edt\[\] = {/+1i
{ 0 }
.
w
q
ED
    ;;
  esac
done
exec /usr/bin/cc "$@"
CCEOF
    chmod +x /tmp/cc
    echo "  /tmp/cc created"
else
    echo "  /tmp/cc already exists"
fi

# Step 2: Fetch updated pvdisk.c
echo "=== Fetching pvdisk.c from TFTP ==="
tftp 10.0.2.2 << 'TEOF'
binary
get pvdisk.c /tmp/pvdisk.c
quit
TEOF
ls -la /tmp/pvdisk.c
echo "  pvdisk.c fetched OK"

# Step 3: Compile pvdisk.c
echo "=== Compiling pvdisk.c ==="
CC_FLAGS="-c -n32 -mips3 -O2 -G 8 -non_shared -TENV:kernel -DIP54 -D_KERNEL -I/usr/include"
/usr/bin/cc $CC_FLAGS /tmp/pvdisk.c -o /var/sysgen/boot/pvdisk.o
ls -la /var/sysgen/boot/pvdisk.o
nm /var/sysgen/boot/pvdisk.o | grep pvdiskdevflag
echo "  pvdisk.o compiled OK"

# Step 4: Check master.d/pvdisk
if [ ! -f /var/sysgen/master.d/pvdisk ]; then
    echo "=== Fetching master.d/pvdisk ==="
    tftp 10.0.2.2 << 'TEOF'
binary
get master.d/pvdisk /var/sysgen/master.d/pvdisk
quit
TEOF
    echo "  master.d/pvdisk installed"
else
    echo "  master.d/pvdisk already exists"
fi

# Step 5: Ensure IP54.sm is in place
if [ ! -f /var/sysgen/system/IP54.sm ]; then
    echo "=== Fetching IP54.sm ==="
    tftp 10.0.2.2 << 'TEOF'
binary
get IP54.sm /var/sysgen/system/IP54.sm
quit
TEOF
    echo "  IP54.sm installed"
else
    echo "  IP54.sm already exists"
fi

# Step 6: Check kernel.o exists (must be the merged IP54+IP22 version)
if [ ! -f /var/sysgen/boot/kernel.o ]; then
    echo "ERROR: /var/sysgen/boot/kernel.o not found!"
    echo "  The full setup_ip54.sh must be run first to create the merged kernel.o"
    exit 1
fi
echo "  kernel.o present: `ls -la /var/sysgen/boot/kernel.o | awk '{print $5}'` bytes"

# Step 7: Run lboot
echo "=== Running lboot ==="
echo "  Command: lboot -s /var/sysgen/system/IP54.sm"
lboot -s /var/sysgen/system/IP54.sm
echo "  lboot completed"

# Step 8: Verify /unix.new
echo "=== Verifying /unix.new ==="
ls -la /unix.new
file /unix.new
echo "=== rebuild_pvdisk: SUCCESS ==="
