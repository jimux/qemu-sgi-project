B=/var/sysgen/boot
KB=/usr/tmp/v/irix/kern/IP22bootarea
echo "=== gfx/input/audio objects in /var/sysgen/boot ==="
for f in ng1.a gr2.a gfx.o gfxs.a rrm.o xconn.o kdsp.a a2_dd.o shmiq.o idev.o mouse.a keyboard.a tport.o tportpckbd.o htport.o; do
  if [ -f $B/$f ]; then echo "BOOT yes $f"; else echo "BOOT NO  $f"; fi
done
echo "=== which already staged in IP22bootarea ==="
for f in ng1.a gfx.o gfxs.a rrm.o xconn.o kdsp.a shmiq.o idev.o mouse.a keyboard.a tport.o tportpckbd.o htport.o gfxstubs.a ng1stubs.a gr2stubs.a; do
  if [ -f $KB/$f ]; then echo "KB yes $f"; else echo "KB NO  $f"; fi
done
echo "=== kdebug spec stubs line (the gfxstubs/ng1stubs source) ==="
grep -n 'gfxstubs\|ng1stubs\|gr2stubs' /usr/tmp/v/root/usr/sysgen/IP22boot/system.kdebug/irix.sm
echo "=== does kdebug spec already have ANY graphics module lines? ==="
grep -nc 'shmiq\|: ng1\|: gfx\| tport\|kdsp' /usr/tmp/v/root/usr/sysgen/IP22boot/system.kdebug/irix.sm
echo INSPECT_DONE
