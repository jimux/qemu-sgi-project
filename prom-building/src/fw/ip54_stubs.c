/*
 * ip54_stubs.c -- Stub and real implementations for IP54 PROM
 *
 * This file provides:
 * - Global variables needed by PROM code
 * - ARCS component tree (hardware inventory)
 * - ARCS memory descriptors
 * - ARCS I/O functions (Open/Read/Write/Seek/Close)
 * - ARCS firmware callbacks
 * - Program loader (ECOFF + ELF32)
 * - SPB initialization
 * - GUI/serial stubs
 */

// clang-format off
#include <sys/types.h>
#include <arcs/types.h>
#include <arcs/hinv.h>
#include <arcs/signal.h>
#include <arcs/cfgtree.h>
#include <arcs/io.h>
#include <arcs/time.h>
#include <arcs/dirent.h>
#include <arcs/spb.h>
#include <arcs/eiob.h>
#include <arcs/errno.h>
#include <libsc.h>
#include <menu.h>
#include <genpda.h>
#include <setjmp.h>
#include <tty.h>
// clang-format on

/* fs_init / fs_search from libsk/fs/fs.c */
extern void fs_init(void);
extern int  fs_search(struct eiob *);

/* F_READ flag (from saio.h) needed to mark read-only opens */
#ifndef F_READ
#define F_READ 0x0001
#endif

/* Pull in cmd_table definition for command stubs */
struct cmd_table;

/* Forward declarations */
static void stub_puts(const char *);
static void stub_puthex(unsigned long v);
void init_component_tree(void);
void init_memory_descriptors(void);

/* ================================================================
 * DEBUG OUTPUT
 * ================================================================ */

static void debug_putchar(char c) { *(volatile unsigned char *)0xBF62017B = c; }

static void debug_marker(char c) {
  debug_putchar('[');
  debug_putchar(c);
  debug_putchar(']');
}

static void debug_puts(const char *s) {
  while (*s)
    debug_putchar(*s++);
}

static void stub_puts(const char *s) {
  volatile unsigned char *uart_thr = (volatile unsigned char *)0xBF62017B;
  volatile unsigned char *uart_lsr = (volatile unsigned char *)0xBF62017E;
  while (*s) {
    while ((*uart_lsr & 0x20) == 0)
      ;
    *uart_thr = *s++;
  }
}

static void stub_putchar_polled(char c) {
  volatile unsigned char *uart_thr = (volatile unsigned char *)0xBF62017B;
  volatile unsigned char *uart_lsr = (volatile unsigned char *)0xBF62017E;
  while ((*uart_lsr & 0x20) == 0)
    ;
  *uart_thr = c;
}

static void stub_puthex(unsigned long v) {
  const char hex[] = "0123456789abcdef";
  int i;
  stub_puts("0x");
  for (i = 28; i >= 0; i -= 4)
    stub_putchar_polled(hex[(v >> i) & 0xf]);
}

static void stub_putdec(unsigned long v) {
  char buf[12];
  int i = 0;
  if (v == 0) {
    stub_putchar_polled('0');
    return;
  }
  while (v > 0) {
    buf[i++] = '0' + (v % 10);
    v /= 10;
  }
  while (--i >= 0)
    stub_putchar_polled(buf[i]);
}

/* ================================================================
 * GLOBAL VARIABLES
 * ================================================================ */

int _icache_size = 32768;
int _icache_linesize = 32;
int _dcache_size = 32768;
int _dcache_linesize = 32;
int _scache_linesize = 32;
int _sidcache_size = 0;
int _r4600sc_sidcache_size = 0;

static libsc_private_t _libsc_private_storage[1];
libsc_private_t *_libsc_private = _libsc_private_storage;

int Debug = 0;
int Verbose = 0;
int _udpcksum = 1;
char *netaddr_default = "0.0.0.0";
unsigned long *_fault_sp = 0;
jmp_buf restart_buf;
gen_pda_t gen_pda_tab[1];
lock_t arcs_ui_lock = 0;
/* 256KB heap — malloc.c uses extern char malbuf[] with symbol addr as buffer base */
char malbuf[256 * 1024];
int _max_malloc = sizeof(malbuf);
volatile void *crm_GBE_Base = (volatile void *)0xB6000000;
int month_days[12] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
int symmon = 0;

int noLogo(void) { return 0; }

/* ================================================================
 * ARCS COMPONENT TREE
 * Static pool of cfgnode_t nodes, linked into a proper tree.
 * ================================================================ */

#define MAX_CFG_NODES 24

static cfgnode_t cfg_pool[MAX_CFG_NODES];
static int cfg_pool_used = 0;

/* Identifier strings (must persist since COMPONENT.Identifier points here) */
static char id_root[] = "SGI-IP32";
static char id_cpu[] = "MIPS-R5000";
static char id_fpu[] = "MIPS-R5000FPC";
static char id_mem[] = "memory";
static char id_scsi[] = "MACE_SCSI";
static char id_disk_ctrl[] = "disk_ctrl";
static char id_disk[] = "disk";

/* Allocate a cfgnode_t from the pool */
static cfgnode_t *cfg_alloc(void) {
  if (cfg_pool_used >= MAX_CFG_NODES)
    return (cfgnode_t *)0;
  return &cfg_pool[cfg_pool_used++];
}

/* AddChild -- add a component as a child of parent */
COMPONENT *AddChild(COMPONENT *parent, COMPONENT *tmpl, void *data) {
  cfgnode_t *pnode;
  cfgnode_t *newnode;
  cfgnode_t *peer;
  int i;

  (void)data;

  newnode = cfg_alloc();
  if (!newnode)
    return (COMPONENT *)0;

  /* Copy template into node */
  newnode->comp = *tmpl;
  newnode->parent = (cfgnode_t *)0;
  newnode->peer = (cfgnode_t *)0;
  newnode->child = (cfgnode_t *)0;
  newnode->driver = (PDRIVERSTRATEGY)0;
  newnode->cfgdata = (struct cfgdatahdr *)0;

  if (!parent) {
    /* This is the root node */
    return &newnode->comp;
  }

  /* Find parent's cfgnode_t by scanning pool */
  pnode = (cfgnode_t *)0;
  for (i = 0; i < cfg_pool_used; i++) {
    if (&cfg_pool[i].comp == parent) {
      pnode = &cfg_pool[i];
      break;
    }
  }
  if (!pnode) {
    /* Parent not in pool -- just return the component anyway */
    return &newnode->comp;
  }

  newnode->parent = pnode;

  if (!pnode->child) {
    pnode->child = newnode;
  } else {
    /* Append as last peer of existing children */
    peer = pnode->child;
    while (peer->peer)
      peer = peer->peer;
    peer->peer = newnode;
  }

  return &newnode->comp;
}

/* GetChild -- get first child of a component */
COMPONENT *GetChild(COMPONENT *c) {
  cfgnode_t *node;
  int i;

  if (!c) {
    /* GetChild(NULL) returns root */
    if (cfg_pool_used > 0)
      return &cfg_pool[0].comp;
    return (COMPONENT *)0;
  }

  for (i = 0; i < cfg_pool_used; i++) {
    if (&cfg_pool[i].comp == c) {
      node = &cfg_pool[i];
      if (node->child)
        return &node->child->comp;
      return (COMPONENT *)0;
    }
  }
  return (COMPONENT *)0;
}

/* GetPeer -- get next sibling of a component */
COMPONENT *GetPeer(COMPONENT *c) {
  int i;
  if (!c)
    return (COMPONENT *)0;

  for (i = 0; i < cfg_pool_used; i++) {
    if (&cfg_pool[i].comp == c) {
      if (cfg_pool[i].peer)
        return &cfg_pool[i].peer->comp;
      return (COMPONENT *)0;
    }
  }
  return (COMPONENT *)0;
}

/* GetParent -- get parent of a component */
COMPONENT *GetParent(COMPONENT *c) {
  int i;
  if (!c)
    return (COMPONENT *)0;

  for (i = 0; i < cfg_pool_used; i++) {
    if (&cfg_pool[i].comp == c) {
      if (cfg_pool[i].parent)
        return &cfg_pool[i].parent->comp;
      return (COMPONENT *)0;
    }
  }
  return (COMPONENT *)0;
}

/* DeleteComponent -- remove a component (stub, returns success) */
LONG DeleteComponent(COMPONENT *c) {
  (void)c;
  return 0;
}

/* GetComponent -- find component by path string */
COMPONENT *GetComponent(CHAR *path) {
  (void)path;
  return (COMPONENT *)0;
}

/* SaveConfiguration -- persist config (stub) */
LONG SaveConfiguration(void) { return 0; }

/* GetConfigData -- get config data for a component */
LONG GetConfigData(void *data, COMPONENT *c) {
  (void)data;
  (void)c;
  return 0;
}

/* ================================================================
 * MPCONF BLOCK
 * ================================================================ */

#define IP54_MPCONF_MAGIC 0x4D503534
#define IP54_MPCONF_ADDR 0x80001800
#define SGI_SMP_BASE 0xBF480000

typedef struct {
  unsigned int magic;
  unsigned int num_cpus;
  struct {
    unsigned int cpuid;
    unsigned int pr_id;
    unsigned int status; /* 1 = present */
    unsigned int reserved;
  } cpus[128];
} ip54_mpconf_t;

static void init_mpconf(void) {
  ip54_mpconf_t *mpc = (ip54_mpconf_t *)IP54_MPCONF_ADDR;
  unsigned int num_cpus = *(volatile unsigned int *)SGI_SMP_BASE;
  int i;

  mpc->magic = IP54_MPCONF_MAGIC;
  mpc->num_cpus = num_cpus;

  for (i = 0; i < 128; i++) {
    mpc->cpus[i].cpuid = i;
    if (i < num_cpus) {
      mpc->cpus[i].status = 1;     /* Present */
      mpc->cpus[i].pr_id = 0x0900; /* R5000 PR_ID approx */
    } else {
      mpc->cpus[i].status = 0; /* Absent */
      mpc->cpus[i].pr_id = 0;
    }
  }
}

/*
 * Build the component tree during initialization.
 * Tree structure:
 *   Root (SystemClass/ARC, "SGI-IP32")
 *   ├── CPU (ProcessorClass/CPU, "MIPS-R5000", key=0)
 *   │   ├── FPU (ProcessorClass/FPU, "MIPS-R5000FPC", key=0)
 *   │   ├── PrimaryICache (CacheClass/PrimaryICache, 32KB/32B line)
 *   │   └── PrimaryDCache (CacheClass/PrimaryDCache, 32KB/32B line)
 *   ├── Memory (MemoryClass/Memory, key=pages)
 *   └── SCSIAdapter (AdapterClass/SCSIAdapter, key=0)
 *       └── DiskController (ControllerClass/DiskController, key=1)
 *           └── DiskPeripheral (PeripheralClass/DiskPeripheral, key=0)
 */
void init_component_tree(void) {
  COMPONENT tmpl;
  COMPONENT *root, *cpu_c, *scsi_c, *disk_ctrl_c;
  union key_u cachekey;

  init_mpconf();

  /* Root */
  tmpl.Class = SystemClass;
  tmpl.Type = ARC;
  tmpl.Flags = 0;
  tmpl.Version = SGI_ARCS_VERS;
  tmpl.Revision = SGI_ARCS_REV;
  tmpl.Key = 0;
  tmpl.AffinityMask = 0;
  tmpl.ConfigurationDataSize = 0;
  tmpl.IdentifierLength = sizeof(id_root);
  tmpl.Identifier = id_root;
  root = AddChild((COMPONENT *)0, &tmpl, (void *)0);

  /* CPU */
  tmpl.Class = ProcessorClass;
  tmpl.Type = CPU;
  tmpl.Flags = 0;
  tmpl.Version = 0;
  tmpl.Revision = 0;
  tmpl.Key = 0;
  tmpl.AffinityMask = 1;
  tmpl.ConfigurationDataSize = 0;
  tmpl.IdentifierLength = sizeof(id_cpu);
  tmpl.Identifier = id_cpu;
  cpu_c = AddChild(root, &tmpl, (void *)0);

  /* FPU */
  tmpl.Class = ProcessorClass;
  tmpl.Type = FPU;
  tmpl.Flags = 0;
  tmpl.Key = 0;
  tmpl.IdentifierLength = sizeof(id_fpu);
  tmpl.Identifier = id_fpu;
  AddChild(cpu_c, &tmpl, (void *)0);

  /* Primary I-Cache: 32KB, 32-byte lines, 1 block per line */
  cachekey.cache.c_bsize = 1;  /* 1 block per line */
  cachekey.cache.c_lsize = 32; /* 32 bytes per line */
  cachekey.cache.c_size = 8;   /* 32KB = 8 * 4KB pages */
  tmpl.Class = CacheClass;
  tmpl.Type = PrimaryICache;
  tmpl.Flags = 0;
  tmpl.Key = cachekey.FullKey;
  tmpl.IdentifierLength = 0;
  tmpl.Identifier = (char *)0;
  AddChild(cpu_c, &tmpl, (void *)0);

  /* Primary D-Cache: 32KB, 32-byte lines */
  tmpl.Type = PrimaryDCache;
  tmpl.Key = cachekey.FullKey;
  AddChild(cpu_c, &tmpl, (void *)0);

  /* Memory -- key = total MB */
  tmpl.Class = MemoryClass;
  tmpl.Type = Memory;
  tmpl.Flags = 0;
  tmpl.Key = 64; /* 64MB default */
  tmpl.IdentifierLength = sizeof(id_mem);
  tmpl.Identifier = id_mem;
  AddChild(root, &tmpl, (void *)0);

  /* SCSI Adapter */
  tmpl.Class = AdapterClass;
  tmpl.Type = SCSIAdapter;
  tmpl.Flags = 0;
  tmpl.Key = 0;
  tmpl.IdentifierLength = sizeof(id_scsi);
  tmpl.Identifier = id_scsi;
  scsi_c = AddChild(root, &tmpl, (void *)0);

  /* Disk Controller */
  tmpl.Class = ControllerClass;
  tmpl.Type = DiskController;
  tmpl.Flags = 0;
  tmpl.Key = 1; /* SCSI ID 1 */
  tmpl.IdentifierLength = sizeof(id_disk_ctrl);
  tmpl.Identifier = id_disk_ctrl;
  disk_ctrl_c = AddChild(scsi_c, &tmpl, (void *)0);

  /* Disk Peripheral */
  tmpl.Class = PeripheralClass;
  tmpl.Type = DiskPeripheral;
  tmpl.Flags = 0;
  tmpl.Key = 0; /* partition 0 */
  tmpl.IdentifierLength = sizeof(id_disk);
  tmpl.Identifier = id_disk;
  AddChild(disk_ctrl_c, &tmpl, (void *)0);
}

/* ================================================================
 * ARCS MEMORY DESCRIPTORS
 * ================================================================ */

#define MAX_MEM_DESCS 12

static MEMORYDESCRIPTOR mem_descs[MAX_MEM_DESCS];
static int num_mem_descs = 0;

static void md_init_add(MEMORYTYPE type, LONG base, LONG count) {
  if (num_mem_descs < MAX_MEM_DESCS) {
    mem_descs[num_mem_descs].Type = type;
    mem_descs[num_mem_descs].BasePage = base;
    mem_descs[num_mem_descs].PageCount = count;
    num_mem_descs++;
  }
}

// clang-format off
void init_memory_descriptors(void) {
  unsigned long long total_ram = *(volatile unsigned long long *)(0xBF480100 + 0x00);
  unsigned long long high_base = *(volatile unsigned long long *)(0xBF480100 + 0x08);
  unsigned long long high_size = *(volatile unsigned long long *)(0xBF480100 + 0x10);

  unsigned long ram_MB = (unsigned long)(total_ram / (1024 * 1024));
  if (ram_MB < 64) ram_MB = 64; /* enforce minimum physical expectation */

  /*
   * SGI physical memory layout — RAM at 0x08000000, low alias at 0x00000000.
   * IRIX kernels link at kseg0 0x88xxxxxx = physical 0x08xxxxxx.
   *
   * With 64MB RAM (0x4000 pages of 4KB each, base page 0x8000):
   *
   *   Low alias (physical 0x00000000 = alias of main RAM):
   *   0x0000000-0x0000FFF:  Exception vectors (page 0)           [alias]
   *   0x0001000-0x0001FFF:  SPB (page 1)                         [alias]
   *   0x0002000-0x02FFFFF:  PROM scratch (pages 2-767)           [alias]
   *
   *   Main RAM (physical 0x08000000 = page 0x8000):
   *   0x8000000-0x8001FFF:  Exception/SPB mirror (pages 0x8000-0x8001)
   *   0x8002000-0x83FFFFF:  Kernel load area (pages 0x8002-0x83FF, 4MB)
   *   0x8400000-0xBEFFFFF:  Free memory (pages 0x8400-0xBEFF, ~55MB)
   *   0xBF00000-0xBFFFFFF:  PROM data/BSS (pages 0xBF00-0xBFFF, 1MB)
   *                          (= same bytes as low alias 0x3F00000-0x3FFFFFF)
   */
  num_mem_descs = 0;

  /* Low alias: exception vectors and PROM scratch */
  md_init_add(ExceptionBlock,    0,      1);           /* phys 0x0000000 */
  md_init_add(SPBPage,           1,      1);           /* phys 0x0001000 */
  md_init_add(FirmwareTemporary, 2,      766);         /* phys 0x0002000-0x02FFFFF */

  /* Main RAM at physical 0x08000000 */
  md_init_add(FirmwareTemporary, 0x8000, 2);           /* phys 0x8000000-0x8001FFF */
  md_init_add(FirmwareTemporary, 0x8002, 0x3FE);       /* phys 0x8002000-0x83FFFFF (kernel) */
  md_init_add(FreeMemory,        0x8400, 0x3B00);      /* phys 0x8400000-0xBEFFFFF (~55MB) */
  md_init_add(FirmwarePermanent, 0xBF00, 0x100);       /* phys 0xBF00000-0xBFFFFFF (PROM data) */

  /*
   * Add memory beyond 64MB up to 256MB (pages 0xC000+).
   * pvtimer and bootdisk are in the PV bank (PA 0x1F480000+), outside RAM.
   *
   * CRITICAL: the HEART compatibility shim occupies PA 0x0FF00000..0x0FF70000
   * (pages 0xFF00..0xFF70).  It is NOT RAM-transparent: ~16 register offsets
   * (COUNT 0x20000, ISR/IMR cluster 0x10000-0x10040, COMPARE 0x30000, etc.)
   * are intercepted, so a kernel page allocated there silently loses any data
   * stored at a register offset (writes vanish / reads return device state).
   * Under desktop memory pressure the kernel allocates into this region and a
   * lock-queue or zone-list pointer landing on a register offset reads back as
   * a garbage value → zone_shake / mrlock_resort_queue / PC=0 panics.
   * Reserve the whole shim span as FirmwarePermanent so the kernel never uses
   * it.  (448KB out of 256MB — negligible.)
   */
  if (ram_MB > 64) {
      unsigned long mb_to_add = (ram_MB > 256 ? 256 : ram_MB) - 64;
      unsigned long start = 0xC000;                 /* PA 0x0C000000 */
      unsigned long end   = 0xC000 + mb_to_add * 256; /* exclusive    */
      unsigned long heart_lo = 0xFF00;              /* PA 0x0FF00000 */
      unsigned long heart_hi = 0xFF70;              /* + 0x70000     */
      if (end <= heart_lo || start >= heart_hi) {
          md_init_add(FreeMemory, start, end - start);
      } else {
          if (heart_lo > start)
              md_init_add(FreeMemory, start, heart_lo - start);
          md_init_add(FirmwarePermanent, heart_lo, heart_hi - heart_lo);
          if (end > heart_hi)
              md_init_add(FreeMemory, heart_hi, end - heart_hi);
      }
  }

  /* Add High Memory beyond 256MB (PV-MEM) */
  if (high_size > 0) {
      unsigned long high_pages = (unsigned long)(high_size / 4096);
      unsigned long high_base_pages = (unsigned long)(high_base / 4096);
      md_init_add(FreeMemory, high_base_pages, high_pages);
  }
}
// clang-format on

MEMORYDESCRIPTOR *md_alloc(unsigned long base, unsigned long pages,
                           MEMORYTYPE type) {
  /*
   * All regions are already accounted for in init_memory_descriptors.
   * Return existing entry without adding overlapping entries.
   */
  (void)base;
  (void)pages;
  (void)type;
  return &mem_descs[num_mem_descs > 0 ? num_mem_descs - 1 : 0];
}

MEMORYDESCRIPTOR *md_add(unsigned long base, unsigned long pages,
                         MEMORYTYPE type) {
  return md_alloc(base, pages, type);
}

/* GetMemoryDescriptor -- iterate memory descriptors */
MEMORYDESCRIPTOR *GetMemoryDescriptor(MEMORYDESCRIPTOR *cur) {
  int i;
  if (!cur) {
    if (num_mem_descs > 0)
      return &mem_descs[0];
    return (MEMORYDESCRIPTOR *)0;
  }
  /* Find cur in array, return next */
  for (i = 0; i < num_mem_descs; i++) {
    if (&mem_descs[i] == cur) {
      if (i + 1 < num_mem_descs)
        return &mem_descs[i + 1];
      return (MEMORYDESCRIPTOR *)0;
    }
  }
  return (MEMORYDESCRIPTOR *)0;
}

/* ================================================================
 * ARCS I/O -- FILE DESCRIPTOR TABLE
 * fd 0 = stdin (console serial)
 * fd 1 = stdout (console serial)
 * fd 2+ = opened devices (disk, etc.)
 * ================================================================ */

#define MAX_FDS 8

/* Device types for open fds */
#define FD_TYPE_UNUSED 0
#define FD_TYPE_CONSOLE 1
#define FD_TYPE_DISK 2
#define FD_TYPE_XFS  3  /* filesystem file opened via FSBLOCK/xfs layer */

typedef struct {
  int type;
  int controller;
  int unit;
  int partition;
  unsigned long position;   /* byte offset within partition/file */
  unsigned long part_start; /* partition start in bytes */
  unsigned long part_size;  /* partition size in bytes */
  void *xfs_data;           /* FD_TYPE_XFS: points to static struct eiob */
} fd_entry_t;

static fd_entry_t fd_table[MAX_FDS];
static int fd_table_inited = 0;

static void init_fd_table(void) {
  int i;
  for (i = 0; i < MAX_FDS; i++)
    fd_table[i].type = FD_TYPE_UNUSED;
  /* fd 0 = stdin, fd 1 = stdout */
  fd_table[0].type = FD_TYPE_CONSOLE;
  fd_table[1].type = FD_TYPE_CONSOLE;
  fd_table_inited = 1;
}

/* ================================================================
 * VIRTUAL BOOT DISK I/O
 * Communicates with sgi_bootdisk QEMU device at 0x1F480600
 * (kseg1 address: 0xBF480600)
 * ================================================================ */

#define BOOTDISK_BASE 0xBF480600UL
#define BD_REG_SECTOR_LO 0x00
#define BD_REG_SECTOR_HI 0x04
#define BD_REG_COUNT 0x08
#define BD_REG_COMMAND 0x0C
#define BD_REG_STATUS 0x10
#define BD_REG_DISK_SIZE_LO 0x14
#define BD_REG_DISK_SIZE_HI 0x18
#define BD_REG_DATA 0x200

#define BD_CMD_READ 1
#define BD_STATUS_READY 0x80000000UL
#define BD_STATUS_ERROR 0x00000001UL

static volatile unsigned long *bd_reg(int offset) {
  return (volatile unsigned long *)(BOOTDISK_BASE + offset);
}

/* Read a single 512-byte sector from the boot disk */
static int bd_read_sector(unsigned long sector, void *buf) {
  volatile unsigned char *data =
      (volatile unsigned char *)(BOOTDISK_BASE + BD_REG_DATA);
  unsigned char *p = (unsigned char *)buf;
  volatile unsigned long *reg;
  unsigned long status;
  int i;

  reg = bd_reg(BD_REG_SECTOR_LO);
  *reg = sector;
  *bd_reg(BD_REG_SECTOR_HI) = 0;
  *bd_reg(BD_REG_COUNT) = 512;
  *bd_reg(BD_REG_COMMAND) = BD_CMD_READ;

  /* Poll for completion */
  do {
    status = *bd_reg(BD_REG_STATUS);
  } while (!(status & BD_STATUS_READY));

  if (status & BD_STATUS_ERROR)
    return -1;

  /* Copy data from device window */
  for (i = 0; i < 512; i++)
    p[i] = data[i];

  return 0;
}

/* Read multiple bytes from the boot disk at a byte offset */
static int bd_read_bytes(unsigned long byte_offset, void *buf,
                         unsigned long count) {
  unsigned char sector_buf[512];
  unsigned char *p = (unsigned char *)buf;
  unsigned long sector, offset_in_sector, chunk;

  while (count > 0) {
    sector = byte_offset / 512;
    offset_in_sector = byte_offset % 512;
    chunk = 512 - offset_in_sector;
    if (chunk > count)
      chunk = count;

    if (bd_read_sector(sector, sector_buf) < 0)
      return -1;

    for (; chunk > 0; chunk--) {
      *p++ = sector_buf[offset_in_sector++];
      byte_offset++;
      count--;
    }
  }
  return 0;
}

/* ================================================================
 * SGI VOLUME HEADER DEFINITIONS (from dvh.h)
 * ================================================================ */

#define VHMAGIC 0x0be5a941
#define VDNAMESIZE 8
#define BFNAMESIZE 16
#define NVDIR 15
#define NPARTAB 16

struct volume_directory {
  char vd_name[VDNAMESIZE];
  int vd_lbn;
  int vd_nbytes;
};

struct partition_table {
  int pt_nblks;
  int pt_firstlbn;
  int pt_type;
};

struct device_parameters {
  unsigned char dp_unused1[4];
  unsigned short _dp_cylinders;
  unsigned short dp_unused2;
  unsigned short _dp_heads;
  unsigned char dp_ctq_depth;
  unsigned char dp_unused3[3];
  unsigned short _dp_sect;
  unsigned short dp_secbytes;
  unsigned char dp_unused4[2];
  int dp_flags;
  unsigned char dp_unused5[20];
  unsigned int dp_drivecap;
};

struct volume_header {
  int vh_magic;
  short vh_rootpt;
  short vh_swappt;
  char vh_bootfile[BFNAMESIZE];
  struct device_parameters vh_dp;
  struct volume_directory vh_vd[NVDIR];
  struct partition_table vh_pt[NPARTAB];
  int vh_csum;
  int vh_fill;
};

/* ================================================================
 * ARCS I/O FUNCTIONS
 * ================================================================ */

/* Simple string comparison */
static int str_eq(const char *a, const char *b) {
  while (*a && *b) {
    if (*a != *b)
      return 0;
    a++;
    b++;
  }
  return *a == *b;
}

/* Simple memset */
static void mem_set(void *dst, int val, unsigned long n) {
  unsigned char *d = (unsigned char *)dst;
  while (n--)
    *d++ = (unsigned char)val;
}

/*
 * Parse device path: dksc(controller,unit,partition)
 * Returns 0 on success, fills controller/unit/partition.
 * Also handles scsi(c)disk(u)rdisk(0)partition(p) format.
 */
static int parse_disk_path(const char *path, int *ctrl, int *unit, int *part) {
  const char *p = path;

  *ctrl = 0;
  *unit = 1;
  *part = 0; /* default: root filesystem partition (partition 0) */

  /* Skip leading "dksc(" or "disk(" */
  if (p[0] == 'd' && p[1] == 'k' && p[2] == 's' && p[3] == 'c' && p[4] == '(') {
    p += 5;
    /* Parse controller */
    *ctrl = 0;
    while (*p >= '0' && *p <= '9')
      *ctrl = *ctrl * 10 + (*p++ - '0');
    if (*p == ',')
      p++;
    /* Parse unit */
    *unit = 0;
    while (*p >= '0' && *p <= '9')
      *unit = *unit * 10 + (*p++ - '0');
    if (*p == ',')
      p++;
    /* Parse partition */
    *part = 0;
    while (*p >= '0' && *p <= '9')
      *part = *part * 10 + (*p++ - '0');
    return 0;
  }

  /* Also accept bare paths -- default SCSI controller/unit */
  return 0;
}

