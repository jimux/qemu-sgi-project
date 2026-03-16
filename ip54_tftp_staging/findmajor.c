/* Try opening /dev with different major numbers to find pvfb */
extern int open(const char *, int, ...);
extern int close(int);
extern int write(int, const void *, unsigned int);
extern int mknod(const char *, int, int);
extern int unlink(const char *);
extern void exit(int);

void putnum(int fd, int n) {
  char buf[12];
  int i = 0, neg = 0;
  if (n < 0) { neg=1; n=-n; }
  if (n == 0) { buf[i++]='0'; }
  else { while(n>0) { buf[i++]='0'+(n%10); n/=10; } }
  if (neg) buf[i++]='-';
  /* reverse */
  { int j; char t; for(j=0;j<i/2;j++){t=buf[j];buf[j]=buf[i-1-j];buf[i-1-j]=t;} }
  write(fd, buf, i);
}

void __start() {
  int maj, fd;
  /* S_IFCHR = 0020000 */
  for (maj = 0; maj < 40; maj++) {
    unlink("/tmp/_probe");
    mknod("/tmp/_probe", 0020666, (maj << 8));
    fd = open("/tmp/_probe", 2);
    if (fd >= 0) {
      write(1, "major ", 6);
      putnum(1, maj);
      write(1, " opened ok\n", 11);
      close(fd);
    }
  }
  unlink("/tmp/_probe");
  write(1, "scan done\n", 10);
  exit(0);
}
