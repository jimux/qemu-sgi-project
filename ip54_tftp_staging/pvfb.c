/*
 * IP54 Paravirtual Framebuffer Driver (pvfb)
 *
 * Char device /dev/pvfb that exposes the sgi-glaccel framebuffer blitter.
 * Registers at PHYS_TO_K1(0x1F480300), 4-byte aligned, big-endian.
 *
 * Usage:
 *   open("/dev/pvfb")
 *   ioctl(PVFB_SET_MODE, &mode)  — set resolution/format, map FB
 *   mmap(0, fbsize, PROT_RW, MAP_SHARED, fd, 0) — map framebuffer
 *   ioctl(PVFB_FLIP)             — display current framebuffer contents
 *   close()
 *
 * See: include/hw/display/sgi_glaccel.h for register definitions.
 *
 * Copyright 1996-2024, Silicon Graphics, Inc. / QEMU IP54 project.
 */
#ident "$Revision: 1.0 $"

#if IP54

#include "sys/types.h"
#include "sys/param.h"
#include "sys/systm.h"
#include "sys/sysmacros.h"
#include "sys/cmn_err.h"
#include "sys/debug.h"
#include "sys/errno.h"
#include "sys/immu.h"
#include "sys/kmem.h"
#include "sys/mman.h"
#include "sys/sbd.h"
#include "sys/cpu.h"
#include "sys/conf.h"
#include "sys/cred.h"
#include "ksys/ddmap.h"

/*
 * sgi-glaccel register base (KSEG1 uncached).
 * Registers are 32-bit, big-endian, 4-byte aligned.
 */
#define GLACCEL_BASE        PHYS_TO_K1(0x1F480300ULL)
#define GLACCEL_REG(off)    (*(volatile __uint32_t *)(GLACCEL_BASE + (off)))

/* Register offsets (4-byte stride) */
#define GLACCEL_STATUS      0x00    /* read: STATUS bits */
#define GLACCEL_WIDTH       0x04    /* framebuffer width in pixels */
#define GLACCEL_HEIGHT      0x08    /* framebuffer height in pixels */
#define GLACCEL_CMD_BASE    0x0C    /* DMA command buffer physical base */
#define GLACCEL_CMD_LEN     0x10    /* DMA command buffer length */
#define GLACCEL_FB_BASE     0x14    /* framebuffer physical base address */
#define GLACCEL_EXEC        0x18    /* write: execution command */
#define GLACCEL_FORMAT      0x1C    /* pixel format */
#define GLACCEL_STRIDE      0x20    /* bytes per scanline (0 = width*bpp) */

/* EXEC commands */
#define GLACCEL_EXEC_RESET      (1 << 0)
#define GLACCEL_EXEC_PROCESS    (1 << 1)

/* Pixel formats */
#define GLACCEL_FMT_RGBA8888    0
#define GLACCEL_FMT_RGB565      1

/* STATUS bits */
#define GLACCEL_STATUS_DONE     (1 << 0)

/* User-visible ioctl numbers */
#define PVFB_SET_MODE   0x5000      /* arg: struct pvfb_mode * */
#define PVFB_FLIP       0x5001      /* arg: none */

/* Pixel bytes per format */
#define PVFB_BPP_RGBA8888   4
#define PVFB_BPP_RGB565     2

/* Maximum framebuffer dimensions */
#define PVFB_MAX_WIDTH      1920
#define PVFB_MAX_HEIGHT     1200
#define PVFB_MAX_BPP        4
#define PVFB_MAX_FBSIZE     (PVFB_MAX_WIDTH * PVFB_MAX_HEIGHT * PVFB_MAX_BPP)

/* ioctl argument for PVFB_SET_MODE */
struct pvfb_mode {
    __uint32_t width;
    __uint32_t height;
    __uint32_t format;      /* GLACCEL_FMT_* */
};

/* Per-open state */
struct pvfb_state {
    int           ps_open;      /* non-zero if device is open */
    __uint32_t    ps_width;
    __uint32_t    ps_height;
    __uint32_t    ps_format;
    __uint32_t    ps_bpp;
    size_t        ps_fbsize;    /* current framebuffer size in bytes */
    void         *ps_fbk1;     /* KSEG1 virtual pointer to framebuffer */
    __uint64_t    ps_fbphys;   /* physical address of framebuffer */
    uint          ps_fbpages;  /* pages allocated (for kvpfree) */
};

static struct pvfb_state pvfb_state;

int pvfbdevflag = 0;

/*
 * pvfb_open - open /dev/pvfb.
 * Resets the device and allocates the maximum-sized framebuffer.
 */