/* Extract filename after the device path: "dksc(0,1,8)sash" → "sash".
 * For bare paths like "/unix.new" (no device specifier), returns the path itself. */
static const char *extract_filename(const char *path) {
  const char *p = path;

  /* Find the closing ')' of the device specifier */
  while (*p) {
    if (*p == ')') {
      p++;
      if (*p)
        return p;
      return (const char *)0;
    }
    p++;
  }
  /* No device specifier: if path starts with '/', it is a bare filesystem path */
  if (path && path[0] == '/')
    return path;
  return (const char *)0;
}

/* ================================================================
 * XFS / FILESYSTEM LAYER
 * Wires the ARCS FSBLOCK machinery to our pvBootDisk reader so that
 * files on XFS/EFS partitions can be opened, read, and seeked using
 * the same Open/Read/Seek/Close fd layer that load_program() uses.
 * ================================================================ */

/* Static XFS state — only one fs file open at a time (boot path). */
static IOBLOCK    s_xfs_io;
static COMPONENT  s_xfs_comp;
static struct eiob s_xfs_eiob;
static int         s_fs_init_done = 0;

/*
 * ip54_disk_strategy — ARCS device strategy called by DEVREAD() in xfs.c.
 *   dev->Key  = partition start LBN on the pvBootDisk
 *   io->StartBlock = LBN within the partition
 *   io->Count  = bytes to transfer
 *   io->Address = destination buffer
 */
static STATUS ip54_disk_strategy(COMPONENT *dev, IOBLOCK *io) {
  unsigned long part_lbn  = (unsigned long)dev->Key;
  unsigned long blk       = (unsigned long)(unsigned int)io->StartBlock;
  unsigned long byte_off  = (part_lbn + blk) * 512UL;
  unsigned long byte_cnt  = (unsigned long)(unsigned int)io->Count;
  if (io->FunctionCode == FC_READ) {
    stub_puts("[dkst] blk="); stub_puthex(blk);
    stub_puts(" off="); stub_puthex(byte_off);
    stub_puts(" cnt="); stub_puthex(byte_cnt); stub_puts("\n");
    if (bd_read_bytes(byte_off, io->Address, byte_cnt) != 0) {
      stub_puts("[dkst] read ERROR\n");
      return 1; /* EIO */
    }
    return 0;
  }
  return 1; /* unsupported */
}

static void ensure_fs_init(void) {
  if (!s_fs_init_done) {
    fs_init();
    s_fs_init_done = 1;
  }
}

/*
 * open_fs_file — open an absolute-path file on a filesystem partition.
 *   part     = partition number (from dksc(c,u,part)/filename)
 *   filename = absolute path within the filesystem, e.g. "/unix.new"
 *   fd_out   = receives the allocated fd on success
 * Returns 0 (ESUCCESS) on success, error code on failure.
 */
static LONG open_fs_file(int part, const char *filename, ULONG *fd_out) {
  struct volume_header vh;
  unsigned long part_start_lbn = 0;
  LONG err;
  int i;

  /* Determine partition start LBN from the volume header. */
  if (bd_read_bytes(0, &vh, sizeof(vh)) == 0 && vh.vh_magic == VHMAGIC) {
    if (part < NPARTAB && vh.vh_pt[part].pt_nblks > 0)
      part_start_lbn = (unsigned long)(unsigned int)vh.vh_pt[part].pt_firstlbn;
  }

  stub_puts("[IP54] open_fs_file: part=");
  stub_putdec(part);
  stub_puts(" lbn=0x");
  stub_puthex(part_start_lbn);
  stub_puts(" file=");
  stub_puts(filename);
  stub_puts("\n");

  /* Debug: peek at first 4 bytes of the partition sector to verify disk reads */
  {
    unsigned char peek[512];
    unsigned long peek_off = part_start_lbn * 512UL;
    if (bd_read_bytes(peek_off, peek, 512) == 0) {
      unsigned long magic = ((unsigned long)peek[0] << 24) | ((unsigned long)peek[1] << 16) |
                            ((unsigned long)peek[2] << 8) | peek[3];
      /* sb_versionnum is at offset 100 (0x64) in xfs_sb_t */
      unsigned long ver = ((unsigned long)peek[100] << 8) | peek[101];
      stub_puts("[IP54] XFS magic="); stub_puthex(magic);
      stub_puts(" ver="); stub_puthex(ver); stub_puts("\n");
    } else {
      stub_puts("[IP54] peek read FAILED\n");
    }
  }

  ensure_fs_init();

  /* IOBLOCK — read-only, starting at the partition */
  mem_set(&s_xfs_io, 0, sizeof(s_xfs_io));
  s_xfs_io.Flags = F_READ;

  /* COMPONENT — type=DiskPeripheral, Key=partition start LBN */
  mem_set(&s_xfs_comp, 0, sizeof(s_xfs_comp));
  s_xfs_comp.Type = DiskPeripheral;
  s_xfs_comp.Key  = (ULONG)part_start_lbn;

  /* eiob — wire everything together */
  mem_set(&s_xfs_eiob, 0, sizeof(s_xfs_eiob));
  s_xfs_eiob.dev                = &s_xfs_comp;
  s_xfs_eiob.fsb.Device         = &s_xfs_comp;  /* DEVREAD uses fsb.Device */
  s_xfs_eiob.fsb.DeviceStrategy = ip54_disk_strategy;
  s_xfs_eiob.fsb.IO             = &s_xfs_io;
  s_xfs_eiob.fsb.Filename       = (CHAR *)filename;

  /* Identify the filesystem on this partition */
  if (fs_search(&s_xfs_eiob) != ESUCCESS) {
    stub_puts("[IP54] open_fs_file: no recognized filesystem on partition\n");
    return 6; /* ENODEV */
  }

  /* Open the named file within the filesystem */
  s_xfs_eiob.fsb.FunctionCode = FS_OPEN;
  err = s_xfs_eiob.fsstrat(&s_xfs_eiob.fsb);
  if (err != ESUCCESS) {
    stub_puts("[IP54] open_fs_file: file not found: ");
    stub_puts(filename);
    stub_puts("\n");
    return 2; /* ENOENT */
  }

  stub_puts("[IP54] open_fs_file: opened OK\n");

  /* Allocate an fd */
  if (!fd_table_inited) init_fd_table();
  for (i = 2; i < MAX_FDS; i++) {
    if (fd_table[i].type == FD_TYPE_UNUSED) {
      fd_table[i].type     = FD_TYPE_XFS;
      fd_table[i].position = 0;
      fd_table[i].xfs_data = (void *)&s_xfs_eiob;
      *fd_out = (ULONG)i;
      return 0; /* ESUCCESS */
    }
  }

  /* No free fds — close and fail */
  s_xfs_eiob.fsb.FunctionCode = FS_CLOSE;
  s_xfs_eiob.fsstrat(&s_xfs_eiob.fsb);
  return 6; /* ENODEV */
}

/* Open -- open a device/file path */
LONG Open(CHAR *path, OPENMODE mode, ULONG *fd) {
  int i, ctrl, unit, part;

  (void)mode;

  if (!fd)
    return 5; /* EINVAL */

  if (!fd_table_inited)
    init_fd_table();

  /* Check for console device */
  if (!path || str_eq(path, "console") || str_eq(path, "")) {
    *fd = 0;
    return 0;
  }

  /* Parse disk device path */
  if (path[0] == 'd' || path[0] == 's') {
    if (parse_disk_path(path, &ctrl, &unit, &part) < 0) {
      *fd = 0;
      return 5; /* EINVAL */
    }

    /* Find a free fd */
    for (i = 2; i < MAX_FDS; i++) {
      if (fd_table[i].type == FD_TYPE_UNUSED) {
        fd_table[i].type = FD_TYPE_DISK;
        fd_table[i].controller = ctrl;
        fd_table[i].unit = unit;
        fd_table[i].partition = part;
        fd_table[i].position = 0;

        /*
         * Read the volume header to find partition bounds.
         * Partition 8 is the volume header partition (conventional).
         * Partition 10 is the entire volume.
         */
        {
          struct volume_header vh;
          if (bd_read_bytes(0, &vh, 512) == 0 && vh.vh_magic == VHMAGIC) {
            if (part < NPARTAB && vh.vh_pt[part].pt_nblks > 0) {
              fd_table[i].part_start =
                  (unsigned long)vh.vh_pt[part].pt_firstlbn * 512;
              fd_table[i].part_size =
                  (unsigned long)vh.vh_pt[part].pt_nblks * 512;
            } else {
              /* Partition not found -- use entire disk */
              fd_table[i].part_start = 0;
              fd_table[i].part_size = 0xFFFFFFFF;
            }
          } else {
            /* No valid VH -- use raw disk */
            fd_table[i].part_start = 0;
            fd_table[i].part_size = 0xFFFFFFFF;
          }
        }

        *fd = i;
        return 0; /* ESUCCESS */
      }
    }
    return 6; /* ENODEV -- no free fds */
  }

  *fd = 0;
  return 6; /* ENODEV */
}

/* Close -- close a file descriptor */
LONG Close(ULONG fd) {
  if (fd < MAX_FDS && fd >= 2) {
    if (fd_table[fd].type == FD_TYPE_XFS && fd_table[fd].xfs_data) {
      struct eiob *eiob = (struct eiob *)fd_table[fd].xfs_data;
      eiob->fsb.FunctionCode = FS_CLOSE;
      eiob->fsstrat(&eiob->fsb);
    }
    fd_table[fd].type     = FD_TYPE_UNUSED;
    fd_table[fd].xfs_data = 0;
  }
  return 0;
}

/* Read -- read from a file descriptor */
LONG Read(ULONG fd, void *buf, ULONG len, ULONG *count) {
  if (!fd_table_inited)
    init_fd_table();

  /* Console read (fd 0) */
  if (fd == 0 && buf && len > 0) {
    volatile unsigned char *uart_rbr = (volatile unsigned char *)0xBF62017B;
    volatile unsigned char *uart_lsr = (volatile unsigned char *)0xBF62017E;
    unsigned char *p = (unsigned char *)buf;
    ULONG i;
    for (i = 0; i < len; i++) {
      while ((*uart_lsr & 0x01) == 0)
        ;
      p[i] = *uart_rbr;
    }
    if (count)
      *count = len;
    return 0;
  }

  /* XFS filesystem file read */
  if (fd < MAX_FDS && fd_table[fd].type == FD_TYPE_XFS && buf) {
    struct eiob *eiob  = (struct eiob *)fd_table[fd].xfs_data;
    IOBLOCK     *io    = eiob->fsb.IO;
    unsigned long prev = fd_table[fd].position;
    LONG err;
    io->Address    = buf;
    io->Count      = (LONG)len;
    io->Offset.lo  = (unsigned long)prev;
    io->Offset.hi  = 0;
    eiob->fsb.FunctionCode = FS_READ;
    err = eiob->fsstrat(&eiob->fsb);
    if (err == ESUCCESS) {
      /* _xfs_read advances io->Offset.lo by actual bytes consumed */
      ULONG nread = (ULONG)(io->Offset.lo - (unsigned long)prev);
      fd_table[fd].position = (unsigned long)io->Offset.lo;
      if (count) *count = nread;
      return 0;
    }
    if (count) *count = 0;
    return err;
  }

  /* Disk read */
  if (fd < MAX_FDS && fd_table[fd].type == FD_TYPE_DISK && buf && len > 0) {
    unsigned long disk_offset = fd_table[fd].part_start + fd_table[fd].position;
    if (bd_read_bytes(disk_offset, buf, len) == 0) {
      fd_table[fd].position += len;
      if (count)
        *count = len;
      return 0;
    }
    if (count)
      *count = 0;
    return 7; /* EIO */
  }

  if (count)
    *count = 0;
  return 6; /* ENODEV */
}

/* Write -- write to a file descriptor */
LONG Write(ULONG fd, void *buf, ULONG len, ULONG *count) {
  if (fd <= 1 && buf && len > 0) {
    const char *p = (const char *)buf;
    ULONG i;
    for (i = 0; i < len; i++)
      stub_putchar_polled(p[i]);
    if (count)
      *count = len;
    return 0;
  }
  if (count)
    *count = 0;
  return 0;
}

/* GetReadStatus -- check if data available */
LONG GetReadStatus(ULONG fd) {
  if (fd == 0) {
    volatile unsigned char *uart_lsr = (volatile unsigned char *)0xBF62017E;
    if (*uart_lsr & 0x01)
      return 0;
  }
  return 8; /* EAGAIN */
}

/* Seek -- seek on a file descriptor */
LONG Seek(ULONG fd, LARGEINTEGER *offset, SEEKMODE mode) {
  if (!offset)
    return 5; /* EINVAL */

  if (fd < MAX_FDS && (fd_table[fd].type == FD_TYPE_DISK ||
                       fd_table[fd].type == FD_TYPE_XFS)) {
    unsigned long off = (unsigned long)offset->lo;
    if (mode == SeekAbsolute)
      fd_table[fd].position = off;
    else
      fd_table[fd].position += off;
    return 0;
  }
  return 5; /* EINVAL */
}

/* Mount -- mount a filesystem (stub) */
LONG Mount(CHAR *path, MOUNTOPERATION op) {
  (void)path;
  (void)op;
  return 0;
}

/* GetDirEntry -- get directory entry (stub) */
LONG GetDirEntry(ULONG fd, DIRECTORYENTRY *de, ULONG n, ULONG *count) {
  (void)fd;
  (void)de;
  (void)n;
  if (count)
    *count = 0;
  return 5; /* EINVAL */
}

/* GetFileInformation -- get file info (stub) */
LONG GetFileInformation(ULONG fd, FILEINFORMATION *info) {
  (void)fd;
  (void)info;
  return 5; /* EINVAL */
}

/* SetFileInformation -- set file info (stub) */
LONG SetFileInformation(ULONG fd, ULONG flags, ULONG mask) {
  (void)fd;
  (void)flags;
  (void)mask;
  return 0;
}

/* FlushAllCaches -- flush all caches */
VOID FlushAllCaches(VOID) { /* No-op in emulation */ }

/* ================================================================
 * ARCS SYSTEM ID & TIME
 * ================================================================ */

static SYSTEMID sys_id = {{'S', 'G', 'I', '\0', '\0', '\0', '\0', '\0'},
                          {'I', 'P', '3', '2', '\0', '\0', '\0', '\0'}};

SYSTEMID *GetSystemId(void) { return &sys_id; }

static TIMEINFO current_time = {2000, 1, 1, 0, 0, 0, 0};

TIMEINFO *GetTime(void) { return &current_time; }

/* GetRelativeTime -- use CP0 Count instead of CRIME timer */
ULONG GetRelativeTime(void) {
  unsigned long count;
  __asm__ volatile("mfc0 %0, $9" : "=r"(count));
  return count / 100000000;
}

/* ================================================================
 * PROGRAM LOADER -- ECOFF + ELF32
 * ================================================================ */

/* ECOFF header structures (MIPS big-endian) */
#define ECOFF_MAGIC_MIPSEB2 0x0162 /* MIPS BE, MIPS II */
#define ECOFF_MAGIC_MIPSEB3 0x0163 /* MIPS BE, MIPS III */
#define ECOFF_MAGIC_MIPSEL 0x0160  /* MIPS LE */
#define ECOFF_OMAGIC 0x0107
#define ECOFF_NMAGIC 0x0108
#define ECOFF_ZMAGIC 0x010B

struct ecoff_filehdr {
  unsigned short f_magic;
  unsigned short f_nscns;
  int f_timdat;
  int f_symptr;
  int f_nsyms;
  unsigned short f_opthdr;
  unsigned short f_flags;
};

struct ecoff_aouthdr {
  unsigned short magic;
  unsigned short vstamp;
  int tsize;      /* text segment size */
  int dsize;      /* data segment size */
  int bsize;      /* BSS segment size */
  int entry;      /* entry point */
  int text_start; /* text start address */
  int data_start; /* data start address */
  int bss_start;  /* BSS start address */
  int gprmask;
  int cprmask[4];
  int gp_value;
};

/* ECOFF section header (40 bytes) */
struct ecoff_scnhdr {
  char s_name[8];
  int s_paddr;
  int s_vaddr;
  int s_size;
  int s_scnptr; /* file offset to section data */
  int s_relptr;
  int s_lnnoptr;
  unsigned short s_nreloc;
  unsigned short s_nlnno;
  int s_flags;
};

/* ELF32 definitions */
#define ELF_MAGIC 0x7f454c46 /* \x7fELF */
#define ELFCLASS32 1
#define ELFDATA2MSB 2
#define PT_LOAD 1

struct elf32_hdr {
  unsigned char e_ident[16];
  unsigned short e_type;
  unsigned short e_machine;
  unsigned int e_version;
  unsigned int e_entry;
  unsigned int e_phoff;
  unsigned int e_shoff;
  unsigned int e_flags;
  unsigned short e_ehsize;
  unsigned short e_phentsize;
  unsigned short e_phnum;
  unsigned short e_shentsize;
  unsigned short e_shnum;
  unsigned short e_shstrndx;
};

struct elf32_phdr {
  unsigned int p_type;
  unsigned int p_offset;
  unsigned int p_vaddr;
  unsigned int p_paddr;
  unsigned int p_filesz;
  unsigned int p_memsz;
  unsigned int p_flags;
  unsigned int p_align;
};

/* ELF32 section header */
#define SHT_SYMTAB 2
#define SHT_STRTAB 3

struct elf32_shdr {
  unsigned int sh_name;
  unsigned int sh_type;
  unsigned int sh_flags;
  unsigned int sh_addr;
  unsigned int sh_offset;
  unsigned int sh_size;
  unsigned int sh_link;      /* for SYMTAB: index of associated STRTAB */
  unsigned int sh_info;
  unsigned int sh_addralign;
  unsigned int sh_entsize;
};

/* ELF32 symbol table entry */
#define STB_GLOBAL 1
#define STB_WEAK   2
#define ELF32_ST_BIND(info) ((info) >> 4)
#define ELF32_ST_TYPE(info) ((info) & 0xf)
#define STT_FUNC   2
#define STT_OBJECT 1

struct elf32_sym {
  unsigned int st_name;
  unsigned int st_value;
  unsigned int st_size;
  unsigned char st_info;
  unsigned char st_other;
  unsigned short st_shndx;
};

/*
 * Kernel symbol table — loaded from ELF .symtab + .strtab at boot time.
 * Stored in high physical memory above the kernel BSS.
 */
static struct elf32_sym *kern_symtab = 0;
static char *kern_strtab = 0;
static unsigned int kern_symcount = 0;

/* Forward declaration */
static unsigned long to_kseg1(unsigned long addr);

/*
 * kern_sym -- look up a kernel symbol by name, return KSEG0 address (or 0).
 */
static unsigned int
kern_sym(const char *name)
{
    unsigned int i;
    if (!kern_symtab || !kern_strtab || !kern_symcount)
        return 0;
    for (i = 0; i < kern_symcount; i++) {
        unsigned char bind = ELF32_ST_BIND(kern_symtab[i].st_info);
        if (bind != STB_GLOBAL && bind != STB_WEAK)
            continue;
        if (kern_symtab[i].st_value == 0)
            continue;
        /* Compare names */
        {
            const char *sym_name = kern_strtab + kern_symtab[i].st_name;
            const char *p = name;
            const char *q = sym_name;
            while (*p && *q && *p == *q) { p++; q++; }
            if (*p == 0 && *q == 0)
                return kern_symtab[i].st_value;
        }
    }
    return 0;
}

/*
 * kern_sym_uncached -- same as kern_sym but returns KSEG1 (uncached) address.
 */
static unsigned int
kern_sym_u(const char *name)
{
    unsigned int v = kern_sym(name);
    if (v)
        return to_kseg1(v);
    return 0;
}

/*
 * mips_lui -- generate LUI instruction: lui $reg, hi16(addr)
 */
static unsigned int mips_lui(int reg, unsigned int addr)
{
    unsigned int hi = (addr >> 16) & 0xffff;
    /* If low 16 bits are negative (sign-extended addiu), adjust */
    if (addr & 0x8000) hi++;
    return (0x3c000000U | (reg << 16) | hi);
}

/*
 * mips_addiu -- generate ADDIU instruction: addiu $dst, $src, lo16(addr)
 */
static unsigned int mips_addiu(int dst, int src, unsigned int addr)
{
    return (0x24000000U | (src << 21) | (dst << 16) | (addr & 0xffff));
}

/*
 * mips_j -- generate J instruction to a KSEG0 address
 */
static unsigned int mips_j(unsigned int addr)
{
    return 0x08000000U | ((addr >> 2) & 0x03ffffffU);
}

/*
 * mips_jal -- generate JAL instruction to a KSEG0 address
 */
static unsigned int mips_jal(unsigned int addr)
{
    return 0x0c000000U | ((addr >> 2) & 0x03ffffffU);
}

/*
 * kern_sym_or_warn -- look up symbol, warn if not found, return address
 */
static unsigned int
kern_sym_or_warn(const char *name)
{
    unsigned int v = kern_sym(name);
    if (!v) {
        stub_puts("[IP54] WARN: symbol not found: ");
        stub_puts(name);
        stub_puts("\n");
    }
    return v;
}

/*
 * Convert kseg0 (0x80xxxxxx) or kseg1 (0xA0xxxxxx) address to
 * kseg1 (uncached) for safe writes during loading.
 */
static unsigned long to_kseg1(unsigned long addr) {
  /* Strip to physical, add kseg1 base */
  return (addr & 0x1FFFFFFF) | 0xA0000000;
}

/*
 * BSS region recorded by ELF loader so Execute() can zero it
 * just before jumping to the kernel (after all PROM I/O is done).
 */
static unsigned long elf_bss_start = 0;
static unsigned long elf_bss_size = 0;

/*
 * Load a program from an open fd at the current position.
 * Detects ECOFF or ELF32 format, loads segments, returns entry point.
 * Returns 0 on success, non-zero on error.
 */
static LONG load_program(ULONG fd, unsigned long *entry_out) {
  unsigned char hdr_buf[512];
  ULONG got;
  int rc;

  /* Read the first 512 bytes for format detection */
  rc = Read(fd, hdr_buf, 512, &got);
  if (rc != 0 || got < 64) {
    stub_puts("[IP54] load_program: failed to read header\n");
    return 7; /* EIO */
  }

  /* Check for ELF magic: 0x7f 'E' 'L' 'F' */
  if (hdr_buf[0] == 0x7f && hdr_buf[1] == 'E' && hdr_buf[2] == 'L' &&
      hdr_buf[3] == 'F') {

    struct elf32_hdr *ehdr = (struct elf32_hdr *)hdr_buf;
    struct elf32_phdr phdr;
    LARGEINTEGER seek_off;
    int i;

    stub_puts("[IP54] ELF32 binary detected\n");

    if (ehdr->e_ident[4] != ELFCLASS32) {
      stub_puts("[IP54] Not ELF32 class\n");
      return 5;
    }

    stub_puts("[IP54] Entry point: ");
    stub_puthex(ehdr->e_entry);
    stub_puts("\n");

    /* Load each PT_LOAD segment */
    for (i = 0; i < ehdr->e_phnum; i++) {
      unsigned long phdr_offset = ehdr->e_phoff + i * ehdr->e_phentsize;

      /* Seek to program header */
      seek_off.hi = 0;
      seek_off.lo = phdr_offset;
      Seek(fd, &seek_off, SeekAbsolute);
      Read(fd, &phdr, sizeof(phdr), &got);

      if (phdr.p_type != PT_LOAD)
        continue;
      if (phdr.p_filesz == 0 && phdr.p_memsz == 0)
        continue;

      stub_puts("[IP54]   PT_LOAD: vaddr=");
      stub_puthex(phdr.p_vaddr);
      stub_puts(" filesz=");
      stub_puthex(phdr.p_filesz);
      stub_puts(" memsz=");
      stub_puthex(phdr.p_memsz);
      stub_puts("\n");

      /*
       * Record BSS range (memsz > filesz) for zeroing later.
       * We defer the actual bzero to Execute(), just before the
       * jump, so all PROM I/O is complete first.
       */
      if (phdr.p_memsz > phdr.p_filesz) {
        elf_bss_start = phdr.p_vaddr + phdr.p_filesz;
        elf_bss_size = phdr.p_memsz - phdr.p_filesz;
      }

      /* Copy file data */
      if (phdr.p_filesz > 0) {
        unsigned long dest = to_kseg1(phdr.p_vaddr);
        LONG rrc;
        seek_off.hi = 0;
        seek_off.lo = phdr.p_offset;
        Seek(fd, &seek_off, SeekAbsolute);
        stub_puts("[IP54]   Loading to ");
        stub_puthex(dest);
        stub_puts(" from file offset ");
        stub_puthex(phdr.p_offset);
        stub_puts("\n");
        rrc = Read(fd, (void *)dest, phdr.p_filesz, &got);
        stub_puts("[IP54]   Read returned ");
        stub_putdec(rrc);
        stub_puts(", got=");
        stub_putdec(got);
        stub_puts(" bytes\n");
        /* Verify first 4 bytes at dest */
        {
          unsigned char *vp = (unsigned char *)dest;
          stub_puts("[IP54]   First bytes: ");
          stub_puthex(vp[0]);
          stub_puts(" ");
          stub_puthex(vp[1]);
          stub_puts(" ");
          stub_puthex(vp[2]);
          stub_puts(" ");
          stub_puthex(vp[3]);
          stub_puts("\n");
          /* Also check entry point offset */
          {
            unsigned long ep_off = ehdr->e_entry - phdr.p_vaddr;
            unsigned char *ep_bytes = (unsigned char *)(dest + ep_off);
            stub_puts("[IP54]   Entry bytes at ");
            stub_puthex(dest + ep_off);
            stub_puts(": ");
            stub_puthex(ep_bytes[0]);
            stub_puts(" ");
            stub_puthex(ep_bytes[1]);
            stub_puts(" ");
            stub_puthex(ep_bytes[2]);
            stub_puts(" ");
            stub_puthex(ep_bytes[3]);
            stub_puts("\n");
          }
        }
      }
    }

    /*
     * Load ELF symbol table (.symtab + .strtab) into memory above BSS.
     * This allows the patcher to find function addresses by name.
     */
    if (ehdr->e_shoff && ehdr->e_shnum && ehdr->e_shentsize >= 40) {
      struct elf32_shdr shdr;
      int si;
      unsigned int symtab_off = 0, symtab_size = 0, symtab_link = 0;
      unsigned int strtab_off = 0, strtab_size = 0;

      /* Scan section headers to find SHT_SYMTAB */
      for (si = 0; si < ehdr->e_shnum; si++) {
        seek_off.hi = 0;
        seek_off.lo = ehdr->e_shoff + si * ehdr->e_shentsize;
        Seek(fd, &seek_off, SeekAbsolute);
        Read(fd, &shdr, sizeof(shdr), &got);
        if (shdr.sh_type == SHT_SYMTAB) {
          symtab_off = shdr.sh_offset;
          symtab_size = shdr.sh_size;
          symtab_link = shdr.sh_link;
          break;
        }
      }

      /* Read the linked string table */
      if (symtab_off && symtab_link < ehdr->e_shnum) {
        seek_off.hi = 0;
        seek_off.lo = ehdr->e_shoff + symtab_link * ehdr->e_shentsize;
        Seek(fd, &seek_off, SeekAbsolute);
        Read(fd, &shdr, sizeof(shdr), &got);
        if (shdr.sh_type == SHT_STRTAB) {
          strtab_off = shdr.sh_offset;
          strtab_size = shdr.sh_size;
        }
      }

      if (symtab_off && strtab_off) {
        /*
         * Place symbol + string tables at phys 0x03800000 (56MB mark).
         * Kernel uses ~6MB starting at phys 0x08002000, so this is safe
         * in a 64MB system — it's in the low 64MB alias region.
         */
        unsigned long sym_load = 0xA3800000U; /* KSEG1: phys 0x03800000 */
        unsigned long str_load = sym_load + symtab_size;

        /* Align string table to 4 bytes */
        str_load = (str_load + 3) & ~3;

        /* Read symtab */
        seek_off.hi = 0;
        seek_off.lo = symtab_off;
        Seek(fd, &seek_off, SeekAbsolute);
        Read(fd, (void *)sym_load, symtab_size, &got);

        /* Read strtab */
        seek_off.hi = 0;
        seek_off.lo = strtab_off;
        Seek(fd, &seek_off, SeekAbsolute);
        Read(fd, (void *)str_load, strtab_size, &got);

        kern_symtab = (struct elf32_sym *)sym_load;
        kern_strtab = (char *)str_load;
        kern_symcount = symtab_size / sizeof(struct elf32_sym);

        stub_puts("[IP54] Loaded ");
        stub_putdec(kern_symcount);
        stub_puts(" symbols (");
        stub_putdec(symtab_size + strtab_size);
        stub_puts(" bytes)\n");

        /* Quick test: look up a known symbol */
        {
          unsigned int test = kern_sym("intr");
          if (test) {
            stub_puts("[IP54] kern_sym(\"intr\") = ");
            stub_puthex(test);
            stub_puts("\n");
          } else {
            stub_puts("[IP54] WARN: kern_sym(\"intr\") failed!\n");
          }
        }
      } else {
        stub_puts("[IP54] WARN: no .symtab in kernel ELF\n");
      }
    }

    *entry_out = ehdr->e_entry;
    return 0;
  }

  /* Check for ECOFF magic */
  {
    struct ecoff_filehdr *fhdr = (struct ecoff_filehdr *)hdr_buf;
    unsigned short magic = fhdr->f_magic;

    if (magic == ECOFF_MAGIC_MIPSEB2 || magic == ECOFF_MAGIC_MIPSEB3 ||
        magic == ECOFF_MAGIC_MIPSEL) {

      struct ecoff_aouthdr *ahdr;
      unsigned long text_off;
      unsigned long dest;
      LARGEINTEGER seek_off;

      stub_puts("[IP54] ECOFF binary detected (magic=");
      stub_puthex(magic);
      stub_puts(")\n");

      if (fhdr->f_opthdr < sizeof(struct ecoff_aouthdr)) {
        stub_puts("[IP54] ECOFF optional header too small\n");
        return 5;
      }

      ahdr = (struct ecoff_aouthdr *)(hdr_buf + sizeof(struct ecoff_filehdr));

      stub_puts("[IP54] Entry: ");
      stub_puthex(ahdr->entry);
      stub_puts(" text_start=");
      stub_puthex(ahdr->text_start);
      stub_puts(" tsize=");
      stub_puthex(ahdr->tsize);
      stub_puts("\n");
      stub_puts("[IP54] data_start=");
      stub_puthex(ahdr->data_start);
      stub_puts(" dsize=");
      stub_puthex(ahdr->dsize);
      stub_puts(" bsize=");
      stub_puthex(ahdr->bsize);
      stub_puts("\n");

      /*
       * ECOFF loading: use s_scnptr from section headers
       * to find where each section's data is in the file.
       * The reference IRIX loader (loader.c) does the same.
       */
      {
        struct ecoff_scnhdr *scn;
        unsigned long scn_off;
        int si;

        scn_off = sizeof(struct ecoff_filehdr) + fhdr->f_opthdr;

        /* Section headers are in hdr_buf (we read 512 bytes) */
        if (scn_off + fhdr->f_nscns * sizeof(struct ecoff_scnhdr) > 512) {
          stub_puts("[IP54] ECOFF headers exceed 512 bytes\n");
          return 5;
        }

        /* Zero BSS first */
        if (ahdr->bsize > 0) {
          dest = to_kseg1(ahdr->bss_start);
          mem_set((void *)dest, 0, ahdr->bsize);
        }

        /* Load each section with file data */
        for (si = 0; si < fhdr->f_nscns; si++) {
          scn = (struct ecoff_scnhdr *)(hdr_buf + scn_off +
                                        si * sizeof(struct ecoff_scnhdr));

          /* Skip sections with no file data (e.g. .bss) */
          if (scn->s_scnptr == 0 || scn->s_size == 0)
            continue;

          dest = to_kseg1((unsigned long)scn->s_vaddr);
          stub_puts("[IP54] Loading ");
          {
            int ni;
            for (ni = 0; ni < 8 && scn->s_name[ni]; ni++)
              stub_putchar_polled(scn->s_name[ni]);
          }
          stub_puts(" to 0x");
          stub_puthex(dest);
          stub_puts(" (0x");
          stub_puthex(scn->s_size);
          stub_puts(" bytes from file 0x");
          stub_puthex(scn->s_scnptr);
          stub_puts(")\n");

          seek_off.hi = 0;
          seek_off.lo = (unsigned long)scn->s_scnptr;
          Seek(fd, &seek_off, SeekAbsolute);
          Read(fd, (void *)dest, (unsigned long)scn->s_size, &got);
        }
      }

      *entry_out = ahdr->entry;
      return 0;
    }
  }

  stub_puts("[IP54] Unknown binary format: ");
  stub_puthex((hdr_buf[0] << 8) | hdr_buf[1]);
  stub_puts("\n");
  return 5; /* EINVAL */
}

