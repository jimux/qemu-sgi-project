#!/opt/linux/bin/sh
# IRIX Appliance Container — BusyBox-style exec interceptor
#
# Installed at /usr/local/bin/irix-dispatch with symlinks from bash, sh,
# and ~60 common commands. All docker exec invocations route through here.
#
# Invocation detection:
#   sh -c "CMD"       → non-interactive rsh to IRIX
#   bash (no args)    → interactive telnet to IRIX
#   $0 = other name   → BusyBox-style: basename $0 becomes the command
#
# All internal operations use /opt/linux/bin/ absolute paths.

# ── Absolute paths to hidden Linux tools ──
CAT=/opt/linux/bin/cat
ECHO=/opt/linux/bin/echo
SLEEP=/opt/linux/bin/sleep
GREP=/opt/linux/bin/grep
TEST=/opt/linux/bin/test
BASENAME=/opt/linux/bin/basename
PRINTF=/opt/linux/bin/printf
KILL=/opt/linux/bin/kill
DATE=/opt/linux/bin/date
SOCAT=/opt/linux/bin/socat
STAT=/opt/linux/bin/stat
WC=/opt/linux/bin/wc
HEAD=/opt/linux/bin/head
TAIL=/opt/linux/bin/tail
TR=/opt/linux/bin/tr

STATE_DIR=/var/run/irix
STATE_FILE=$STATE_DIR/boot_state
BOOT_LOG=$STATE_DIR/boot.log
ERROR_FILE=$STATE_DIR/error_detail
SERIAL_SOCK=/tmp/serial.sock

RSH_PORT=5140
TELNET_PORT=2323

# ── Boot state check ──
get_state() {
    $CAT "$STATE_FILE" 2>/dev/null || $ECHO "unknown"
}

check_ready() {
    local STATE
    STATE=$(get_state)
    case "$STATE" in
        ready)
            return 0
            ;;
        error)
            $ECHO "IRIX container error:" >&2
            $CAT "$ERROR_FILE" 2>/dev/null >&2
            exit 1
            ;;
        *)
            $ECHO "IRIX is still booting (state: $STATE)" >&2
            $ECHO "Run 'docker exec <container> wait' to block until ready." >&2
            exit 2
            ;;
    esac
}

# ── Pseudo-commands (intercepted locally) ──
cmd_wait() {
    local WAIT_TIMEOUT=${1:-300}
    local DEADLINE=$($DATE +%s)
    DEADLINE=$(( DEADLINE + WAIT_TIMEOUT ))

    while true; do
        local STATE
        STATE=$(get_state)
        case "$STATE" in
            ready)
                $ECHO "IRIX is ready."
                exit 0
                ;;
            error)
                $ECHO "IRIX container error:" >&2
                $CAT "$ERROR_FILE" 2>/dev/null >&2
                exit 1
                ;;
            *)
                NOW=$($DATE +%s)
                if $TEST "$NOW" -ge "$DEADLINE"; then
                    $ECHO "Timed out waiting for IRIX (state: $STATE)" >&2
                    exit 1
                fi
                $ECHO "Waiting for IRIX... (state: $STATE)"
                $SLEEP 5
                ;;
        esac
    done
}

cmd_status() {
    local STATE
    STATE=$(get_state)
    $ECHO "Boot state: $STATE"

    # QEMU PID
    local QEMU_PID
    QEMU_PID=$($GREP -o 'QEMU PID: [0-9]*' "$BOOT_LOG" 2>/dev/null | $HEAD -1)
    if $TEST -n "$QEMU_PID"; then
        $ECHO "$QEMU_PID"
    fi

    # Log size
    if $TEST -f "$BOOT_LOG"; then
        local LOG_LINES
        LOG_LINES=$($WC -l < "$BOOT_LOG" 2>/dev/null)
        $ECHO "Boot log: $LOG_LINES lines"
    fi

    if $TEST "$STATE" = "error" && $TEST -f "$ERROR_FILE"; then
        $ECHO ""
        $ECHO "Error detail:"
        $CAT "$ERROR_FILE"
    fi
}

