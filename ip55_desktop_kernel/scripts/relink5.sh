exec > /tmp/relink5.out 2>&1
set -x
R=/usr/tmp/v/root
KB=/usr/tmp/v/irix/kern/IP22bootarea
MD=$R/usr/sysgen/master.d
SPEC=$R/usr/sysgen/IP22boot/system.kdebug/irix.sm

echo "=== restore pristine kdebug spec, re-derive cleanly ==="
cp -f $SPEC.preg $SPEC
# remove gfxstubs + ng1stubs tokens (real gfx/ng1 provide those symbols)
sed -e 's/gfxstubs, //g' -e 's/ng1stubs, //g' -e 's/, ng1stubs//g' -e 's/, gfxstubs//g' $SPEC > $SPEC.t && mv $SPEC.t $SPEC
# append clean Indy graphics+input+textport+audio block (all INCLUDE/static)
cat >> $SPEC <<'GEOF'

* ====== Indy desktop drivers re-introduced (genuine gfx.sm/audio.sm) ======
INCLUDE: shmiq idev
INCLUDE: mouse keyboard
INCLUDE: tport tportpckbd
INCLUDE: htport
INCLUDE: ng1
INCLUDE: gfxs rrm xconn
INCLUDE: gfx
INCLUDE: kdsp
GEOF
echo "--- block ---"; tail -11 $SPEC
echo "--- stub line check (gr2stubs kept) ---"; grep -n 'gfxstubs\|ng1stubs\|gr2stubs' $SPEC

echo "=== relink ==="
cd /
/usr/sbin/lboot -v -m $MD -b $KB -u /unix.g -s $R/usr/sysgen/IP22boot/system.kdebug -c $R/usr/sysgen/stune -n $R/usr/sysgen/mtune > /tmp/lb5.out 2>&1
echo "LBOOT_RC=$?"
echo "=== remaining undefined (unique) ==="
grep -E 'Undefined' /tmp/lb5.out | sed -e 's/.*symbol //' | sort -u
echo "=== fatal errors ==="
grep -iE 'cannot open master|ERROR 3|FATAL|removed because' /tmp/lb5.out | head
echo "=== result ==="
ls -l /unix.g /unix.mp 2>&1
echo RELINK5_DONE
