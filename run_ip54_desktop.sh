#!/bin/bash
# Launch the IP54 gold image with the Indigo Magic Desktop visible.
#
# What this does:
#   - Boots prebuilt_disks/ip54-6.5.5-gold.qcow2 on machine=sgi-ip54
#   - Uses the qemu-sgi-repo build (has IP54 paravirtual machine + devices)
#   - Sets IP54_CAUSE_IP5_COUNT_PA so the pvclock device finds the
#     kernel's cause_ip5_count symbol (DRIFTS WITH EVERY KERNEL REBUILD;
#     re-extract via `nm /unix.new | grep cause_ip5_count`)
#   - Shows the GTK framebuffer window
#   - Exposes telnet at host:2324 (forwarded to guest:23)
#
# After boot (~90 s): the X Window System login dialog appears on a
# solid SGI light-blue background. Log in as `root` with no password.
# Xsession spawns 4Dwm + toolchest — the Indigo Magic Desktop.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
QEMU="$PROJECT_ROOT/qemu-sgi-repo/build-linux/qemu-system-mips64"
PROM="$PROJECT_ROOT/PROM_library/bins/cpu/ip54/ip54.bin"
DISK="${1:-$PROJECT_ROOT/prebuilt_disks/ip54-6.5.5-gold.qcow2}"

# pvclock — IP54_CAUSE_IP5_COUNT_PA tracks the kernel's cause_ip5_count
# symbol. Stale value desyncs the timer; symptoms are weird stalls
# during boot. Recompute on every kernel rebuild.
if [[ -n "${IP54_CAUSE_IP5_COUNT_PA:-}" ]]; then
    echo "Using IP54_CAUSE_IP5_COUNT_PA=$IP54_CAUSE_IP5_COUNT_PA"
else
    export IP54_CAUSE_IP5_COUNT_PA=0x0829fee0
    echo "Using default IP54_CAUSE_IP5_COUNT_PA=$IP54_CAUSE_IP5_COUNT_PA"
    echo "  (for the v0.1 IP54 gold; rebuilds shift this address)"
fi

exec "$QEMU" \
    -M sgi-ip54 \
    -bios "$PROM" \
    -m 256M \
    -L "$PROJECT_ROOT/qemu-sgi-repo/build-linux/pc-bios" \
    -display gtk \
    -serial mon:stdio \
    -drive if=mtd,file="$DISK",format=qcow2,cache=writeback,file.locking=off \
    -nic user,tftp="$PROJECT_ROOT/ip54_tftp_staging",hostfwd=tcp::2324-10.0.2.15:23 \
    "$@"
