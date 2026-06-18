#!/opt/linux/bin/sh
# Docker HEALTHCHECK script for IRIX appliance container.
# Reads boot state — exit 0 if ready, exit 1 otherwise.

STATE=$(/opt/linux/bin/cat /var/run/irix/boot_state 2>/dev/null)

case "$STATE" in
    ready) exit 0 ;;
    *)     exit 1 ;;
esac
