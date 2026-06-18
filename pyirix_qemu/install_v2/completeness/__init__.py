"""Completeness manifest + verifier for IRIX installs.

See manifest_6_5_5.yaml for the contract — entries declare what MUST exist
on a fresh install. check.py walks the manifest against either a mounted
disk image (HostBackend) or a booted guest (GuestBackend) and reports
pass/fail per entry.

The orchestrator treats manifest failure as install failure. "Did inst
succeed?" is not enough — `inst` happily skips packages on prereq cascade.
"""

from .check import Backend, HostBackend, GuestBackend, Report, CheckResult, verify, load_manifest

__all__ = ["Backend", "HostBackend", "GuestBackend", "Report",
           "CheckResult", "verify", "load_manifest"]
