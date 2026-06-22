exec > /tmp/relink6.out 2>&1
set -x
R=/usr/tmp/v/root; KB=/usr/tmp/v/irix/kern/IP22bootarea
MD=$R/usr/sysgen/master.d; SMD=/var/sysgen/master.d; B=/var/sysgen/boot
SPEC=$R/usr/sysgen/IP22boot/system.kdebug/irix.sm
echo "=== add qcntl descriptor + object (shmiq dependency) ==="
cp -f $SMD/qcntl $MD/ && echo "desc qcntl"
cp -f $B/qcntl.o $KB/ && echo "obj qcntl.o"
echo "=== relink /unix.g ==="
cd /
/usr/sbin/lboot -v -m $MD -b $KB -u /unix.g -s $R/usr/sysgen/IP22boot/system.kdebug -c $R/usr/sysgen/stune -n $R/usr/sysgen/mtune > /tmp/lb6.out 2>&1
echo "LBOOT_RC=$?"
echo "=== undefined / qcntl / shmiq lines ==="
grep -iE 'Undefined|cannot open master|shmiq|qcntl' /tmp/lb6.out | head
echo "=== result ==="; ls -l /unix.g 2>&1
echo RELINK6_DONE
