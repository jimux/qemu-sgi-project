/* pvfb framebuffer test - minimal startup, no crt1.o needed */
extern int open(const char *, int, ...);
extern int close(int);
extern int ioctl(int, int, ...);
extern void *mmap(void *, unsigned int, int, int, int, long long);
extern int write(int, const void *, unsigned int);
extern unsigned int sginap(long);
extern void exit(int);
extern void perror(const char *);
extern int chmod(const char *, int);

struct pvfb_mode { unsigned int width, height, format; };

void __start() {
  int fd, x, y, w=640, h=480, sz=640*480*4;
  struct pvfb_mode mode;
  unsigned int *fb;
  chmod("/hw/pvfb", 0666);
  fd = open("/hw/pvfb", 2);
  if (fd == -1) { perror("open /dev/pvfb"); exit(1); }
  write(1, "opened ok\n", 10);
  mode.width=w; mode.height=h; mode.format=0;
  if (ioctl(fd, 0x5000, &mode) == -1) { perror("setmode"); exit(1); }
  write(1, "setmode ok\n", 11);
  fb = (unsigned int *)mmap(0, sz, 3, 1, fd, 0);
  if ((long)fb == -1) { perror("mmap"); exit(1); }
  write(1, "mmap ok\n", 8);
  for (y=0; y != h; y++)
    for (x=0; x != w; x++)
      fb[y*w+x]=((x*255/w)<<24)|((y*255/h)<<16)|(128<<8)|255;
  ioctl(fd, 0x5001, 0);
  write(1, "done\n", 5);
  sginap(500);
  close(fd);
  exit(0);
}