/* Load -- ARCS Load function */
LONG Load(CHAR *path, ULONG topaddr, ULONG *entry, ULONG *lowaddr) {
  ULONG fd;
  LONG rc;
  unsigned long ep;

  (void)topaddr;

  stub_puts("[IP54] Load: ");
  if (path)
    stub_puts(path);
  stub_puts("\n");

  rc = Open(path, OpenReadOnly, &fd);
  if (rc != 0) {
    stub_puts("[IP54] Load: Open failed\n");
    return rc;
  }

  rc = load_program(fd, &ep);
  Close(fd);

  if (rc == 0) {
    if (entry)
      *entry = (ULONG)ep;
    if (lowaddr)
      *lowaddr = 0;
  }

  return rc;
}

/* Invoke -- invoke a loaded program (stub) */
LONG Invoke(ULONG entry, ULONG stack, LONG argc, CHAR *argv[], CHAR *envp[]) {
  (void)entry;
  (void)stack;
  (void)argc;
  (void)argv;
  (void)envp;
  stub_puts("[IP54] Invoke called\n");
  return 6; /* ENODEV */
}

/*
 * Execute -- load and execute a program from a device path.
 *
 * This is the main boot path:
 *   1. Open the disk device
 *   2. Read volume header
 *   3. Find the requested file in the volume directory
 *   4. Seek to the file, load it
 *   5. Jump to entry point
 */
