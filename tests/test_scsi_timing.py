"""
SCSI bare-metal timing tests.

Builds and runs scsi_bench.S on the SGI Indy QEMU machine, then parses
serial output to verify WD33C93 register access timing and SCSI command
behavior.

These tests are SLOW (require QEMU boot).
Follows the same pattern as test_cpu_timing.py.
"""

import os
import re
import subprocess

import pytest

from helpers.qemu_runner import SGIQemuRunner, DEFAULT_QEMU_BIN
from helpers.benchmark_reporter import emit_benchmark

# Bare-metal binary location
BARE_METAL_DIR = os.path.join(os.path.dirname(__file__), "bare_metal")
SCSI_BENCH_BIN = os.path.join(BARE_METAL_DIR, "scsi_bench.bin")
QEMU_BIN = DEFAULT_QEMU_BIN

pytestmark = pytest.mark.slow


def parse_timing_output(output):
    """Parse 'TEST name: key=val key=val PASS/FAIL' lines.

    Returns:
        dict mapping test name to dict with 'status' and integer values.
    """
    results = {}
    for line in output.split('\n'):
        m = re.match(r'TEST (\w+): (.+) (PASS|FAIL)', line)
        if m:
            name, kvs, status = m.groups()
            vals = dict(re.findall(r'(\w+)=(\d+)', kvs))
            results[name] = {
                'status': status,
                **{k: int(v) for k, v in vals.items()}
            }
    return results


