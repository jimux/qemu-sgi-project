/*
 * Bare-metal SCSI benchmarks for SGI Indy WD33C93 via HPC3.
 *
 * Loaded as PROM replacement (-bios), runs at reset vector 0xBFC00000.
 * Outputs results via Z85C30 UART (HPC3 Serial Port 1 = console).
 *
 * WD33C93 registers are accessed indirectly through HPC3:
 *   HPC3 base (kseg1):  0xBFB80000
 *   SCSI0 registers:    HPC3 + 0x40000 = 0xBFBC0000
 *     Addr port (word): 0xBFBC0000  (write: set register, read: ASR)
 *     Data port (word): 0xBFBC0004  (read/write selected register)
 *
 * CP0 Count is used for timing. With R4600 at 100MHz and CCRes=2,
 * Count increments at 50MHz (20ns per tick).
 *
 * Tests:
 *   RESET_TIMING      - CMD_RESET, measure ticks until ASR_INT set
 *   SELECT_TIMEOUT     - CMD_SELECT_ATN to absent target, measure timeout
 *   REG_WRITE_THRUPUT  - 100 addr+data register writes, measure ticks
 *   ASR_POLL_THRUPUT   - 1000 ASR reads, measure ticks
 */

    .set    noreorder
    .set    noat

/* ====== Constants ====== */

/* HPC3 serial port 1 (console) addresses in kseg1 */
#define SERIAL_CMD   0xBFBD9830
#define SERIAL_DATA  0xBFBD9834

/* WD33C93 via HPC3 SCSI0 (kseg1-mapped) */
#define WD_ADDR_PORT  0xBFBC0000
#define WD_DATA_PORT  0xBFBC0004

/* WD33C93 register addresses (indirect) */
#define WD_OWN_ID           0x00
#define WD_CONTROL          0x01
#define WD_TIMEOUT_PERIOD   0x02
#define WD_DESTINATION_ID   0x15
#define WD_COMMAND          0x18
#define WD_AUXILIARY_STATUS 0x1f

/* WD33C93 commands */
#define CMD_RESET           0x00
#define CMD_SELECT_ATN      0x06

/* ASR bits */
#define ASR_INT  0x80

/* CP0 register numbers */
#define C0_COUNT   $9
#define C0_STATUS  $12
#define C0_CAUSE   $13

/* Stack in kseg0 RAM */
#define STACK_TOP   0x88100000

/* Test parameters */
#define REG_WRITE_COUNT   100
#define ASR_POLL_COUNT    1000
#define POLL_TIMEOUT      10000000


/* ====== Reset vector entry ====== */

    .text
    .globl _start
_start:
    /* CPU starts at 0xBFC00000 in kernel mode, BEV=1, ERL=1 */

    /* Clear Status: no interrupts, no BEV, no ERL */
    li      $t0, 0
    mtc0    $t0, C0_STATUS
    nop
    nop

    /* Clear Cause */
    mtc0    $zero, C0_CAUSE
    nop

    /* Zero Count */
    mtc0    $zero, C0_COUNT
    nop

    /* Set up stack */
    li      $sp, STACK_TOP

    /* Run tests */
    jal     test_reset_timing
    nop

    jal     test_select_timeout
    nop

    jal     test_reg_write_thruput
    nop

    jal     test_asr_poll_thruput
    nop

    /* Print "DONE\n" */
    la      $a0, str_done
    jal     print_string
    nop

    /* Halt: infinite loop */
1:  b       1b
    nop


/* ====== Exception handler (unused but required at offset 0x380) ====== */

    .org    0x380
exception_handler_bev1:
    eret
    nop


/* ====== Helper: write WD33C93 register ====== */
/* $a0 = register address, $a1 = value */
wd_write_reg:
    li      $t9, WD_ADDR_PORT
    sw      $a0, 0($t9)         /* Set register address */
    nop
    nop
    li      $t9, WD_DATA_PORT
    sw      $a1, 0($t9)         /* Write data */
    nop
    nop
    jr      $ra
    nop


/* ====== Helper: read WD33C93 register ====== */
/* $a0 = register address. Returns value in $v0 */
wd_read_reg:
    li      $t9, WD_ADDR_PORT
    sw      $a0, 0($t9)         /* Set register address */
    nop
    nop
    li      $t9, WD_DATA_PORT
    lw      $v0, 0($t9)         /* Read data */
    nop
    andi    $v0, $v0, 0xFF      /* Only low 8 bits valid */
    jr      $ra
    nop


/* ====== Helper: read ASR (Auxiliary Status Register) ====== */
/* Returns ASR in $v0 */
wd_read_asr:
    li      $t9, WD_ADDR_PORT
    lw      $v0, 0($t9)         /* Read address port = ASR */
    nop
    andi    $v0, $v0, 0xFF
    jr      $ra
    nop


/* ====== Test: RESET_TIMING ====== */
/*
 * Write CMD_RESET, then poll ASR until INT bit set.
 * Measure CP0 Count ticks for reset completion.
 */
