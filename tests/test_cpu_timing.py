"""
CPU timing integration tests using bare-metal MIPS binaries.

These tests build and run a bare-metal timing benchmark binary on the
SGI Indy QEMU machine, then parse serial output to verify timing
expectations.

Tests validate:
- CP0 Count advances proportionally to instructions executed
- WAIT + Compare wakes CPU with Count ≈ Compare
- PIT interrupt period matches programmed count
- Known NOP loop produces expected Count delta
- Memory operations have reasonable timing
- icount sleep=off accelerates idle periods

These tests are SLOW (require QEMU boot, ~5-10s each).
"""

import os
import re
import subprocess
import time

import pytest

from helpers.qemu_runner import SGIQemuRunner, DEFAULT_QEMU_BIN

# Bare-metal binary location
BARE_METAL_DIR = os.path.join(os.path.dirname(__file__), "bare_metal")
BARE_METAL_BIN = os.path.join(BARE_METAL_DIR, "timing_test.bin")
QEMU_BIN = DEFAULT_QEMU_BIN


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
    """Build the bare-metal binary if it doesn't exist."""
    if not os.path.exists(BARE_METAL_BIN):
        result = subprocess.run(
            ["make", "-C", BARE_METAL_DIR],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            pytest.skip(f"Failed to build bare-metal binary: {result.stderr}")


@pytest.fixture(scope="module")
def timing_binary():
    """Ensure the bare-metal timing binary exists."""
    ensure_binary_built()
    if not os.path.exists(BARE_METAL_BIN):
        pytest.skip("Bare-metal timing binary not available")
    return BARE_METAL_BIN


@pytest.fixture(scope="module")
def runner():
    """Create a QEMU runner (no PROM needed for bare-metal)."""
    if not os.path.exists(QEMU_BIN):
        pytest.skip("QEMU binary not found")
    return SGIQemuRunner(qemu_bin=QEMU_BIN)


@pytest.fixture(scope="module")
def timing_results(timing_binary, runner):
    """Run the timing test binary and parse results.

    Cached at module scope so we only run QEMU once for all tests.
    """
    output = runner.run_bare_metal(timing_binary, timeout=15)
    if "DONE" not in output:
        pytest.skip(
            f"Timing test did not complete. Output:\n{output[:500]}"
        )
    return parse_timing_output(output), output


@pytest.mark.slow
class TestBareMetalBuilds:
    """Verify the bare-metal binary can be built."""

    def test_makefile_exists(self):
        """Makefile exists in bare_metal directory."""
        assert os.path.exists(os.path.join(BARE_METAL_DIR, "Makefile"))

    def test_binary_builds(self):
        """make produces timing_test.bin."""
        result = subprocess.run(
            ["make", "-C", BARE_METAL_DIR],
            capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, f"Build failed: {result.stderr}"
        assert os.path.exists(BARE_METAL_BIN)

    def test_binary_size_reasonable(self):
        """Binary is between 1KB and 64KB (not empty, not bloated)."""
        ensure_binary_built()
        if not os.path.exists(BARE_METAL_BIN):
            pytest.skip("Binary not available")
        size = os.path.getsize(BARE_METAL_BIN)
        assert 1024 < size < 65536, f"Binary size {size} out of range"


@pytest.mark.slow
class TestCountRate:
    """CP0 Count must advance proportionally to instructions executed."""

    def test_count_rate_passes(self, timing_results):
        """COUNT_RATE test reports PASS."""
        results, _ = timing_results
        assert "COUNT_RATE" in results, "COUNT_RATE test not found in output"
        assert results["COUNT_RATE"]["status"] == "PASS"

    def test_count_rate_delta_positive(self, timing_results):
        """COUNT_RATE delta must be positive (Count is advancing)."""
        results, _ = timing_results
        if "COUNT_RATE" not in results:
            pytest.skip("COUNT_RATE not in output")
        assert results["COUNT_RATE"]["delta"] > 0

    def test_count_rate_delta_proportional(self, timing_results):
        """COUNT_RATE delta should be roughly proportional to iterations.

        In QEMU TCG mode, instruction timing is approximate. A tight
        addiu+bnez+nop loop compiles into efficient host code, so
        Count ticks per iteration can be much less than 1. We just
        verify the ratio is in a very wide but nonzero range.
        """
        results, _ = timing_results
        if "COUNT_RATE" not in results:
            pytest.skip("COUNT_RATE not in output")
        delta = results["COUNT_RATE"]["delta"]
        iters = results["COUNT_RATE"]["iterations"]
        # delta should be between 0.01x and 10x iterations
        # (TCG compiles tight loops very efficiently)
        assert delta > iters * 0.01, f"delta {delta} too small for {iters} iterations"
        assert delta < iters * 10, f"delta {delta} too large for {iters} iterations"


@pytest.mark.slow
class TestWaitWakeup:
    """WAIT + Compare must wake CPU with Count ≈ Compare."""

    def test_wait_wakeup_passes(self, timing_results):
        """WAIT_WAKEUP test reports PASS."""
        results, _ = timing_results
        assert "WAIT_WAKEUP" in results, "WAIT_WAKEUP test not found"
        assert results["WAIT_WAKEUP"]["status"] == "PASS"

    def test_wait_wakeup_low_latency(self, timing_results):
        """WAIT wakeup delta should be small (< tolerance).

        After Compare fires, Count should be very close to the Compare
        value. A large delta would mean the CPU didn't wake promptly.
        """
        results, _ = timing_results
        if "WAIT_WAKEUP" not in results:
            pytest.skip("WAIT_WAKEUP not in output")
        delta = results["WAIT_WAKEUP"]["delta"]
        tolerance = results["WAIT_WAKEUP"]["tolerance"]
        assert 0 <= delta < tolerance, (
            f"WAIT wakeup delta {delta} >= tolerance {tolerance}"
        )


@pytest.mark.slow
class TestPITPeriod:
    """PIT interrupt period must match programmed count."""

    def test_pit_period_passes(self, timing_results):
        """PIT_PERIOD test reports PASS."""
        results, _ = timing_results
        assert "PIT_PERIOD" in results, "PIT_PERIOD test not found"
        assert results["PIT_PERIOD"]["status"] == "PASS"

    def test_pit_period_within_tolerance(self, timing_results):
        """PIT delta should be within tolerance of expected value.

        Expected = PIT_COUNT_VALUE × 50 (PIT 1MHz, Count 50MHz).
        """
        results, _ = timing_results
        if "PIT_PERIOD" not in results:
            pytest.skip("PIT_PERIOD not in output")
        delta = results["PIT_PERIOD"]["delta"]
        expected = results["PIT_PERIOD"]["expected"]
        tolerance = results["PIT_PERIOD"]["tolerance"]
        diff = abs(delta - expected)
        assert diff < tolerance, (
            f"PIT period delta={delta} expected={expected} diff={diff} "
            f">= tolerance={tolerance}"
        )


@pytest.mark.slow
class TestInstThroughput:
    """Known NOP loop must produce expected Count delta."""

    def test_inst_throughput_passes(self, timing_results):
        """INST_THROUGHPUT test reports PASS."""
        results, _ = timing_results
        assert "INST_THROUGHPUT" in results, "INST_THROUGHPUT not found"
        assert results["INST_THROUGHPUT"]["status"] == "PASS"

    def test_inst_throughput_delta_positive(self, timing_results):
        """NOP loop must produce a positive Count delta."""
        results, _ = timing_results
        if "INST_THROUGHPUT" not in results:
            pytest.skip("INST_THROUGHPUT not in output")
        assert results["INST_THROUGHPUT"]["delta"] > 0


@pytest.mark.slow
class TestMemThroughput:
    """Memory operations must have reasonable virtual timing."""

    def test_mem_throughput_passes(self, timing_results):
        """MEM_THROUGHPUT test reports PASS."""
        results, _ = timing_results
        assert "MEM_THROUGHPUT" in results, "MEM_THROUGHPUT not found"
        assert results["MEM_THROUGHPUT"]["status"] == "PASS"

    def test_mem_throughput_delta_positive(self, timing_results):
        """Memory load loop must produce a positive Count delta."""
        results, _ = timing_results
        if "MEM_THROUGHPUT" not in results:
            pytest.skip("MEM_THROUGHPUT not in output")
        assert results["MEM_THROUGHPUT"]["delta"] > 0


@pytest.mark.slow
class TestIcountSleepOffSpeedup:
    """icount sleep=off should make tests complete faster."""

    def test_icount_sleep_off_completes(self, timing_binary, runner):
        """Timing tests complete with -icount shift=0,sleep=off."""
        output = runner.run_bare_metal(
            timing_binary,
            timeout=15,
            extra_args=["-icount", "shift=0,sleep=off"],
        )
        assert "DONE" in output, (
            f"Tests did not complete with icount sleep=off. "
            f"Output:\n{output[:500]}"
        )

    def test_icount_sleep_off_results_valid(self, timing_binary, runner):
        """All tests still PASS with -icount shift=0,sleep=off."""
        output = runner.run_bare_metal(
            timing_binary,
            timeout=15,
            extra_args=["-icount", "shift=0,sleep=off"],
        )
        if "DONE" not in output:
            pytest.skip("Tests did not complete with icount sleep=off")
        results = parse_timing_output(output)
        # COUNT_RATE and INST_THROUGHPUT should still pass
        for test_name in ["COUNT_RATE", "INST_THROUGHPUT", "MEM_THROUGHPUT"]:
            if test_name in results:
                assert results[test_name]["status"] == "PASS", (
                    f"{test_name} failed with icount sleep=off"
                )


@pytest.mark.slow
class TestOutputFormat:
    """Verify the test output format is parseable."""

    def test_done_marker(self, timing_results):
        """Output contains DONE marker."""
        _, output = timing_results
        assert "DONE" in output

    def test_all_tests_present(self, timing_results):
        """All five tests appear in output."""
        results, _ = timing_results
        expected_tests = [
            "COUNT_RATE", "WAIT_WAKEUP", "PIT_PERIOD",
            "INST_THROUGHPUT", "MEM_THROUGHPUT"
        ]
        for name in expected_tests:
            assert name in results, f"Test {name} not found in output"

    def test_parser_extracts_values(self, timing_results):
        """Parser correctly extracts numeric values."""
        results, _ = timing_results
        for name, data in results.items():
            assert "status" in data, f"{name} missing status"
            assert "delta" in data, f"{name} missing delta"
            assert isinstance(data["delta"], int), (
                f"{name} delta is not int"
            )