LONG Execute(CHAR *path, LONG argc, CHAR *argv[], CHAR *envp[]) {
  ULONG fd;
  LONG rc;
  unsigned long entry;
  struct volume_header vh;
  const char *filename;
  int ctrl, unit, part;
  int i;
  LARGEINTEGER seek_off;
  ULONG got;
  typedef void (*entry_fn_t)(LONG, CHAR *[], CHAR *[]);

  stub_puts("[IP54] Execute: ");
  if (path)
    stub_puts(path);
  stub_puts("\n");

  if (!path)
    return 5;

  if (!fd_table_inited)
    init_fd_table();

  /* Parse the device path and extract filename */
  parse_disk_path(path, &ctrl, &unit, &part);
  filename = extract_filename(path);

  /*
   * If the filename starts with '/', it is a path inside a filesystem
   * (XFS or EFS).  Use the FSBLOCK layer to open and load it.
   */
  if (filename && filename[0] == '/') {
    ULONG fs_fd;
    rc = open_fs_file(part, filename, &fs_fd);
    if (rc != 0) {
      stub_puts("[IP54] Execute: cannot open fs file\n");
      return rc;
    }
    rc = load_program(fs_fd, &entry);
    Close(fs_fd);
    if (rc != 0) {
      stub_puts("[IP54] Execute: load_program failed\n");
      return rc;
    }
    goto do_jump;
  }

  /*
   * Otherwise: look up the file in the volume header directory.
   * Open the raw disk (partition 8 = volume header partition).
   */
  {
    char dev_path[64];
    char *dp = dev_path;
    /* Build "dksc(c,u,8)" */
    *dp++ = 'd';
    *dp++ = 'k';
    *dp++ = 's';
    *dp++ = 'c';
    *dp++ = '(';
    *dp++ = '0' + ctrl;
    *dp++ = ',';
    *dp++ = '0' + unit;
    *dp++ = ',';
    *dp++ = '8'; /* VH partition */
    *dp++ = ')';
    *dp = '\0';

    rc = Open(dev_path, OpenReadOnly, &fd);
    if (rc != 0) {
      stub_puts("[IP54] Execute: cannot open disk\n");
      return rc;
    }
  }

  /* Read the volume header (sector 0 of the VH partition = sector 0 of disk) */
  /* But the VH partition starts at firstlbn. Sector 0 of disk has the VH. */
  /* Since Open sets position to 0 relative to partition start, and VH partition
   * typically starts at LBN 0, we read from position 0. But for safety,
   * just read directly from disk byte 0. */
  if (bd_read_bytes(0, &vh, sizeof(vh)) != 0) {
    stub_puts("[IP54] Execute: cannot read volume header\n");
    Close(fd);
    return 7;
  }

  if (vh.vh_magic != VHMAGIC) {
    stub_puts("[IP54] Execute: bad volume header magic: ");
    stub_puthex(vh.vh_magic);
    stub_puts("\n");
    Close(fd);
    return 6;
  }

  stub_puts("[IP54] Volume header OK, bootfile=\"");
  stub_puts(vh.vh_bootfile);
  stub_puts("\"\n");

  /* Determine which file to load */
  if (!filename || *filename == '\0') {
    /* Use default bootfile from VH */
    filename = vh.vh_bootfile;
  }

  stub_puts("[IP54] Looking for \"");
  stub_puts(filename);
  stub_puts("\" in volume directory...\n");

  /* Search volume directory */
  for (i = 0; i < NVDIR; i++) {
    if (vh.vh_vd[i].vd_name[0] == '\0')
      continue;

    /* Compare filename (vd_name is 8 chars, possibly not null-terminated) */
    {
      const char *a = filename;
      const char *b = vh.vh_vd[i].vd_name;
      int match = 1;
      int j;
      for (j = 0; j < VDNAMESIZE && *a && *b; j++) {
        if (*a != *b) {
          match = 0;
          break;
        }
        a++;
        b++;
      }
      /* Match if both strings ended (or vd_name filled VDNAMESIZE) */
      if (match && *a == '\0' && (j >= VDNAMESIZE || *b == '\0'))
        goto found_file;
    }
  }

  stub_puts("[IP54] File not found in volume directory\n");
  /* List what's available */
  stub_puts("[IP54] Volume directory contents:\n");
  for (i = 0; i < NVDIR; i++) {
    if (vh.vh_vd[i].vd_name[0] == '\0')
      continue;
    stub_puts("[IP54]   \"");
    {
      int j;
      for (j = 0; j < VDNAMESIZE && vh.vh_vd[i].vd_name[j]; j++)
        stub_putchar_polled(vh.vh_vd[i].vd_name[j]);
    }
    stub_puts("\" lbn=");
    stub_putdec(vh.vh_vd[i].vd_lbn);
    stub_puts(" size=");
    stub_putdec(vh.vh_vd[i].vd_nbytes);
    stub_puts("\n");
  }
  Close(fd);
  return 6; /* ENODEV */

found_file:
  stub_puts("[IP54] Found: lbn=");
  stub_putdec(vh.vh_vd[i].vd_lbn);
  stub_puts(" size=");
  stub_putdec(vh.vh_vd[i].vd_nbytes);
  stub_puts(" bytes\n");

  /* Seek to the file within the disk */
  Close(fd);

  /* Reopen with raw access to load from the file's location */
  {
    /* Set up a direct fd for the file */
    int fi;
    for (fi = 2; fi < MAX_FDS; fi++) {
      if (fd_table[fi].type == FD_TYPE_UNUSED) {
        fd_table[fi].type = FD_TYPE_DISK;
        fd_table[fi].controller = ctrl;
        fd_table[fi].unit = unit;
        fd_table[fi].partition = -1; /* raw */
        fd_table[fi].position = 0;
        fd_table[fi].part_start = (unsigned long)vh.vh_vd[i].vd_lbn * 512;
        fd_table[fi].part_size = (unsigned long)vh.vh_vd[i].vd_nbytes;
        fd = fi;
        break;
      }
    }
    if (fi >= MAX_FDS) {
      stub_puts("[IP54] No free fds\n");
      return 6;
    }
  }

  /* Load the program */
  rc = load_program(fd, &entry);
  Close(fd);

  if (rc != 0) {
    stub_puts("[IP54] Execute: load_program failed\n");
    return rc;
  }

do_jump:
  stub_puts("[IP54] Jumping to entry point: ");
  stub_puthex(entry);
  stub_puts("\n");

  /*
   * Build kernel-compatible argument arrays.
   *
   * The IRIX kernel's getargs() (kopt.c) expects:
   *   argv: NULL-terminated array of strings (program name)
   *   envp: NULL-terminated array of "KEY=VALUE" strings
   *
   * Critical: init_sysid() ASSERTs that "eaddr" is present.
   * We pass the PROM's own environ array (which has all set variables)
   * plus ensure critical variables are present.
   */
  {
    extern char **environ;
    static char *kern_argv[2];
    static char *kern_envp[64];
    int ke = 0;
    int has_eaddr = 0;
    int has_console = 0;
    int has_console_in = 0;
    int has_console_out = 0;

    /* argv: just the kernel filename */
    kern_argv[0] = (char *)(filename ? filename : "unix");
    kern_argv[1] = (char *)0;

    /* Copy PROM environment variables into envp.
     * Override console= to "d" (serial) since we don't have
     * working graphics yet. Skip the PROM's console= value
     * and replace it with console=d.
     */
    if (environ) {
      char **ep;
      for (ep = environ; *ep && ke < 60; ep++) {
        /* Check if critical vars already exist */
        if ((*ep)[0] == 'e' && (*ep)[1] == 'a' && (*ep)[2] == 'd' &&
            (*ep)[3] == 'd' && (*ep)[4] == 'r' && (*ep)[5] == '=')
          has_eaddr = 1;
        if ((*ep)[0] == 'C' && (*ep)[1] == 'o' && (*ep)[2] == 'n' &&
            (*ep)[3] == 's' && (*ep)[4] == 'o' && (*ep)[5] == 'l' &&
            (*ep)[6] == 'e' && (*ep)[7] == 'I')
          has_console_in = 1;
        if ((*ep)[0] == 'C' && (*ep)[1] == 'o' && (*ep)[2] == 'n' &&
            (*ep)[3] == 's' && (*ep)[4] == 'o' && (*ep)[5] == 'l' &&
            (*ep)[6] == 'e' && (*ep)[7] == 'O')
          has_console_out = 1;
        /* Skip existing console= — we force serial below */
        if ((*ep)[0] == 'c' && (*ep)[1] == 'o' && (*ep)[2] == 'n' &&
            (*ep)[3] == 's' && (*ep)[4] == 'o' && (*ep)[5] == 'l' &&
            (*ep)[6] == 'e' && (*ep)[7] == '=') {
          has_console = 1;
          continue; /* don't copy console=g to kernel */
        }
        kern_envp[ke++] = *ep;
      }
    }

    /* Always add console=d for serial output */
    if (ke < 60)
      kern_envp[ke++] = "console=d";
    /* Add other critical variables if not already present */
    if (!has_eaddr && ke < 60)
      kern_envp[ke++] = "eaddr=08:00:69:aa:bb:cc";
    if (!has_console_in && ke < 60)
      kern_envp[ke++] = "ConsoleIn=serial(0)";
    if (!has_console_out && ke < 60)
      kern_envp[ke++] = "ConsoleOut=serial(0)";
    /* Root device and boot partition for kernel devinit() */
    if (ke < 60)
      kern_envp[ke++] = "OSLoadPartition=dksc(0,1,0)";
    if (ke < 60)
      kern_envp[ke++] = "root=dks0d1s0";
    if (ke < 60)
      kern_envp[ke++] = "SystemPartition=dksc(0,1,8)";
    if (ke < 60)
      kern_envp[ke++] = "OSLoadFilename=unix";
    if (ke < 60)
      kern_envp[ke++] = "showconfig=1";
    kern_envp[ke] = (char *)0;

    stub_puts("[IP54] Kernel argc=1, envp count=");
    stub_putdec(ke);
    stub_puts("\n");

    /*
     * Set Status.ERL based on entry point address space:
     * - kuseg (< 0x80000000): Set ERL=1 for identity mapping
     *   (ECOFF programs like sash are linked at kuseg addresses)
     * - kseg0/kseg1 (>= 0x80000000): Clear ERL
     *   (kernel is linked at kseg0, e.g. 0x80006ba0)
     *
     * The CPU starts with ERL=1 from reset, so we must explicitly
     * clear it for kseg0 programs. With ERL=1, kseg0 TLB handling
     * is altered and can cause spurious TLB exceptions.
     */
    {
      unsigned long sr;
      __asm__ volatile("mfc0 %0, $12" : "=r"(sr));
      if (entry < 0x80000000) {
        sr |= 0x04; /* Set ERL for kuseg identity mapping */
      } else {
        sr &= ~0x07; /* Clear ERL, EXL, IE for kseg0 kernel */
      }
      __asm__ volatile("mtc0 %0, $12" : : "r"(sr));
    }

    /*
     * Do NOT install exception handler stubs here. The IRIX kernel
     * clears BEV early in csu.s and installs its own handlers later
     * in _hook_exceptions(). With a proper CRIME timer (host clock),
     * the divide-by-zero break exception should not occur. If it does,
     * we want the exception to be visible (crash) rather than silently
     * skipped, which would leave incorrect CPU frequency values and
     * cause harder-to-debug failures later.
     */

    /*
     * Zero BSS before jumping to the kernel.
     *
     * The ELF loader records BSS range (memsz > filesz) but defers
     * zeroing until here so all PROM I/O is complete and no PROM
     * data structures are needed anymore.
     */
    if (elf_bss_size > 0) {
      unsigned long bss_dest = to_kseg1(elf_bss_start);
      stub_puts("[IP54] Zeroing BSS at ");
      stub_puthex(bss_dest);
      stub_puts(" size=");
      stub_puthex(elf_bss_size);
      stub_puts("\n");
      mem_set((void *)bss_dest, 0, elf_bss_size);
    }

    /*
     * Pre-patch: initialise putbuf/putbufsz in DATA segment (not BSS)
     * so cmn_err works from the very first call in mlreset.
     *
     * N32 code accesses these via GP-relative slots, not the ELF symbol
     * addresses.  We write both the symbol locations and the GP slots.
     * All writes via KSEG1 (uncached) to avoid stale I-cache issues.
     */
    /*
     * Patch 0: NULL-guard cprintf's putbuf write.
     *
     * cprintf (0x8820370c) writes to putbuf ring at 0x882037b4:
     *   0x882037b0: addu v0, v0, v1    (v0 = buf_base + index)
     *   0x882037b4: sb   s0, (v0)      (store char — crashes if putbuf==NULL)
     *
     * Replace 0x882037b0 with "beqz v1, +4" which skips the sb and index
     * update when putbuf base (v1) is NULL.  The sb becomes the delay slot;
     * when taken (v1==0), v0 = 0 + something, but sb to low address is
     * harmless in kseg0/kseg1 (not a TLB miss, just a store to PROM area
     * or similar — and we skip the index update).
     *
     * Actually, v0 = v1 + masked_index.  If v1=0, v0 is a small number in
     * kuseg, which WILL TLB-miss.  So instead, replace the sb itself with
     * a conditional move: only store if v1 != 0.
     *
     * Cleanest: replace "addu v0,v0,v1" at 0x882037b0 with
     * "beqz v1, +3" to skip sb + index-update (3 instructions).
     * Delay slot = sb s0,(v0) which still executes, but v0 is garbage.
     *
     * Safest: NOP out the sb at 0x882037b4 AND the index update at
     * 0x882037b8-0x882037c0.  Once the trampoline in config_cache sets
     * putbuf, further calls will still go through this code path but
     * with putbuf!=0 they'd work.  Wait, but we NOP'd the sb...
     *
     * Best: replace "sb s0,(v0)" at 0x882037b4 with a conditional:
     * we can't easily do conditionals in one instruction on MIPS.
     *
     * Simplest working fix: NOP the addu at 0x882037b0 and sb at
     * 0x882037b4.  The trampoline at config_cache still writes to the
     * DATA putbuf.  LATER code (after putbuf is initialized) accesses
     * it through the same GP-relative slots, but we've NOP'd the actual
     * stores.  This means putbuf logging is disabled for the cprintf
     * putbuf ring (but conbuf and serial still work).
     *
     * Actually — the real fix: replace addu+sb with a proper null guard.
     * Use the conbuf path as template: it hardcodes the buffer address.
     * But that's invasive.
     *
     * PRAGMATIC: NOP the sb at 0x882037b4.  The putbuf ring won't
     * record anything, but the kernel boots.  The conbuf ring (separate
     * code path) and serial output still work.  We keep the trampoline
     * in config_cache so if we ever un-NOP this, it'll work.
     */
    /* Patch 0: cprintf putbuf sb null-guard.
     * Scan kernel text for the sb s0,0(v0) instruction preceded by
     * addu v0,v0,v1 — this is the cprintf putbuf ring store.
     * No symbol for cprintf; scan the 0x88200000-0x88210000 range.
     */
    {
      volatile unsigned int *scan;
      int found = 0;
      for (scan = (volatile unsigned int *)0xa8200000U;
           scan < (volatile unsigned int *)0xa8210000U && !found; scan++) {
        if (*scan == 0xa0500000U) {  /* sb s0, 0(v0) */
          /* Verify preceding instruction is addu v0,v0,v1 */
          if (scan[-1] == 0x00431021U) {  /* addu v0,v0,v1 */
            *scan = 0x00000000U;  /* nop */
            stub_puts("[IP54] Patched cprintf: NOP putbuf sb (null-safe)\n");
            found = 1;
          }
        }
      }
      if (!found) {
        stub_puts("[IP54] WARN: cprintf sb pattern not found\n");
      }
    }

    /*
     * Patch 0b: _bclean_caches null-guard.
     *
     * _bclean_caches walks an array of (func, info_ptr) pairs.
     * At 0x88205944: lw at, 4(s0)    — loads info_ptr
     * At 0x88205948: lw at, 0x38(at) — dereferences info_ptr->flags
     * If info_ptr is NULL, this crashes with TLB miss at VA 0x38.
     *
     * Replace 0x88205948 with "beqz at, +0x13" to skip the call when
     * info_ptr is NULL.  The original beqz at 0x88205950 goes to +0x13
     * (0x882059a0) which is the skip path.  We reuse that target.
     * delay slot: andi at, at, 0x10 — harmless with at=0 (gives 0).
     */
    {
      unsigned int bclean = kern_sym_u("_bclean_caches");
      if (bclean) {
        /* Scan _bclean_caches for "lw at, 0x38(at)" pattern */
        volatile unsigned int *p = (volatile unsigned int *)bclean;
        int i, found = 0;
        for (i = 0; i < 200 && !found; i++) {
          if (p[i] == 0x8c210038U) {  /* lw at, 0x38(at) */
            /* Replace with beqz at, +N to skip the call.
             * The original beqz further down skips to the same target.
             * Compute target offset: scan forward for the "b" skip target. */
            p[i] = 0x10200015U;  /* beqz at, +0x15 (skip path) */
            stub_puts("[IP54] Patched _bclean_caches: null-guard info_ptr\n");
            found = 1;
          }
        }
        if (!found) {
          stub_puts("[IP54] WARN: _bclean_caches pattern not found\n");
        }
      }
    }

    /*
     * Kernel code patches.
     *
     * These are applied to the TEXT segment via KSEG1 (uncached) writes,
     * so they survive the kernel's own `jal bzero` at the start of
     * mlsetup() which zeros all of BSS.  (Code patches to BSS variables
     * may also be needed if mlsetup zeroes a wider range.)
     *
     * config_cache (VA 0x88010ae4) switches to uncached execution at
     * 0x88010b5c by jumping to 0xa8010b74 (KSEG1 mirror).  All patched
     * code runs from KSEG1, so I-cache coherency is not an issue.
     *
     * Patch 1: size_2nd_cache → constant 1 MB
     *   0x88010b74: jal size_2nd_cache (0x0e00433d)
     *            →  lui v0, 0x0010     (0x3c020010)  v0 = 1 MB
     *
     *   QEMU R10000 has no secondary cache; size_2nd_cache returns 0.
     *   With boot_sidcache_size==0, mlsetup skips all init and panics.
     *
     * Patch 2: putbuf/putbufsz initialisation trampoline
     *   After Patch 1, config_cache stores v0 (1 MB) to boot_sidcache_size
     *   at 0x88010b84, then at 0x88010b88 branches forward (bnez v0,
     *   0x88010c40) because v0 is nonzero.  The fall-through code at
     *   0x88010b90-0x88010c3c is dead (never reached).
     *
     *   We redirect the bnez to our trampoline at 0x88010b90 which sets:
     *     putbuf   (0x8829f440) = 0x882e0000 (4 KiB temp ring above BSS)
     *     putbufsz (0x8829f410) = 4096
     *   then jumps to the original branch target 0x88010c40.
     *
     *   Without this, cmn_err() in early init writes *(putbuf + ...)
     *   with putbuf==NULL → TLB miss at VA 0 → tlbmiss asserts
     *   IS_KSEG2(0) → assfail → semawait reads curthreadp (NULL) →
     *   double panic.
     */
    {
      volatile unsigned int *p;
      unsigned int cc = kern_sym_u("config_cache");
      unsigned int putbuf_addr = kern_sym("putbuf");
      unsigned int putbufsz_addr = kern_sym("putbufsz");
      unsigned int maxcpus_addr = kern_sym("maxcpus");
      unsigned int psema_addr = kern_sym("psema");
      unsigned int semawait_addr = kern_sym("semawait");

      if (!cc || !putbuf_addr || !putbufsz_addr || !maxcpus_addr || !psema_addr) {
        stub_puts("[IP54] WARN: config_cache/putbuf/maxcpus/psema symbols not found\n");
      } else {
      /* Patch 1: replace jal size_2nd_cache with lui v0, 0x0010 */
      /* config_cache+0x90: jal size_2nd_cache → lui v0, 0x0010 */
      p = (volatile unsigned int *)(cc + 0x90);
      {
        unsigned int expect_jal = mips_jal(kern_sym("size_2nd_cache"));
        if (*p == expect_jal) {
          *p = 0x3c020010U; /* lui v0, 0x0010 */
          stub_puts("[IP54] Patched size_2nd_cache call -> 1MB\n");
        } else {
          stub_puts("[IP54] WARN: size_2nd_cache patch mismatch: ");
          stub_puthex(*p);
          stub_puts("\n");
        }
      }

      /*
       * Patch 2: config_cache dead-code trampoline.
       * config_cache+0xa4: bnez v0, +N (always taken after patch 1)
       * Redirect to +1 (our trampoline at config_cache+0xac).
       */
      p = (volatile unsigned int *)(cc + 0xa4);
      if ((*p >> 16) == 0x1440U) { /* bnez v0, +N */
        unsigned int orig_target_off = (*p & 0xffffU); /* save for later jump */
        *p = 0x14400001U;      /* bnez v0, +1 (→ config_cache+0xac) */

        p = (volatile unsigned int *)(cc + 0xac);
        /* --- (a) putbuf = 0x882e0000 --- */
        p[0]  = mips_lui(1, putbuf_addr);      /* lui at, hi(putbuf) */
        p[1]  = mips_addiu(1, 1, putbuf_addr);  /* addiu at, at, lo(putbuf) */
        p[2]  = 0x3c03882eU;                    /* lui v1, 0x882e (temp buffer) */
        p[3]  = 0xac230000U;                    /* sw v1, 0(at) */
        /* --- putbufsz = 4096 --- */
        p[4]  = mips_lui(1, putbufsz_addr);
        p[5]  = mips_addiu(1, 1, putbufsz_addr);
        p[6]  = 0x24031000U;                    /* addiu v1, zero, 0x1000 */
        p[7]  = 0xac230000U;                    /* sw v1, 0(at) */
        /* --- (a2) putbuf GP-relative slot: symbol_addr + 0x98 --- */
        /* The GP slot is at a fixed offset from the symbol address.
         * In the old kernel: putbuf=0x882a0068, GP_slot=0x882a0100 (delta=0x98)
         * This delta is linker-dependent; try to keep it working. */
        {
          unsigned int gp_putbuf = putbuf_addr + 0x98;
          unsigned int gp_putbufsz = putbufsz_addr + 0x98;
          p[8]  = mips_lui(1, gp_putbuf);
          p[9]  = mips_addiu(1, 1, gp_putbuf);
          p[10] = 0x3c03882eU;       /* lui v1, 0x882e */
          p[11] = 0xac230000U;       /* sw v1, 0(at) */
          p[12] = mips_lui(1, gp_putbufsz);
          p[13] = mips_addiu(1, 1, gp_putbufsz);
          p[14] = 0x24031000U;       /* addiu v1, zero, 0x1000 */
          p[15] = 0xac230000U;       /* sw v1, 0(at) */
        }
        /* --- (b) maxcpus = 1 --- */
        p[16] = mips_lui(1, maxcpus_addr);
        p[17] = mips_addiu(1, 1, maxcpus_addr);
        p[18] = 0x24030001U;        /* addiu v1, zero, 1 */
        p[19] = 0xac230000U;        /* sw v1, 0(at) */
        /* --- jump to original continuation --- */
        /* Original bnez target was config_cache+0xa4 + 4 + (orig_target_off<<2) */
        {
          unsigned int cc_kseg0 = kern_sym("config_cache");
          unsigned int cont = cc_kseg0 + 0xa4 + 4 + (orig_target_off << 2);
          /* Encode as relative branch from current PC = config_cache+0xac+20*4 */
          unsigned int pc_here = cc_kseg0 + 0xac + 20 * 4;
          int branch_off = (int)(cont - pc_here - 4) >> 2;
          p[20] = 0x10000000U | (branch_off & 0xffffU);  /* b cont */
        }
        p[21] = 0x00000000U;        /* nop (delay slot) */
        stub_puts("[IP54] Installed putbuf/maxcpus trampoline\n");

        /*
         * --- (c) psema early-boot trampoline ---
         * Placed at config_cache+0x104 (22 words of dead code space)
         */
        {
          unsigned int tramp_kseg0 = kern_sym("config_cache") + 0x104;
          p = (volatile unsigned int *)(cc + 0x104);
          /* bgez v1, +7 → count >= 0, success */
          p[0]  = 0x04610007U;
          p[1]  = 0x00000000U;
          /* count < 0: check curthreadp */
          p[2]  = 0x8C01A018U; /* lw at, -24552($0) (PDA+0x18) */
          /* bnez at, +7 → real block */
          p[3]  = 0x14200007U;
          p[4]  = 0x00000000U;
          /* Early boot: undo count-- and succeed */
          p[5]  = 0x86030000U; /* lh v1, 0(s0) */
          p[6]  = 0x24630001U; /* addiu v1, v1, 1 */
          p[7]  = 0xA6030000U; /* sh v1, 0(s0) */
          /* j psema+0x38 (success path) */
          p[8]  = mips_j(psema_addr + 0x38);
          p[9]  = 0x00000000U;
          p[10] = 0x00000000U; /* gap */
          /* Real blocking (curthreadp != NULL): */
          p[11] = 0x02002025U; /* move a0, s0 */
          p[12] = 0xDFA50008U; /* ld a1, 8(sp) */
          p[13] = 0x00403025U; /* move a2, v0 */
          p[14] = mips_jal(semawait_addr);  /* jal semawait */
          p[15] = 0x8FA70004U; /* lw a3, 4(sp) (delay slot) */
          p[16] = mips_j(psema_addr + 0x48);  /* j psema ret */
          p[17] = 0xDFB00010U; /* ld s0, 16(sp) (delay slot) */

          /* Redirect psema's bltz to our trampoline */
          p = (volatile unsigned int *)(kern_sym_u("psema") + 0x30);
          if (*p == 0x04600008U) { /* bltz v1, +8 */
            *p = mips_j(tramp_kseg0);
            stub_puts("[IP54] Installed psema safety trampoline\n");
          } else {
            stub_puts("[IP54] WARN: psema bltz mismatch: ");
            stub_puthex(*p);
            stub_puts("\n");
          }
        }
      } /* else: bnez mismatch */
      } /* end cc/putbuf/maxcpus/psema check */
    }

    /*
     * Patch 4: ip54_get_timestamp — fix pvtimer address.
     *
     * The compiled kernel has ip54_get_timestamp reading from 0x0FF00080
     * (IP30 HEART PIU base, unmapped on IP54).  The correct address is
     * 0xBF480538 — KSEG1 of physical 0x1F480538 (pvtimer counter reg).
     *
     * 0x88003020: lui  $12, 0x0ff0   → lui  $12, 0xbf48
     * 0x8800324c: ori  $12,$12,0x80  → ori  $12,$12,0x38
     */
    {
      unsigned int ts = kern_sym_u("ip54_get_timestamp");
      if (ts) {
        volatile unsigned int *p = (volatile unsigned int *)ts;
        if (*p == 0x3c0c0ff0U) {       /* lui $12, 0x0ff0 */
          p[0] = 0x3c0cbf48U;          /* lui $12, 0xb400 */
          p[1] = 0x358c0538U;          /* ori $12,$12, 0x38 */
          stub_puts("[IP54] Patched ip54_get_timestamp -> pvtimer 0xBF480538\n");
        }
      }
    }

    /*
     * Patch 4b: second _get_timestamp template at 0x88003470.
     *
     * _hook_exceptions copies this template into a fast-path locore slot
     * at VA 0x88003020.  If left unpatched, it reads HEART 0x0FF00080
     * (unmapped on IP54) and causes a TLB miss panic during early boot.
     */
    /* Patch 4b: Scan near ip54_get_timestamp for a second lui $12, 0x0ff0 (template copy) */
    {
      unsigned int ts = kern_sym_u("ip54_get_timestamp");
      if (ts) {
        volatile unsigned int *scan;
        for (scan = (volatile unsigned int *)(ts + 0x100);
             scan < (volatile unsigned int *)(ts + 0x800); scan++) {
          if (*scan == 0x3c0c0ff0U) {
            scan[0] = 0x3c0cbf48U;
            scan[1] = 0x358c0538U;
            stub_puts("[IP54] Patched _get_timestamp template -> pvtimer\n");
            break;
          }
        }
      }
    }

    /*
     * Patch 4c: Pre-install pvtimer reader at locore slot 0x88003020.
     *
     * The kernel's _hook_exceptions or locore setup installs a HEART
     * counter reader at VA 0x88003020 (zeros in the ELF).  At runtime,
     * this slot gets called as a fast-path timestamp reader.  The
     * installed code reads VA 0x0FF00080 (HEART counter via XUSEG),
     * which faults because no TLB entry maps that address on IP54.
     *
     * Pre-install pvtimer reader code here.  If the kernel later copies
     * from the already-patched template at 0x88003020, the copy will
     * also contain pvtimer code.  If nothing overwrites this slot,
     * it already has the correct code.
     *
     *   lui  $12, 0xBF48     # 3c0cb400
     *   ori  $12, $12, 0x0538  # 358c0038
     *   ld   $v0, 0($12)     # dd820000
     *   jr   $ra             # 03e00008
     *   nop                  # 00000000
     */
    {
      unsigned int ts = kern_sym_u("ip54_get_timestamp");
      if (ts) {
        volatile unsigned int *p = (volatile unsigned int *)ts;
        p[0] = 0x3c0cbf48U;  /* lui  $12, 0xBF48 */
        p[1] = 0x358c0538U;  /* ori  $12, $12, 0x0038 */
        p[2] = 0xdd820000U;  /* ld   $v0, 0($12) */
        p[3] = 0x03e00008U;  /* jr   $ra */
        p[4] = 0x00000000U;  /* nop */
        stub_puts("[IP54] Pre-installed pvtimer reader\n");
      }
    }

    /*
     * Patch 5: pvdiskedtinit — bypass badaddr() probe.
     *
     * pvdiskedtinit() calls badaddr(0xB7000000, 4) to detect the
     * sgi-bootdisk device.  badaddr() uses the IP22 MC bus-error
     * registers at 0xBFA000EC/FC, and the fake MC on IP54 may not
     * emulate the bus-error detection correctly, causing the probe
     * to silently fail and the driver to never register hwgraph paths.
     *
     * We know the device is present (the PROM loaded the kernel from it).
     * Replace `jal badaddr` at 0x8802b960 with `move v0, zero` so
     * badaddr appears to return 0 (success).
     *
     * 0x8802b960: 0e006eb9  jal badaddr  → 00001025  move v0, zero
     */
    {
      /*
       * The disk kernel's pvdiskedtinit (0x8802b918) checks:
       *   1. badaddr(0xB7000000, 4) — probes pvdisk MMIO (succeeds on QEMU)
       *   2. SIZE_LO != 0 OR SIZE_HI != 0 — our 4GB disk has SIZE_LO=0x800000
       *   3. pvdisk_do_read(0, buf) — reads volume header
       *   4. vh_magic == 0x0BE5A941
       * No EDT guard in this build — compiler inlined the check differently.
       * No patches needed for pvdiskedtinit instructions.
       */
      /* Find pvdiskedtinit by scanning for its signature:
       * It starts with a store 0 to offset 0x28 from some register (clearing a flag),
       * OR we can find it by looking for the pvdisk MMIO address 0xB7000000 nearby.
       * Use io_init to find it — scan the io_init table for the pvdisk entry. */
      /* pvdiskedtinit address will be found dynamically below */
      stub_puts("[IP54] pvdiskedtinit: no patches needed (disk kernel build)\n");
    }

    /*
     * Patch 6: Add pvdiskedtinit to the io_init[] function pointer array.
     * Use kern_sym to find both io_init and pvdiskedtinit.
     */
    {
      unsigned int io_init_addr = kern_sym_u("io_init");
      unsigned int pvdisk_edt = kern_sym("pvdiskedtinit");
      unsigned int pvnet_edt = kern_sym("if_pvnetedtinit");
      unsigned int pvfb_edt = kern_sym("pvfbedtinit");
      unsigned int pvaudio_edt = kern_sym("pvaudioedtinit");

      if (io_init_addr && pvdisk_edt) {
        volatile unsigned int *io = (volatile unsigned int *)io_init_addr;
        int j;

        /*
         * Remove dangerous init functions from io_init[].
         * ng1_init probes GIO addresses for real Newport hardware;
         * on IP54 (no GIO bus), reads return 0 instead of bus error,
         * so ng1 registers a ghost board that crashes gfxinit/Xsgi.
         * pckminit probes IOC2 8042 PS/2 controller which doesn't
         * exist on IP54.
         */
        {
          unsigned int ng1_init_fn = kern_sym("ng1_init");
          unsigned int pckminit_fn = kern_sym("pckminit");
          int k, dst;
          /* Compact io_init[] by removing unwanted entries */
          for (k = 0, dst = 0; k < 30 && io[k] != 0; k++) {
            if (io[k] == ng1_init_fn) {
              stub_puts("[IP54] Removed ng1_init from io_init[");
              stub_puthex(k);
              stub_puts("]\n");
              continue;
            }
            /* pckminit kept — probe fails harmlessly via badaddr,
             * but shmiq gets initialized for Xsgi input devices */
            io[dst] = io[k];
            dst++;
          }
          io[dst] = 0; /* re-NULL-terminate */
        }

        /* Dump io_init[] array (after cleanup) */
        stub_puts("[IP54] io_init[] at ");
        stub_puthex(io_init_addr);
        stub_puts(":\n");
        for (j = 0; j < 30; j++) {
          stub_puts("  [");
          stub_puthex(j);
          stub_puts("]=");
          stub_puthex(io[j]);
          if (io[j] == 0) stub_puts(" NULL");
          stub_puts("\n");
          if (io[j] == 0) break;
        }
        /* Find NULL terminator and append PV device edtinit functions */
        for (j = 0; j < 30; j++) {
          if (io[j] == 0) {
            /* Append pvdiskedtinit */
            io[j] = pvdisk_edt;
            stub_puts("[IP54] Appended pvdiskedtinit at io_init[");
            stub_puthex(j);
            stub_puts("] = ");
            stub_puthex(pvdisk_edt);
            stub_puts("\n");
            j++;
            /* Append if_pvnetedtinit */
            if (pvnet_edt) {
              io[j] = pvnet_edt;
              stub_puts("[IP54] Appended if_pvnetedtinit at io_init[");
              stub_puthex(j);
              stub_puts("] = ");
              stub_puthex(pvnet_edt);
              stub_puts("\n");
              j++;
            }
            /* Append pvfbedtinit */
            if (pvfb_edt) {
              io[j] = pvfb_edt;
              stub_puts("[IP54] Appended pvfbedtinit at io_init[");
              stub_puthex(j);
              stub_puts("] = ");
              stub_puthex(pvfb_edt);
              stub_puts("\n");
              j++;
            }
            /* Append pvaudioedtinit */
            if (pvaudio_edt) {
              io[j] = pvaudio_edt;
              stub_puts("[IP54] Appended pvaudioedtinit at io_init[");
              stub_puthex(j);
              stub_puts("] = ");
              stub_puthex(pvaudio_edt);
              stub_puts("\n");
              j++;
            }
            /* NULL-terminate */
            io[j] = 0;
            break;
          }
        }
        if (j >= 30) {
          stub_puts("[IP54] WARN: io_init[] full\n");
        }
      } else {
        if (!io_init_addr) stub_puts("[IP54] WARN: io_init symbol not found\n");
        if (!pvdisk_edt) stub_puts("[IP54] WARN: pvdiskedtinit not found by scan\n");
      }
      if (!pvnet_edt) stub_puts("[IP54] WARN: if_pvnetedtinit not found\n");
    }

    /*
     * Patch 7: REMOVED — pvdisk prefix mismatch fixed in source code.
     * pvdisk.c now passes "pvdisk" (not "pvdisk_") to
     * hwgraph_block_device_add, matching the master.d PREFIX.
     */

    /*
     * Patch 8: Stub rtodc() — return constant time.
     *
     * rtodc() (VA 0x8800b474) reads the Dallas DS1286 RTC via _clock_func.
     * On IP54 there is no real RTC, so _clock_func reads garbage MMIO,
     * producing invalid BCD values.  The month loop overflows month_days[]
     * causing a Data Bus Error.
     *
     * Patch: replace the first 3 instructions with:
     *   lui v0, 0x4000      → v0 = 0x40000000 (Jan 10, 2004)
     *   jr  ra
     *   nop
     */
    {
      unsigned int rtodc_a = kern_sym_u("rtodc");
      if (rtodc_a) {
        volatile unsigned int *p = (volatile unsigned int *)rtodc_a;
        p[0] = 0x3c024000U;             /* lui v0, 0x4000 */
        p[1] = 0x03e00008U;             /* jr  ra         */
        p[2] = 0x00000000U;             /* nop            */
        stub_puts("[IP54] Stubbed rtodc -> 0x40000000 (Jan 2004)\n");
      }
    }

    /*
     * Patch 9: Sign-extend ovbcopy/bcopy src and dst for MIPS64.
     *
     * ovbcopy/bcopy (VA 0x8801221c) receives 32-bit pointers from N32
     * code but runs on a 64-bit CPU.  Sign-extend both src ($a0) and
     * dst ($a1) via sll-by-0, then branch unconditionally to the real
     * copy logic at 0x88012240.
     *
     * IMPORTANT: The original Patch 9 used "bltz $a1" to reject non-KSEG
     * destinations, which broke copyout() to user-space (address 0x10000000
     * is positive → copy was silently skipped → icode page empty → init
     * got SIGSEGV).  Rev 2 used unconditional branch but exposed an
     * existing bug: some caller passes NULL dst, crashing at 0x880122CC.
     * Rev 3 used bne for NULL, but exec path passes dst=0x200 (NULL+offset).
     *
     * Rev 6 (current): sign-extend + skip if dst < 0x10000 (64KB).
     *
     * Threshold rationale: NULL+offset bad addresses seen are 0x0,
     * 0x200, 0x4000 (all < 64KB).  IRIX shared libs load at ~0x0fa00000
     * (e.g. /lib32/libc.so.1 entry at 0x0fae952c), so threshold MUST be
     * below 0x0fa00000.  64KB catches all NULL+offset while allowing
     * shared library mapping.
     *
     *  +0: sll  $a1, $a1, 0          sign-extend dst
     *  +4: sll  $a0, $a0, 0          sign-extend src
     *  +8: lui  $at, 0x0001          $at = 0x00010000 (64KB)
     * +12: sltu $at, $a1, $at        $at=1 if dst < 64KB (unsigned)
     * +16: beq  $at, $zero, +4       if dst >= 64KB → 0x88012240
     * +20: nop                       delay slot
     * +24: jr   $ra                  return early (bad dst)
     * +28: move $v0, $zero           return 0 (delay slot)
     * +32: nop                       (unused, fill original space)
     */
    {
      unsigned int ov = kern_sym_u("ovbcopy");
      if (ov) {
        volatile unsigned int *p = (volatile unsigned int *)ov;
        /* Overwrite the first 9 instructions with sign-extend + guard */
        p[0] = 0x00052800U;             /* sll  $a1, $a1, 0           */
        p[1] = 0x00042000U;             /* sll  $a0, $a0, 0           */
        p[2] = 0x3C010001U;             /* lui  $at, 0x0001           */
        p[3] = 0x00A1082BU;             /* sltu $at, $a1, $at         */
        p[4] = 0x10200004U;             /* beq  $at, $zero, +4        */
        p[5] = 0x00000000U;             /* nop  (delay slot)           */
        p[6] = 0x03E00008U;             /* jr   $ra                    */
        p[7] = 0x00001025U;             /* or   $v0,$zero,$zero (=0)   */
        p[8] = 0x00000000U;             /* nop (unused)               */
        stub_puts("[IP54] Patched ovbcopy: sign-extend + low-addr guard\n");
      }
    }

/* Patch 10: Fix Context register PTEBase for QEMU mtc0 sign-extension bug.
     *
     * The kernel sets CP0_Context (PTEBase) via:
     *   0x88004D40: addiu v0, zero, 0x1FF    (0x240201FF)
     *   0x88004D44: dsll  v0, v0, 24          (0x00021638)  → v0 = 0x00000001FF000000
     *   0x88004D48: mtc0  v0, C0_Context      (0x40822000)
     *
     * On real hardware, mtc0 sign-extends v0[31:0]=0xFF000000 → 0xFFFFFFFFFF000000.
     * QEMU's helper_mtc0_context() uses the full 64-bit GPR value, so it stores
     * 0x00000001FF000000 — putting the page table in user space instead of KSEG3.
     *
     * Fix: replace addiu+dsll with lui v0, 0xFF00 + nop.
     * lui sign-extends on MIPS64: 0xFF00<<16 = 0xFF000000 → 0xFFFFFFFFFF000000.
     * QEMU sees the correct 64-bit value in the GPR, so mtc0 stores the right PTEBase.
     */
    /* Scan kernel text for the Context PTEBase sequence:
     * addiu v0, zero, 0x1FF (0x240201ff) followed by dsll v0,v0,24 (0x00021638) */
    {
      volatile unsigned int *scan;
      int found = 0;
      for (scan = (volatile unsigned int *)0xa8003000U;
           scan < (volatile unsigned int *)0xa8010000U && !found; scan++) {
        if (scan[0] == 0x240201ffU && scan[1] == 0x00021638U) {
          scan[0] = 0x3c02ff00U;  /* lui v0, 0xFF00 */
          scan[1] = 0x00000000U;  /* nop */
          stub_puts("[IP54] Patched Context PTEBase: lui v0,0xFF00 (QEMU mtc0 fix)\n");
          found = 1;
        }
      }
      if (!found) {
        stub_puts("[IP54] WARN: Context PTEBase pattern not found\n");
      }
    }

/* Patch 11: (removed) */

    /* Patch 12: Diagnostic — dump ELF header bytes in getelfhead validation.
     *
     * getelfhead (static func at 0x8825BA70) reads the ELF header via
     * exrdhead, then validates: ehdr[2]=='L', ehdr[3]=='F', class, etc.
     * The trace shows validation FAILS at ehdr[2] != 'L'.
     *
     * At entry to this code path (0x8825BAE8):
     *   a7 ($11) = pointer to ELF header buffer
     *   v1 ($3)  = ehdr[2] (loaded at 0x8825BAE4: lbu v1, 2(a7))
     *
     * Previous version had two bugs:
     *   1. PVUART THR is at +0x17B (byte 3 of 32-bit reg), not +0x178
     *   2. a7 = $11 in N32 ABI, not $7 (lbu encodings were wrong)
     *
     * Outputs to PVUART: 'X', ehdr[2], ehdr[0], ehdr[1], then → error.
     */
    /* Patch 12 (getelfhead diagnostic) REMOVED — addresses shifted in new
     * kernel and the patch corrupted normal ELF loading flow, causing init
     * to fail with SIGFPE. The diagnostic is no longer needed. */

    /* Patch 13: NULL-guard for early-boot mtextnode_vnodeops calls.
     *
     * During early kernel boot (curthreadp==NULL, before mlsetup finishes),
     * something in the call chain invokes a VOP on a vnode whose v_fbhv
     * (first behavior descriptor) is still NULL.  Every affected mtextnode_*
     * function begins with:
     *   lw v0, 0(a0)   ; a0 = v_fbhv — crashes if a0==NULL
     * This causes execution at address 0 → TLB miss → IS_KSEG2(0) assertion
     * in tlbmiss().
     *
     * Key MIPS constraint: the delay slot of a branch ALWAYS executes, so
     * we cannot put "lw v0,0(a0)" in a beqz delay slot when a0 might be NULL.
     *
     * Solution: patch 4 instructions per function (overwriting [+0]..[+12]).
     * Original layout (uniform across all 15 affected functions):
     *   [+0]:  lw v0, 0(a0)       ; 0x8c820000 — crashes when a0==NULL
     *   [+4]:  lw v0, 0(v0)       ; 0x8c420000 — deref mtextnode->real_vnode
     *   [+8]:  bnez v0, cont      ; 0x14400004 — branch if real_vnode!=NULL
     *   [+12]: addiu sp, sp, -N   ; start of ENXIO path (and stack setup)
     *   [+16]: li v0, 6           ; ENXIO return value
     *   ...
     *
     * Patched layout:
     *   [+0]:  beqz a0, +3        ; 0x10800003 — if a0==NULL → FUNC+16 = li v0,6
     *   [+4]:  nop                ; 0x00000000 — safe delay slot (no dereference)
     *   [+8]:  lw v0, 0(a0)      ; 0x8c820000 — original [+0], safe (a0!=NULL)
     *   [+12]: bnez v0, +3       ; 0x14400003 — adjusted offset (was 4, now 3)
     *                             ;   target: FUNC+12+4+3*4 = FUNC+28 = original cont ✓
     *
     * Verification:
     *   a0==NULL: beqz taken, delay nop, → FUNC+16 = li v0,6 → jr ra → ENXIO ✓
     *   a0!=NULL: beqz not taken, nop, → [+8] lw v0,0(a0) → [+12] bnez → cont ✓
     *
     * The bnez offset must be adjusted from 4→3 because we inserted nop at [+4],
     * shifting the bnez instruction from its original address (+8) to +12.
     * Both branch targets remain at the same absolute addresses.
     *
     * Function addresses from kernel nm output (mtextnode_vnodeops offsets 0x74–0xac):
     */
    {
      /* mtextnode_vnodeops is a vtable (array of function pointers).
       * Read each function pointer from the vtable and apply the guard.
       * The vtable is at kern_sym("mtextnode_vnodeops"), 228 bytes = 57 pointers.
       * Functions at offsets 0x74-0xAC (indices 29-43, 15 functions spaced 0x4C apart? No.)
       * Actually they're at offsets within the vnode_ops table structure.
       * Scan all pointers, apply guard to any that start with lw v0,0(a0). */
      unsigned int mvn = kern_sym_u("mtextnode_vnodeops");
      if (mvn) {
        volatile unsigned int *vtbl = (volatile unsigned int *)mvn;
        int fi, patched = 0;
        /* The structure has function pointers at every 4 bytes */
        for (fi = 0; fi < 57; fi++) {
          unsigned int fptr = vtbl[fi];
          if (fptr >= 0x88002000U && fptr < 0x882a0000U) {
            volatile unsigned int *fp = (volatile unsigned int *)(0x20000000U | fptr);
            if (fp[0] == 0x8c820000U && fp[1] == 0x8c420000U) {
              fp[0] = 0x10800003U;  /* beqz a0, +3 → ENXIO */
              fp[1] = 0x00000000U;  /* nop */
              fp[2] = 0x8c820000U;  /* lw v0, 0(a0) */
              fp[3] = 0x14400003U;  /* bnez v0, +3 */
              patched++;
            }
          }
        }
        stub_puts("[IP54] Patched mtextnode_vnodeops: ");
        stub_putdec(patched);
        stub_puts(" functions NULL-guarded\n");
      }
    }

/* PROM-level /sbin/init read test: CONFIRMED disk data is valid ELF.
     * Bytes: 7F 45 4C 46 01 02 01 00 ... (correct).
     * Diagnostic removed to avoid side effects on kernel boot. */

    /*
     * Patch 14a: Stub reset_leds — prevent bus error from ISA LED access.
     *
     * reset_leds (0x8800b1b4) calls set_leds which writes to 0xBFBD9870
     * (ISA bus LED register on Indy/Indigo2).  This address doesn't exist
     * on IP54 PV, causing a Data Bus Error.  The error triggers
     * ecc_exception_recovery → ktext_recover, which tries to access
     * page tables at 0xff83fc00 before they're set up → TLB PANIC.
     *
     * 0x8800b1b4: 27bdfff0  addiu sp,sp,-16  →  03e00008  jr ra
     * 0x8800b1b8: ffbf0000  sd ra, 0(sp)     →  00000000  nop
     *
     * Also stub set_leds (0x8800b1ec) since other callers may also
     * write to the ISA LED register.
     * 0x8800b1ec: 3c02bfbd  lui v0, 0xbfbd   →  03e00008  jr ra
     * 0x8800b1f0: 34429870  ori v0, 0x9870   →  00000000  nop
     */
    {
      unsigned int rl = kern_sym_u("reset_leds");
      volatile unsigned int *p = rl ? (volatile unsigned int *)rl : (volatile unsigned int *)0;
      if (p && (*p == 0x27bdfff0U || *p == 0x27bdfff8U)) {
        p[0] = 0x03e00008U;  /* jr ra */
        p[1] = 0x00000000U;  /* nop   */
        stub_puts("[IP54] Patched reset_leds: stubbed (no ISA LEDs)\n");
      } else {
        stub_puts("[IP54] WARN: reset_leds patch mismatch: ");
        stub_puthex(*p);
        stub_puts("\n");
      }
      {
        unsigned int sl = kern_sym_u("set_leds");
        p = sl ? (volatile unsigned int *)sl : (volatile unsigned int *)0;
      }
      if (p && *p == 0x3c02bfbdU) {
        p[0] = 0x03e00008U;  /* jr ra */
        p[1] = 0x00000000U;  /* nop   */
        stub_puts("[IP54] Patched set_leds: stubbed (no ISA LEDs)\n");
      }
    }

    /*
     * Patch 14b: NOP calloutinit_cpu call from alloc_cpupda.
     *
     * alloc_cpupda (0x88218db8) calls calloutinit_cpu (0x881c0460) at
     * 0x88219044.  But calloutinit_cpu dereferences the global `calltodo`
     * which is only allocated later by calloutinit.
     * At this point calltodo==0, so the store `sw s1, 0(cpu*128 + calltodo)`
     * faults with badva=0x80 (cpu=1, calltodo=0: 1*128 + 0 = 0x80).
     *
     * calloutinit will call calloutinit_cpu again later once calltodo is
     * allocated, so skipping the early call is safe.
     *
     * 0x88219044: 0e070118  jal calloutinit_cpu  →  00000000  nop
     */
    {
      unsigned int ap = kern_sym_u("alloc_cpupda");
      unsigned int ci = kern_sym("calloutinit_cpu");
      if (ap && ci) {
        /* Scan alloc_cpupda for jal calloutinit_cpu and NOP it */
        volatile unsigned int *p = (volatile unsigned int *)ap;
        unsigned int expect_jal = mips_jal(ci);
        int i, found = 0;
        for (i = 0; i < 200 && !found; i++) {
          if (p[i] == expect_jal) {
            p[i] = 0x00000000U;  /* nop */
            stub_puts("[IP54] Patched alloc_cpupda: skip early calloutinit_cpu\n");
            found = 1;
          }
        }
        if (!found) {
          stub_puts("[IP54] WARN: alloc_cpupda calloutinit_cpu jal not found\n");
        }
      }
    }

    /*
     * Patch 15: Stub findcpufreq_raw() — return 200000000 (200MHz).
     *
     * findcpufreq_raw (0x88011784) calls _ticksper1024inst() and
     * _cpuclkper100ticks() which use the 8254 timer (not present on IP54).
     * More importantly, its caller timestamp_init triggers installation of
     * HEART counter reading code at VA 0x88003020.  That code does
     *   lui $12,0x0FF0; ld $25,0x80($12)
     * which on MIPS64 targets VA 0x000000000FF00080 (XUSEG) — a TLB miss
     * panic since no TLB entry exists.
     *
     * Stub: return 200000000 (0x0BEBC200) immediately.
     *   lui  v0, 0x0BEB      # 3c020beb
     *   ori  v0, v0, 0xC200  # 3442c200
     *   jr   ra              # 03e00008
     *   nop                  # 00000000
     */
    {
      unsigned int fcf_addr = kern_sym_u("findcpufreq_raw");
      volatile unsigned int *p = fcf_addr ? (volatile unsigned int *)fcf_addr : 0;
      if (!p) {
        stub_puts("[IP54] WARN: findcpufreq_raw symbol not found\n");
      } else {
      /* Verify first instruction looks like the function prologue */
      unsigned int first = *p;
      /* Accept any addiu sp,sp,XX or lui XX,XX as prologue */
      if ((first >> 16) == 0x27bd || (first >> 26) == 0x0f) {
        p[0] = 0x3c020bebU;  /* lui  v0, 0x0BEB */
        p[1] = 0x3442c200U;  /* ori  v0, v0, 0xC200 */
        p[2] = 0x03e00008U;  /* jr   ra */
        p[3] = 0x00000000U;  /* nop */
        stub_puts("[IP54] Patched findcpufreq_raw: return 200MHz\n");
      } else {
        stub_puts("[IP54] WARN: findcpufreq_raw prologue mismatch: ");
        stub_puthex(first);
        stub_puts("\n");
      }
      } /* end if(p) */
    }

    /*
     * Patch 16: Guard badaddr_val against XUSEG probes.
     *
     * badaddr_val(addr, width, ptr) probes a device address.  On IP22,
     * some probes target KSEG1 addresses (0xBFxxxxxx).  On IP54 (MIPS64),
     * some callers pass addresses in XUSEG (bit 63=0, requires TLB),
     * causing TLB miss panics when no TLB entry exists.
     *
     * Patch the function prologue to check if $a0 >= 0 (XUSEG on MIPS64)
     * and return 1 (bad address) immediately if so.
     *
     * Original first 8 instructions (32 bytes at 0x8801bc58):
     *   0: addiu sp, sp, -0x10
     *   1: sd    ra, 8(sp)
     *   2: mfc0  t4, $12        (save Status)
     *   3-6: nop (CP0 hazard)
     *   7: mtc0  zero, $12      (disable interrupts)
     *
     * Patched:
     *   0: bgez  a0, +7         -> jr ra at instruction 8
     *   1: nop
     *   2: addiu sp, sp, -0x10  (moved from 0)
     *   3: sd    ra, 8(sp)      (moved from 1)
     *   4: mfc0  t4, $12        (moved from 2)
     *   5: nop                  (hazard)
     *   6: b     +5             -> instruction C (0x8801bc88)
     *   7: mtc0  zero, $12      (delay slot, moved from 7)
     *   8: jr    ra             (XUSEG return target)
     *   9: addiu v0, zero, 1    (delay slot: return 1)
     */
    {
      unsigned int bav_addr = kern_sym_u("badaddr_val");
      volatile unsigned int *p = bav_addr ? (volatile unsigned int *)bav_addr : 0;
      if (!p) {
        stub_puts("[IP54] WARN: badaddr_val symbol not found\n");
      } else if (p[0] == 0x27bdfff0U && p[7] == 0x40806000U) {
        p[0] = 0x04810007U;  /* bgez  $a0, +7 */
        p[1] = 0x00000000U;  /* nop */
        p[2] = 0x27bdfff0U;  /* addiu $sp, $sp, -0x10 */
        p[3] = 0xffbf0008U;  /* sd    $ra, 8($sp) */
        p[4] = 0x400c6000U;  /* mfc0  $t4, $12 */
        p[5] = 0x00000000U;  /* nop (hazard) */
        p[6] = 0x10000005U;  /* b     +5 */
        p[7] = 0x40806000U;  /* mtc0  $zero, $12 (delay slot) */
        p[8] = 0x03e00008U;  /* jr    $ra */
        p[9] = 0x24020001U;  /* addiu $v0, $zero, 1 */
        stub_puts("[IP54] Patched badaddr_val: XUSEG guard\n");
      } else {
        stub_puts("[IP54] WARN: badaddr_val prologue mismatch: ");
        stub_puthex(p[0]);
        stub_puts(" ");
        stub_puthex(p[7]);
        stub_puts("\n");
      }
    }

    /*
     * Patch 16b: Same guard for wbadaddr.
     * wbadaddr has the same prologue structure.
     */
    {
      unsigned int wba_addr = kern_sym_u("wbadaddr");
      volatile unsigned int *p = wba_addr ? (volatile unsigned int *)wba_addr : 0;
      if (!p) {
        stub_puts("[IP54] WARN: wbadaddr symbol not found\n");
      } else
      if (p[0] == 0x27bdfff0U && p[7] == 0x40806000U) {
        p[0] = 0x04810007U;  /* bgez  $a0, +7 */
        p[1] = 0x00000000U;  /* nop */
        p[2] = 0x27bdfff0U;  /* addiu $sp, $sp, -0x10 */
        p[3] = 0xffbf0008U;  /* sd    $ra, 8($sp) */
        p[4] = 0x400c6000U;  /* mfc0  $t4, $12 */
        p[5] = 0x00000000U;  /* nop (hazard) */
        p[6] = 0x10000005U;  /* b     +5 */
        p[7] = 0x40806000U;  /* mtc0  $zero, $12 (delay slot) */
        p[8] = 0x03e00008U;  /* jr    $ra */
        p[9] = 0x24020001U;  /* addiu $v0, $zero, 1 */
        stub_puts("[IP54] Patched wbadaddr: XUSEG guard\n");
      } else {
        stub_puts("[IP54] WARN: wbadaddr prologue mismatch: ");
        stub_puthex(p[0]);
        stub_puts("\n");
      }
    }

    /*
     * Patch 17: Stub wd93_init — no WD93 SCSI controller on IP54.
     *
     * wd93_init probes WD93 registers via badaddr, then writes to
     * hardware registers.  On IP54 these addresses are unmapped,
     * causing TLB miss panics.
     *
     * Two entry points:
     *   0x8800aba8 — fall-through entry (inline call path)
     *   0x8800abec — function pointer entry (driver init table)
     */
    {
      unsigned int w93i_addr = kern_sym_u("wd93_init");
      if (!w93i_addr) {
        stub_puts("[IP54] WARN: wd93_init symbol not found\n");
      } else {
        volatile unsigned int *p;
        /* Stub the function at its entry point */
        p = (volatile unsigned int *)w93i_addr;
        stub_puts("[IP54] wd93_init @"); stub_puthex(w93i_addr);
        stub_puts(" = "); stub_puthex(*p); stub_puts("\n");
        p[0] = 0x03e00008U;    /* jr $ra */
        p[1] = 0x00001025U;    /* move $v0, $zero */
        stub_puts("[IP54] Patched wd93_init: stubbed\n");
      }
    }

    /*
     * Patch 18: Stub wd93_earlyinit (0x88054ba0, 496 bytes) and
     * wd93edtinit (0x88054d90, 724 bytes).
     *
     * These are called during device init and try to set up WD93 SCSI
     * controller structures.  Without hardware, they read garbage and
     * pass it to kern_malloc, causing "invalid size" warnings and
     * eventually a null function pointer call.
     */
    {
      volatile unsigned int *p;
      unsigned int addr;
      /* wd93_earlyinit */
      addr = kern_sym_u("wd93_earlyinit");
      if (addr) {
        p = (volatile unsigned int *)addr;
        stub_puts("[IP54] wd93_earlyinit @"); stub_puthex(addr);
        stub_puts(" = "); stub_puthex(*p); stub_puts("\n");
        p[0] = 0x03e00008U;    /* jr $ra */
        p[1] = 0x00001025U;    /* move $v0, $zero */
        stub_puts("[IP54] Patched wd93_earlyinit: stubbed\n");
      } else stub_puts("[IP54] WARN: wd93_earlyinit not found\n");

      /* wd93edtinit */
      addr = kern_sym_u("wd93edtinit");
      if (addr) {
        p = (volatile unsigned int *)addr;
        stub_puts("[IP54] wd93edtinit @"); stub_puthex(addr);
        stub_puts(" = "); stub_puthex(*p); stub_puts("\n");
        p[0] = 0x03e00008U;    /* jr $ra */
        p[1] = 0x00001025U;    /* move $v0, $zero */
        stub_puts("[IP54] Patched wd93edtinit: stubbed\n");
      } else stub_puts("[IP54] WARN: wd93edtinit not found\n");

      /* wd93alloc */
      addr = kern_sym_u("wd93alloc");
      if (addr) {
        p = (volatile unsigned int *)addr;
        p[0] = 0x03e00008U;    /* jr $ra */
        p[1] = 0x00001025U;    /* move $v0, $zero */
        stub_puts("[IP54] Patched wd93alloc: stubbed\n");
      } else stub_puts("[IP54] WARN: wd93alloc not found\n");
    }

    /*
     * Patch 19: Fix c0vec_tbl NULL entries.
     *
     * The `intr` function dispatches through c0vec_tbl[pri].isr (a static
     * array in IP30.c).  Several entries reference functions that don't
     * exist in the IP54 kernel (counter_intr, heart_intr_err, etc.)
     * and are NULL.  When any interrupt fires at those priority levels,
     * intr calls through NULL → crash at PC=0.
     *
     * c0vec_tbl has 11 entries of 16 bytes each: {func, msk, ipmsk, swibit}.
     * We find it by scanning the .data section for entry [1] which points
     * to timein (known address), preceded by entry [0] = all zeros.
     *
     * We use ip54_dummy_func (jr $ra at 0x88003010) as the stub.
     */
    unsigned int c0vec_tbl_addr = 0;  /* filled by scan, used by Patch 21 */
    {
      volatile unsigned int *scan;
      int found = 0;
      unsigned int dummy_func = kern_sym("ip54_dummy_func");
      if (!dummy_func) dummy_func = 0x88003010U;  /* fallback */

      /* Scan .data section for c0vec_tbl: 16 zero bytes (entry[0]) followed
       * by a valid KSEG0 function pointer (entry[1].isr).
       * Use wide scan range to handle kernel layout changes.
       */
      for (scan = (volatile unsigned int *)0xa8260000U;
           scan < (volatile unsigned int *)0xa82a0000U && !found;
           scan++) {
        /* Look for c0vec_tbl[0] = {0,0,0,0} followed by c0vec_tbl[1].func */
        if (scan[0] == 0 && scan[1] == 0 && scan[2] == 0 && scan[3] == 0) {
          unsigned int func1 = scan[4]; /* c0vec_tbl[1].isr */
          if (func1 >= 0x88002000U && func1 < 0x882f0000U) {
            /* Looks like a valid function pointer */
            unsigned int func2 = scan[8]; /* c0vec_tbl[2].isr */
            if (func2 >= 0x88002000U && func2 < 0x882f0000U) {
              /* This is likely c0vec_tbl.  Check entries 9 and 10 for NULL */
              unsigned int func9  = scan[9 * 4]; /* c0vec_tbl[9].isr */
              unsigned int func10 = scan[10 * 4]; /* c0vec_tbl[10].isr */
              unsigned int va = 0x88260000U + ((unsigned int)((char*)scan - (char*)0xa8260000U));
              c0vec_tbl_addr = va;
              stub_puts("[IP54] Found c0vec_tbl at 0x");
              stub_puthex(va);
              stub_puts(" func[1]=0x");
              stub_puthex(func1);
              stub_puts("\n");

              /* Patch all NULL func entries to ip54_dummy_func */
              {
                int e;
                unsigned int dummy = dummy_func;
                for (e = 0; e < 11; e++) {
                  if (scan[e * 4] == 0 && e > 0) {
                    scan[e * 4] = dummy;
                    /* Also set msk and ipmsk to something safe */
                    if (scan[e * 4 + 1] == 0) scan[e * 4 + 1] = 0x0000e001U;
                    if (scan[e * 4 + 2] == 0) scan[e * 4 + 2] = 0x00000001U;
                    stub_puts("[IP54] Patched c0vec_tbl[");
                    stub_puthex(e);
                    stub_puts("] = dummy\n");
                  }
                }
              }
              found = 1;
            }
          }
        }
      }
      if (!found) {
        stub_puts("[IP54] WARN: c0vec_tbl not found\n");
      }
    }

    /*
     * Patch 20: Set CP0 Compare to max value to prevent early timer
     * interrupts from firing before the kernel installs its handler.
     */
    {
      __asm__ volatile(
        ".set push\n\t"
        ".set noreorder\n\t"
        "li $8, 0x7fffffff\n\t"
        "mtc0 $8, $11\n\t"  /* Compare = MAX */
        "nop\n\t"
        ".set pop\n\t"
        ::: "$8"
      );
      stub_puts("[IP54] Set CP0 Compare to 0x7FFFFFFF\n");
    }

    /*
     * Patch 22: Stub perr_init (parity error init).
     *
     * perr_init at 0x88017048 reads a pointer from physical 0xe50 (MC/PDA)
     * which is NULL on IP54, causing a TLBMISS at offset 0x5d0.
     * Parity error handling is irrelevant for an emulator.
     */
    {
      unsigned int perr_addr = kern_sym_u("perr_init");
      if (perr_addr) {
        volatile unsigned int *p = (volatile unsigned int *)perr_addr;
        p[0] = 0x03e00008U;  /* jr $ra */
        p[1] = 0x00000000U;  /* nop    */
        stub_puts("[IP54] Patched perr_init: stubbed\n");
      } else stub_puts("[IP54] WARN: perr_init not found\n");
    }

    /*
     * Patch 21: Fix intr() dispatch table pointer and enable timer ticks.
     *
     * The kernel's intr() at 0x88007edc dispatches interrupts through
     * c0vec_tbl + ffintrtbl.  Due to a linker symbol mismatch, intr()
     * references the wrong base address (via lui+addiu at 0x88007f5c),
     * but the actual INITIALIZED c0vec_tbl + ffintrtbl data was found
     * by Patch 19's scan (stored in c0vec_tbl_addr).
     *
     * Fix: Patch the two instructions that load $s4 in intr() to point
     * to the actual c0vec_tbl found by scanning.
     *
     * Also set is_ioc1_flag = 1 so intr() processes cause_ip5_count /
     * cause_ip6_count, enabling clock() calls from timer interrupts.
     */
    {
      /* Patch intr() $s4 load to use c0vec_tbl_addr from Patch 19 scan.
       * Scan from intr() for the 'lui $s4, xxxx' instruction.
       */
      unsigned int intr_addr = kern_sym_u("intr");
      if (c0vec_tbl_addr && intr_addr) {
        volatile unsigned int *p;
        int i, patched = 0;
        /* Scan first 256 instructions of intr() for lui $s4 */
        for (i = 0; i < 256 && !patched; i++) {
          p = (volatile unsigned int *)(intr_addr + i * 4);
          if ((*p >> 16) == 0x3c14U) {  /* lui $s4, xxxx */
            unsigned int hi = (c0vec_tbl_addr >> 16) & 0xFFFF;
            unsigned int lo = c0vec_tbl_addr & 0xFFFF;
            if (lo >= 0x8000) hi += 1;  /* sign-extension compensation */
            *p = 0x3c140000U | hi;      /* lui $s4, hi */
            /* Find the next addiu $s4, $s4 within 4 instructions */
            {
              int j;
              for (j = 1; j <= 4; j++) {
                if ((p[j] >> 16) == 0x2694U) {  /* addiu $s4, $s4, xxxx */
                  p[j] = 0x26940000U | lo;
                  patched = 1;
                  break;
                }
              }
            }
            if (patched) {
              stub_puts("[IP54] Patched intr() c0vec_tbl base -> 0x");
              stub_puthex(c0vec_tbl_addr);
              stub_puts("\n");
            }
          }
        }
        if (!patched) {
          stub_puts("[IP54] WARNING: intr() lui $s4 not found in scan\n");
        }
      } else if (!c0vec_tbl_addr) {
        stub_puts("[IP54] WARNING: c0vec_tbl not found, cannot patch intr()\n");
      } else {
        stub_puts("[IP54] WARNING: intr symbol not found\n");
      }

      /* Verify c0vec_tbl has correct entries */
      if (c0vec_tbl_addr) {
        volatile unsigned int *tbl = (volatile unsigned int *)(0x20000000U | c0vec_tbl_addr);
        stub_puts("[IP54] c0vec_tbl[5] (clock) = ");
        stub_puthex(tbl[5 * 4]);
        stub_puts("  [8] (r4kcount) = ");
        stub_puthex(tbl[8 * 4]);
        stub_puts("\n");
      }

      /* Set is_ioc1_flag = 1 so intr() processes cause_ip5/ip6 counts.
       * Without this, clock() is never called from timer interrupts.
       */
      {
        unsigned int ioc1_addr = kern_sym_u("is_ioc1_flag");
        if (ioc1_addr) {
          *(volatile unsigned int *)ioc1_addr = 1;
          stub_puts("[IP54] Set is_ioc1_flag = 1\n");
        } else stub_puts("[IP54] WARN: is_ioc1_flag not found\n");
      }
    }

    /*
     * Note: du_init() uses makedevice(260, 0/1). The lboot-generated
     * MAJOR table has MAJOR[260]=28 (internal cdevsw index 28 = pvuart_cn).
     * This is correct — external major 260 was assigned via SOFT=260 in
     * master.d/pvuart_cn. No patch needed; the original code is right.
     */

    /*
     * Diagnostic patches: DISABLED — exception trace confirmed system is
     * working normally (no RI/SIGILL). Trampolines produce too much serial
     * noise, obscuring real IRIX output. Also, psig's a0 is backup_pc
     * (not signal number), so P04/P10 output was misleading.
     */
#if 0
    /*
     * Diagnostic patches: PVUART boot-phase traces.
     * Trampolines in dead body of wd93_earlyinit (0x88054ba8+).
     *
     *   'N' (0x4e) = newproc() called  -> process 1 being created
     *   '0' (0x30) = p0exit() called   -> swapper entering idle
     *   'E' (0x45) = exece() called    -> icode launching /etc/init
     *   'W' (0x57) = du_wput() called  -> STREAMS write reaches pvuart driver
     *   'C' (0x43) = cnwrite() called  -> cn console write put called
     */

    /* Trampoline A @ 0x88054ba8: exece() → 'E'
     * exece() @ 0x88252918
     * J in: 0x0a0152ea → 0x88054ba8, J back: 0x0a094a48 → exece+08 */
    {
      volatile unsigned int *tramp = (volatile unsigned int *)0xa8054ba8U;
      volatile unsigned int *fn_p  = (volatile unsigned int *)0xa8252918U;

      if (fn_p[0] == 0x27bdffe0U && fn_p[1] == 0xffbe0008U) {
        tramp[0] = 0x3c01bf62U;  /* lui   $at, 0xbf62              */
        tramp[1] = 0x24080045U;  /* addiu $t0, $0, 0x45  ('E')     */
        tramp[2] = 0xa028017bU;  /* sb    $t0, 0x17b($at) (PVUART) */
        tramp[3] = 0x27bdffe0U;  /* addiu $sp, $sp, -32 (exece+00) */
        tramp[4] = 0xffbe0008U;  /* sd    $fp, 8($sp)   (exece+04) */
        tramp[5] = 0xffbf0000U;  /* sd    $ra, 0($sp)   (exece+08) */
        tramp[6] = 0x0a094a48U;  /* j 0x88252920        (exece+0c) */
        tramp[7] = 0x00000000U;  /* nop                            */
        fn_p[0]  = 0x0a0152eaU;  /* j 0x88054ba8 (trampoline A)    */
        fn_p[1]  = 0x00000000U;  /* nop                            */
        stub_puts("[IP54] Patched exece(): 'E' trace at 0x88054ba8\n");
      } else {
        stub_puts("[IP54] WARNING: exece() mismatch, exece+00=");
        stub_puthex(fn_p[0]); stub_puts(" +04="); stub_puthex(fn_p[1]); stub_puts("\n");
      }
    }

    /* Trampoline B @ 0x88054bc8: newproc() → 'N'
     * newproc() @ 0x8823a414
     * J in: 0x0a0152f2 → 0x88054bc8, J back: 0x0a08e907 → newproc+08 */
    {
      volatile unsigned int *tramp = (volatile unsigned int *)0xa8054bc8U;
      volatile unsigned int *fn_p  = (volatile unsigned int *)0xa823a414U;

      if (fn_p[0] == 0x00002025U && fn_p[1] == 0x00003025U) {
        tramp[0] = 0x3c01bf62U;  /* lui   $at, 0xbf62              */
        tramp[1] = 0x2408004eU;  /* addiu $t0, $0, 0x4e  ('N')     */
        tramp[2] = 0xa028017bU;  /* sb    $t0, 0x17b($at) (PVUART) */
        tramp[3] = 0x00002025U;  /* move $a0,$zero    (newproc+00) */
        tramp[4] = 0x00003025U;  /* move $a2,$zero    (newproc+04) */
        tramp[5] = 0x0a08e907U;  /* j 0x8823a41c      (newproc+08) */
        tramp[6] = 0x00000000U;  /* nop                            */
        fn_p[0]  = 0x0a0152f2U;  /* j 0x88054bc8 (trampoline B)    */
        fn_p[1]  = 0x00000000U;  /* nop                            */
        stub_puts("[IP54] Patched newproc(): 'N' trace at 0x88054bc8\n");
      } else {
        stub_puts("[IP54] WARNING: newproc() mismatch, newproc+00=");
        stub_puthex(fn_p[0]); stub_puts(" +04="); stub_puthex(fn_p[1]); stub_puts("\n");
      }
    }

    /* Trampoline C @ 0x88054be4: p0exit() → '0'
     * p0exit() @ 0x8823ef40
     * J in: 0x0a0152f9 → 0x88054be4, J back: 0x0a08fbd2 → p0exit+08 */
    {
      volatile unsigned int *tramp = (volatile unsigned int *)0xa8054be4U;
      volatile unsigned int *fn_p  = (volatile unsigned int *)0xa823ef40U;

      if (fn_p[0] == 0x27bdffe0U && fn_p[1] == 0xffb00000U) {
        tramp[0] = 0x3c01bf62U;  /* lui   $at, 0xbf62              */
        tramp[1] = 0x24080030U;  /* addiu $t0, $0, 0x30  ('0')     */
        tramp[2] = 0xa028017bU;  /* sb    $t0, 0x17b($at) (PVUART) */
        tramp[3] = 0x27bdffe0U;  /* addiu $sp,$sp,-32   (p0exit+00)*/
        tramp[4] = 0xffb00000U;  /* sd    $s0,0($sp)    (p0exit+04)*/
        tramp[5] = 0x0a08fbd2U;  /* j 0x8823ef48        (p0exit+08)*/
        tramp[6] = 0x00000000U;  /* nop                            */
        fn_p[0]  = 0x0a0152f9U;  /* j 0x88054be4 (trampoline C)    */
        fn_p[1]  = 0x00000000U;  /* nop                            */
        stub_puts("[IP54] Patched p0exit(): '0' trace at 0x88054be4\n");
      } else {
        stub_puts("[IP54] WARNING: p0exit() mismatch, p0exit+00=");
        stub_puthex(fn_p[0]); stub_puts(" +04="); stub_puthex(fn_p[1]); stub_puts("\n");
      }
    }

    /* Trampoline D @ 0x88054c00: du_wput() -> 'W'
     * du_wput() @ 0x880658bc  (pvuart_cn STREAMS write put procedure)
     * First 2 insns: 0x27bdffc0 (addiu sp,sp,-64), 0xffb00010 (sd s0,16(sp))
     * J in:  0x0a015300 -> 0x88054c00
     * J back: 0x0a019631 -> du_wput+8 (0x880658c4) */
    {
      volatile unsigned int *tramp = (volatile unsigned int *)0xa8054c00U;
      volatile unsigned int *fn_p  = (volatile unsigned int *)0xa80658bcU;

      if (fn_p[0] == 0x27bdffc0U && fn_p[1] == 0xffb00010U) {
        tramp[0] = 0x3c01bf62U;  /* lui   $at, 0xbf62               */
        tramp[1] = 0x24080057U;  /* addiu $t0, $0, 0x57  ('W')      */
        tramp[2] = 0xa028017bU;  /* sb    $t0, 0x17b($at) (PVUART)  */
        tramp[3] = 0x27bdffc0U;  /* addiu $sp,$sp,-64  (du_wput+00) */
        tramp[4] = 0xffb00010U;  /* sd    $s0,16($sp)  (du_wput+04) */
        tramp[5] = 0x0a019631U;  /* j 0x880658c4       (du_wput+08) */
        tramp[6] = 0x00000000U;  /* nop                             */
        fn_p[0]  = 0x0a015300U;  /* j 0x88054c00 (trampoline D)     */
        fn_p[1]  = 0x00000000U;  /* nop                             */
        stub_puts("[IP54] Patched du_wput(): 'W' trace at 0x88054c00\n");
      } else {
        stub_puts("[IP54] WARNING: du_wput() mismatch, +00=");
        stub_puthex(fn_p[0]); stub_puts(" +04="); stub_puthex(fn_p[1]); stub_puts("\n");
      }
    }

    /* Trampoline E @ 0x88054c1c: cnwrite() body -> 'C'
     * cnwrite body @ 0x880645f4  (cn console STREAMS wput, ELF symbol correct)
     * First 2 insns: 0x27bdffa0 (addiu sp,sp,-96), 0xffb20028 (sd s2,40(sp))
     * J in:  0x0a015307 -> 0x88054c1c
     * J back: 0x0a01917f -> cnwrite+8 (0x880645fc) */
    {
      volatile unsigned int *tramp = (volatile unsigned int *)0xa8054c1cU;
      volatile unsigned int *fn_p  = (volatile unsigned int *)0xa80645f4U;

      if (fn_p[0] == 0x27bdffa0U && fn_p[1] == 0xffb20028U) {
        tramp[0] = 0x3c01bf62U;  /* lui   $at, 0xbf62               */
        tramp[1] = 0x24080043U;  /* addiu $t0, $0, 0x43  ('C')      */
        tramp[2] = 0xa028017bU;  /* sb    $t0, 0x17b($at) (PVUART)  */
        tramp[3] = 0x27bdffa0U;  /* addiu $sp,$sp,-96  (cnwrite+00) */
        tramp[4] = 0xffb20028U;  /* sd    $s2,40($sp)  (cnwrite+04) */
        tramp[5] = 0x0a01917fU;  /* j 0x880645fc       (cnwrite+08) */
        tramp[6] = 0x00000000U;  /* nop                             */
        fn_p[0]  = 0x0a015307U;  /* j 0x88054c1c (trampoline E)     */
        fn_p[1]  = 0x00000000U;  /* nop                             */
        stub_puts("[IP54] Patched cnwrite(): 'C' trace at 0x88054c1c\n");
      } else {
        stub_puts("[IP54] WARNING: cnwrite() mismatch, +00=");
        stub_puthex(fn_p[0]); stub_puts(" +04="); stub_puthex(fn_p[1]); stub_puts("\n");
      }
    }

    /* Trampoline F @ 0x88054c38: cnopen() -> 'O'
     * cnopen body @ 0x8806455c  (disk kernel)
     * First 2 insns: 0x27bdffe0 (addiu sp,-32), 0xffbf0000 (sd ra,0(sp))
     * J in:  0x0a01530e -> 0x88054c38
     * J back: 0x0a019559 -> cnopen+8 (0x88064564) */
    {
      volatile unsigned int *tramp = (volatile unsigned int *)0xa8054c38U;
      volatile unsigned int *fn_p  = (volatile unsigned int *)0xa806455cU;

      if (fn_p[0] == 0x27bdffe0U && fn_p[1] == 0xffbf0000U) {
        tramp[0] = 0x3c01bf62U;  /* lui   $at, 0xbf62              */
        tramp[1] = 0x2408004fU;  /* addiu $t0, $0, 0x4f  ('O')    */
        tramp[2] = 0xa028017bU;  /* sb    $t0, 0x17b($at) PVUART  */
        tramp[3] = 0x27bdffe0U;  /* addiu sp,sp,-32  (cnopen+00)  */
        tramp[4] = 0xffbf0000U;  /* sd    ra,0(sp)   (cnopen+04)  */
        tramp[5] = 0x0a019159U;  /* j 0x88064564     (cnopen+08)  */
        tramp[6] = 0x00000000U;  /* nop                            */
        fn_p[0]  = 0x0a01530eU;  /* j 0x88054c38 (trampoline F)   */
        fn_p[1]  = 0x00000000U;  /* nop                            */
        stub_puts("[IP54] Patched cnopen(): 'O' trace at 0x88054c38\n");
      } else {
        stub_puts("[IP54] WARNING: cnopen() mismatch, +00=");
        stub_puthex(fn_p[0]); stub_puts(" +04="); stub_puthex(fn_p[1]); stub_puts("\n");
      }
    }

    /* Patch du_open sflag check: remove ENXIO guard.
     * du_open @ 0x880657bc in disk kernel.
     * +4 (0x880657c0): beqz a3,.cont (0x10e00004) -> b .cont (0x10000004) */
    {
      volatile unsigned int *du_sflag = (volatile unsigned int *)0xa80657c0U;
      if (*du_sflag == 0x10e00004U) {
        *du_sflag = 0x10000004U;
        stub_puts("[IP54] Patched du_open: sflag check removed\n");
      } else {
        stub_puts("[IP54] WARNING: du_open sflag mismatch: ");
        stub_puthex(*du_sflag); stub_puts("\n");
      }
    }

    /* Trampoline G @ 0x88054c54: cn_write() -> 'g' (0x67)
     * cn_write body @ 0x88064aa4  (disk kernel, non-STREAMS write path)
     * First 2 insns: 0x27bdffb0 (addiu sp,-80), 0xffb10008 (sd s1,8(sp))
     * J in:  0x0a015315 -> 0x88054c54
     * J back: 0x0a0192ab -> cn_write+8 (0x88064aac) */
    {
      volatile unsigned int *tramp = (volatile unsigned int *)0xa8054c54U;
      volatile unsigned int *fn_p  = (volatile unsigned int *)0xa8064aa4U;

      if (fn_p[0] == 0x27bdffb0U && fn_p[1] == 0xffb10008U) {
        tramp[0] = 0x3c01bf62U;  /* lui   $at, 0xbf62             */
        tramp[1] = 0x24080067U;  /* addiu $t0, $0, 0x67  ('g')   */
        tramp[2] = 0xa028017bU;  /* sb    $t0, 0x17b($at) PVUART */
        tramp[3] = 0x27bdffb0U;  /* addiu sp,sp,-80  (cn_write+00) */
        tramp[4] = 0xffb10008U;  /* sd    s1,8(sp)   (cn_write+04) */
        tramp[5] = 0x0a0192abU;  /* j 0x88064aac    (cn_write+08) */
        tramp[6] = 0x00000000U;  /* nop                           */
        fn_p[0]  = 0x0a015315U;  /* j 0x88054c54 (trampoline G)  */
        fn_p[1]  = 0x00000000U;  /* nop                          */
        stub_puts("[IP54] Patched cn_write(): 'g' trace at 0x88054c54\n");
      } else {
        stub_puts("[IP54] WARNING: cn_write() mismatch, +00=");
        stub_puthex(fn_p[0]); stub_puts(" +04="); stub_puthex(fn_p[1]); stub_puts("\n");
      }
    }

/* Trampoline H @ 0x88054c70: strwrite() -> 'S' (0x53)
     * strwrite body @ 0x880db008  (disk kernel, STREAMS stream head write)
     * First 2 insns: 0x27bdff50 (addiu sp,-176), 0xffb20078 (sd s2,120(sp))
     * J in:  0x0a01531c -> 0x88054c70
     * J back: 0x0a036c04 -> strwrite+8 (0x880db010) */
    {
      volatile unsigned int *tramp = (volatile unsigned int *)0xa8054c70U;
      volatile unsigned int *fn_p  = (volatile unsigned int *)0xa80db008U;
      if (fn_p[0] == 0x27bdff50U && fn_p[1] == 0xffb20078U) {
        tramp[0] = 0x3c01bf62U;  /* lui   $at, 0xbf62             */
        tramp[1] = 0x24080053U;  /* addiu $t0, $0, 0x53  ('S')   */
        tramp[2] = 0xa028017bU;  /* sb    $t0, 0x17b($at) PVUART  */
        tramp[3] = 0x27bdff50U;  /* addiu sp,-176  (strwrite+00)  */
        tramp[4] = 0xffb20078U;  /* sd    s2,120(sp) (strwrite+04)*/
        tramp[5] = 0x0a036c04U;  /* j 0x880db010  (strwrite+08)  */
        tramp[6] = 0x00000000U;  /* nop                           */
        fn_p[0]  = 0x0a01531cU;  /* j 0x88054c70 (trampoline H)  */
        fn_p[1]  = 0x00000000U;  /* nop                           */
        stub_puts("[IP54] Patched strwrite(): 'S' trace at 0x88054c70\n");
      } else {
        stub_puts("[IP54] WARNING: strwrite() mismatch, +00=");
        stub_puthex(fn_p[0]); stub_puts(" +04="); stub_puthex(fn_p[1]); stub_puts("\n");
      }
    }

/* Trampoline @ 0x88054d98: psig() -> 'P' + 2-digit hex signal number
     * psig body @ 0x881d76b8  (disk kernel, signal delivery)
     * First 2 insns: 0x00a01025 (move v0,a1), 0x27bdff70 (addiu sp,-144)
     * Uses free wd93edtinit body space (trap diagnostic removed).
     * Prints "Pxx" where xx = hex(a0 & 0x1f), e.g. P04=SIGILL, P0b=SIGSEGV
     * J in:  0x0a015366 -> 0x88054d98
     * J back: 0x0a075db0 -> psig+8 (0x881d76c0) */
    {
      volatile unsigned int *tramp = (volatile unsigned int *)0xa8054d98U;
      volatile unsigned int *fn_p  = (volatile unsigned int *)0xa81d76b8U;
      if (fn_p[0] == 0x00a01025U && fn_p[1] == 0x27bdff70U) {
        tramp[0]  = 0x3c01bf62U;  /* lui   at, 0xbf62              */
        tramp[1]  = 0x24080050U;  /* addiu t0, zero, 0x50  ('P')   */
        tramp[2]  = 0xa028017bU;  /* sb    t0, 0x17b(at)  print P  */
        tramp[3]  = 0x3088001fU;  /* andi  t0, a0, 0x1f   sig#     */
        tramp[4]  = 0x00084902U;  /* srl   t1, t0, 4      hi nib   */
        tramp[5]  = 0x25290030U;  /* addiu t1, t1, 0x30   '0'+hi   */
        tramp[6]  = 0xa029017bU;  /* sb    t1, 0x17b(at)  print hi */
        tramp[7]  = 0x3109000fU;  /* andi  t1, t0, 0xf    lo nib   */
        tramp[8]  = 0x2d2a000aU;  /* sltiu t2, t1, 10              */
        tramp[9]  = 0x15400002U;  /* bnez  t2, +2 (→[12])          */
        tramp[10] = 0x252a0030U;  /* addiu t2, t1, 0x30   [delay]  */
        tramp[11] = 0x252a0057U;  /* addiu t2, t1, 0x57   hex a-f  */
        tramp[12] = 0xa02a017bU;  /* sb    t2, 0x17b(at)  print lo */
        tramp[13] = 0x00a01025U;  /* move  v0, a1   (psig+00)      */
        tramp[14] = 0x27bdff70U;  /* addiu sp, -144 (psig+04)      */
        tramp[15] = 0x0a075db0U;  /* j     0x881d76c0 (psig+08)    */
        tramp[16] = 0x00000000U;  /* nop                           */
        fn_p[0]   = 0x0a015366U;  /* j     0x88054d98 (trampoline) */
        fn_p[1]   = 0x00000000U;  /* nop                           */
        stub_puts("[IP54] Patched psig(): 'Pxx' trace at 0x88054d98\n");
      } else {
        stub_puts("[IP54] WARNING: psig() mismatch, +00=");
        stub_puthex(fn_p[0]); stub_puts(" +04="); stub_puthex(fn_p[1]); stub_puts("\n");
      }
    }

/* Trampoline J @ 0x88054ca8: exit() -> 'X' (0x58)
     * exit body @ 0x8820e500  (disk kernel, process exit)
     * First 2 insns: 0x27bdffa0 (addiu sp,-96), 0xffb30028 (sd s3,40(sp))
     * J in:  0x0a01532a -> 0x88054ca8
     * J back: 0x0a083942 -> exit+8 (0x8820e508) */
    {
      volatile unsigned int *tramp = (volatile unsigned int *)0xa8054ca8U;
      volatile unsigned int *fn_p  = (volatile unsigned int *)0xa820e500U;
      if (fn_p[0] == 0x27bdffa0U && fn_p[1] == 0xffb30028U) {
        tramp[0] = 0x3c01bf62U;  /* lui   $at, 0xbf62             */
        tramp[1] = 0x24080058U;  /* addiu $t0, $0, 0x58  ('X')   */
        tramp[2] = 0xa028017bU;  /* sb    $t0, 0x17b($at) PVUART  */
        tramp[3] = 0x27bdffa0U;  /* addiu sp,-96  (exit+00)       */
        tramp[4] = 0xffb30028U;  /* sd    s3,40(sp) (exit+04)     */
        tramp[5] = 0x0a083942U;  /* j 0x8820e508  (exit+08)       */
        tramp[6] = 0x00000000U;  /* nop                           */
        fn_p[0]  = 0x0a01532aU;  /* j 0x88054ca8 (trampoline J)  */
        fn_p[1]  = 0x00000000U;  /* nop                           */
        stub_puts("[IP54] Patched exit(): 'X' trace at 0x88054ca8\n");
      } else {
        stub_puts("[IP54] WARNING: exit() mismatch, +00=");
        stub_puthex(fn_p[0]); stub_puts(" +04="); stub_puthex(fn_p[1]); stub_puts("\n");
      }
    }
#endif /* disabled diagnostic trampolines */

/* Guard: xfs_da_node_lookup_int null btree pointer crash.
     *
     * Pattern: b offset (0x1000xxxx) followed by lw s5, 12(s5) (0x8eb5000c).
     * The delay slot faults when s5 is a bogus pointer (null btree base).
     *
     * Guard checks s5 < 0x10000 (bogus), returns error if so.
     * Uses free space in wd93_earlyinit body for trampoline.
     *
     * error_return = crash_addr - 0x264 (function epilogue, fixed internal offset)
     * loop_back computed from branch instruction offset.
     */
    {
      unsigned int xfs_func = kern_sym_u("xfs_da_node_lookup_int");
      unsigned int earlyinit = kern_sym_u("wd93_earlyinit");
      if (xfs_func && earlyinit) {
        /* Scan for the crash pattern within the function (up to 2048 insns) */
        volatile unsigned int *scan;
        int i, found = 0;
        for (i = 0; i < 2048 && !found; i++) {
          scan = (volatile unsigned int *)(xfs_func + i * 4);
          if ((scan[0] & 0xFFFF0000U) == 0x10000000U && scan[1] == 0x8eb5000cU) {
            /* Found: b offset + lw s5, 12(s5) */
            unsigned int crash_kseg0 = (xfs_func ^ 0x20000000U) + i * 4;
            unsigned int guard_kseg0 = (earlyinit ^ 0x20000000U) + 0x124;
            volatile unsigned int *guard = (volatile unsigned int *)(earlyinit + 0x124);
            /* Compute loop_back from branch offset */
            int boff = (int)(short)(scan[0] & 0xFFFF);
            unsigned int loop_back = crash_kseg0 + 4 + boff * 4;
            /* error_return = crash - 0x264 (fixed internal offset) */
            unsigned int error_ret = crash_kseg0 - 0x264;

            guard[0] = 0x3c010001U;  /* lui  at, 0x0001               */
            guard[1] = 0x02a1082bU;  /* sltu at, s5, at               */
            guard[2] = 0x10200004U;  /* beqz at, do_load (+4)         */
            guard[3] = 0x00000000U;  /* nop                           */
            guard[4] = 0x24040001U;  /* li a0, 1                      */
            guard[5] = mips_j(error_ret);
            guard[6] = 0x00000000U;  /* nop                           */
            guard[7] = 0x8eb5000cU;  /* lw s5, 12(s5) (original)     */
            guard[8] = mips_j(loop_back);
            guard[9] = 0x00000000U;  /* nop                           */
            scan[0]  = mips_j(guard_kseg0);
            scan[1]  = 0x00000000U;  /* nop                           */
            stub_puts("[IP54] Patched xfs_da_node_lookup_int: null btree guard\n");
            found = 1;
          }
        }
        if (!found) {
          stub_puts("[IP54] WARNING: xfs btree crash pattern not found in scan\n");
        }
      } else {
        if (!xfs_func) stub_puts("[IP54] WARN: xfs_da_node_lookup_int not found\n");
        if (!earlyinit) stub_puts("[IP54] WARN: wd93_earlyinit not found (xfs guard)\n");
      }
    }

    /* Stub: Ng1PixelDma — the real ng1 driver's bulk-image DMA, linked
     * into the kernel via IP54.sm "USE: ng1".  Xsgi's rex3DrawImage
     * sends large PutImages through it; on IP54 there is no Indy GIO
     * DMA hardware, so the transfer silently goes nowhere (granite
     * bands, missing weave/fm icons) and its descriptor bookkeeping is
     * the prime suspect for the zone_shake heap corruption.  Make it
     * fail fast with EINVAL: the DDX logs "NG1_PIXELDMA failed" and
     * (hopefully) falls back to PIO.
     */
    {
      unsigned int pd = kern_sym_u("Ng1PixelDma");
      if (pd) {
        volatile unsigned int *p = (volatile unsigned int *)pd;
        p[0] = 0x03e00008U;  /* jr   ra            */
        p[1] = 0x24020016U;  /* li   v0, 22 (EINVAL) — delay slot */
        stub_puts("[IP54] Patched Ng1PixelDma: return EINVAL (no DMA hw)\n");
      } else {
        stub_puts("[IP54] WARN: Ng1PixelDma not found\n");
      }
    }

/* Guard: swtch() crash — lw a2, 0x368(s0) where s0 is an
     * invalid "nkt" (next kthread pointer).
     *
     * Pattern: scan swtch() for lw a2, 0x368(s0) (0x8E060368).
     * The guard checks s0 < 0x88000000, skips resume() if bogus.
     * skip_target = crash_addr + 0x10, continue_target = crash_addr + 4.
     */
    {
      unsigned int swtch_addr = kern_sym_u("swtch");
      unsigned int earlyinit = kern_sym_u("wd93_earlyinit");
      if (swtch_addr && earlyinit) {
        volatile unsigned int *scan;
        int i, found = 0;
        /* Scan swtch() for the crash pattern (up to 512 insns) */
        for (i = 0; i < 512 && !found; i++) {
          scan = (volatile unsigned int *)(swtch_addr + i * 4);
          if (*scan == 0x8E060368U) {  /* lw a2, 0x368(s0) */
            unsigned int crash_kseg0 = (swtch_addr ^ 0x20000000U) + i * 4;
            unsigned int guard_kseg0 = (earlyinit ^ 0x20000000U) + 0x14c;
            volatile unsigned int *guard = (volatile unsigned int *)(earlyinit + 0x14c);
            unsigned int skip_target = crash_kseg0 + 0x10;     /* after jal+ds */
            unsigned int cont_target = crash_kseg0 + 4;        /* ld a1,0(sp) */

            guard[0] = 0x3C018800U;  /* lui  at, 0x8800            */
            guard[1] = 0x0201082BU;  /* sltu at, s0, at            */
            guard[2] = 0x10200003U;  /* beqz at, do_load (+3)      */
            guard[3] = 0x00000000U;  /* nop                        */
            guard[4] = mips_j(skip_target);
            guard[5] = 0x00000000U;  /* nop                        */
            guard[6] = 0x8E060368U;  /* lw a2, 0x368(s0) (orig)   */
            guard[7] = mips_j(cont_target);
            guard[8] = 0x00000000U;  /* nop                        */
            scan[0]  = mips_j(guard_kseg0);
            /* scan[1] = ld a1,0(sp) — unchanged, runs as delay slot */
            stub_puts("[IP54] Patched swtch: null nkt guard\n");
            found = 1;
          }
        }
        if (!found) stub_puts("[IP54] WARNING: swtch crash pattern not found\n");
      } else {
        if (!swtch_addr) stub_puts("[IP54] WARN: swtch not found\n");
      }
    }

    /* Guard L: resume() entry — check a1 (kt) for bogus values.
     *
     * resume(nkt, kt) starts with mtc0 zero, cp0r12 (0x40806000).
     * If a1 < 0x88000000, force a1=0 so resume treats it as "no old thread".
     * Guard lives at wd93_earlyinit + 0x170.
     */
    {
      unsigned int resume_addr = kern_sym_u("resume");
      unsigned int earlyinit = kern_sym_u("wd93_earlyinit");
      if (resume_addr && earlyinit) {
        volatile unsigned int *entry = (volatile unsigned int *)resume_addr;
        if (entry[0] == 0x40806000U) {  /* mtc0 zero, cp0r12 */
          unsigned int resume_kseg0 = resume_addr ^ 0x20000000U;
          unsigned int guard_kseg0 = (earlyinit ^ 0x20000000U) + 0x170;
          volatile unsigned int *guard = (volatile unsigned int *)(earlyinit + 0x170);

          guard[0] = 0x3C018800U;  /* lui  at, 0x8800           */
          guard[1] = 0x00A1082BU;  /* sltu at, a1, at           */
          guard[2] = 0x10200002U;  /* beqz at, ok (+2)          */
          guard[3] = 0x00000000U;  /* nop                       */
          guard[4] = 0x00002821U;  /* move a1, zero             */
          guard[5] = 0x40806000U;  /* mtc0 zero, cp0r12 (orig) */
          guard[6] = mips_j(resume_kseg0 + 8);  /* j resume+8  */
          guard[7] = 0x00000000U;  /* nop                       */
          entry[0] = mips_j(guard_kseg0);
          /* entry[1] = addiu t1,zero,1 — runs as delay slot */
          stub_puts("[IP54] Patched resume: null kt guard\n");
        } else {
          stub_puts("[IP54] WARNING: resume entry mismatch: ");
          stub_puthex(entry[0]); stub_puts("\n");
        }
      } else {
        if (!resume_addr) stub_puts("[IP54] WARN: resume not found\n");
      }
    }

    /* Guard N v3: uthread_dup helper crash — lw v0, 0x388(s0)
     * where s0 is an invalid new_uthread pointer.
     *
     * Scan uthread_dup for lw v0, 0x388(s0) (0x8e020388).
     * Guard checks sign-extension + kernel address range of s0.
     * If invalid, abort function: return v0=0, restore callee-saved.
     * Guard lives at wd93_earlyinit + 0x190.
     */
    {
      unsigned int uthdup_addr = kern_sym_u("uthread_dup");
      unsigned int earlyinit = kern_sym_u("wd93_earlyinit");
      if (uthdup_addr && earlyinit) {
        volatile unsigned int *scan;
        int i, found = 0;
        /* Scan uthread_dup for lw v0, 0x388(s0) — up to 1024 insns */
        for (i = 0; i < 1024 && !found; i++) {
          scan = (volatile unsigned int *)(uthdup_addr + i * 4);
          if (*scan == 0x8e020388U) {
            unsigned int crash_kseg0 = (uthdup_addr ^ 0x20000000U) + i * 4;
            unsigned int guard_kseg0 = (earlyinit ^ 0x20000000U) + 0x190;
            volatile unsigned int *guard = (volatile unsigned int *)(earlyinit + 0x190);
            unsigned int resume_kseg0 = crash_kseg0 + 4;  /* next insn */

            guard[0]  = 0x00100800U;  /* sll   at, s0, 0           */
            guard[1]  = 0x14300004U;  /* bne   at, s0, bad (+4)    */
            guard[2]  = 0x3c018800U;  /* lui   at, 0x8800 [delay]  */
            guard[3]  = 0x0201082bU;  /* sltu  at, s0, at          */
            guard[4]  = 0x10200006U;  /* beqz  at, ok (+6)         */
            guard[5]  = 0x00000000U;  /* nop                       */
            guard[6]  = 0x00001025U;  /* move  v0, zero            */
            guard[7]  = 0xdfb00080U;  /* ld    s0, 0x80(sp)        */
            guard[8]  = 0xdfbf0078U;  /* ld    ra, 0x78(sp)        */
            guard[9]  = 0x03e00008U;  /* jr    ra                  */
            guard[10] = 0x27bd00a0U;  /* addiu sp, sp, 0xa0        */
            guard[11] = 0x8e020388U;  /* lw    v0, 0x388(s0)       */
            guard[12] = mips_j(resume_kseg0);
            guard[13] = 0x00000000U;  /* nop                       */
            scan[0]   = mips_j(guard_kseg0);
            stub_puts("[IP54] Patched uthread_dup: s0 guard + abort\n");
            found = 1;
          }
        }
        if (!found) stub_puts("[IP54] WARNING: uthread_dup crash pattern not found\n");
      } else {
        if (!uthdup_addr) stub_puts("[IP54] WARN: uthread_dup not found\n");
      }
    }

    /* Trap diagnostic removed — async DBE lands inside trampoline causing
     * recursive kernel-mode exception → PANIC.  The wd93edtinit space at
     * 0x88054d98 is now free for future use. */

    /*
     * Patch 35: NOP out spurious Compare writes that prevent CP0 timer
     * interrupts from being delivered.
     *
     * QEMU TCG checks for pending interrupts only at translation block
     * boundaries.  Two kernel functions write CP0 Compare *during* TB
     * execution, clearing IP7 before the TB exits and the interrupt can
     * be dispatched:
     *
     *   get_r4k_counter+0xdc (0x88004af8): mtc0 $a3, Compare
     *     — Software timer tickle: sets Compare = Count + small_delta.
     *       Races with the QEMU timer callback that sets IP7.
     *
     *   locore_eret_4+0x44 (0x880228c8): mtc0 $zero, Compare
     *     — Inline IP7 ACK during exception return.  Clears IP7 without
     *       running r4kcount_intr, so cause_ip5_count is never incremented
     *       and clock() never runs.
     *
     * With both NOPed out, the QEMU main-loop timer fires IP7, which is
     * dispatched as a proper hardware interrupt → intr() → r4kcount_intr
     * → set_r4k_compare (clears IP7, writes next Compare) → eret →
     * cause_ip5_count > 0 → clock().
     */
    {
      volatile unsigned int *p;
      int i;

      /* NOP the mtc0 $a3, Compare in get_r4k_counter.
       * Scan the function for mtc0 $a3, $11 (0x40875800). */
      {
        unsigned int grk_addr = kern_sym_u("get_r4k_counter");
        int patched = 0;
        if (grk_addr) {
          for (i = 0; i < 128 && !patched; i++) {
            p = (volatile unsigned int *)(grk_addr + i * 4);
            if (*p == 0x40875800U) {
              *p = 0x00000000U;
              stub_puts("[IP54] Patched get_r4k_counter: NOP'd Compare write\n");
              patched = 1;
            }
          }
          if (!patched) stub_puts("[IP54] WARN: get_r4k_counter mtc0 not found\n");
        } else stub_puts("[IP54] WARN: get_r4k_counter not found\n");
      }

      /* NOP the mtc0 $zero, Compare in exception return code.
       * Scan from locore_eret or exception handler area for mtc0 $zero, $11 (0x40805800).
       * This is in locore, typically near the eret handler area.
       */
      {
        unsigned int eret_addr = kern_sym_u("locore_eret_4");
        if (!eret_addr) {
          /* locore_eret_4 may not be a global symbol; scan from locore area.
           * Use elocore or exception_exit or scan wider range near 0x88020000. */
          unsigned int elocore = kern_sym_u("elocore");
          if (elocore) eret_addr = elocore;
        }
        if (eret_addr) {
          int patched = 0;
          /* Scan ±256 instructions around the symbol for mtc0 zero, Compare */
          for (i = -256; i < 256 && !patched; i++) {
            p = (volatile unsigned int *)(eret_addr + i * 4);
            if (*p == 0x40805800U) {
              *p = 0x00000000U;
              stub_puts("[IP54] Patched locore_eret: NOP'd Compare write\n");
              patched = 1;
            }
          }
          if (!patched) stub_puts("[IP54] WARN: locore eret mtc0 not found\n");
        } else {
          /* Fallback: scan exception handler area 0x88020000-0x88028000 */
          int patched = 0;
          for (i = 0; i < 8192 && !patched; i++) {
            p = (volatile unsigned int *)(0xa8020000U + i * 4);
            if (*p == 0x40805800U) {
              *p = 0x00000000U;
              stub_puts("[IP54] Patched locore (scan): NOP'd Compare write\n");
              patched = 1;
            }
          }
          if (!patched) stub_puts("[IP54] WARN: locore mtc0 $zero, Compare not found\n");
        }
      }
    }

    /*
     * Patch 36: Wire pvnet RX interrupt — HEART ISR bit 20 → IP4 → pvnet_intr.
     *
     * This kernel has no heart.c interrupt infrastructure.  IP4 is already
     * used by the INT2/INT3 handler (from the IP22 base kernel) in
     * c0vec_tbl[4].  We install a dispatch trampoline in c0vec_tbl[4] that:
     *   1. Checks PVNET_INTR_STATUS for pending interrupts
     *   2. If set: calls pvnet_intr(), then returns
     *   3. If not: tail-calls the original handler (preserves IP22 behavior)
     *
     * Also enables HEART IMR0 bit 20 (unmask ISR bit 20 → IP4).
     * No kernel code writes IMR0 (no heart.c), so the write persists.
     *
     * Trampoline code lives in wd93_earlyinit dead space (stubbed by Patch 18).
     *
     * Interrupt path:
     *   pvnet RX done → QEMU pvnet GPIO → HEART shim ISR bit 20
     *   → ISR & IMR[0] ≠ 0 (LEVEL1) → CPU IP4
     *   → intr() → c0vec_tbl[4].isr = trampoline
     *   → trampoline checks PVNET_INTR_STATUS → pvnet_intr()
     *   → W1C INTR_STATUS → QEMU GPIO low → ISR bit 20 clear → IP4 deasserted
     */
    {
      unsigned int lcl2vec_k = kern_sym("lcl2vec_tbl");
      unsigned int wd93_u = kern_sym_u("wd93_earlyinit");
      unsigned int wd93_k = kern_sym("wd93_earlyinit");

      stub_puts("[IP54] lcl2vec_tbl = ");
      stub_puthex(lcl2vec_k);
      stub_puts("\n");

      if (0 && c0vec_tbl_addr && lcl2vec_k && wd93_u && wd93_k) {
        /* Interrupt-driven keyboard/mouse (2026-06-12): 8042 IRQ → HEART ISR
         * bit 23 → IP3 → c0vec_tbl[4] → this trampoline → pckm_intr.  pckm_intr
         * is static (kern_sym resolves only globals), so we call it through the
         * function pointer the kernel installs at lcl2vec_tbl[5].isr.
         *
         * DISABLED 2026-06-12: the trampoline code is CORRECT (encoding bug
         * fixed: lw $t9 used $k0 not $t2 → Bad addr 0xa1; now 0x8D5900A0) and
         * dispatches lcl2vec_tbl[5].isr(0,0) properly.  But the handler crashes
         * when invoked from hard-interrupt context (intermittent; BadVAddr
         * 0x10037fec, backtrace via pvfbioctl/wd93intr/icmn_err) — the local
         * interrupt handler expects the kernel's local-dispatch context, not a
         * direct call from a c0vec trampoline.  Needs deeper interrupt-context
         * work.  See progress_notes/ip54/interrupt_wiring_progress.md. */
        unsigned int lhi = (lcl2vec_k >> 16) & 0xffff;
        unsigned int llo = lcl2vec_k & 0xffff;
        unsigned int c0vec4_u = (c0vec_tbl_addr | 0x20000000U) + 4 * 16;
        volatile unsigned int *e4 = (volatile unsigned int *)c0vec4_u;
        unsigned int orig_handler = e4[0];  /* save original IP4 handler */

        stub_puts("[IP54] c0vec_tbl[4] original isr=");
        stub_puthex(orig_handler);
        stub_puts("\n");

        /*
         * Build dispatch trampoline at wd93_earlyinit + 8.
         *
         * MIPS register conventions for intr() dispatch:
         *   a0 = argument passed to handler (cause bits or similar)
         *   $ra = return address back to intr()
         *
         * Trampoline:
         *   1. Read PVNET_INTR_STATUS (KSEG1 0xBF480210)
         *   2. If nonzero: save $ra, call pvnet_intr, restore $ra, return
         *   3. If zero: tail-jump to original handler
         *
         * PVNET base = PHYS_TO_K1(0x1F480200) → KSEG1 0xBF480200
         * INTR_STATUS at offset 0x10 → 0xBF480210
         */
        volatile unsigned int *code = (volatile unsigned int *)(wd93_u + 8);
        unsigned int tramp_k = wd93_k + 8;
        int n = 0;

        /* Check 8042 status (KSEG1 0xBFBD9847) & (SR_OBF|SR_MSFULL = 0x21) */
        code[n++] = 0x3C08BFBDU;           /* lui  $t0, 0xBFBD             */
        code[n++] = 0x35089847U;           /* ori  $t0, $t0, 0x9847       */
        code[n++] = 0x91090000U;           /* lbu  $t1, 0($t0)  # status  */
        code[n++] = 0x31290021U;           /* andi $t1, $t1, 0x21         */
        code[n++] = 0x1120000CU;           /* beqz $t1, +12 → .orig       */
        code[n++] = 0x00000000U;           /* nop (delay slot)            */

        /* 8042 data pending: call lcl2vec_tbl[5].isr(arg,0) == pckm_intr.
         * entry stride 32: isr @ +0xA0, arg @ +0xA4 (32-bit, sign-extended
         * on load → KSEG0). */
        code[n++] = 0x3C0A0000U | lhi;     /* lui  $t2, hi(lcl2vec_tbl)   */
        code[n++] = 0x354A0000U | llo;     /* ori  $t2, $t2, lo           */
        code[n++] = 0x8D5900A0U;           /* lw   $t9, 0xA0($t2) # isr   */
        code[n++] = 0x8D4400A4U;           /* lw   $a0, 0xA4($t2) # arg   */
        code[n++] = 0x27BDFFF0U;           /* addiu $sp, $sp, -16         */
        code[n++] = 0xFFBF0000U;           /* sd   $ra, 0($sp)            */
        code[n++] = 0x0320F809U;           /* jalr $t9                    */
        code[n++] = 0x00002825U;           /* move $a1, $zero (delay slot)*/
        code[n++] = 0xDFBF0000U;           /* ld   $ra, 0($sp)            */
        code[n++] = 0x03E00008U;           /* jr   $ra                    */
        code[n++] = 0x27BD0010U;           /* addiu $sp, $sp, 16 (delay)  */

        /* .orig: tail-call original handler (no $ra save needed) */
        code[n++] = mips_j(orig_handler);  /* j    original_handler       */
        code[n++] = 0x00000000U;           /* nop (delay slot)            */

        /* Patch c0vec_tbl[4].isr → trampoline */
        e4[0] = tramp_k;

        stub_puts("[IP54] Patched c0vec_tbl[4].isr = pckm trampoline @ ");
        stub_puthex(tramp_k);
        stub_puts(" via lcl2vec_tbl[5] / orig @ ");
        stub_puthex(orig_handler);
        stub_puts("\n");
      } else {
        if (!c0vec_tbl_addr) stub_puts("[IP54] WARN: c0vec_tbl not found\n");
        if (!lcl2vec_k) stub_puts("[IP54] WARN: lcl2vec_tbl not found\n");
        if (!wd93_u) stub_puts("[IP54] WARN: wd93_earlyinit not found\n");
      }

      /* Part B: Enable HEART IMR0 bit 23 (8042 kbd/mouse) → IP3 → c0vec_tbl[4]
       * → pckm trampoline.  Keep bit 20 (pvnet) enabled too (harmless; pvnet
       * polls and never asserts its GPIO).  NOTE: do NOT overwrite
       * c0vec_tbl[4].isr below — the trampoline installed above must survive. */
      {
        volatile unsigned long long *imr0 =
            (volatile unsigned long long *)0xAFF10000U;
        /* bit 20 (pvnet) only.  Do NOT unmask bit 23 (8042) while the
         * trampoline above is disabled — otherwise the 8042 IRQ would reach
         * c0vec_tbl[4]'s ORIGINAL (non-8042) handler and storm.  Re-add
         * (1ULL << 23) here together with re-enabling the trampoline. */
        *imr0 |= (1ULL << 20);
      }

      /* Part C: Patch ip54_intr_init to also enable IMR0 bit 20 at runtime.
       * Safety net: re-sets the bit when if_pvnetedtinit → ip54_intr_init.
       * Uses space AFTER the dispatch trampoline in wd93_earlyinit.
       */
      {
        unsigned int ii_u = kern_sym_u("ip54_intr_init");

        if (0 && ii_u && wd93_u && wd93_k) {
          /* Place IMR0 code after dispatch trampoline (offset +64 = 16 instrs) */
          volatile unsigned int *code2 = (volatile unsigned int *)(wd93_u + 8 + 64);
          unsigned int imr_tramp = wd93_k + 8 + 64;
          int n = 0;

          code2[n++] = 0x3C08AFF1U;  /* lui $t0, 0xAFF1       */
          code2[n++] = 0xDD090000U;  /* ld $t1, 0($t0)  # IMR0 */
          code2[n++] = 0x3C0A0010U;  /* lui $t2, 0x0010 # bit 20 */
          code2[n++] = 0x012A4825U;  /* or $t1, $t1, $t2      */
          code2[n++] = 0xFD090000U;  /* sd $t1, 0($t0)  # IMR0 */
          code2[n++] = 0x03E00008U;  /* jr $ra */
          code2[n++] = 0x00000000U;  /* nop */

          {
            volatile unsigned int *ii = (volatile unsigned int *)ii_u;
            ii[0] = mips_j(imr_tramp);
            ii[1] = 0x00000000U;
          }
          stub_puts("[IP54] Patched ip54_intr_init: IMR0 bit 20 trampoline\n");
        }
      }
    }

/*
     * Patch 37: Fix pvfb gf_Attach and gf_MapGfx for Xsgi.
     *
     * The golden kernel's pvfb driver has all gfx_fncs as stubs (return 0).
     * Xsgi needs gf_Attach to call gfxdd_mmap (create DDV mapping region)
     * and gf_MapGfx to call ddv_mappages (populate page tables).
     *
     * We find the pvfb_gfx_fncs vtable by searching the kernel's data
     * section for 28 consecutive function pointers with many identical
     * entries (the common "return 0" stub). Then we write trampolines
     * in wd93edtinit's dead body and update the vtable pointers.
     */
    {
      unsigned int gfxdd_mmap_fn = kern_sym("gfxdd_mmap");
      unsigned int ddv_mappages_fn = kern_sym("ddv_mappages");
      unsigned int gfx_fault_fn = kern_sym("gfx_fault");
      unsigned int gfxdd_munmap_fn = kern_sym("gfxdd_munmap");
      unsigned int wd93edt_u = kern_sym_u("wd93edtinit");
      unsigned int wd93edt_k = kern_sym("wd93edtinit");

      if (gfxdd_mmap_fn && ddv_mappages_fn && gfx_fault_fn && wd93edt_u && wd93edt_k) {
        /* Find pvfb_gfx_fncs vtable by scanning pvfbedtinit's code for
         * the jal GfxRegisterBoard call, then reading the lui/addiu that
         * loads the first argument (a0/$4 = vtable address).
         *
         * Pattern: lui $4, hi_addr; ... addiu $4, $4, lo_addr; ...
         *          jal GfxRegisterBoard
         */
        unsigned int vtbl_u = 0;
        unsigned int grb_fn = kern_sym("GfxRegisterBoard");
        unsigned int pvfb_edt_k = kern_sym("pvfbedtinit");
        unsigned int pvfb_edt_u = pvfb_edt_k ? to_kseg1(pvfb_edt_k) : 0;
        unsigned int i;

        if (grb_fn && pvfb_edt_u) {
          volatile unsigned int *code = (volatile unsigned int *)pvfb_edt_u;
          unsigned int jal_grb = mips_jal(grb_fn);
          unsigned int lui_hi = 0, addiu_lo = 0;

          /* Scan for jal GfxRegisterBoard */
          for (i = 0; i < 120; i++) {
            if (code[i] == jal_grb) {
              /* Found! Look backwards for lui $4, hi and addiu $4, $4, lo */
              int j;
              for (j = i - 1; j >= 0 && j > (int)i - 20; j--) {
                unsigned int w = code[j];
                /* lui $4, imm = 0x3C04xxxx */
                if ((w & 0xFFFF0000U) == 0x3C040000U) {
                  lui_hi = (w & 0xFFFF) << 16;
                }
                /* addiu $4, $4, imm = 0x2484xxxx */
                if ((w & 0xFFFF0000U) == 0x24840000U) {
                  short soff = (short)(w & 0xFFFF);
                  addiu_lo = (unsigned int)soff;
                }
              }
              if (lui_hi) {
                unsigned int vtbl_k = lui_hi + addiu_lo;
                vtbl_u = to_kseg1(vtbl_k);
              }
              break;
            }
          }
        }

        if (vtbl_u) {
          volatile unsigned int *vtbl = (volatile unsigned int *)vtbl_u;
          /* Slots: [1]=gf_Attach, [2]=gf_Detach, [12]=gf_MapGfx */

          /*
           * Strategy: use v_mapphys (correct for device MMIO) via a custom
           * page fault handler, instead of ddv_mappages (hangs on device pages).
           *
           * Layout in wd93edtinit dead body:
           *   +8:  pvfb_fault handler (called on first user access to mapping)
           *   +48: gf_Attach trampoline (calls gfxdd_mmap with pvfb_fault)
           *   +132: gf_MapGfx stub (returns 0 — fault handler does the real work)
           */
          unsigned int v_mapphys_fn = kern_sym("v_mapphys");
          volatile unsigned int *code = (volatile unsigned int *)(wd93edt_u + 8);
          unsigned int code_k = wd93edt_k + 8;
          int n = 0;

          /*
           * pvfb_fault(vt=$4, arg=$5, vaddr=$6, rw=$7):
           *   return v_mapphys(vt, 0xBF490000, 0x2000)
           *
           * This is the page fault handler for the gfx mapping region.
           * Called when the DDX first accesses the mapped pvrex3 address.
           * v_mapphys handles device MMIO mapping correctly (uncacheable,
           * no pfdat needed), unlike ddv_mappages which hangs on device pages.
           */
          unsigned int fault_k = code_k;
          /* a0=$4 already has vt (pass through) */
          code[n++] = 0x3C050000U | 0xBF49U;  /* lui $a1, 0xBF49           */
          code[n++] = 0x24062000U;             /* addiu $a2, $zero, 0x2000  */
          code[n++] = 0x3C190000U | ((v_mapphys_fn >> 16) & 0xFFFF);
                                                /* lui $t9, hi(v_mapphys)   */
          code[n++] = 0x37390000U | (v_mapphys_fn & 0xFFFF);
                                                /* ori $t9, $t9, lo         */
          code[n++] = 0x03200008U;             /* jr $t9 (tail call)        */
          code[n++] = 0x00000000U;             /* nop (delay slot)          */
          /* pad to 10 words for alignment */
          code[n++] = 0x00000000U;
          code[n++] = 0x00000000U;
          code[n++] = 0x00000000U;
          code[n++] = 0x00000000U;

          /*
           * gf_Attach(gfxp=$4, vaddr=$5):
           *   gfxdd_mmap(0, 0x2000, vaddr, pvfb_fault, gfxp, NULL, 0, &gfxp->gx_ddv)
           */
          unsigned int att_k = code_k + n * 4;
          code[n++] = 0x27BDFFF0U;           /* addiu $sp, $sp, -16       */
          code[n++] = 0xFFBF0000U;           /* sd $ra, 0($sp)            */
          code[n++] = 0xFFB00008U;           /* sd $s0, 8($sp)            */
          code[n++] = 0x00808025U;           /* or $s0, $a0, $zero (save gfxp) */
          code[n++] = 0x00A03025U;           /* or $a2, $a1, $zero (uvaddr=vaddr) */
          code[n++] = 0x00002025U;           /* or $a0, $zero, $zero (flag=0)   */
          code[n++] = 0x24052000U;           /* addiu $a1, $zero, 0x2000 (size) */
          code[n++] = 0x3C070000U | ((fault_k >> 16) & 0xFFFF);
                                              /* lui $a3, hi(pvfb_fault)   */
          code[n++] = 0x34E70000U | (fault_k & 0xFFFF);
                                              /* ori $a3, $a3, lo          */
          code[n++] = 0x02004025U;           /* or $8, $s0, $zero (a4=gfxp)    */
          code[n++] = 0x00004825U;           /* or $9, $zero, $zero (a5=NULL)  */
          code[n++] = 0x00005025U;           /* or $10, $zero, $zero (a6=0)    */
          code[n++] = 0x24000000U | (16 << 21) | (11 << 16) | 20;
                                              /* addiu $11, $s0, 20 (a7=&gx_ddv) */
          code[n++] = 0x3C190000U | ((gfxdd_mmap_fn >> 16) & 0xFFFF);
                                              /* lui $t9, hi(gfxdd_mmap)   */
          code[n++] = 0x37390000U | (gfxdd_mmap_fn & 0xFFFF);
                                              /* ori $t9, $t9, lo          */
          code[n++] = 0x0320F809U;           /* jalr $ra, $t9              */
          code[n++] = 0x00000000U;           /* nop (delay slot)           */
          code[n++] = 0xDFB00008U;           /* ld $s0, 8($sp)            */
          code[n++] = 0xDFBF0000U;           /* ld $ra, 0($sp)            */
          code[n++] = 0x03E00008U;           /* jr $ra                    */
          code[n++] = 0x27BD0010U;           /* addiu $sp, $sp, 16 (delay) */

          /*
           * gf_MapGfx — just return 0.
           * Pages are mapped on first access by pvfb_fault → v_mapphys.
           */
          unsigned int mapg_k = code_k + n * 4;
          code[n++] = 0x00001025U;           /* or $v0, $zero, $zero (ret 0) */
          code[n++] = 0x03E00008U;           /* jr $ra                    */
          code[n++] = 0x00000000U;           /* nop                       */

          /* Update vtable: slot[1]=gf_Attach, slot[12]=gf_MapGfx,
           * slot[25]=gf_Private (return 0 instead of EINVAL).
           *
           * The Newport DDX uses board-specific ioctl 0x520E for
           * SETDISPLAYMODE, which routes through gf_Private.
           * The original stub returns EINVAL → DDX thinks mode failed.
           * Returning 0 = success makes all 12 modes succeed. */
          vtbl[1] = att_k;
          vtbl[12] = mapg_k;
          vtbl[25] = mapg_k;  /* reuse gf_MapGfx stub (returns 0) */

          stub_puts("[IP54] pvfb vtable at ");
          stub_puthex(vtbl_u);
          stub_puts(": gf_Attach=");
          stub_puthex(att_k);
          stub_puts(" gf_MapGfx=");
          stub_puthex(mapg_k);
          stub_puts("\n");
        } else {
          stub_puts("[IP54] WARN: pvfb_gfx_fncs vtable not found\n");
        }
      } else {
        stub_puts("[IP54] WARN: pvfb gfx patch skipped (missing symbols)\n");
      }
    }

/* Jump to the loaded program */
    {
      entry_fn_t fn = (entry_fn_t)entry;
      fn(1, kern_argv, kern_envp);
    }
  }

  /* Should not return */
  stub_puts("[IP54] Program returned from Execute\n");
  return 0;
}


