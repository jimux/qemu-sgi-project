"""Regression test for the OSMesa render backend + IRIS GL->OpenGL translation (#57/#58).

Renders the gltri command stream (winopen, ortho2, RGBcolor, clear, bgnpolygon/v2f/endpolygon)
and asserts a red triangle on black. Requires PyOpenGL+OSMesa+numpy (container only); skipped
gracefully elsewhere.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

osmesa_backend = pytest.importorskip("sgi_glremote.osmesa_backend",
                                     reason="PyOpenGL/OSMesa not available")


def test_gltri_renders_red_triangle(tmp_path):
    out = str(tmp_path / "tri.ppm")
    assert osmesa_backend.selftest(out=out) is True
    assert os.path.exists(out)
