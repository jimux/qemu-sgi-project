"""MCP launch-builder: virtuix boots the IP55-native kernel by default.

[CROSS-REF] sgi_mcp/server.py _build_qemu_launch — the canonical promotion of
unix.ip55.g as the virtuix default kernel (with MTTCG + user net), overridable
via extra_args. Authentic indy must NOT inherit any of these.
"""
import shutil
import pytest

from sgi_mcp.server import _build_qemu_launch


def _launch(args):
    cmd, ser, mon, tmp, prom, err = _build_qemu_launch(args)
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    if err:
        pytest.skip(f"launch builder unavailable: {err}")
    return " ".join(cmd)


class TestVirtuixLaunchDefaults:
    def test_virtuix_defaults(self):
        s = _launch({"machine": "virtuix"})
        assert "-M virtuix" in s
        assert "-smp 4" in s, "virtuix should default to a multi-CPU SMP config"
        assert "-accel tcg,thread=multi" in s, "MTTCG enables real parallelism"
        assert "unix.ip55.g" in s, "virtuix must boot the IP55-native kernel"
        assert "-nic user" in s, "virtuix should default to user networking"

    def test_extra_args_override_suppresses_defaults(self):
        s = _launch({"machine": "virtuix",
                     "extra_args": "-smp 16 -kernel /custom/unix"})
        assert s.count(" -smp ") == 1, "no duplicate -smp when caller supplies one"
        assert "-smp 16" in s
        assert "unix.ip55.g" not in s, "auto -kernel suppressed when caller gives one"

    def test_custom_smp_and_kernel_args(self):
        s = _launch({"machine": "virtuix", "smp": 8})
        assert "-smp 8" in s

    def test_indy_unaffected(self):
        """[REGRESSION GUARD] indy gets none of the virtuix defaults."""
        s = _launch({"machine": "indy"})
        assert "-M indy" in s
        assert "unix.ip55.g" not in s, "indy must not auto-boot the IP55 kernel"
        assert "thread=multi" not in s, "indy stays single-threaded TCG"
