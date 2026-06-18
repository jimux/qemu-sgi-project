#!/bin/sh
# Start full IRIX 4Dwm desktop on IP54
# Run as root from serial console after boot

export HOME=/root
export USER=root
export DISPLAY=:0
export SHELL=/bin/sh
export PATH=/usr/sbin:/usr/bsd:/sbin:/usr/bin:/bin:/usr/bin/X11

# Ensure graphics init
/usr/gfx/gfxinit > /dev/null 2>&1

# Start X server with SGI defaults
/usr/bin/X11/Xsgi :0 -ac -bs -nobitscale -c -pseudomap 4sight \
    -solidroot sgilightblue -cursorFG red -cursorBG white \
    -gamma 1.7 \
    > /tmp/xsgi.log 2>&1 &
XPID=$!
echo "Xsgi started (PID $XPID), waiting for server..."

# Wait for X server to be ready
sleep 10
n=0
while [ $n -lt 10 ]; do
    if xdpyinfo > /dev/null 2>&1; then
        echo "X server ready"
        break
    fi
    n=`expr $n + 1`
    sleep 2
done

# Disable autorepeat
xset r off 2>/dev/null

# Load SGI scheme resources BEFORE starting any apps
# This is critical — apps read resources at startup
if [ -f /tmp/sgi_scheme.resources ]; then
    xrdb -load /tmp/sgi_scheme.resources 2>/dev/null
    echo "Loaded pre-resolved SGI scheme resources"
elif [ -f /usr/lib/X11/app-defaults/Scheme ]; then
    xrdb -load /usr/lib/X11/app-defaults/Scheme 2>/dev/null
    echo "Loaded Scheme app-defaults"
fi

# Load user resources (merged on top of scheme)
if [ -r $HOME/.Xresources ]; then
    xrdb -merge -quiet $HOME/.Xresources
fi
if [ -r $HOME/.Sgiresources ]; then
    xrdb -merge -quiet $HOME/.Sgiresources
fi

# Set up environment
if [ -x /usr/bin/X11/userenv ]; then
    eval `/usr/bin/X11/userenv`
fi

# Start 4Dwm
echo "Starting 4Dwm..."
/usr/bin/X11/4Dwm > /dev/null 2>&1 &
sleep 2
/usr/bin/X11/wait4wm 2>/dev/null
/usr/bin/X11/wait4wm 2>/dev/null
echo "4Dwm ready"

# Start console (iconic)
/usr/sbin/startconsole -iconic > /dev/null 2>&1 &

# Run user sgisession if present
if [ -x $HOME/.sgisession ]; then
    $HOME/.sgisession > /dev/null 2>&1 &
fi

# Start toolchest
echo "Starting toolchest..."
if [ -r $HOME/.chestrc ]; then
    chestrc=$HOME/.chestrc
else
    chestrc=/usr/lib/X11/nodesktop.chestrc
fi
/usr/bin/X11/toolchest -name ToolChest $chestrc > /dev/null 2>&1 &

# Start an xterm for interaction
/usr/bin/X11/xterm -geometry 80x40+50+50 > /dev/null 2>&1 &

echo "=== Desktop started ==="
echo "4Dwm + toolchest + xterm running on :0"
echo "Use newport_screendump to view"

# Keep script running (reaper role)
exec /usr/bin/X11/reaper