cmd_logs() {
    if $TEST -f "$BOOT_LOG"; then
        $CAT "$BOOT_LOG"
    else
        $ECHO "No boot log available." >&2
        exit 1
    fi
}

cmd_serial() {
    # Raw serial console access (bypasses telnet, uses serial pipe)
    check_ready
    exec $SOCAT -,raw,echo=0 UNIX-CONNECT:/tmp/serial.sock 2>/dev/null \
        || exec /opt/linux/bin/busybox telnet 127.0.0.1 $TELNET_PORT
}

# ── Command routing ──
run_interactive() {
    check_ready
    # Connect via telnet — telnetd allocates a PTY for proper terminal handling.
    # Root has no password; type "root" at the login prompt.
    # exec ensures clean exit and signal handling.
    exec /opt/linux/bin/busybox telnet 127.0.0.1 $TELNET_PORT
}

run_rexec() {
    local CMD="$1"
    check_ready
    # Custom rexec service: send command terminated by newline, read output.
    # The service runs /bin/sh -c "$cmd" and sends stdout+stderr back.
    $PRINTF '%s\n' "$CMD" \
        | $SOCAT -T10 - TCP:127.0.0.1:$RSH_PORT,connect-timeout=5 2>/dev/null
}

# ── Determine invocation mode ──
INVOKED_AS=$($BASENAME "$0" 2>/dev/null || $ECHO "irix-dispatch")

case "$INVOKED_AS" in
    sh)
        # docker exec runs: sh -c "COMMAND"
        if $TEST "$1" = "-c" && $TEST -n "$2"; then
            CMD="$2"

            # Extract the actual command for pseudo-command check
            FIRST_WORD=$($ECHO "$CMD" | $HEAD -1)
            case "$FIRST_WORD" in
                wait|wait\ *)
                    # Extract optional timeout argument
                    TIMEOUT_ARG=$($ECHO "$CMD" | $TR -s ' ' | $HEAD -1)
                    TIMEOUT_ARG=${TIMEOUT_ARG#wait}
                    TIMEOUT_ARG=$($ECHO "$TIMEOUT_ARG" | $TR -d ' ')
                    cmd_wait ${TIMEOUT_ARG:-300}
                    ;;
                status)  cmd_status ;;
                logs)    cmd_logs ;;
                serial)  cmd_serial ;;
                *)       run_rexec "$CMD" ;;
            esac
        elif $TEST $# -eq 0; then
            # Bare `sh` — interactive
            run_interactive
        else
            # sh with other args — pass through as command
            run_rexec "sh $*"
        fi
        ;;
    bash)
        if $TEST $# -eq 0; then
            # docker exec -it <container> bash → interactive
            run_interactive
        elif $TEST "$1" = "-c" && $TEST -n "$2"; then
            run_rexec "$2"
        else
            run_rexec "bash $*"
        fi
        ;;
    irix-dispatch)
        # Direct invocation: irix-dispatch COMMAND
        if $TEST $# -eq 0; then
            run_interactive
        else
            case "$1" in
                wait)    shift; cmd_wait "$@" ;;
                status)  cmd_status ;;
                logs)    cmd_logs ;;
                serial)  cmd_serial ;;
                *)       run_rexec "$*" ;;
            esac
        fi
        ;;
    wait)    cmd_wait "$@" ;;
    status)  cmd_status ;;
    logs)    cmd_logs ;;
    serial)  cmd_serial ;;
    *)
        # BusyBox-style: invoked as /usr/local/bin/ls → run "ls ARGS" on IRIX
        # If invoked with no args AND we have a TTY (docker exec -it), route
        # shell-like commands to interactive telnet instead of rexec.
        if $TEST $# -eq 0 && $TEST -t 0; then
            case "$INVOKED_AS" in
                csh|ksh|login) run_interactive ;;
                *)             run_rexec "$INVOKED_AS" ;;
            esac
        elif $TEST $# -eq 0; then
            run_rexec "$INVOKED_AS"
        else
            run_rexec "$INVOKED_AS $*"
        fi
        ;;
esac