/* ================================================================
 * CACHE / CPU FUNCTIONS
 * ================================================================ */

void config_cache(void) { debug_marker('C'); }

void flush_cache(void) {}

unsigned long GetSR(void) {
  unsigned long sr;
  __asm__ volatile("mfc0 %0, $12" : "=r"(sr));
  return sr;
}

unsigned long SetSR(unsigned long sr) {
  unsigned long old;
  __asm__ volatile("mfc0 %0, $12" : "=r"(old));
  __asm__ volatile("mtc0 %0, $12" : : "r"(sr));
  return old;
}

int splhi(void) { return 0; }
int spl(int level) {
  (void)level;
  return 0;
}
int splerr(void) { return 0; }
int splockspl(lock_t lock, int (*splfn)(void)) {
  (void)lock;
  (void)splfn;
  return 0;
}
void spunlockspl(lock_t lock, int ospl) {
  (void)lock;
  (void)ospl;
}
int cpuid(void) { return 0; }

unsigned long r4k_getticker(void) {
  unsigned long count;
  __asm__ volatile("mfc0 %0, $9" : "=r"(count));
  return count;
}

/* ================================================================
 * EXCEPTION HANDLING
 * ================================================================ */

/* Emergency stack for __exc_handler (4 KB, static = BSS, always valid) */
static unsigned int __exc_stack[1024];

