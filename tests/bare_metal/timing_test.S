/*
 * Bare-metal MIPS timing benchmarks for SGI Indy emulation.
 *
 * Loaded as PROM replacement (-bios), runs at reset vector 0xBFC00000.
 * Outputs results via Z85C30 UART (HPC3 Serial Port 1 = console).
 *
 * Serial output addresses (kseg1-mapped):
 *   HPC3 base:    0xBFB80000
 *   Serial1 CMD:  0xBFBD9830  (RR0 bit 2 = TX Buffer Empty)
 *   Serial1 DATA: 0xBFBD9834
 *
 * CP0 registers used:
 *   Count    = $9,0   (increments at cpu_clock / CCRes)
 *   Compare  = $11,0  (fires IP7 when Count == Compare)
 *   Status   = $12,0  (interrupt enable, IM bits)
 *   Cause    = $13,0  (IP bits, exception code)
 *   EPC      = $14,0
 *
 * Tests:
 *   COUNT_RATE      - Tight loop, measure Count delta
 *   WAIT_WAKEUP     - WAIT instruction woken by Compare interrupt
 *   PIT_PERIOD      - PIT Timer 0 interrupt period in Count ticks
 *   INST_THROUGHPUT - 1000 NOPs, measure Count delta
 *   MEM_THROUGHPUT  - 1000 cached loads, measure Count delta
 */

    .set    noreorder
    .set    noat

/* ====== Constants ====== */

/* HPC3 serial port 1 (console) addresses in kseg1 */
#define SERIAL_CMD   0xBFBD9830
#define SERIAL_DATA  0xBFBD9834

/* HPC3 INT3 PIT registers in kseg1
 * HPC3 base = 0x1FB80000, PIT at offset 0x598B0
 * Physical = 0x1FBD98B0, kseg1 = 0xBFBD98B0 */
#define PIT_COUNTER0   0xBFBD98B0
#define PIT_CONTROL    0xBFBD98BC
#define PIT_TIMER_CLR  0xBFBD98A0

/* CP0 register numbers */
#define C0_COUNT    $9
#define C0_COMPARE  $11
#define C0_STATUS   $12
#define C0_CAUSE    $13
#define C0_EPC      $14

/* CP0 Status bits */
#define ST_IE       (1 << 0)
#define ST_EXL      (1 << 1)
#define ST_ERL      (1 << 2)
#define ST_BEV      (1 << 22)
#define ST_IM7      (1 << 15)
#define ST_IM4      (1 << 12)

/* CP0 Cause bits */
#define CA_IP7      (1 << 15)
#define CA_IP4      (1 << 12)

/* Test parameters */
#define COUNT_LOOP_ITERS  10000
#define WAIT_OFFSET       10000
#define WAIT_TOLERANCE    100000
#define PIT_COUNT_VALUE   10000
#define PIT_TOLERANCE     100000
#define NOP_COUNT         1000
#define MEM_LOAD_COUNT    1000

/* Stack in kseg0 RAM.
 * MC maps RAM at SEG0 (physical 0x08000000 for rev < 5).
 * kseg0 maps virtual 0x80000000 to physical 0x00000000,
 * so RAM at physical 0x08000000 is at kseg0 0x88000000. */
#define STACK_TOP   0x88100000

/* BSS variables in kseg0 RAM */
#define VAR_BASE          0x88090000
#define VAR_COMPARE_FIRED     (VAR_BASE + 0)
#define VAR_COMPARE_FIRE_CNT  (VAR_BASE + 4)
#define VAR_PIT_FIRE_CNT      (VAR_BASE + 8)
#define VAR_PIT_FIRE_CNT2     (VAR_BASE + 12)
#define VAR_PIT_FIRE_TOTAL    (VAR_BASE + 16)


/* ====== Reset vector entry ====== */

    .text
    .globl _start
