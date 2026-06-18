#!/bin/sh
# Bring up a fresh X + the 4Dwm desktop session, bypassing xdm/greeter. All output to files
# under /var/tmp so the host can read them after. Run as: sh /tmp/sdesktop.sh &
exec > /var/tmp/sdesktop.log 2>&1
set -x
PATH=/usr/bin/X11:/usr/sbin:/usr/bsd:/usr/bin:/sbin; export PATH
HOME=/root; export HOME
USER=root; export USER; LOGNAME=root; export LOGNAME

/etc/init.d/xdm stop
sleep 5
echo "=== xdm stopped, starting X ==="
/usr/bin/X11/X :0 -bs -nobitscale -c -pseudomap 4sight -solidroot sgilightblue -cursorFG red -cursorBG white -gamma 1.7 &
XPID=$!
sleep 10
DISPLAY=:0.0; export DISPLAY
echo "=== X pid=$XPID; xdpyinfo ==="
xdpyinfo > /var/tmp/xd.txt 2>&1
echo "xdpyinfo rc=$?  (lines: `wc -l < /var/tmp/xd.txt`)"

echo "=== colormap prep ==="
/usr/lib/desktop/makeIconVisuals > /var/tmp/miv.txt 2>&1; echo "makeIconVisuals rc=$?"
/usr/lib/desktop/preallocColors > /var/tmp/pac.txt 2>&1; echo "preallocColors rc=$?"

echo "=== 4Dwm ==="
/usr/bin/X11/4Dwm -launch -xrm "*SG_UseBackgrounds: True" > /var/tmp/wm.txt 2>&1 &
WM=$!
sleep 10
echo "4Dwm pid=$WM alive: `ps -e | egrep 4Dwm | grep -v grep`"

echo "=== xterm ==="
/usr/bin/X11/xterm -geometry 80x24+150+150 > /var/tmp/xt.txt 2>&1 &
sleep 8
echo "xterm alive: `ps -e | egrep xterm | grep -v grep`"

echo "=== toolchest ==="
/usr/bin/X11/toolchest -name ToolChest > /var/tmp/tc.txt 2>&1 &
sleep 5

echo "=== fm -b ==="
/usr/sbin/fm -b > /var/tmp/fm.txt 2>&1 &
sleep 10

ps -e | egrep "Xsgi| X$|4Dwm|toolchest|xterm| fm" | grep -v grep > /var/tmp/ps.txt 2>&1
echo "=== SDESKTOP_DONE ==="