/*
 * __exc_handler -- diagnostic C handler for early-boot exceptions.
 *
 * Called via a JR-through-register trampoline installed at the BEV=0
 * general exception vector (kseg0 0x80000180).  The trampoline also
 * sets $sp to the top of __exc_stack, so calling C is safe.
 *
 * Reads CP0 Cause and EPC, saves them to physical 0x100/0x104 (readable
 * via QEMU monitor: xp /2xw 0x100), then prints them to pvuart and loops.
 */
static __attribute__((noinline)) void __exc_handler(void)
{
    unsigned int cause, epc, badvaddr, ra, sp_val, gp_val, at_val;
    /* Read $ra before any calls change it */
    __asm__ volatile("move %0, $ra" : "=r"(ra));
    __asm__ volatile("move %0, $sp" : "=r"(sp_val));
    __asm__ volatile("move %0, $gp" : "=r"(gp_val));
    __asm__ volatile("move %0, $at" : "=r"(at_val));
    __asm__ volatile("mfc0 %0, $13" : "=r"(cause));
    __asm__ volatile("mfc0 %0, $14" : "=r"(epc));
    __asm__ volatile("mfc0 %0, $8"  : "=r"(badvaddr));

    /* Save to kseg1 for QEMU monitor: xp /8xw 0x100 */
    *(volatile unsigned int *)0xA0000100 = cause;
    *(volatile unsigned int *)0xA0000104 = epc;
    *(volatile unsigned int *)0xA0000108 = ra;
    *(volatile unsigned int *)0xA000010C = sp_val;
    *(volatile unsigned int *)0xA0000110 = gp_val;
    *(volatile unsigned int *)0xA0000114 = at_val;
    *(volatile unsigned int *)0xA0000118 = badvaddr;

    stub_puts("\r\n!EX! Cause=");
    stub_puthex(cause);
    stub_puts(" EPC=");
    stub_puthex(epc);
    stub_puts(" BadVA=");
    stub_puthex(badvaddr);
    stub_puts("\r\n     RA=");
    stub_puthex(ra);
    stub_puts(" SP=");
    stub_puthex(sp_val);
    stub_puts(" GP=");
    stub_puthex(gp_val);

    /*
     * Ring buffer diagnostics: read kernel conbuf vars via GP-relative
     * KSEG1 addresses (uncached, no TLB needed) to determine if
     * pagecoloralign initialised the ring buffer before mlreset's cmn_err.
     *
     * gp-0x5c90 = ring_buf_base  (sbss, should be non-zero after pagecoloralign)
     * gp-0x5cc0 = ring_buf_write (sbss, write count)
     * gp-0x6e14 = ring_buf_mask  (sdata, capacity-1; 0 means uninitialised)
     */
    if (gp_val != 0) {
        unsigned int rb_base_va  = gp_val - 0x5c90u;
        unsigned int rb_write_va = gp_val - 0x5cc0u;
        unsigned int rb_mask_va  = gp_val - 0x6e14u;
        /* Convert KSEG0 VA → KSEG1 (strip top 3 bits, add 0xA0000000) */
        unsigned int rb_base  = *(volatile unsigned int *)((rb_base_va  & 0x1FFFFFFFu) | 0xA0000000u);
        unsigned int rb_write = *(volatile unsigned int *)((rb_write_va & 0x1FFFFFFFu) | 0xA0000000u);
        unsigned int rb_mask  = *(volatile unsigned int *)((rb_mask_va  & 0x1FFFFFFFu) | 0xA0000000u);
        stub_puts("\r\n     RingBase=");
        stub_puthex(rb_base);
        stub_puts(" RingWrite=");
        stub_puthex(rb_write);
        stub_puts(" RingMask=");
        stub_puthex(rb_mask);
        *(volatile unsigned int *)0xA000011C = rb_base;
        *(volatile unsigned int *)0xA0000120 = rb_write;
        *(volatile unsigned int *)0xA0000124 = rb_mask;
    }
    stub_puts("\r\n");

    while (1) {}
}

