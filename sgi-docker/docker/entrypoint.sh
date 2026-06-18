#!/opt/linux/bin/sh
# IRIX Appliance Container -- QEMU lifecycle manager (PID 1)
#
# Boot state machine: starting -> prom -> booting -> configuring -> ready (or error)
# State written to /var/run/irix/boot_state for dispatcher and healthcheck.
#
# Serial I/O uses named pipes (FIFOs) so we can read and write independently.
# QEMU's -chardev pipe reads from <path>.in and writes to <path>.out.
#
# All internal operations use /opt/linux/bin/ absolute paths to avoid
# triggering the dispatcher symlinks on $PATH.

# -- Absolute paths to hidden Linux tools --
SH=/opt/linux/bin/sh
CAT=/opt/linux/bin/cat
ECHO=/opt/linux/bin/echo
SLEEP=/opt/linux/bin/sleep
GREP=/opt/linux/bin/grep
TEST=/opt/linux/bin/test
PRINTF=/opt/linux/bin/printf
KILL=/opt/linux/bin/kill
DATE=/opt/linux/bin/date
MKDIR=/opt/linux/bin/mkdir
SOCAT=/opt/linux/bin/socat
RM=/opt/linux/bin/rm
TEE=/opt/linux/bin/tee

QEMU=/opt/qemu/bin/qemu-system-mips64

# -- Configuration --
IRIX_RAM=${IRIX_RAM:-64}
IRIX_SNAPSHOT=${IRIX_SNAPSHOT:-}
IRIX_BOOT_TIMEOUT=${IRIX_BOOT_TIMEOUT:-300}
IRIX_VNC=${IRIX_VNC:-}
IRIX_VNC_PORT=${IRIX_VNC_PORT:-0}
IRIX_MACHINE=${IRIX_MACHINE:-indy}
IRIX_EXTRA_ARGS=${IRIX_EXTRA_ARGS:-}

STATE_DIR=/var/run/irix
STATE_FILE=$STATE_DIR/boot_state
BOOT_LOG=$STATE_DIR/boot.log
SERIAL_PIPE=/tmp/serial_pipe
SERIAL_SOCK=/tmp/serial.sock
MONITOR_SOCK=/tmp/monitor.sock
DISK=/data/disk.qcow2
NVRAM=/data/nvram.bin
PROM=/opt/irix/prom.bin

# -- Helpers --
set_state() {
    $ECHO "$1" > "$STATE_FILE"
}

log() {
    $ECHO "[entrypoint] $1" | $TEE -a "$BOOT_LOG"
}

die() {
    log "ERROR: $1"
    set_state error
    $ECHO "$1" > "$STATE_DIR/error_detail"
    exec $SLEEP infinity
}

# Send text to IRIX via the serial input pipe.
# $PRINTF interprets escape sequences in the format string, so we use
# %b to interpret \r in the argument.
serial_send() {
    $PRINTF '%b' "$1" > ${SERIAL_PIPE}.in
}

serial_send_line() {
    $PRINTF '%b\r' "$1" > ${SERIAL_PIPE}.in
    $SLEEP 1
}

# -- Signal forwarding --
cleanup() {
    $KILL "$SERIAL_WRITER_PID" "$SERIAL_READER_PID" 2>/dev/null
    if $TEST -n "$QEMU_PID" && $KILL -0 "$QEMU_PID" 2>/dev/null; then
        log "Shutting down QEMU (PID $QEMU_PID)"
        $KILL "$QEMU_PID"
        wait "$QEMU_PID" 2>/dev/null
    fi
    exit 0
}
trap cleanup TERM INT QUIT

# -- Initialize state --
$MKDIR -p "$STATE_DIR"
$ECHO "" > "$BOOT_LOG"
set_state starting
log "IRIX Appliance Container starting"
log "Machine: ${IRIX_MACHINE}, RAM: ${IRIX_RAM}MB, Snapshot: ${IRIX_SNAPSHOT:-none}, VNC: ${IRIX_VNC:-off}"

# -- Resolve disk image --
# Check /data/ (user-mounted volume) first, fall back to built-in (desktop image)
if ! $TEST -f "$DISK"; then
    if $TEST -f /opt/irix/disk.qcow2; then
        DISK=/opt/irix/disk.qcow2
        log "Using built-in disk image"
    else
        die "No disk image found at $DISK

To use this container, mount a volume with an IRIX disk image:

  docker run -d --name irix \\
    -v /path/to/irix_disk.qcow2:/data/disk.qcow2 \\
    -e IRIX_SNAPSHOT=irix65_booted \\
    irix

Or build the desktop image with a disk baked in:

  docker build --target desktop \\
    --build-arg DISK_IMAGE=irix_disk.qcow2 \\
    -f sgi-docker/docker/Dockerfile.irix -t irix-desktop .

Create a disk image with: harness_install(version='6.5')
See README.md for details."
    fi
fi

log "Disk image: $DISK"

