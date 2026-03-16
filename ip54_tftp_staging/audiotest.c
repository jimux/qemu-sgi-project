/* pvaudio test - 440Hz tone, minimal startup */
extern int open(const char *, int, ...);
extern int close(int);
extern int ioctl(int, int, ...);
extern int write(int, const void *, unsigned int);
extern void *malloc(unsigned int);
extern void free(void *);
extern void exit(int);

static int sine_approx(int phase_1024) {
  long x, x3;
  int quadrant = (phase_1024 >> 8) & 3;
  int frac = phase_1024 & 255;
  if (quadrant == 0) x = frac;
  else if (quadrant == 1) x = 255 - frac;
  else if (quadrant == 2) x = -frac;
  else x = -(255 - frac);
  x = x * 201 / 128;
  x3 = x * x * x / (256 * 256);
  x = x - x3 / 6;
  return (int)(x * 128);
}

void __start() {
  int fd, i;
  int rate = 44100;
  int channels = 2;
  int bits = 16;
  int nsamples = 44100 * 2;
  int bufsize = nsamples * 2 * 2;
  short *buf;

  fd = open("/hw/pvaudio", 1);
  if (fd == -1) { write(2, "open fail\n", 10); exit(1); }
  ioctl(fd, 0x6000, &rate);
  ioctl(fd, 0x6001, &channels);
  ioctl(fd, 0x6002, &bits);

  buf = (short *)malloc(bufsize);
  if (!buf) { write(2, "malloc fail\n", 12); exit(1); }

  for (i = 0; i < nsamples; i++) {
    int phase = (int)((long)i * 1024 * 440 / 44100) & 1023;
    short sample = (short)(sine_approx(phase) / 2);
    buf[i * 2] = sample;
    buf[i * 2 + 1] = sample;
  }

  write(fd, buf, bufsize);
  write(1, "audio done\n", 11);
  free(buf);
  close(fd);
  exit(0);
}