_start:
    /*
     * Reset vector: 0xBFC00000
     * CPU starts in kernel mode, BEV=1, ERL=1
     */

    /* Clear BEV, ERL, EXL; keep IE disabled for now */
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

    /* Set up stack pointer in kseg0 RAM.
     * MC reset already maps bank 0 at SEG0 (physical 0x08000000). */
    li      $sp, STACK_TOP

    /* Clear BSS variables */
    li      $t0, VAR_BASE
    sw      $zero, 0($t0)
    sw      $zero, 4($t0)
    sw      $zero, 8($t0)
    sw      $zero, 12($t0)
    sw      $zero, 16($t0)

    /* Run tests */
    jal     test_count_rate
    nop

    jal     test_wait_wakeup
    nop

    jal     test_pit_period
    nop

    jal     test_inst_throughput
    nop

    jal     test_mem_throughput
    nop

    /* Print "DONE\n" */
    la      $a0, str_done
    jal     print_string
    nop

    /* Halt: infinite loop */
1:  b       1b
    nop


/* ====== Exception handler ====== */

    /*
     * With BEV=1, the general exception vector is at 0xBFC00380.
     * Pad to offset 0x380 from _start (0xBFC00000).
     */
    .org    0x380
exception_handler_bev1:
    /*
     * Minimal exception handler for Compare and PIT interrupts.
     * Sets global flag variables, clears cause, returns from exception.
     */

    /* Check if this is a Compare interrupt (IP7) */
    mfc0    $k0, C0_CAUSE
    nop
    andi    $k1, $k0, CA_IP7
    beqz    $k1, check_pit_irq
    nop

    /* Compare interrupt: ack by writing Compare = Count + large offset */
    mfc0    $k1, C0_COUNT
    nop
    addiu   $k1, $k1, 0x7FFF
    mtc0    $k1, C0_COMPARE
    nop

    /* Set compare_fired flag */
    li      $k1, VAR_COMPARE_FIRED
    li      $k0, 1
    sw      $k0, 0($k1)

    /* Store Count at time of Compare fire */
    mfc0    $k0, C0_COUNT
    nop
    li      $k1, VAR_COMPARE_FIRE_CNT
    sw      $k0, 0($k1)

    eret
    nop

check_pit_irq:
    /* Check if PIT Timer0 interrupt (IP4) */
    andi    $k1, $k0, CA_IP4
    beqz    $k1, exception_return
    nop

    /* PIT interrupt: record Count value */
    mfc0    $k0, C0_COUNT
    nop
    li      $k1, VAR_PIT_FIRE_CNT
    lw      $1, 0($k1)
    nop
    /* If first fire (pit_fire_count == 0), store in pit_fire_count */
    bnez    $1, pit_second_fire
    nop
    sw      $k0, 0($k1)
    b       pit_ack
    nop

pit_second_fire:
    /* Second fire: store in pit_fire_count2 */
    li      $k1, VAR_PIT_FIRE_CNT2
    sw      $k0, 0($k1)

pit_ack:
    /* Clear PIT Timer 0 interrupt in HPC3 (bit 0 of TIMER_CLEAR) */
    li      $k1, PIT_TIMER_CLR
    li      $k0, 1
    sw      $k0, 0($k1)
    nop

    /* Increment total fire count */
    li      $k1, VAR_PIT_FIRE_TOTAL
    lw      $k0, 0($k1)
    nop
    addiu   $k0, $k0, 1
    sw      $k0, 0($k1)

    /* Disable IP4 after second fire to prevent endless interrupts */
    li      $1, 2
    slt     $1, $k0, $1          /* $1 = (total < 2) */
    bnez    $1, exception_return
    nop

    /* total >= 2: disable IP4 in Status */
    mfc0    $k0, C0_STATUS
    nop
    li      $k1, ~ST_IM4
    and     $k0, $k0, $k1
    mtc0    $k0, C0_STATUS
    nop

exception_return:
    eret
    nop


/* ====== Test functions ====== */