# -- Create serial I/O pipes --
# QEMU's -chardev pipe reads from <path>.in and writes to <path>.out
$RM -f ${SERIAL_PIPE}.in ${SERIAL_PIPE}.out
/opt/linux/bin/busybox mkfifo ${SERIAL_PIPE}.in ${SERIAL_PIPE}.out

# -- Build QEMU command line --
# Primary serial uses pipe chardev for boot monitoring (read .out, write .in).
# A second chardev on a Unix socket is available for the dispatcher's
# interactive serial access via the "serial" pseudo-command.
QEMU_CMD="$QEMU \
    -M $IRIX_MACHINE \
    -m ${IRIX_RAM}M \
    -bios $PROM \
    -display none \
    -chardev pipe,id=ser0,path=$SERIAL_PIPE \
    -serial chardev:ser0 \
    -monitor unix:$MONITOR_SOCK,server,nowait \
    -drive if=scsi,bus=0,unit=1,file=$DISK,format=qcow2,cache=writethrough,file.locking=off \
    -global sgi-hpc3.autoload=true \
    -icount shift=0,sleep=off \
    -nic user,model=sgi-hpc3,hostfwd=tcp:127.0.0.1:2323-10.0.2.15:23,hostfwd=tcp:127.0.0.1:5140-10.0.2.15:514"

# Add VNC display if requested
if $TEST -n "$IRIX_VNC"; then
    QEMU_CMD="$QEMU_CMD -vnc :${IRIX_VNC_PORT},to=99"
    log "VNC enabled on display :${IRIX_VNC_PORT}"
fi

# Append extra QEMU arguments
if $TEST -n "$IRIX_EXTRA_ARGS"; then
    QEMU_CMD="$QEMU_CMD $IRIX_EXTRA_ARGS"
fi

# Add NVRAM if present
if $TEST -f "$NVRAM"; then
    QEMU_CMD="$QEMU_CMD -global sgi-hpc3.nvram-file=$NVRAM"
    log "NVRAM: $NVRAM"
fi

# Add snapshot restore if specified
if $TEST -n "$IRIX_SNAPSHOT"; then
    QEMU_CMD="$QEMU_CMD -loadvm $IRIX_SNAPSHOT"
    log "Restoring snapshot: $IRIX_SNAPSHOT"
fi

# -- Launch QEMU --
log "Starting QEMU..."

# Open both ends of the serial FIFOs before starting QEMU.
# FIFO open() blocks until both ends are connected, so we need:
#   1. A reader on .out (so QEMU can open .out for writing)
#   2. A writer on .in (so QEMU can open .in for reading)
# The reader pipes serial output into the boot log.
# The writer holds the pipe open via a file descriptor; serial_send
# writes to the same FIFO for actual commands.
$CAT ${SERIAL_PIPE}.out >> "$BOOT_LOG" &
SERIAL_READER_PID=$!

# Hold .in open with a persistent fd (sleep infinity keeps it open)
$SLEEP infinity > ${SERIAL_PIPE}.in &
SERIAL_WRITER_PID=$!

$SH -c "$QEMU_CMD" >> "$BOOT_LOG" 2>&1 &
QEMU_PID=$!
log "QEMU PID: $QEMU_PID"

# Give QEMU time to start (snapshot restore needs longer)
if $TEST -n "$IRIX_SNAPSHOT"; then
    $SLEEP 5
else
    $SLEEP 3
fi

# Verify QEMU started
if ! $KILL -0 "$QEMU_PID" 2>/dev/null; then
    die "QEMU failed to start. Check logs: docker exec <container> logs"
fi

# -- Snapshot path: skip serial monitoring --
if $TEST -n "$IRIX_SNAPSHOT"; then
    log "Snapshot restore -- probing for IRIX readiness..."
    set_state booting

    DEADLINE=$($DATE +%s)
    DEADLINE=$(( DEADLINE + IRIX_BOOT_TIMEOUT ))

    while true; do
        NOW=$($DATE +%s)
        if $TEST "$NOW" -ge "$DEADLINE"; then
            log "Snapshot timeout -- attempting fallback network config"
            break
        fi

        # Probe rsh port (514 forwarded to 5140)
        if $ECHO "" | $SOCAT -T2 - TCP:127.0.0.1:5140,connect-timeout=2 2>/dev/null; then
            log "rsh port responding -- IRIX is ready"
            set_state ready
            wait "$QEMU_PID" 2>/dev/null
            exit 0
        fi

        $SLEEP 3
    done
fi

# -- Serial monitoring path (cold boot or snapshot fallback) --
set_state prom
log "Monitoring serial output for boot milestones..."

DEADLINE=$($DATE +%s)
DEADLINE=$(( DEADLINE + IRIX_BOOT_TIMEOUT ))
LAST_STATE=prom
BOOT_ASSIST_DONE=0

while true; do
    NOW=$($DATE +%s)
    if $TEST "$NOW" -ge "$DEADLINE"; then
        log "Boot timeout after ${IRIX_BOOT_TIMEOUT}s (state: $LAST_STATE)"
        die "Boot timeout -- IRIX did not reach login prompt within ${IRIX_BOOT_TIMEOUT}s.