/*
 * _hook_exceptions -- called by finit.c just before SR_BEV is cleared.
 *
 * Installs a 7-instruction trampoline at the BEV=0 general exception
 * vector (physical 0x00000180 = kseg1 0xA0000180 = kseg0 0x80000180):
 *
 *   lui  k0, HI(__exc_handler)   ; load C handler address
 *   ori  k0, k0, LO(__exc_handler)
 *   lui  k1, HI(stack_top)       ; set up emergency stack
 *   ori  k1, k1, LO(stack_top)
 *   addu sp, k1, $0              ; move $sp = stack_top
 *   jr   k0                      ; jump to __exc_handler
 *   nop                          ; branch-delay slot
 */
void _hook_exceptions(void)
{
    volatile unsigned int *vec;
    unsigned int fn_addr = (unsigned int)(uintptr_t)__exc_handler;
    unsigned int sp_addr = (unsigned int)(uintptr_t)(__exc_stack + 1024);
    unsigned int fn_hi   = fn_addr >> 16;
    unsigned int fn_lo   = fn_addr & 0xFFFFu;
    unsigned int sp_hi   = sp_addr >> 16;
    unsigned int sp_lo   = sp_addr & 0xFFFFu;

    vec = (volatile unsigned int *)0xA0000180;
    vec[0] = 0x3C1A0000u | fn_hi;   /* lui  k0, fn_hi         */
    vec[1] = 0x375A0000u | fn_lo;   /* ori  k0, k0, fn_lo     */
    vec[2] = 0x3C1B0000u | sp_hi;   /* lui  k1, sp_hi         */
    vec[3] = 0x377B0000u | sp_lo;   /* ori  k1, k1, sp_lo     */
    vec[4] = 0x0360E821u;           /* addu sp, k1, $0  (move $sp = stack_top) */
    vec[5] = 0x03400008u;           /* jr   k0                */
    vec[6] = 0x00000000u;           /* nop  (delay slot)      */
}

void _save_exceptions(void) {}
void _restore_exceptions(void) {}

void panic(char *fmt, ...) {
  stub_puts("[IP54 PANIC] ");
  if (fmt)
    stub_puts(fmt);
  stub_puts("\n");
  while (1)
    ;
}

/* ================================================================
 * INITIALIZATION FUNCTIONS
 * ================================================================ */

void init_spb(void);

void _init_saio(void) {
  debug_puts("[IP54] _init_saio\n");
  init_spb();
  init_fd_table();
  init_component_tree();
  init_memory_descriptors();
}

void initConsole(void) { debug_puts("[IP54] initConsole\n"); }

void initGraphics(int functionCode) {
  (void)functionCode;
  debug_puts("[IP54] initGraphics\n");
}

void init_consenv(char *console) { (void)console; }
void _init_bootenv(void) {}

int post2(int functionCode, int resetCount) {
  (void)functionCode;
  (void)resetCount;
  debug_puts("[IP54] post2\n");
  return 0;
}

int post3(int functionCode, int resetCount) {
  (void)functionCode;
  (void)resetCount;
  debug_puts("[IP54] post3\n");
  return 0;
}

/* ================================================================
 * PROM MENU / COMMAND STUBS
 * ================================================================ */

static int stub_cmd(void) { return 0; }
static int enter_command_monitor(void) { return 1; }

static mitem_t prom_menu_items[] = {
    {"Start System", stub_cmd, 0, 0},
    {"Install System Software", stub_cmd, 0, 0},
    {"Run Diagnostics", stub_cmd, 0, 0},
    {"Recover System", stub_cmd, 0, 0},
    {"Enter Command Monitor", enter_command_monitor, 0, 0},
    {"Select Keyboard Layout", stub_cmd, 0, 0},
};

menu_t prom_menu = {prom_menu_items,
                    0,
                    "System Maintenance Menu",
                    "\nOption? ",
                    "Select an option from the menu.",
                    6};

int init_prom_menu(void) { return 0; }


/* ================================================================
 * GUI FUNCTIONS (serial mode stubs)
 * ================================================================ */

int isGuiMode(void) { return 0; }
void setGuiMode(int m, int flags) {
  (void)m;
  (void)flags;
}
int doGui(void) { return 0; }
int isgraphic(ULONG fd) {
  (void)fd;
  return 0;
}
int popupDialog(const char *msg, int *buttons, int type, int flags) {
  (void)buttons;
  (void)type;
  (void)flags;
  if (msg)
    debug_puts(msg);
  debug_putchar('\n');
  return 0;
}
void pongui_cleanup(void) {}
void setTpButtonAction(void (*fn)(void), int a, int b) {
  (void)fn;
  (void)a;
  (void)b;
}
void guiRefresh(void) {}
int gfxWidth(void) { return 640; }
int gfxHeight(void) { return 480; }

void *createButton(void) { return (void *)0; }
void *createDialog(void) { return (void *)0; }
void *createText(void) { return (void *)0; }
void addButtonCallBack(void) {}
void addButtonImage(void) {}
void addButtonText(void) {}
void addDialogButton(void) {}
void deleteObject(void) {}
void drawObject(void) {}
void invalidateButton(void) {}
void moveObject(void) {}
void moveTextPoint(void) {}

/* ================================================================
 * SERIAL I/O HELPERS
 * ================================================================ */

void _scandevs(void) {}

int _circ_getc(struct device_buf *db) {
  (void)db;
  return -1;
}
int _circ_nread(struct device_buf *db) {
  (void)db;
  return 0;
}
void _ttyinput(struct device_buf *db, char c) {
  (void)db;
  (void)c;
}
void rbsetbs(int bs) { (void)bs; }

void close_noncons(void) {
  int i;
  if (!fd_table_inited)
    return;
  for (i = 2; i < MAX_FDS; i++)
    fd_table[i].type = FD_TYPE_UNUSED;
}

/* ================================================================
 * MISCELLANEOUS
 * ================================================================ */

void playTune(int tune) { (void)tune; }
int usingFlatPanel(void) { return 0; }
void i2cfp_PanelOff(void) {}
int validate_passwd(void) { return 1; }
int ParseData(void) { return 0; }


char *kl_inv_find(void) { return (char *)0; }

/* ================================================================
 * FIRMWARE CALLBACKS & SPB
 * ================================================================ */

char *getversion(void) { return "IP54 PROM v0.2 (QEMU SGI O2)"; }

