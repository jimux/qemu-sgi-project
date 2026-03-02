#!/bin/sh
# Search all boot archives for a defined symbol
SYM="${1:-nested_spinlock}"
echo "Searching for DEFINED $SYM in /var/sysgen/boot/*.a ..."
for f in /var/sysgen/boot/*.a; do
    result=`nm "$f" 2>/dev/null | grep "$SYM" | grep -v "UNDEF"`
    if [ -n "$result" ]; then
        echo "=== $f ==="
        echo "$result"
    fi
done
echo "Done."
