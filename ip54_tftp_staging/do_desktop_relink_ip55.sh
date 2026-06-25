#!/bin/sh
# Desktop IP55 relink: link /unix.ip55.g from the -DIP55 IP55bootarea objects
# with the REAL Indy gfx/input/textport drivers (not gfxstubs), so the IP55-
# sourced kernel boots to the 4Dwm desktop. Consolidates relink2/4/5/6 of the
# proven IP22-virtuix recipe, adapted to IP55bootarea + the IP55 build master.d.
# Idempotent. (tport.a/tportpckbd.a/qcntl.o must already be staged into $KB.)
R=/usr/tmp/v/root
KB=/usr/tmp/v/irix/kern/IP55bootarea
MD=$R/usr/sysgen/master.d
SMD=/var/sysgen/master.d        # stock descriptors
B=/var/sysgen/boot              # stock gfx objects
SPEC=$R/usr/sysgen/IP22boot/system.kdebug/irix.sm

echo "=== 1. spec: restore pristine, drop gfxstubs/ng1stubs, append real-gfx block ==="
[ -f $SPEC.preg ] || cp -f $SPEC $SPEC.preg
cp -f $SPEC.preg $SPEC
sed -e 's/gfxstubs, //g' -e 's/ng1stubs, //g' -e 's/, ng1stubs//g' -e 's/, gfxstubs//g' \
    $SPEC > $SPEC.t && mv $SPEC.t $SPEC
cat >> $SPEC <<'GEOF'

* ====== IP55: genuine Indy gfx + input + textport (replaces gfxstubs) ======
INCLUDE: shmiq idev
INCLUDE: mouse keyboard
INCLUDE: tport tportpckbd
INCLUDE: htport
INCLUDE: ng1
INCLUDE: gfxs rrm xconn
INCLUDE: gfx
INCLUDE: kdsp
GEOF
grep -n 'gfxstubs\|ng1stubs\|gr2stubs' $SPEC | head

echo "=== 2. stage 15 master.d descriptors (stock -> build tree) ==="
for m in gfx gfxs ng1 rrm xconn shmiq idev mouse keyboard htport kdsp a2_dd tport tportpckbd qcntl; do
  if [ -f $SMD/$m ]; then cp -f $SMD/$m $MD/; else echo "  MISSING descriptor $SMD/$m"; fi
done
echo "descriptors now in build master.d:"
for m in gfx shmiq tport qcntl ng1; do ls $MD/$m >/dev/null 2>&1 && echo "  ok $m" || echo "  NO $m"; done

echo "=== 3. vce_avoidance + biozero shims into master.d/gfx C-section (idempotent) ==="
# vce_avoidance=0: stock gfx/audio objects reference this VCE global; 0 is correct
#   on QEMU TCG (no caches -> no virtual-coherency exceptions to avoid).
# biozero(): xfs_rw.o references it under #ifdef _VCE_AVOIDANCE, but ONLY calls it
#   when (vce_avoidance != 0). With vce_avoidance=0 the runtime takes the else
#   (bp_mapin/bzero) path, so this no-op is LINKED-BUT-NEVER-CALLED -> safe.
if grep 'vce_avoidance' $MD/gfx >/dev/null 2>&1; then
  echo "  already present"
else
  echo '' >> $MD/gfx
  echo '/* MP-build shims: SP gfx/audio objects ref the VCE global; xfs refs biozero' >> $MD/gfx
  echo '   under _VCE_AVOIDANCE but only calls it when vce_avoidance!=0 (never, here). */' >> $MD/gfx
  echo 'int vce_avoidance = 0;' >> $MD/gfx
  echo 'void biozero() { }' >> $MD/gfx
  echo "  appended"
fi

echo "=== 4. stage gfx objects (stock boot -> IP55bootarea); drop gfx/ng1 stubs ==="
for f in ng1.a gfx.o gfxs.a rrm.o xconn.o kdsp.a a2_dd.o shmiq.o idev.o mouse.a keyboard.a htport.o; do
  if [ -f $B/$f ]; then cp -f $B/$f $KB/; else echo "  MISSING object $B/$f"; fi
done
# tport.a / tportpckbd.a / qcntl.o were pre-staged from ip55_desktop_kernel/objects/
for f in tport.a tportpckbd.a qcntl.o; do ls $KB/$f >/dev/null 2>&1 && echo "  staged $f" || echo "  NO $f (push from objects/)"; done
rm -f $KB/gfxstubs.a $KB/ng1stubs.a     # keep gr2stubs.a

echo "=== 5. lboot /unix.ip55.g ==="
cd /
/usr/sbin/lboot -v -m $MD -b $KB -u /unix.ip55.g \
   -s $R/usr/sysgen/IP22boot/system.kdebug -c $R/usr/sysgen/stune \
   -n $R/usr/sysgen/mtune > /tmp/lbdesk.out 2>&1
echo "LBOOT_RC=$?"
echo "=undefined (unique)="
grep -E 'Undefined' /tmp/lbdesk.out | sed -e 's/.*symbol //' | sort -u | head -25
echo "=multiply-defined (watch vce_avoidance)="
grep -iE 'multiply defined' /tmp/lbdesk.out | grep -i vce_avoidance | head
echo "=fatal="
grep -iE 'cannot open|ERROR 3|FATAL|removed because' /tmp/lbdesk.out | head
echo "=result="
ls -l /unix.ip55.g 2>&1
file /unix.ip55.g 2>/dev/null