/*
 * test_count_rate: Tight loop of N iterations, measure CP0 Count delta.
 * Expected: delta proportional to iterations x cycles_per_iteration.
 * With CCRes=2, Count increments every 2 cycles.
 */
test_count_rate:
    addiu   $sp, $sp, -16
    sw      $ra, 12($sp)
    sw      $s0, 8($sp)

    la      $a0, str_test_count_rate
    jal     print_string
    nop

    /* Read Count before loop */
    mfc0    $t0, C0_COUNT
    nop

    /* Tight loop: N iterations */
    li      $t2, COUNT_LOOP_ITERS
1:  addiu   $t2, $t2, -1
    bnez    $t2, 1b
    nop

    /* Read Count after loop */
    mfc0    $t1, C0_COUNT
    nop

    /* delta = t1 - t0 */
    subu    $s0, $t1, $t0

    /* Print "delta=" */
    la      $a0, str_delta_eq
    jal     print_string
    nop

    move    $a0, $s0
    jal     print_decimal
    nop

    /* Print " iterations=N" */
    la      $a0, str_iterations_eq
    jal     print_string
    nop

    li      $a0, COUNT_LOOP_ITERS
    jal     print_decimal
    nop

    /* PASS if delta > 0 (Count is advancing) */
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


/*
 * test_wait_wakeup: Set Compare = Count + offset, enable interrupts,
 * execute WAIT. After waking, verify Count ~ Compare.
 */
test_wait_wakeup:
    addiu   $sp, $sp, -16
    sw      $ra, 12($sp)
    sw      $s0, 8($sp)

    la      $a0, str_test_wait_wakeup
    jal     print_string
    nop

    /* Clear compare_fired flag */
    li      $t0, VAR_COMPARE_FIRED
    sw      $zero, 0($t0)

    /* Read current Count */
    mfc0    $t0, C0_COUNT
    nop

    /* Set Compare = Count + WAIT_OFFSET */
    addiu   $t1, $t0, WAIT_OFFSET
    mtc0    $t1, C0_COMPARE
    nop
    nop

    /* Save expected compare value */
    move    $s0, $t1

    /* Enable interrupts: BEV=1, IE=1, IM7=1 */
    li      $t0, (ST_BEV | ST_IE | ST_IM7)
    mtc0    $t0, C0_STATUS
    nop
    nop

    /* Execute WAIT - CPU halts until Compare interrupt */
    .word   0x42000020          /* WAIT instruction encoding */
    nop
    nop

    /* Disable interrupts */
    li      $t0, ST_BEV
    mtc0    $t0, C0_STATUS
    nop

    /* Read Count after wakeup */
    mfc0    $t2, C0_COUNT
    nop

    /* delta = Count_after - expected_compare */
    subu    $s0, $t2, $s0

    /* Print results */
    la      $a0, str_delta_eq
    jal     print_string
    nop

    move    $a0, $s0
    jal     print_decimal
    nop

    la      $a0, str_expected_eq
    jal     print_string
    nop

    li      $a0, WAIT_OFFSET
    jal     print_decimal
    nop

    la      $a0, str_tolerance_eq
    jal     print_string
    nop

    li      $a0, WAIT_TOLERANCE
    jal     print_decimal
    nop

    /* PASS if 0 <= delta < WAIT_TOLERANCE */
    bltz    $s0, wait_fail
    nop
    li      $t4, WAIT_TOLERANCE
    slt     $t4, $s0, $t4
    beqz    $t4, wait_fail
    nop

    la      $a0, str_pass
    b       wait_print_result
    nop
wait_fail:
    la      $a0, str_fail
wait_print_result:
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


/*
 * test_pit_period: Program PIT Timer 0, measure CP0 Count between
 * consecutive interrupts. Expected: count_delta = pit_count x 50
 * (because PIT runs at 1MHz, CP0 Count at 50MHz with 100MHz/CCRes=2).
 */