/* ARGSUSED */
int
pvfbopen(dev_t dev, int oflag, int otyp, cred_t *crp)
{
    struct pvfb_state *ps = &pvfb_state;
    void *k0buf;
    uint npages;

    if (ps->ps_open)
        return EBUSY;

    /* Reset the glaccel device */
    GLACCEL_REG(GLACCEL_EXEC) = GLACCEL_EXEC_RESET;

    /*
     * Pre-allocate framebuffer at maximum size using kvpalloc.
     * kvpalloc returns a page-aligned KSEG0 (direct-mapped) virtual address.
     */
    npages = btoc(PVFB_MAX_FBSIZE);
    k0buf  = kvpalloc(npages, VM_DIRECT | VM_NOSLEEP, 0);
    if (k0buf == NULL) {
        cmn_err(CE_WARN, "pvfb: cannot allocate %d pages for framebuffer",
                npages);
        return ENOMEM;
    }

    ps->ps_fbphys  = (__uint64_t)kvtophys((caddr_t)k0buf);
    ps->ps_fbk1    = (void *)PHYS_TO_K1(ps->ps_fbphys);
    ps->ps_fbpages = npages;
    ps->ps_fbsize  = ctob(npages);

    /* Default mode: 640x480 RGBA8888 */
    ps->ps_width   = 640;
    ps->ps_height  = 480;
    ps->ps_format  = GLACCEL_FMT_RGBA8888;
    ps->ps_bpp     = PVFB_BPP_RGBA8888;
    ps->ps_open    = 1;

    return 0;
}

/*
 * pvfb_close - close /dev/pvfb.
 * Resets the device and frees the framebuffer.
 */
/* ARGSUSED */
int
pvfbclose(dev_t dev, int oflag, int otyp, cred_t *crp)
{
    struct pvfb_state *ps = &pvfb_state;

    if (!ps->ps_open)
        return EINVAL;

    GLACCEL_REG(GLACCEL_EXEC) = GLACCEL_EXEC_RESET;

    if (ps->ps_fbk1 != NULL) {
        /* kvpfree takes a KSEG0 pointer, so convert back via physical */
        caddr_t k0 = (caddr_t)PHYS_TO_K0(ps->ps_fbphys);
        kvpfree(k0, ps->ps_fbpages);
        ps->ps_fbk1    = NULL;
        ps->ps_fbphys  = 0;
        ps->ps_fbpages = 0;
        ps->ps_fbsize  = 0;
    }

    ps->ps_open = 0;
    return 0;
}

/*
 * pvfb_ioctl - handle device control requests.
 *
 * PVFB_SET_MODE: configure resolution and pixel format, program device.
 * PVFB_FLIP:     tell device to display the current framebuffer.
 */
/* ARGSUSED */
int
pvfbioctl(dev_t dev, int cmd, caddr_t arg, int mode, cred_t *crp, int *rvalp)
{
    struct pvfb_state *ps = &pvfb_state;
    struct pvfb_mode   m;

    if (!ps->ps_open)
        return EINVAL;

    switch (cmd) {
    case PVFB_SET_MODE:
        if (copyin(arg, &m, sizeof(m)))
            return EFAULT;

        if (m.width == 0 || m.width > PVFB_MAX_WIDTH  ||
            m.height == 0 || m.height > PVFB_MAX_HEIGHT) {
            return EINVAL;
        }

        switch (m.format) {
        case GLACCEL_FMT_RGBA8888:
            ps->ps_bpp = PVFB_BPP_RGBA8888;
            break;
        case GLACCEL_FMT_RGB565:
            ps->ps_bpp = PVFB_BPP_RGB565;
            break;
        default:
            return EINVAL;
        }

        ps->ps_width  = m.width;
        ps->ps_height = m.height;
        ps->ps_format = m.format;

        /* Verify the requested mode fits our pre-allocated buffer */
        if (ps->ps_width * ps->ps_height * ps->ps_bpp > (uint)ps->ps_fbsize) {
            cmn_err(CE_WARN, "pvfb: mode %dx%d fmt %d exceeds buffer",
                    m.width, m.height, m.format);
            return ENOMEM;
        }

        /* Program the device */
        GLACCEL_REG(GLACCEL_WIDTH)   = ps->ps_width;
        GLACCEL_REG(GLACCEL_HEIGHT)  = ps->ps_height;
        GLACCEL_REG(GLACCEL_FORMAT)  = ps->ps_format;
        GLACCEL_REG(GLACCEL_STRIDE)  = 0;   /* auto: width * bpp */
        GLACCEL_REG(GLACCEL_FB_BASE) = (__uint32_t)(ps->ps_fbphys & 0xFFFFFFFF);
        GLACCEL_REG(GLACCEL_EXEC)    = GLACCEL_EXEC_PROCESS;
        break;

    case PVFB_FLIP:
        /* Trigger display update */
        GLACCEL_REG(GLACCEL_EXEC) = GLACCEL_EXEC_PROCESS;

        /* Optionally poll for completion (non-blocking in v1) */
        {
            int tries = 10000;
            while (tries-- > 0 &&
                   !(GLACCEL_REG(GLACCEL_STATUS) & GLACCEL_STATUS_DONE))
                ;
        }
        break;

    default:
        return EINVAL;
    }

    return 0;
}

/*
 * pvfb_map - called by the kernel when user does mmap() on /dev/pvfb.
 *
 * Maps the framebuffer pages into the user's address space.
 * v_mapphys() takes the KSEG1 (uncached) virtual address of the buffer
 * and sets up the page table entries for the user mapping.
 */
/* ARGSUSED */
int
pvfbmap(dev_t dev, vhandl_t *vt, off_t off, int len, int prot)
{
    struct pvfb_state *ps = &pvfb_state;

    if (!ps->ps_open || ps->ps_fbk1 == NULL)
        return EINVAL;

    if ((size_t)(off + len) > ps->ps_fbsize)
        return EINVAL;

    return v_mapphys(vt, (caddr_t)ps->ps_fbk1 + off, len);
}

#endif /* IP54 */