test_reset_timing:
    addiu   $sp, $sp, -16
    sw      $ra, 12($sp)
    sw      $s0, 8($sp)

    la      $a0, str_test_reset
    jal     print_string
    nop

    /* Read Count before reset */
    mfc0    $s0, C0_COUNT
    nop

    /* Write CMD_RESET to WD33C93 command register */
    li      $a0, WD_COMMAND
    li      $a1, CMD_RESET
    jal     wd_write_reg
    nop

    /* Poll ASR until INT bit is set */
    li      $t3, POLL_TIMEOUT
reset_poll:
    jal     wd_read_asr
    nop
    andi    $t0, $v0, ASR_INT
    bnez    $t0, reset_done
    nop
    addiu   $t3, $t3, -1
    bnez    $t3, reset_poll
    nop
    /* Timeout */
    b       reset_fail
    nop

reset_done:
    /* Read Count after */
    mfc0    $t1, C0_COUNT
    nop
    subu    $s0, $t1, $s0       /* delta = after - before */

    /* Print delta */
    la      $a0, str_delta_eq
    jal     print_string
    nop
    move    $a0, $s0
    jal     print_decimal
    nop

    la      $a0, str_pass
    jal     print_string
    nop
    b       reset_exit
    nop

reset_fail:
    la      $a0, str_fail
    jal     print_string
    nop

reset_exit:
    la      $a0, str_newline
    jal     print_string
    nop

    lw      $s0, 8($sp)
    lw      $ra, 12($sp)
    addiu   $sp, $sp, 16
    jr      $ra
    nop


/* ====== Test: SELECT_TIMEOUT ====== */
/*
 * Set DESTINATION_ID to target 7 (unused), write CMD_SELECT_ATN,
 * poll ASR until INT set. Should get SELECTION_TIMEOUT (0x42).
 */
test_select_timeout:
    addiu   $sp, $sp, -16
    sw      $ra, 12($sp)
    sw      $s0, 8($sp)

    la      $a0, str_test_select_timeout
    jal     print_string
    nop

    /* First read and discard any pending status from reset */
    li      $a0, 0x17           /* WD_SCSI_STATUS */
    jal     wd_read_reg
    nop

    /* Set timeout period to something reasonable */
    li      $a0, WD_TIMEOUT_PERIOD
    li      $a1, 0x20           /* Short timeout */
    jal     wd_write_reg
    nop

    /* Set destination ID to target 7 (no device there) */
    li      $a0, WD_DESTINATION_ID
    li      $a1, 7
    jal     wd_write_reg
    nop

    /* Read Count before select */
    mfc0    $s0, C0_COUNT
    nop

    /* Issue CMD_SELECT_ATN */
    li      $a0, WD_COMMAND
    li      $a1, CMD_SELECT_ATN
    jal     wd_write_reg
    nop

    /* Poll ASR until INT bit set */
    li      $t3, POLL_TIMEOUT
select_poll:
    jal     wd_read_asr
    nop
    andi    $t0, $v0, ASR_INT
    bnez    $t0, select_done
    nop
    addiu   $t3, $t3, -1
    bnez    $t3, select_poll
    nop
    /* Timeout */
    b       select_fail
    nop

select_done:
    /* Read Count after */
    mfc0    $t1, C0_COUNT
    nop
    subu    $s0, $t1, $s0

    /* Read SCSI status to verify it's SELECTION_TIMEOUT (0x42) */
    li      $a0, 0x17           /* WD_SCSI_STATUS */
    jal     wd_read_reg
    nop
    move    $t4, $v0            /* Save status */

    /* Print delta */
    la      $a0, str_delta_eq
    jal     print_string
    nop
    move    $a0, $s0
    jal     print_decimal
    nop

    /* Print status */
    la      $a0, str_status_eq
    jal     print_string
    nop
    move    $a0, $t4
    jal     print_decimal
    nop

    /* PASS if status == 0x42 */
    li      $t5, 0x42
    bne     $t4, $t5, select_fail
    nop

    la      $a0, str_pass
    jal     print_string
    nop
    b       select_exit
    nop

select_fail:
    la      $a0, str_fail
    jal     print_string
    nop

select_exit:
    la      $a0, str_newline
    jal     print_string
    nop

    lw      $s0, 8($sp)
    lw      $ra, 12($sp)
    addiu   $sp, $sp, 16
    jr      $ra
    nop


/* ====== Test: REG_WRITE_THRUPUT ====== */
/*
 * Write 100 addr+data register pairs in a tight loop.
 * Uses OWN_ID register (harmless to write repeatedly).
 */
test_reg_write_thruput:
    addiu   $sp, $sp, -16
    sw      $ra, 12($sp)
    sw      $s0, 8($sp)

    la      $a0, str_test_reg_write
    jal     print_string
    nop

    li      $t5, WD_ADDR_PORT
    li      $t6, WD_DATA_PORT

    /* Read Count before */
    mfc0    $s0, C0_COUNT
    nop

    /* 100 addr+data writes to OWN_ID register */
    li      $t3, REG_WRITE_COUNT
    li      $t7, WD_OWN_ID     /* Register address */
    li      $t8, 0x07           /* Value: SCSI ID 7 */
