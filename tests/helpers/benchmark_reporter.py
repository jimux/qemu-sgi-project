"""
Structured benchmark output for SGI QEMU performance tests.

Emits BENCHMARK: lines with JSON payloads that can be parsed by test code
or CI systems for tracking performance over time.
"""

import json
import re
import time


def emit_benchmark(name, metrics):
    """Print a structured benchmark result line.

    Args:
        name: Benchmark name (e.g. "prom_boot_default")
        metrics: Dict of metric name → value (e.g. {"elapsed_seconds": 12.3})

    The output format is:
        BENCHMARK: {"name": "...", "metrics": {...}, "timestamp": "..."}
    """
    record = {
        "name": name,
        "metrics": metrics,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    print(f"BENCHMARK: {json.dumps(record)}")


def parse_benchmarks(output):
    """Parse BENCHMARK: lines from text output.

    Args:
        output: Multi-line string containing BENCHMARK: lines

    Returns:
        Dict mapping benchmark name → metrics dict.
    """
    results = {}
    for line in output.split('\n'):
        m = re.match(r'BENCHMARK:\s*(\{.*\})', line)
        if m:
            try:
                record = json.loads(m.group(1))
                name = record.get("name", "unknown")
                results[name] = record.get("metrics", {})
            except json.JSONDecodeError:
                continue
    return results


def compare_benchmarks(baseline, current, key="elapsed_seconds"):
    """Print a comparison table between two benchmark result sets.

    Args:
        baseline: Dict from parse_benchmarks (baseline run)
        current: Dict from parse_benchmarks (current run)
        key: Metric key to compare (default: elapsed_seconds)
    """
    all_names = sorted(set(list(baseline.keys()) + list(current.keys())))
    if not all_names:
        print("No benchmarks to compare.")
        return

    print(f"\n{'Benchmark':<40} {'Baseline':>10} {'Current':>10} {'Delta':>10} {'%':>8}")
    print("-" * 80)

    for name in all_names:
        base_val = baseline.get(name, {}).get(key)
        curr_val = current.get(name, {}).get(key)

        base_str = f"{base_val:.2f}" if base_val is not None else "N/A"
        curr_str = f"{curr_val:.2f}" if curr_val is not None else "N/A"

        if base_val is not None and curr_val is not None and base_val > 0:
            delta = curr_val - base_val
            pct = (delta / base_val) * 100
            delta_str = f"{delta:+.2f}"
            pct_str = f"{pct:+.1f}%"
        else:
            delta_str = "N/A"
            pct_str = "N/A"

        print(f"{name:<40} {base_str:>10} {curr_str:>10} {delta_str:>10} {pct_str:>8}")