test_pit_period:
    addiu   $sp, $sp, -24
    sw      $ra, 20($sp)
    sw      $s0, 16($sp)
    sw      $s1, 12($sp)

    la      $a0, str_test_pit_period
    jal     print_string
    nop

    /* Clear PIT fire counters */
    li      $t0, VAR_PIT_FIRE_CNT
    sw      $zero, 0($t0)
    li      $t0, VAR_PIT_FIRE_CNT2
    sw      $zero, 0($t0)
    li      $t0, VAR_PIT_FIRE_TOTAL
    sw      $zero, 0($t0)

    /* Program PIT Timer 0:
     * Control word: channel 0, lo/hi byte, mode 2 (rate gen), binary
     * = 0x34
     * Use sw since HPC3 handler truncates to uint8_t anyway.
     */
    li      $t0, PIT_CONTROL
    li      $t1, 0x34
    sw      $t1, 0($t0)
    nop
    nop

    /* Load count value: low byte then high byte
     * The 8254 expects two sequential writes for lo/hi mode. */
    li      $t0, PIT_COUNTER0
    li      $t1, PIT_COUNT_VALUE
    andi    $t2, $t1, 0xFF
    sw      $t2, 0($t0)
    nop
    nop
    srl     $t2, $t1, 8
    andi    $t2, $t2, 0xFF
    sw      $t2, 0($t0)
    nop
    nop

    /* Enable interrupts: BEV=1, IE=1, IM4=1 (PIT Timer0 -> IP4) */
    li      $t0, (ST_BEV | ST_IE | ST_IM4)
    mtc0    $t0, C0_STATUS
    nop
    nop

    /* Wait for 2 PIT fires (spin on pit_fire_total) */
    li      $t0, VAR_PIT_FIRE_TOTAL
    li      $t4, 100000000
pit_wait_loop:
    lw      $t1, 0($t0)
    nop
    slti    $t2, $t1, 2
    beqz    $t2, pit_fires_done
    nop
    addiu   $t4, $t4, -1
    bnez    $t4, pit_wait_loop
    nop
    /* Timeout */
    b       pit_timeout
    nop

pit_fires_done:
    /* Disable interrupts */
    li      $t0, ST_BEV
    mtc0    $t0, C0_STATUS
    nop

    /* Calculate delta between fires */
    li      $t0, VAR_PIT_FIRE_CNT
    lw      $t1, 0($t0)
    nop
    li      $t0, VAR_PIT_FIRE_CNT2
    lw      $t2, 0($t0)
    nop
    subu    $s0, $t2, $t1       /* delta = fire2 - fire1 */

    /* Expected: PIT_COUNT_VALUE * 50
     * PIT at 1MHz, Count at 50MHz -> ratio = 50
     */
    li      $t5, PIT_COUNT_VALUE
    li      $t6, 50
    multu   $t5, $t6
    mflo    $s1                  /* expected = PIT_COUNT_VALUE * 50 */

    /* Print results */
    la      $a0, str_delta_eq
    jal     print_string
    nop

    move    $a0, $s0
    jal     print_decimal
    nop

    la      $a0, str_expected_eq
    jal     print_string
    nop

    move    $a0, $s1
    jal     print_decimal
    nop

    la      $a0, str_tolerance_eq
    jal     print_string
    nop

    li      $a0, PIT_TOLERANCE
    jal     print_decimal
    nop

    /* PASS if |delta - expected| < PIT_TOLERANCE */
    subu    $t4, $s0, $s1
    bgez    $t4, 1f
    nop
    negu    $t4, $t4
1:
    li      $t6, PIT_TOLERANCE
    slt     $t6, $t4, $t6
    beqz    $t6, pit_test_fail
    nop

    la      $a0, str_pass
    b       pit_print_result
    nop
pit_test_fail:
pit_timeout:
    la      $a0, str_fail