reg_write_loop:
    sw      $t7, 0($t5)         /* Write addr port */
    nop
    sw      $t8, 0($t6)         /* Write data port */
    nop
    addiu   $t3, $t3, -1
    bnez    $t3, reg_write_loop
    nop

    /* Read Count after */
    mfc0    $t1, C0_COUNT
    nop
    subu    $s0, $t1, $s0

    /* Print delta */
    la      $a0, str_delta_eq
    jal     print_string
    nop
    move    $a0, $s0
    jal     print_decimal
    nop

    la      $a0, str_iterations_eq
    jal     print_string
    nop
    li      $a0, REG_WRITE_COUNT
    jal     print_decimal
    nop

    /* PASS if delta > 0 */
    la      $a0, str_pass
    bgtz    $s0, 1f
    nop
    la      $a0, str_fail
1:
    jal     print_string
    nop

    la      $a0, str_newline
    jal     print_string
    nop

    lw      $s0, 8($sp)
    lw      $ra, 12($sp)
    addiu   $sp, $sp, 16
    jr      $ra
    nop


/* ====== Test: ASR_POLL_THRUPUT ====== */
/*
 * Read ASR (address port) 1000 times in a tight loop.
 * Measures MMIO read path throughput.
 */
test_asr_poll_thruput:
    addiu   $sp, $sp, -16
    sw      $ra, 12($sp)
    sw      $s0, 8($sp)

    la      $a0, str_test_asr_poll
    jal     print_string
    nop

    li      $t5, WD_ADDR_PORT

    /* Read Count before */
    mfc0    $s0, C0_COUNT
    nop

    /* 1000 ASR reads */
    li      $t3, ASR_POLL_COUNT
asr_poll_loop:
    lw      $t0, 0($t5)
    nop
    addiu   $t3, $t3, -1
    bnez    $t3, asr_poll_loop
    nop

    /* Read Count after */
    mfc0    $t1, C0_COUNT
    nop
    subu    $s0, $t1, $s0

    /* Print delta */
    la      $a0, str_delta_eq
    jal     print_string
    nop
    move    $a0, $s0
    jal     print_decimal
    nop

    la      $a0, str_iterations_eq
    jal     print_string
    nop
    li      $a0, ASR_POLL_COUNT
    jal     print_decimal
    nop

    /* PASS if delta > 0 */
    la      $a0, str_pass
    bgtz    $s0, 1f
    nop
    la      $a0, str_fail
1:
    jal     print_string
    nop

    la      $a0, str_newline
    jal     print_string
    nop

    lw      $s0, 8($sp)
    lw      $ra, 12($sp)
    addiu   $sp, $sp, 16
    jr      $ra
    nop


/* ====== I/O routines (same as timing_test.S) ====== */

putchar:
    li      $t9, SERIAL_DATA
    sb      $a0, 0($t9)
    jr      $ra
    nop

print_string:
    addiu   $sp, $sp, -8
    sw      $ra, 4($sp)
    move    $t7, $a0
1:  lbu     $a0, 0($t7)
    nop
    beqz    $a0, 2f
    nop
    jal     putchar
    nop
    addiu   $t7, $t7, 1
    b       1b
    nop
2:
    lw      $ra, 4($sp)
    addiu   $sp, $sp, 8
    jr      $ra
    nop

print_decimal:
    addiu   $sp, $sp, -32
    sw      $ra, 28($sp)

    /* Special case: 0 */
    bnez    $a0, 1f
    nop
    li      $a0, '0'
    jal     putchar
    nop
    b       print_dec_done
    nop

1:
    move    $t0, $a0
    addiu   $t1, $sp, 0
    li      $t2, 0

2:  beqz    $t0, 3f
    nop
    li      $t3, 10
    divu    $t0, $t3
    mfhi    $t4
    mflo    $t0
    addiu   $t4, $t4, '0'
    sb      $t4, 0($t1)
    addiu   $t1, $t1, 1
    addiu   $t2, $t2, 1
    b       2b
    nop

3:  addiu   $t1, $t1, -1
4:  blez    $t2, print_dec_done
    nop
    lbu     $a0, 0($t1)
    nop
    jal     putchar
    nop
    addiu   $t1, $t1, -1
    addiu   $t2, $t2, -1
    b       4b
    nop

print_dec_done:
    lw      $ra, 28($sp)
    addiu   $sp, $sp, 32
    jr      $ra
    nop


/* ====== String constants ====== */

    .section .rodata

str_test_reset:
    .asciiz "TEST RESET_TIMING: "
str_test_select_timeout:
    .asciiz "TEST SELECT_TIMEOUT: "
str_test_reg_write:
    .asciiz "TEST REG_WRITE_THRUPUT: "
str_test_asr_poll:
    .asciiz "TEST ASR_POLL_THRUPUT: "
str_done:
    .asciiz "DONE\n"
str_delta_eq:
    .asciiz "delta="
str_status_eq:
    .asciiz " status="
str_iterations_eq:
    .asciiz " iterations="
str_pass:
    .asciiz " PASS"
str_fail:
    .asciiz " FAIL"
str_newline:
    .asciiz "\n"