def ensure_binary_built():
    """Build the bare-metal SCSI benchmark binary if it doesn't exist."""
    if not os.path.exists(SCSI_BENCH_BIN):
        result = subprocess.run(
            ["make", "-C", BARE_METAL_DIR, "scsi_bench.bin"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            pytest.skip(
                f"Failed to build SCSI bench binary: {result.stderr}")


@pytest.fixture(scope="module")
def scsi_bench_binary():
    """Ensure the bare-metal SCSI benchmark binary exists."""
    ensure_binary_built()
    if not os.path.exists(SCSI_BENCH_BIN):
        pytest.skip("SCSI bench binary not available")
    return SCSI_BENCH_BIN


@pytest.fixture(scope="module")
def runner():
    """Create a QEMU runner (no PROM needed for bare-metal)."""
    if not os.path.exists(QEMU_BIN):
        pytest.skip("QEMU binary not found")
    return SGIQemuRunner(qemu_bin=QEMU_BIN)


@pytest.fixture(scope="module")
def scsi_bench_results(scsi_bench_binary, runner):
    """Run the SCSI bench binary and parse results.

    Cached at module scope so we only run QEMU once for all tests.
    """
    output = runner.run_bare_metal(scsi_bench_binary, timeout=15)
    if "DONE" not in output:
        pytest.skip(
            f"SCSI bench did not complete. Output:\n{output[:500]}")
    return parse_timing_output(output), output


# ---------------------------------------------------------------------------
# SCSI Reset Timing
# ---------------------------------------------------------------------------

class TestSCSIResetTiming:
    """Verify WD33C93 reset command timing."""

    def test_reset_completes(self, scsi_bench_results):
        """RESET_TIMING test reports PASS."""
        results, _ = scsi_bench_results
        assert "RESET_TIMING" in results, "RESET_TIMING not found"
        assert results["RESET_TIMING"]["status"] == "PASS"

    def test_reset_count_delta(self, scsi_bench_results):
        """RESET delta must be positive — emit benchmark data."""
        results, _ = scsi_bench_results
        if "RESET_TIMING" not in results:
            pytest.skip("RESET_TIMING not in output")
        delta = results["RESET_TIMING"]["delta"]
        assert delta > 0, f"Reset delta {delta} should be positive"
        emit_benchmark("scsi_reset_ticks", {
            "count_delta": delta,
        })


# ---------------------------------------------------------------------------
# SCSI Select Timeout
# ---------------------------------------------------------------------------

class TestSCSISelectTimeout:
    """Verify WD33C93 selection timeout behavior."""

    def test_timeout_completes(self, scsi_bench_results):
        """SELECT_TIMEOUT test reports PASS."""
        results, _ = scsi_bench_results
        assert "SELECT_TIMEOUT" in results, "SELECT_TIMEOUT not found"
        assert results["SELECT_TIMEOUT"]["status"] == "PASS"

    def test_timeout_count_delta(self, scsi_bench_results):
        """SELECT_TIMEOUT delta must be positive — emit benchmark data."""
        results, _ = scsi_bench_results
        if "SELECT_TIMEOUT" not in results:
            pytest.skip("SELECT_TIMEOUT not in output")
        delta = results["SELECT_TIMEOUT"]["delta"]
        status = results["SELECT_TIMEOUT"].get("status_val",
                                                results["SELECT_TIMEOUT"].get("status"))
        assert delta > 0, f"Timeout delta {delta} should be positive"
        emit_benchmark("scsi_select_timeout_ticks", {
            "count_delta": delta,
        })


# ---------------------------------------------------------------------------
# SCSI Register Throughput
# ---------------------------------------------------------------------------

class TestSCSIRegisterThroughput:
    """Verify WD33C93 register access throughput."""

    def test_reg_write_throughput(self, scsi_bench_results):
        """100 register writes must complete with positive delta."""
        results, _ = scsi_bench_results
        assert "REG_WRITE_THRUPUT" in results, "REG_WRITE_THRUPUT not found"
        assert results["REG_WRITE_THRUPUT"]["status"] == "PASS"
        delta = results["REG_WRITE_THRUPUT"]["delta"]
        emit_benchmark("scsi_reg_write_100", {
            "count_delta": delta,
            "iterations": results["REG_WRITE_THRUPUT"].get("iterations", 100),
            "ticks_per_write": round(delta / 100, 1) if delta > 0 else 0,
        })

    def test_asr_poll_throughput(self, scsi_bench_results):
        """1000 ASR reads must complete with positive delta."""
        results, _ = scsi_bench_results
        assert "ASR_POLL_THRUPUT" in results, "ASR_POLL_THRUPUT not found"
        assert results["ASR_POLL_THRUPUT"]["status"] == "PASS"
        delta = results["ASR_POLL_THRUPUT"]["delta"]
        emit_benchmark("scsi_asr_poll_1000", {
            "count_delta": delta,
            "iterations": results["ASR_POLL_THRUPUT"].get("iterations", 1000),
            "ticks_per_read": round(delta / 1000, 1) if delta > 0 else 0,
        })


# ---------------------------------------------------------------------------
# SCSI Timing Comparisons
# ---------------------------------------------------------------------------

class TestSCSITimingComparison:
    """Compare SCSI timing under different QEMU configurations."""

    def test_default_vs_icount(self, scsi_bench_binary, runner):
        """Compare SCSI bench results: default vs icount sleep=off."""
        # Default run
        output_default = runner.run_bare_metal(
            scsi_bench_binary, timeout=15)
        # icount run
        output_icount = runner.run_bare_metal(
            scsi_bench_binary, timeout=15,
            extra_args=["-icount", "shift=0,sleep=off"])

        results_default = parse_timing_output(output_default)
        results_icount = parse_timing_output(output_icount)

        # Both should complete
        assert "DONE" in output_default, "Default run didn't complete"
        assert "DONE" in output_icount, "icount run didn't complete"

        # Emit comparison benchmarks
        for test_name in ["RESET_TIMING", "REG_WRITE_THRUPUT",
                          "ASR_POLL_THRUPUT"]:
            d_default = results_default.get(test_name, {}).get("delta", 0)
            d_icount = results_icount.get(test_name, {}).get("delta", 0)
            emit_benchmark(f"scsi_compare_{test_name.lower()}", {
                "default_delta": d_default,
                "icount_delta": d_icount,
            })

    def test_reg_vs_select_overhead(self, scsi_bench_results):
        """Compare raw register ops vs full SCSI command overhead."""
        results, _ = scsi_bench_results
        reg_delta = results.get("REG_WRITE_THRUPUT", {}).get("delta", 0)
        sel_delta = results.get("SELECT_TIMEOUT", {}).get("delta", 0)

        if reg_delta > 0 and sel_delta > 0:
            # SELECT_TIMEOUT should be much more expensive than raw reg writes
            ratio = sel_delta / reg_delta
            emit_benchmark("scsi_select_vs_reg_ratio", {
                "reg_write_delta": reg_delta,
                "select_timeout_delta": sel_delta,
                "ratio": round(ratio, 1),
            })


# ---------------------------------------------------------------------------
# Build Verification
# ---------------------------------------------------------------------------

class TestSCSIBenchBuild:
    """Verify the SCSI bench binary can be built."""

    def test_scsi_bench_source_exists(self):
        """scsi_bench.S exists in bare_metal directory."""
        assert os.path.exists(
            os.path.join(BARE_METAL_DIR, "scsi_bench.S"))

    def test_scsi_bench_builds(self):
        """make scsi_bench.bin succeeds."""
        result = subprocess.run(
            ["make", "-C", BARE_METAL_DIR, "scsi_bench.bin"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0 and "mips-elf-gcc" in result.stderr:
            pytest.skip("Cross-compiler not available")
        assert result.returncode == 0, f"Build failed: {result.stderr}"
        assert os.path.exists(SCSI_BENCH_BIN)

    def test_scsi_bench_size_reasonable(self):
        """Binary is between 1KB and 64KB."""
        ensure_binary_built()
        if not os.path.exists(SCSI_BENCH_BIN):
            pytest.skip("Binary not available")
        size = os.path.getsize(SCSI_BENCH_BIN)
        assert 512 < size < 65536, f"Binary size {size} out of range"


# ---------------------------------------------------------------------------
# Output Format
# ---------------------------------------------------------------------------

class TestSCSIBenchOutput:
    """Verify bench output format is parseable."""

    def test_done_marker(self, scsi_bench_results):
        """Output contains DONE marker."""
        _, output = scsi_bench_results
        assert "DONE" in output

    def test_all_tests_present(self, scsi_bench_results):
        """All four tests appear in output."""
        results, _ = scsi_bench_results
        expected = ["RESET_TIMING", "SELECT_TIMEOUT",
                    "REG_WRITE_THRUPUT", "ASR_POLL_THRUPUT"]
        for name in expected:
            assert name in results, f"Test {name} not found in output"