Current state: $LAST_STATE
Check logs: docker exec <container> logs"
    fi

    if ! $KILL -0 "$QEMU_PID" 2>/dev/null; then
        die "QEMU exited unexpectedly during boot"
    fi

    # Check boot log for milestones (most specific first)
    if $GREP -q "PANIC\|panic:" "$BOOT_LOG" 2>/dev/null; then
        die "Kernel panic detected during boot"
    fi

    if $GREP -q "login:" "$BOOT_LOG" 2>/dev/null; then
        if $TEST "$LAST_STATE" != "configuring"; then
            LAST_STATE=configuring
            set_state configuring
            log "Login prompt detected -- configuring networking"
            break
        fi
    fi

    if $GREP -q "IRIX Release" "$BOOT_LOG" 2>/dev/null; then
        if $TEST "$LAST_STATE" = "prom"; then
            LAST_STATE=booting
            set_state booting
            log "Kernel booting"
        fi
    fi

    # Handle autoboot failure or PROM menu -- send Enter then "1" to boot
    if $TEST "$BOOT_ASSIST_DONE" != "1"; then
        if $GREP -q "Autoboot failed\|Hit Enter to continue\|System Maintenance Menu\|Option?" "$BOOT_LOG" 2>/dev/null; then
            log "PROM menu detected -- selecting boot from disk"
            serial_send "\r"
            $SLEEP 2
            serial_send "1\r"
            BOOT_ASSIST_DONE=1
        fi
    fi

    $SLEEP 3
done

# -- Configure networking via serial console (fallback) --
log "Configuring IRIX networking via serial..."

serial_send_line "root"
$SLEEP 3

# Dismiss TERM prompt if present
serial_send_line ""
$SLEEP 2

# Switch from csh to /bin/sh -- IRIX root shell is csh, which does
# history expansion on '!' (even in single quotes) and doesn't support
# Bourne shell redirect syntax (2>/dev/null).
serial_send_line "exec /bin/sh"
$SLEEP 2

# Configure network (idempotent)
serial_send_line "ifconfig ec0 inet 10.0.2.15 netmask 255.255.255.0 up 2>/dev/null; echo NET_IF_OK"
$SLEEP 2

serial_send_line "route add default 10.0.2.2 2>/dev/null; echo NET_RT_OK"
$SLEEP 2

# Set up rexec service -- a simple command executor that reads a
# newline-terminated command from TCP and runs it with /bin/sh.
serial_send_line "echo '#!/bin/sh' > /tmp/rexec.sh"
$SLEEP 1
serial_send_line "echo 'read cmd' >> /tmp/rexec.sh"
$SLEEP 1
serial_send_line "echo 'exec /bin/sh -c \"\$cmd\" 2>&1' >> /tmp/rexec.sh"
$SLEEP 1
serial_send_line "chmod 755 /tmp/rexec.sh; echo REXEC_SCRIPT_OK"
$SLEEP 2

# Replace rshd with rexec in inetd.conf
serial_send_line "cp /etc/inetd.conf /etc/inetd.conf.bak 2>/dev/null"
$SLEEP 1
serial_send_line "sed 's|^shell.*stream.*tcp.*rshd.*|shell\tstream\ttcp\tnowait\troot\t/tmp/rexec.sh\trexec.sh|' /etc/inetd.conf > /tmp/inetd.conf.new && cp /tmp/inetd.conf.new /etc/inetd.conf; echo INETD_CONF_OK"
$SLEEP 2

# Configure hosts.equiv for rsh trust
serial_send_line "echo 'localhost root' > /etc/hosts.equiv; echo '+ root' >> /etc/hosts.equiv; echo HOSTS_EQUIV_OK"
$SLEEP 2

# Ensure inetd is running (may not be started if network was in standalone mode)
serial_send_line "killall inetd 2>/dev/null; /usr/etc/inetd; echo INETD_STARTED"
$SLEEP 3

# -- Wait for exec port --
# Note: SLIRP accepts TCP connections regardless of whether the guest port
# is open, so we probe by sending a test command and checking for output.
log "Waiting for exec port..."
RSH_DEADLINE=$($DATE +%s)
RSH_DEADLINE=$(( RSH_DEADLINE + 60 ))

while true; do
    NOW=$($DATE +%s)
    if $TEST "$NOW" -ge "$RSH_DEADLINE"; then
        log "WARNING: exec port not responding after 60s, marking ready anyway"
        break
    fi

    # Send a test command and check for actual output (not just TCP connect)
    PROBE=$($PRINTF 'echo IRIX_PROBE_OK\n' | $SOCAT -T5 - TCP:127.0.0.1:5140,connect-timeout=3 2>/dev/null)
    if $ECHO "$PROBE" | $GREP -q "IRIX_PROBE_OK"; then
        log "Exec port responding"
        break
    fi

    $SLEEP 3
done

# -- Ready --
set_state ready
log "IRIX is ready."

wait "$QEMU_PID" 2>/dev/null
exit 0
