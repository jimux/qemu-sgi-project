#!/bin/sh
# Setup full 4Dwm desktop environment on IP54
# Run as root after TFTP transfer

# Copy config files from TFTP staging
cp /tmp/xdm-config /var/X11/xdm/xdm-config
cp /tmp/Xsetup_0 /var/X11/xdm/Xsetup_0
chmod 644 /var/X11/xdm/xdm-config
chmod 755 /var/X11/xdm/Xsetup_0

# Enable windowsystem, disable visuallogin (clogin needs real input)
chkconfig windowsystem on
chkconfig visuallogin off

# Create xdm init link if missing
if [ ! -f /etc/rc2.d/S98xdm ]; then
    ln -s /etc/init.d/xdm /etc/rc2.d/S98xdm
fi

# Verify
echo "=== Desktop Setup Complete ==="
echo "chkconfig:"
chkconfig | grep -E 'windowsystem|desktop|visuallogin|xdm'
echo "xdm-config:"
cat /var/X11/xdm/xdm-config
echo "S98xdm link:"
ls -la /etc/rc2.d/S98xdm
echo "=== Reboot with init 6 to start desktop ==="