pit_print_result:
    jal     print_string
    nop

    la      $a0, str_newline
    jal     print_string
    nop

    lw      $s1, 12($sp)
    lw      $s0, 16($sp)
    lw      $ra, 20($sp)
    addiu   $sp, $sp, 24
    jr      $ra
    nop


/*
 * test_inst_throughput: Execute 1000 NOPs, measure CP0 Count delta.
 * With CCRes=2, each NOP = 1 cycle, so delta ~ 1000/2 = 500.
 */
test_inst_throughput:
    addiu   $sp, $sp, -16
    sw      $ra, 12($sp)
    sw      $s0, 8($sp)

    la      $a0, str_test_inst_throughput
    jal     print_string
    nop

    /* Read Count before */
    mfc0    $t0, C0_COUNT
    nop

    /* 1000 NOPs (in blocks of 10 for compactness) */
    .rept 100
    nop; nop; nop; nop; nop; nop; nop; nop; nop; nop
    .endr

    /* Read Count after */
    mfc0    $t1, C0_COUNT
    nop

    /* delta = t1 - t0 */
    subu    $s0, $t1, $t0

    /* Print results */
    la      $a0, str_delta_eq
    jal     print_string
    nop

    move    $a0, $s0
    jal     print_decimal
    nop

    la      $a0, str_expected_eq
    jal     print_string
    nop

    li      $a0, 500
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


/*
 * test_mem_throughput: N cached word loads, measure CP0 Count delta.
 */
test_mem_throughput:
    addiu   $sp, $sp, -16
    sw      $ra, 12($sp)
    sw      $s0, 8($sp)

    la      $a0, str_test_mem_throughput
    jal     print_string
    nop

    /* Use kseg0 RAM address for loads (in SEG0 region) */
    li      $t5, 0x88080000

    /* Read Count before */
    mfc0    $t0, C0_COUNT
    nop

    /* 1000 word loads */
    li      $t3, MEM_LOAD_COUNT
1:  lw      $t4, 0($t5)
    nop
    addiu   $t3, $t3, -1
    bnez    $t3, 1b
    nop

    /* Read Count after */
    mfc0    $t1, C0_COUNT
    nop

    subu    $s0, $t1, $t0

    /* Print results */
    la      $a0, str_delta_eq
    jal     print_string
    nop

    move    $a0, $s0
    jal     print_decimal
    nop

    la      $a0, str_iterations_eq
    jal     print_string
    nop

    li      $a0, MEM_LOAD_COUNT
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


/* ====== I/O routines ====== */

/*
 * putchar: Write a single byte to the serial console.
 * $a0 = character to write (low 8 bits)
 */
putchar:
    li      $t9, SERIAL_DATA
    sb      $a0, 0($t9)
    jr      $ra
    nop


/*
 * print_string: Print a null-terminated string.
 * $a0 = pointer to string
 */
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


/*
 * print_decimal: Print a 32-bit unsigned integer in decimal.
 * $a0 = value to print
 */
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
    /* Convert digits into stack buffer (reversed) */
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

3:  /* Print digits in reverse order */
    addiu   $t1, $t1, -1
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

str_test_count_rate:
    .asciiz "TEST COUNT_RATE: "
str_test_wait_wakeup:
    .asciiz "TEST WAIT_WAKEUP: "
str_test_pit_period:
    .asciiz "TEST PIT_PERIOD: "
str_test_inst_throughput:
    .asciiz "TEST INST_THROUGHPUT: "
str_test_mem_throughput:
    .asciiz "TEST MEM_THROUGHPUT: "
str_done:
    .asciiz "DONE\n"
str_delta_eq:
    .asciiz "delta="
str_expected_eq:
    .asciiz " expected="
str_iterations_eq:
    .asciiz " iterations="
str_tolerance_eq:
    .asciiz " tolerance="
str_pass:
    .asciiz " PASS"
str_fail:
    .asciiz " FAIL"
str_newline:
    .asciiz "\n"