void FWCB_EnterInteractiveMode(void) {
  debug_puts("[IP54] FWCB_EnterInteractiveMode\n");
}

void FWCB_Halt(void) {
  debug_puts("[IP54] FWCB_Halt\n");
  while (1)
    ;
}

void FWCB_PowerDown(void) {
  debug_puts("[IP54] FWCB_PowerDown\n");
  while (1)
    ;
}

void FWCB_Restart(void) { debug_puts("[IP54] FWCB_Restart\n"); }

void FWCB_Reboot(void) {
    debug_puts("[IP54] FWCB_Reboot\n");

    /* Dump CP0 registers for TLB/exception diagnostics.
     * With -mabi=32, we must extract 64-bit CP0 values as two 32-bit
     * halves using dsrl32 in asm (dmfc0 → unsigned long long fails
     * because the compiler can't map a 64-bit GPR to a register pair).
     */
#define DUMP_CP0_64(name, regnum) do { \
        unsigned int _hi, _lo; \
        __asm__ volatile( \
            ".set push\n\t" \
            ".set noat\n\t" \
            "dmfc0 $1, $" #regnum "\n\t" \
            "dsrl32 %0, $1, 0\n\t" \
            "move %1, $1\n\t" \
            ".set pop\n\t" \
            : "=r"(_hi), "=r"(_lo)); \
        debug_puts(name "="); \
        stub_puthex(_hi); stub_puthex(_lo); \
    } while (0)

    {
        unsigned int val32;

        __asm__ volatile("mfc0 %0, $12" : "=r"(val32)); /* Status */
        debug_puts("[IP54] CP0 Status=");
        stub_puthex(val32);

        __asm__ volatile("mfc0 %0, $13" : "=r"(val32)); /* Cause */
        debug_puts(" Cause=");
        stub_puthex(val32);
        debug_puts("\n");

        debug_puts("[IP54] ");
        DUMP_CP0_64("EPC", 14);
        debug_puts(" ");
        DUMP_CP0_64("BadVAddr", 8);
        debug_puts("\n");

        debug_puts("[IP54] ");
        DUMP_CP0_64("Context", 4);
        debug_puts(" ");
        DUMP_CP0_64("EntryHi", 10);
        debug_puts("\n");

        __asm__ volatile("mfc0 %0, $6" : "=r"(val32)); /* Wired */
        debug_puts("[IP54] CP0 Wired=");
        stub_puthex(val32);
        debug_puts("\n");
    }
#undef DUMP_CP0_64

    /* Dump exception frame at 0x8829c340 (intstack area).
     * IRIX eframe_s layout (N32/64): saved registers at known offsets.
     * On MIPS, EF_RA is at offset 0xF8 (248) in the exception frame.
     * EF_EPC is at offset 0x100 (256).
     * EF_SP is at offset 0xE8 (232).
     * EF_AT is at offset 0x08.
     * EF_V0 is at offset 0x10.
     * EF_A0 is at offset 0x20.
     * Let's just dump several words from ep for analysis.
     */
    {
      volatile unsigned int *ep = (volatile unsigned int *)0xa829c340U;
      int i;
      debug_puts("[IP54] EP frame @0x8829c340 (first 80 words):\n");
      for (i = 0; i < 80; i += 4) {
        debug_puts("  +"); stub_puthex(i*4); debug_puts(": ");
        stub_puthex(ep[i]); debug_puts(" ");
        stub_puthex(ep[i+1]); debug_puts(" ");
        stub_puthex(ep[i+2]); debug_puts(" ");
        stub_puthex(ep[i+3]); debug_puts("\n");
      }
    }

    /* Dump code at VA 0x88003020 (via KSEG1) to see what's installed */
    {
        volatile unsigned int *p = (volatile unsigned int *)0xa8003020U;
        debug_puts("[IP54] Code@0x88003020: ");
        stub_puthex(p[0]); debug_puts(" ");
        stub_puthex(p[1]); debug_puts(" ");
        stub_puthex(p[2]); debug_puts(" ");
        stub_puthex(p[3]); debug_puts("\n");
    }

    /* Dump all 64 TLB entries (only show valid ones for entries >= 8) */
    {
        int i;
        for (i = 0; i < 64; i++) {
            unsigned int hi_h, hi_l, lo0_v, lo1_v, mask;
            __asm__ volatile(
                ".set push\n\t"
                ".set noat\n\t"
                ".set noreorder\n\t"
                "mtc0 %5, $0\n\t"    /* Index = i */
                "nop\n\t"
                "tlbr\n\t"           /* Read TLB[i] */
                "nop\n\t"
                "nop\n\t"
                "dmfc0 $1, $10\n\t"  /* $at = EntryHi (64-bit) */
                "dsrl32 %0, $1, 0\n\t"
                "move %1, $1\n\t"
                "mfc0 %2, $2\n\t"    /* EntryLo0 (32-bit enough) */
                "mfc0 %3, $3\n\t"    /* EntryLo1 */
                "mfc0 %4, $5\n\t"    /* PageMask */
                ".set pop\n\t"
                : "=r"(hi_h), "=r"(hi_l), "=r"(lo0_v),
                  "=r"(lo1_v), "=r"(mask)
                : "r"(i)
            );
            /* For wired entries (0-7): always show.
             * For non-wired (8-63): only show if Lo0 or Lo1 has V bit (bit 1). */
            if (i >= 8 && !(lo0_v & 2) && !(lo1_v & 2))
                continue;
            debug_puts("[IP54] TLB[");
            if (i >= 10) stub_putchar_polled('0' + i / 10);
            stub_putchar_polled('0' + i % 10);
            debug_puts("] Hi=");
            stub_puthex(hi_h); stub_puthex(hi_l);
            debug_puts(" Lo0=");
            stub_puthex(lo0_v);
            debug_puts(" Lo1=");
            stub_puthex(lo1_v);
            debug_puts(" Msk=");
            stub_puthex(mask);
            debug_puts("\n");
        }
    }

    /* Dump physical memory at the icode page (VA 0x10000000).
     * Walk TLB to find the PFN, then read via KSEG0.
     */
    {
        int i;
        for (i = 0; i < 64; i++) {
            unsigned int hi_l, lo0_v;
            __asm__ volatile(
                ".set push\n\t"
                ".set noat\n\t"
                ".set noreorder\n\t"
                "mtc0 %2, $0\n\t"
                "nop\n\t"
                "tlbr\n\t"
                "nop\n\t"
                "nop\n\t"
                "mfc0 %0, $10\n\t"     /* EntryHi low 32 bits */
                "mfc0 %1, $2\n\t"      /* EntryLo0 */
                ".set pop\n\t"
                : "=r"(hi_l), "=r"(lo0_v)
                : "r"(i)
            );
            /* Match VA 0x10000000: VPN2 in EntryHi (bits 31:13), mask ASID */
            if ((hi_l & 0xffffe000U) == 0x10000000U && (lo0_v & 2)) {
                /* Found it! PFN = lo0 >> 6, physical = PFN << 12 */
                unsigned int pfn = (lo0_v >> 6) & 0xfffffU;
                unsigned int phys = pfn << 12;
                volatile unsigned int *mem = (volatile unsigned int *)(0x80000000U | phys);
                int j;
                debug_puts("[IP54] icode page: TLB[");
                stub_putchar_polled('0' + i / 10);
                stub_putchar_polled('0' + i % 10);
                debug_puts("] PFN=");
                stub_puthex(pfn);
                debug_puts(" PA=");
                stub_puthex(phys);
                debug_puts("\n[IP54] icode[0..15]: ");
                for (j = 0; j < 16; j++) {
                    stub_puthex(mem[j]);
                    stub_putchar_polled(' ');
                }
                debug_puts("\n[IP54] icode[16..23]: ");
                for (j = 16; j < 24; j++) {
                    stub_puthex(mem[j]);
                    stub_putchar_polled(' ');
                }
                debug_puts("\n");
                break;
            }
        }
        if (i == 64) {
            debug_puts("[IP54] icode page: no TLB entry for VA 0x10000000\n");
        }
    }
}

/* Static FirmwareVector for SPB */
static FirmwareVector _fw_vector;

void init_spb(void) {
  spb_t *spb = SPB;
  debug_puts("[IP54] init_spb\n");

  spb->Signature = 0x53435241; /* "ARCS" */
  spb->Length = sizeof(spb_t);
  spb->Version = 1;
  spb->Revision = 10;
  spb->TransferVector = &_fw_vector;
  spb->TVLength = sizeof(FirmwareVector);

  /* Populate firmware vector — kernel calls these through SPB->TransferVector */
  _fw_vector.Load                 = Load;
  _fw_vector.Invoke               = Invoke;
  _fw_vector.Execute              = Execute;
  _fw_vector.Halt                 = FWCB_Halt;
  _fw_vector.PowerDown            = FWCB_PowerDown;
  _fw_vector.Restart              = FWCB_Restart;
  _fw_vector.Reboot               = FWCB_Reboot;
  _fw_vector.EnterInteractiveMode = FWCB_EnterInteractiveMode;
  /* reserved1 = 0 */
  _fw_vector.GetPeer              = GetPeer;
  _fw_vector.GetChild             = GetChild;
  _fw_vector.GetParent            = GetParent;
  _fw_vector.GetConfigData        = GetConfigData;
  _fw_vector.AddChild             = AddChild;
  _fw_vector.DeleteComponent      = DeleteComponent;
  _fw_vector.GetComponent         = GetComponent;
  _fw_vector.SaveConfiguration    = SaveConfiguration;
  _fw_vector.GetSystemId          = GetSystemId;
  _fw_vector.GetMemoryDesc        = GetMemoryDescriptor;
  /* reserved2 = 0 */
  _fw_vector.GetTime              = GetTime;
  _fw_vector.GetRelativeTime      = GetRelativeTime;
  _fw_vector.GetDirEntry          = GetDirEntry;
  _fw_vector.Open                 = Open;
  _fw_vector.Close                = Close;
  _fw_vector.Read                 = Read;
  _fw_vector.GetReadStatus        = GetReadStatus;
  _fw_vector.Write                = Write;
  _fw_vector.Seek                 = Seek;
  _fw_vector.Mount                = Mount;
  _fw_vector.GetEnvironmentVariable = GetEnvironmentVariable;
  _fw_vector.SetEnvironmentVariable = SetEnvironmentVariable;
  _fw_vector.GetFileInformation   = GetFileInformation;
  _fw_vector.SetFileInformation   = SetFileInformation;
  _fw_vector.FlushAllCaches       = FlushAllCaches;
}

void IP32processorTCI(void) {}
int readc0_cmd(void) { return 0; }
int writec0_cmd(void) { return 0; }
int gioinfo(void) { return 0; }

/* ================================================================
 * STUBS FOR REMOVED FILES
 * These replace symbols from IP32k.c, IP32asm.s, mte_copy.c,
 * mte_stubs.c, tile.c, ds2502.c, st16c1451.c, DBCuartio.c,
 * DBCuartsim.c, flash.c, flashwrite.c, badaddr.c, ds1685.c,
 * mace_16c550.c, secondary_boot.s
 * ================================================================ */

/* --- From IP32asm.s --- */
void wbflush(void) {}
void flushbus(void) {} /* XLEAF alias of wbflush in original */
unsigned long long read_reg64(volatile unsigned long long *addr) {
  (void)addr;
  return 0;
}
void write_reg64(unsigned long long val, volatile unsigned long long *addr) {
  (void)val;
  (void)addr;
}
int get_crm_rev(void) { return 0; }
void crm_softreset(void) { while (1); }
void crm_hardreset(void) { while (1); }
void crm_deadloop(void) { while (1); }
unsigned int Read_C0_LLADDR(void) {
  unsigned int val;
  __asm__ volatile("mfc0 %0, $17" : "=r"(val));
  return val;
}
unsigned int Read_C0_Config(void) {
  unsigned int val;
  __asm__ volatile("mfc0 %0, $16" : "=r"(val));
  return val;
}
void Write_C0_Config(unsigned int val) {
  __asm__ volatile("mtc0 %0, $16" : : "r"(val));
}
unsigned int Read_C0_PRId(void) {
  unsigned int val;
  __asm__ volatile("mfc0 %0, $15" : "=r"(val));
  return val;
}
void led_on(int mask) { (void)mask; }
void led_off(int mask) { (void)mask; }
void ticksper1024inst(unsigned int *dst) { *dst = 1024; }
void cpuclkper100ticks(unsigned int *dst) { *dst = 200; }

/* --- From IP32k.c --- */
void cpu_errputc(char c) { *(volatile unsigned char *)0xBF62017B = c; }
void cpu_acpanic(char *str) {
  if (str) stub_puts(str);
  stub_puts("\n");
  while (1);
}
char *cpu_get_disp_str(void) { return ""; }
char *cpu_get_serial(void) { return ""; }
char *cpu_get_kbd_str(void) { return "keyboard(0)"; }
char *cpu_get_mouse(void) { return "pointer(0)"; }
void cpu_get_eaddr(unsigned char *ea) {
  ea[0] = 0x08; ea[1] = 0x00; ea[2] = 0x69;
  ea[3] = 0xaa; ea[4] = 0xbb; ea[5] = 0xcc;
}
static char htoa_nibble(int d) {
  return (d < 10) ? '0' + d : 'a' + d - 10;
}
void cpu_get_eaddr_str(char *buf) {
  unsigned char ea[6];
  int i;
  cpu_get_eaddr(ea);
  for (i = 0; i < 6; i++) {
    buf[i * 3]     = htoa_nibble((ea[i] >> 4) & 0xf);
    buf[i * 3 + 1] = htoa_nibble(ea[i] & 0xf);
    buf[i * 3 + 2] = (i < 5) ? ':' : '\0';
  }
}
void cpu_hardreset(void) { while (1); }
void cpu_softreset(void) { while (1); }
void cpu_reset(void) { while (1); }
void cpu_rtcinit(void) {}
void cpu_powerdown(void) { while (1); }
void cpu_scandevs(void) {}
unsigned int cpu_get_freq(void) { return 200000000; }
char *cpu_get_freq_str(void) { return "200"; }
unsigned int cpu_get_memsize(void) { return 64 * 1024 * 1024; }
void cpu_mem_init(void) {}
void alloc_memdesc(void) {}
void cpu_makecfgroot(void) {}
void IP32_cpu_install(void *root) { (void)root; }
int jumper_off(void) { return 0; }
void showException(unsigned long sr, unsigned long cause,
                   unsigned long badvaddr, unsigned long epc) {
  stub_puts("[IP54] Exception: SR=");
  stub_puthex(sr);
  stub_puts(" Cause=");
  stub_puthex(cause);
  stub_puts(" BadVAddr=");
  stub_puthex(badvaddr);
  stub_puts(" EPC=");
  stub_puthex(epc);
  stub_puts("\n");
}

/* envFlash -- referenced by old env.c, now unused but may be referenced */
void *envFlash = 0;

/* --- From mte_copy.c --- */
void mte_zero(unsigned long addr, unsigned long size) {
  bzero((void *)(addr | 0x80000000), size);
}
void mte_delay(void) {}
void mte_spin(void) {}
int mte_set_tlb(unsigned long addr, int bcount, unsigned int tlb_offset,
                int *nbytes) {
  (void)addr; (void)bcount; (void)tlb_offset; (void)nbytes;
  return 0;
}

/* --- From mte_stubs.c --- */
int crmSavePP(void) { return 0; }
void crimeRestore(void) {}
int crimeLockOut(void) { return 0; }

/* --- From tile.c --- */
void initTiles(void) {}
void *getTile(void) { return (void *)0; }
void freeTile(void *tile) { (void)tile; }

/* --- From ds2502.c --- */
void ds2502_get_eaddr(char *eaddr) {
  cpu_get_eaddr((unsigned char *)eaddr);
}

/* --- From st16c1451.c / DBCuartio.c / DBCuartsim.c --- */
void UARTinit(void) {}
int UARTstatus(void) { return 0; }
void DPRINTF(char *fmt, ...) { (void)fmt; }
void DPUTCHAR(char c) { (void)c; }
int Dgetchar(void) { return -1; }
int Dgetc(void) { return -1; }

/* --- From flash.c --- */
void *findFlashSegment(void *flash, char *name, void *seg) {
  (void)flash; (void)name; (void)seg;
  return (void *)0;
}

/* --- From flashwrite.c --- */
int writeFlashSegment(void *flash, void *seg, char *name, char *vsn,
                      long *body, int len) {
  (void)flash; (void)seg; (void)name; (void)vsn; (void)body; (void)len;
  return 0;
}
int rewriteFlashSegment(void *flash, void *seg, long *buf) {
  (void)flash; (void)seg; (void)buf;
  return 0;
}

/* --- From badaddr.c --- */
int badaddr(volatile void *addr, int size) {
  (void)addr; (void)size;
  return 0; /* always succeeds in QEMU */
}
int badaddr_val(volatile void *addr, int size, volatile void *val) {
  (void)addr; (void)size; (void)val;
  return 0;
}

/* --- From ds1685.c --- */
void cpu_set_tod(void *t) { (void)t; }
void cpu_get_tod(void *t) { (void)t; }
int _rtodc(int secs, int year, int month) {
  (void)secs; (void)year; (void)month;
  return 0;
}
int get_dayof_week(long year_secs) { (void)year_secs; return 0; }
void cpu_restart_rtc(void) {}

/* --- From mace_16c550.c --- */
int consgetc(int unit) { (void)unit; return -1; }
void consputc(unsigned char c, int unit) {
  (void)unit;
  *(volatile unsigned char *)0xBF62017B = c;
}
void mace_errputc(char c) {
  *(volatile unsigned char *)0xBF62017B = c;
}
void mace_n16c550_install(void *top) { (void)top; }

/* initMaceSerial -- was inline in old finit.c */
void initMaceSerial(void) {}

/* --- From secondary_boot.s --- */
/* secondary_boot is only called for SMP secondary CPUs; not needed for IP54 */

/* --- __assert from IP32k.c --- */
void __assert(const char *ex, const char *file, int line) {
  stub_puts("[IP54] ASSERT FAILED: ");
  if (ex) stub_puts(ex);
  stub_puts(" at ");
  if (file) stub_puts(file);
  stub_puts(":");
  stub_putdec((unsigned long)line);
  stub_puts("\n");
  while (1);
}

/* ================================================================
 * FILESYSTEM TABLE
 * ================================================================ */

/*
 * _fs_table -- table of filesystem init functions called by fs_init()
 * in libsk/fs/fs.c.  Must be a NULL-terminated array of function pointers.
 * We register XFS, EFS, and the standalone volume-header filesystem (sdvh).
 */
extern int xfs_install(void);
extern int efs_install(void);
extern int sdvh_install(void);

int (*_fs_table[])(void) = {
    xfs_install,
    efs_install,
    sdvh_install,
    (int (*)(void))0
};

/* ================================================================
 * ARCS CONFIGURATION AND DIRECTORY ENTRY STUBS
 * ================================================================ */

/*
 * GetConfigurationData -- returns configuration data for a component node.
 * hinv_cmd.c calls this to fetch device-specific data (e.g. CPU config).
 * We have no extra config data, so always return ENODEV.
 */
LONG GetConfigurationData(void *buf, COMPONENT *comp)
{
    (void)buf; (void)comp;
    return ENODEV;
}

/*
 * GetDirectoryEntry -- enumerates directory entries on an open file
 * descriptor.  ls_cmd.c uses this to implement "ls" on device paths.
 * For pvuart/bootdisk there's no real directory; return ENOENT.
 */
LONG GetDirectoryEntry(ULONG fd, DIRECTORYENTRY *buf, ULONG count, ULONG *actual)
{
    (void)fd; (void)buf; (void)count;
    if (actual) *actual = 0;
    return ENOENT;
}

/* ================================================================
 * ARCS ENVIRONMENT STUB
 * ================================================================ */

/*
 * SetEnvironmentVariable -- set an ARCS/PROM environment variable.
 * mrboot_cmd.c calls this to set "VERBOSE", "OSLoadOptions", etc.
 * Route to our existing setenv() which stores vars in RAM.
 */
CHAR *GetEnvironmentVariable(CHAR *name)
{
    if (!name) return (CHAR *)0;
    return (CHAR *)getenv((const char *)name);
}

LONG SetEnvironmentVariable(CHAR *name, CHAR *value)
{
    if (!name) return EINVAL;
    /* Use setenv for both set and clear; empty value clears the var */
    setenv((char *)name, value ? (char *)value : "");
    return ESUCCESS;
}

/* ================================================================
 * HARDWARE INVENTORY STUBS (hinv_cmd.c)
 * ================================================================ */

/*
 * cpufreq -- return CPU frequency in MHz for a given config Key.
 * hinv_cmd.c formats "MIPS R10000 Processor ... <N> MHZ".
 * The IP54 CPU is R10000 @ 200MHz (matches qemu cpuclk=200000000).
 */
int cpufreq(int key)
{
    (void)key;
    return 200;   /* 200 MHz */
}

/*
 * kl_hinv -- SGI Origin/Octane NUMA hw inventory; not applicable to IP54.
 */
int kl_hinv(int flags, char **argv)
{
    (void)flags; (void)argv;
    return 0;
}

/*
 * _get_numcpus -- return number of active CPUs.
 */
int _get_numcpus(void)
{
    return 1;
}

/*
 * businfo -- print IP32/GIO bus info; no physical bus on IP54.
 */
void businfo(int verbose)
{
    (void)verbose;
}

/* ================================================================
 * GUI STUB FUNCTIONS (mrboot_cmd.c)
 * ================================================================ */

/*
 * GUI stubs for the graphics-based boot menu path (mrboot).
 * On IP54 there is no GFX boot GUI; these are no-ops / return 0.
 */
void cleanGfxGui(void) {}
void changeProgressBox(void *prog, int percent, int tenth)
{
    (void)prog; (void)percent; (void)tenth;
}
void *createProgressBox(char *title, char *msg, int val)
{
    (void)title; (void)msg; (void)val;
    return (void *)0;
}
void resizeDialog(void *dlg, int w, int h)
{
    (void)dlg; (void)w; (void)h;
}

/* ================================================================
 * STDIO FPRINTF STUB
 * ================================================================ */

/*
 * fprintf -- dw.c uses fprintf(stderr, ...) for overflow messages.
 * Route to printf (ignore the FILE* argument).
 */
int fprintf(void *stream, const char *fmt, ...)
{
    (void)stream;
    /* We don't have a varargs printf here — just print the format string */
    printf("%s", fmt);
    return 0;
}

/* ================================================================
 * RESTART BLOCK STUBS (go_cmd.c / rb_cmd.c)
 * ================================================================ */

/*
 * init_rb / save_rb / restore_rb -- manage the ARCS restart block used
 * to re-run the last boot command.  IP54 doesn't need persistent restart
 * support; provide minimal stubs so rb_cmd compiles and links.
 */
void init_rb(void) {}

int save_rb(int argc, char **argv, char **envp)
{
    (void)argc; (void)argv; (void)envp;
    return 0;
}

int restore_rb(int *argc, char ***argv, char ***envp)
{
    (void)argc; (void)argv; (void)envp;
    return 1;   /* non-zero = no saved state */
}

/* ================================================================
 * LOADER STUBS (boot_cmd.c)
 * ================================================================ */

/*
 * load_abs -- load a file at its linked address (the "-a" boot path).
 * Called by boot_cmd.c when the -a flag is used.
 * For IP54 we delegate to Execute() which parses the path and loads the file.
 */
LONG load_abs(CHAR *path, ULONG *execaddr)
{
    (void)execaddr;
    /* Cannot just load without executing on IP54 — delegate fully */
    return Execute(path, 0, (CHAR **)0, (CHAR **)0);
}

/*
 * exec_abs -- load and execute a file at its linked address.
 * Called by boot_cmd.c as the primary kernel-load path.
 */
LONG exec_abs(CHAR *path, LONG argc, CHAR *argv[], CHAR *envp[])
{
    return Execute(path, argc, argv, envp);
}

/*
 * rbclrbs -- clear a bit in the restart block boot-status word.
 * Called by boot_cmd.c to mark that a boot was initiated.
 * IP54 has no real restart block; this is a no-op.
 */
void rbclrbs(int flag)
{
    (void)flag;
}


/* ================================================================
 * ARCS SIGNAL HANDLER
 * ================================================================ */

/*
 * Signal -- install a signal handler, return previous handler.
 * Used by PROM code to set up ctrl-C handling (SIGINT).
 * We maintain a simple per-signal table and let the serial driver
 * call the SIGINT handler when ^C is received.
 */

static SIGNALHANDLER signal_table[NUM_SIG_TYPES + 1];

SIGNALHANDLER Signal(LONG sig, SIGNALHANDLER handler)
{
    SIGNALHANDLER prev;
    if (sig < 0 || sig > NUM_SIG_TYPES)
        return SIGDefault;
    prev = signal_table[sig];
    if (handler != SIGDefault)
        signal_table[sig] = handler;
    else
        signal_table[sig] = (SIGNALHANDLER)0;
    return prev;
}

/* ================================================================
 * DEVICE I/O CONTROL
 * ================================================================ */

/*
 * ioctl -- ARCS device I/O control.
 * Used for terminal width/mode queries. Return success for all requests;
 * most callers only check that ioctl returns 0 (success).
 */
LONG ioctl(ULONG fd, LONG cmd, LONG arg)
{
    (void)fd; (void)cmd; (void)arg;
    return ESUCCESS;
}

/*
 * isatty -- return non-zero if fd is a character (tty) device.
 * In the PROM context, fd 0 (stdin) and 1 (stdout) are the console tty.
 */
int isatty(ULONG fd)
{
    return (fd == 0 || fd == 1) ? 1 : 0;
}

/*
 * prcuroff -- print current file offset (for error messages in copy/dev).
 * We don't track byte offsets; print a placeholder.
 */
void prcuroff(ULONG fd)
{
    (void)fd;
    printf("current offset unavailable\n");
}

/*
 * sn0_getcpuid -- Origin/Octane CPU ID; always 0 on IP54.
 */
int sn0_getcpuid(void)
{
    return 0;
}

/* ================================================================
 * COMMAND STUBS (main.c command table references)
 * ================================================================ */

/*
 * Memory debug commands — referenced by main.c command table.
 * Point to readx for dump; stub the rest.
 */
int dump(int argc, char **argv, char **argp, struct cmd_table *ct)
{
    (void)argc; (void)argv; (void)argp; (void)ct;
    printf("[IP54] dump not implemented\n");
    return 0;
}

int fill(int argc, char **argv, char **argp, struct cmd_table *ct)
{
    (void)argc; (void)argv; (void)argp; (void)ct;
    printf("[IP54] fill not implemented\n");
    return 0;
}

int get(int argc, char **argv, char **argp, struct cmd_table *ct)
{
    (void)argc; (void)argv; (void)argp; (void)ct;
    printf("[IP54] get not implemented\n");
    return 0;
}

int put(int argc, char **argv, char **argp, struct cmd_table *ct)
{
    (void)argc; (void)argv; (void)argp; (void)ct;
    printf("[IP54] put not implemented\n");
    return 0;
}

/*
 * Misc commands referenced by main.c command table.
 */
int passwd_cmd(int argc, char **argv, char **argp, struct cmd_table *ct)
{
    (void)argc; (void)argv; (void)argp; (void)ct;
    printf("[IP54] passwd not implemented\n");
    return 0;
}

int play_cmd(int argc, char **argv, char **argp, struct cmd_table *ct)
{
    (void)argc; (void)argv; (void)argp; (void)ct;
    return 0;
}

int poweroff_cmd(int argc, char **argv, char **argp, struct cmd_table *ct)
{
    (void)argc; (void)argv; (void)argp; (void)ct;
    printf("[IP54] Powering off...\n");
    while (1); /* halt */
    return 0;
}

int reboot_cmd(int argc, char **argv, char **argp, struct cmd_table *ct)
{
    (void)argc; (void)argv; (void)argp; (void)ct;
    printf("[IP54] Rebooting...\n");
    /* Jump to reset vector */
    ((void (*)(void))0xBFC00000)();
    return 0;
}

int resetpw_cmd(int argc, char **argv, char **argp, struct cmd_table *ct)
{
    (void)argc; (void)argv; (void)argp; (void)ct;
    printf("[IP54] resetpw not implemented\n");
    return 0;
}
