#!/bin/sh
# dopatch §5o for the IP55 build tree: skip the cross-CPU icache-flush rendezvous
# (pure overhead + deadlock risk on QEMU TCG, which has no per-cpu icache).
K=/usr/tmp/v/irix/kern
MD=$K/os/machdep.c
echo "anchors: cachesema=`grep -c '^static sema_t cachesema;' $MD` cond=`grep -c '|| icaches_synced) {' $MD`"
if grep -q VTXNOXICACHE $MD; then
  echo "already patched"
else
  awk '
/^static sema_t cachesema;/ && !d { print; print "int virtuix_xicache_local = 1; /* VTXNOXICACHE */"; d=1; next }
{print}
' $MD > $MD.x && mv $MD.x $MD
  sed 's/|| icaches_synced) {/|| icaches_synced || virtuix_xicache_local) {/' $MD > $MD.x && mv $MD.x $MD
fi
echo "PATCHED_5o: decl=`grep -c VTXNOXICACHE $MD` cond=`grep -c 'icaches_synced || virtuix_xicache_local' $MD`"
