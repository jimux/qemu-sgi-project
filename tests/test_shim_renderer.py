"""Regression test for the dgld-shim renderer protocol (#63, VirGL Step 5/6 host side).

Feeds a synthetic shim W/G control stream (wininfo + the gltri GL command stream) and asserts
the renderer extracts the window screen rect AND renders the triangle. Requires PyOpenGL+OSMesa
+numpy (container only); skipped gracefully elsewhere.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

shim_renderer = pytest.importorskip("sgi_glremote.shim_renderer",
                                    reason="PyOpenGL/OSMesa not available")


def test_shim_wg_stream_renders_and_positions():
    assert shim_renderer.selftest() is True
