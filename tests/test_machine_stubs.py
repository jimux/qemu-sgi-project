"""
Machine-level stub and trampoline source assertions.

Verifies kernel boot trampoline encoding, MC semaphores and DMA registers,
ARCS hypercall dispatch layout, HPC3 ethernet stubs, and GIO empty slot
behavior.

These tests are FAST (source code analysis only, no QEMU boot).

Categories:
  - CROSS-REF: verified against MIPS ISA or ARCS spec
  - ASSUMPTION: documents stubs or simplifications
"""

import re


# ---------------------------------------------------------------------------
# Kernel Boot Trampoline [CROSS-REF: MIPS ISA]
# ---------------------------------------------------------------------------

class TestKernelTrampoline:
    """The kernel boot trampoline writes big-endian MIPS instructions
    to the PROM area. It must clear BEV/ERL, set up args, and jump
    to the kernel entry point.
    """

    def test_trampoline_clears_bev(self, indy_machine_source):
        """Trampoline must clear BEV (bit 22) in CP0_Status."""
        fn = re.search(
            r"write_kernel_trampoline\(.*?\{(.*?)^#undef",
            indy_machine_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "write_kernel_trampoline function not found"
        body = fn.group(1)
        # BEV is bit 22 — mask 0xFFBF (upper 16 bits clears bit 22)
        assert "0xFFBF" in body, "Must mask out BEV bit 22"

    def test_trampoline_clears_erl(self, indy_machine_source):
        """Trampoline must clear ERL (bit 2) in CP0_Status."""
        fn = re.search(
            r"write_kernel_trampoline\(.*?\{(.*?)^#undef",
            indy_machine_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "write_kernel_trampoline function not found"
        body = fn.group(1)
        # ERL is bit 2 — mask 0xFFFB (lower 16 bits clears bit 2)
        assert "0xFFFB" in body, "Must mask out ERL bit 2"

    def test_trampoline_sets_a0_zero(self, indy_machine_source):
        """Trampoline must set a0 = 0 (argc)."""
        fn = re.search(
            r"write_kernel_trampoline\(.*?\{(.*?)^#undef",
            indy_machine_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "write_kernel_trampoline function not found"
        body = fn.group(1)
        # MIPS_MOVE(A0, 0) — move a0, zero
        assert "MIPS_MOVE(A0, 0)" in body

    def test_trampoline_sets_a1_zero(self, indy_machine_source):
        """Trampoline must set a1 = 0 (argv)."""
        fn = re.search(
            r"write_kernel_trampoline\(.*?\{(.*?)^#undef",
            indy_machine_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "write_kernel_trampoline function not found"
        body = fn.group(1)
        assert "MIPS_MOVE(A1, 0)" in body

    def test_trampoline_sets_a2_environ(self, indy_machine_source):
        """Trampoline must set a2 = K0BASE + ARCS_ENVIRON_PHYS."""
        fn = re.search(
            r"write_kernel_trampoline\(.*?\{(.*?)^#undef",
            indy_machine_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "write_kernel_trampoline function not found"
        body = fn.group(1)
        assert "ARCS_ENVIRON_PHYS" in body

    def test_trampoline_sets_stack(self, indy_machine_source):
        """Trampoline must set SP to kseg0 stack address."""
        fn = re.search(
            r"write_kernel_trampoline\(.*?\{(.*?)^#undef",
            indy_machine_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "write_kernel_trampoline function not found"
        body = fn.group(1)
        # LUI SP, 0x8080 → sp = 0x80800000
        assert "MIPS_LUI(SP, 0x8080)" in body

    def test_trampoline_jumps_to_entry(self, indy_machine_source):
        """Trampoline must use jr to jump to kernel entry."""
        fn = re.search(
            r"write_kernel_trampoline\(.*?\{(.*?)^#undef",
            indy_machine_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "write_kernel_trampoline function not found"
        body = fn.group(1)
        assert "MIPS_JR(T0)" in body

    def test_trampoline_be_encoded(self, indy_machine_source):
        """Instructions must be big-endian encoded with cpu_to_be32."""
        fn = re.search(
            r"write_kernel_trampoline\(.*?\{(.*?)^#undef",
            indy_machine_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "write_kernel_trampoline function not found"
        body = fn.group(1)
        assert "cpu_to_be32" in body


# ---------------------------------------------------------------------------
# MC Semaphores [ASSUMPTION]
# ---------------------------------------------------------------------------

class TestMCSemaphores:
    """MC hardware semaphores at 0x10000-0x1f000.

    16 semaphores spaced at 0x1000 intervals. Read returns current
    value and then sets to 1 (locked). Write stores value.
    """

    def test_semaphore_count_16(self, mc_header):
        """Semaphore array must have 16 slots."""
        assert re.search(
            r"semaphore\[16\]",
            mc_header
        )

    def test_semaphore_stride(self, mc_header):
        """MC_SEMAPHORE_STRIDE must be 0x1000."""
        assert re.search(
            r"#define\s+MC_SEMAPHORE_STRIDE\s+0x1000",
            mc_header
        )

    def test_semaphore_read_then_lock(self, mc_source):
        """Semaphore read must return current value, then set to 1."""
        # Find the semaphore handling in the read function
        assert re.search(
            r"semaphore\[idx\].*?semaphore\[idx\]\s*=\s*1",
            mc_source, re.DOTALL
        )


# ---------------------------------------------------------------------------
# MC DMA Registers [ASSUMPTION]
# ---------------------------------------------------------------------------

class TestMCDMARegisters:
    """MC DMA registers are stubbed — DMA is not fully implemented."""

    def test_dma_run_clears_after_read(self, mc_source):
        """dma_run must be cleared to 0 after read (instant completion)."""
        fn = re.search(
            r"sgi_mc_read\(.*?\{(.*?)^\}",
            mc_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "sgi_mc_read function not found"
        body = fn.group(1)
        # DMA_RUN read should clear after returning
        assert re.search(r"MC_DMA_RUN.*?dma_run\s*=\s*0", body, re.DOTALL)

    def test_dma_tlb_4_entries(self, mc_header):
        """DMA TLB must have 4 hi/lo pairs."""
        assert re.search(r"dma_tlb_hi\[4\]", mc_header)
        assert re.search(r"dma_tlb_lo\[4\]", mc_header)

    def test_dma_start_sets_run_0x40(self, mc_source):
        """MC_DMA_START write must set dma_run = 0x40."""
        fn = re.search(
            r"sgi_mc_write\(.*?\{(.*?)^\}",
            mc_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "sgi_mc_write function not found"
        body = fn.group(1)
        assert re.search(r"MC_DMA_START.*?dma_run\s*=\s*0x40", body, re.DOTALL)

    def test_dma_gio_addr_start_sets_run(self, mc_source):
        """MC_DMA_GIO_ADDR_START write must also set dma_run = 0x40."""
        fn = re.search(
            r"sgi_mc_write\(.*?\{(.*?)^\}",
            mc_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "sgi_mc_write function not found"
        body = fn.group(1)
        assert re.search(r"MC_DMA_GIO_ADDR_START.*?dma_run\s*=\s*0x40",
                         body, re.DOTALL)

    def test_mc_dma_todo_exists(self, mc_source):
        """DMA_RUN write must have a TODO comment (known stub)."""
        assert re.search(
            r"MC_DMA_RUN.*?TODO.*DMA",
            mc_source, re.DOTALL
        )


# ---------------------------------------------------------------------------
# MC Watchdog and EEPROM [ASSUMPTION]
# ---------------------------------------------------------------------------

class TestMCWatchdogEEPROM:
    """Watchdog and EEPROM registers exist as stubs."""

    def test_watchdog_register_exists(self, mc_header):
        """MC_WATCHDOG must be defined."""
        assert re.search(
            r"#define\s+MC_WATCHDOG\s+0x0010",
            mc_header
        )

    def test_eeprom_register_exists(self, mc_header):
        """MC_EEPROM must be defined."""
        assert re.search(
            r"#define\s+MC_EEPROM\s+0x0030",
            mc_header
        )

    def test_watchdog_in_read_handler(self, mc_source):
        """MC_WATCHDOG must appear in the read handler."""
        fn = re.search(
            r"sgi_mc_read\(.*?\{(.*?)^\}",
            mc_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "sgi_mc_read function not found"
        assert "MC_WATCHDOG" in fn.group(1)

    def test_eeprom_in_read_handler(self, mc_source):
        """MC_EEPROM must appear in the read handler."""
        fn = re.search(
            r"sgi_mc_read\(.*?\{(.*?)^\}",
            mc_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "sgi_mc_read function not found"
        assert "MC_EEPROM" in fn.group(1)


# ---------------------------------------------------------------------------
# ARCS Hypercall Dispatch [CROSS-REF: IRIX ARCS spec]
# ---------------------------------------------------------------------------

class TestARCSHypercallDispatch:
    """ARCS firmware vector layout and physical address constants."""

    def test_arcs_fn_count_35(self, arcs_header):
        """ARCS_FN_COUNT must be 35 (FirmwareVector has 35 slots)."""
        # ARCS_FN_COUNT is the last enum before PFN entries
        assert "ARCS_FN_COUNT" in arcs_header
        # Verify ARCS_FN_FLUSHALLCACHES is entry 34 (0-indexed)
        assert re.search(
            r"ARCS_FN_FLUSHALLCACHES.*ARCS_FN_COUNT",
            arcs_header, re.DOTALL
        )

    def test_arcs_pfn_total_48(self, arcs_header):
        """ARCS_PFN_TOTAL_COUNT must be 48 (35 FV + 13 PV)."""
        assert re.search(
            r"ARCS_PFN_TOTAL_COUNT\s*/\*\s*48",
            arcs_header
        )

    def test_arcs_fw_vector_at_0x1080(self, arcs_header):
        """ARCS_FV_PHYS must be 0x00001080."""
        assert re.search(
            r"#define\s+ARCS_FV_PHYS\s+0x00001080",
            arcs_header
        )

    def test_arcs_priv_vector_at_0x1110(self, arcs_header):
        """ARCS_PV_PHYS must be 0x00001110."""
        assert re.search(
            r"#define\s+ARCS_PV_PHYS\s+0x00001110",
            arcs_header
        )

    def test_arcs_stubs_at_0x1600(self, arcs_header):
        """ARCS_STUBS_PHYS must be 0x00001600."""
        assert re.search(
            r"#define\s+ARCS_STUBS_PHYS\s+0x00001600",
            arcs_header
        )

    def test_arcs_environ_below_0x2000(self, arcs_header):
        """ARCS_ENVSTRS_PHYS + ARCS_ENVSTRS_SIZE must be < 0x2000.

        Physical 0x2000+ aliases to kernel LOAD segment and would
        get overwritten.
        """
        envstrs_match = re.search(
            r"#define\s+ARCS_ENVSTRS_PHYS\s+(0x[0-9a-fA-F]+)", arcs_header
        )
        size_match = re.search(
            r"#define\s+ARCS_ENVSTRS_SIZE\s+(\d+)", arcs_header
        )
        assert envstrs_match and size_match
        envstrs_phys = int(envstrs_match.group(1), 16)
        envstrs_size = int(size_match.group(1))
        assert envstrs_phys + envstrs_size <= 0x2000, (
            f"ARCS_ENVSTRS_PHYS (0x{envstrs_phys:x}) + "
            f"ARCS_ENVSTRS_SIZE ({envstrs_size}) = "
            f"0x{envstrs_phys + envstrs_size:x} exceeds 0x2000"
        )

    def test_arcs_spb_at_0x1000(self, arcs_header):
        """ARCS_SPB_PHYS must be 0x00001000."""
        assert re.search(
            r"#define\s+ARCS_SPB_PHYS\s+0x00001000",
            arcs_header
        )

    def test_arcs_spb_magic(self, arcs_header):
        """SPB magic must be 0x53435241 ('ARCS' in big-endian)."""
        assert re.search(
            r"#define\s+ARCS_SPB_MAGIC\s+0x53435241",
            arcs_header
        )


# ---------------------------------------------------------------------------
# HPC3 Ethernet Stubs [ASSUMPTION]
# ---------------------------------------------------------------------------

class TestHPC3EthernetStub:
    """HPC3 ethernet DMA register offsets must be defined.

    The ethernet controller (SEEQ 8003/8020) is not yet implemented,
    but register offsets are defined and store values to prevent
    bus errors during PROM probing.
    """

    def test_enet_rx_reg_offset(self, hpc3_header):
        """HPC3_ENET_RX_CBP must be defined."""
        assert re.search(
            r"#define\s+HPC3_ENET_RX_CBP\s+0x14000",
            hpc3_header
        )

    def test_enet_tx_reg_offset(self, hpc3_header):
        """HPC3_ENET_TX_CBP must be defined."""
        assert re.search(
            r"#define\s+HPC3_ENET_TX_CBP\s+0x16000",
            hpc3_header
        )

    def test_enet_misc_offset(self, hpc3_header):
        """HPC3_ENET_MISC must be defined."""
        assert re.search(
            r"#define\s+HPC3_ENET_MISC\s+0x15014",
            hpc3_header
        )

    def test_enet_seeq_register(self, hpc3_header):
        """HPC3_ENET_REG (SEEQ PIO) must be at 0x54000."""
        assert re.search(
            r"#define\s+HPC3_ENET_REG\s+0x54000",
            hpc3_header
        )


# ---------------------------------------------------------------------------
# GIO Empty Slot Behavior [ASSUMPTION]
# ---------------------------------------------------------------------------

class TestGIOEmptySlot:
    """Empty GIO slots return 0xffffffff on reads to signal no device.

    On real hardware, empty slots cause bus timeout/error. We return
    all-ones which is what an undriven bus reads as.
    """

    def test_empty_slot_read_returns_ff(self, indy_machine_source):
        """gio_empty_slot_read must return 0xffffffff."""
        fn = re.search(
            r"gio_empty_slot_read\(.*?\{(.*?)^\}",
            indy_machine_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "gio_empty_slot_read function not found"
        body = fn.group(1)
        # Returns all-ones (64-bit pattern since return type is uint64_t)
        assert re.search(r"0xf{16}", body)

    def test_empty_slot_write_ignored(self, indy_machine_source):
        """gio_empty_slot_write must exist (writes are logged but no-op)."""
        assert re.search(
            r"static\s+void\s+gio_empty_slot_write\s*\(",
            indy_machine_source
        )

    def test_gio_exp0_slot_created(self, indy_machine_source):
        """GIO expansion slot 0 must be created as empty."""
        assert re.search(
            r'create_gio_empty_slot.*"gio-exp0".*SGI_GIO_EXP0_BASE',
            indy_machine_source, re.DOTALL
        )

    def test_gio_exp1_slot_created(self, indy_machine_source):
        """GIO expansion slot 1 must be created as empty."""
        assert re.search(
            r'create_gio_empty_slot.*"gio-exp1".*SGI_GIO_EXP1_BASE',
            indy_machine_source, re.DOTALL
        )


# ---------------------------------------------------------------------------
# MC Register Size [CROSS-REF]
# ---------------------------------------------------------------------------

class TestMCRegisterRegion:
    """MC MMIO region must cover semaphores at 0x10000+."""

    def test_mc_reg_size_0x20000(self, mc_header):
        """MC_REG_SIZE must be 0x20000 (128KB to cover semaphores)."""
        assert re.search(
            r"#define\s+MC_REG_SIZE\s+0x20000",
            mc_header
        )

    def test_semaphore_base_0x10000(self, mc_header):
        """MC_SEMAPHORE_BASE must be 0x10000."""
        assert re.search(
            r"#define\s+MC_SEMAPHORE_BASE\s+0x10000",
            mc_header
        )


# ---------------------------------------------------------------------------
# ARCS Environment Variables [CROSS-REF]
# ---------------------------------------------------------------------------

class TestARCSEnvironment:
    """ARCS environment variable setup for kernel boot."""

    def test_arcs_has_cpufreq(self, arcs_source):
        """ARCS environment must include cpufreq."""
        assert re.search(
            r'"cpufreq".*"175"',
            arcs_source, re.DOTALL
        )

    def test_arcs_has_console(self, arcs_source):
        """ARCS environment must include console='d' (serial)."""
        assert re.search(
            r'"console".*"d"',
            arcs_source, re.DOTALL
        )

    def test_arcs_has_eaddr(self, arcs_source):
        """ARCS environment must include an ethernet address."""
        assert re.search(
            r'"eaddr".*"08:00:69',
            arcs_source, re.DOTALL
        )


# ---------------------------------------------------------------------------
# Machine Memory Map Constants [CROSS-REF]
# ---------------------------------------------------------------------------

class TestMachineMemoryMap:
    """Critical memory map addresses defined in sgi_indy.c."""

    def test_ram_low_base(self, indy_machine_source):
        """SGI_RAM_LOW_BASE must be 0x08000000."""
        assert re.search(
            r"#define\s+SGI_RAM_LOW_BASE\s+0x08000000",
            indy_machine_source
        )

    def test_mc_base(self, indy_machine_source):
        """SGI_MC_BASE must be 0x1fa00000."""
        assert re.search(
            r"#define\s+SGI_MC_BASE\s+0x1fa00000",
            indy_machine_source
        )

    def test_hpc3_base(self, indy_machine_source):
        """SGI_HPC3_BASE must be 0x1fb80000."""
        assert re.search(
            r"#define\s+SGI_HPC3_BASE\s+0x1fb80000",
            indy_machine_source
        )

    def test_prom_base(self, indy_machine_source):
        """SGI_PROM_BASE must be 0x1fc00000."""
        assert re.search(
            r"#define\s+SGI_PROM_BASE\s+0x1fc00000",
            indy_machine_source
        )
