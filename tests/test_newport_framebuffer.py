"""
Newport framebuffer rendering tests.

These tests boot the PROM, capture the framebuffer via QOM fb-dump,
and verify the expected blue gradient background is rendered correctly.
This validates the full rendering pipeline: REX3 drawing -> VRAM ->
DID/XMAP mode table -> CMAP palette lookup -> RAMDAC LUT -> PPM output.

The PROM gradient is a good first target because it's deterministic
(same every boot), fills nearly the entire screen, and exercises the
CI (color index) palette path that is also used by IRIX Xsgi.

Marked as slow because PROM boot takes ~30s.
"""

import os
import pytest
import tempfile

from helpers.qemu_runner import SGIQemuRunner
from helpers.image_compare import load_reference, save_reference, pixel_diff_percentage

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def prom_framebuffer():
    """Boot PROM, capture framebuffer, return PIL Image.

    This fixture is module-scoped so PROM only boots once for all tests.
    """
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")

    runner = SGIQemuRunner()
    if not runner.prom_path:
        pytest.skip("No PROM image found")
    if not os.path.exists(runner.qemu_bin):
        pytest.skip(f"QEMU binary not found: {runner.qemu_bin}")

    ppm_path = tempfile.mktemp(suffix='.ppm')
    try:
        runner.boot_prom_background(timeout=45)
        img = runner.capture_framebuffer(ppm_path)
        yield img
    except TimeoutError as e:
        pytest.skip(f"PROM boot timed out: {e}")
    finally:
        runner.cleanup()
        if os.path.exists(ppm_path):
            os.unlink(ppm_path)


class TestPROMGradient:
    """PROM should render a blue gradient background."""

    def test_image_dimensions(self, prom_framebuffer):
        """Framebuffer should be 1280x1024 (Newport standard resolution)."""
        assert prom_framebuffer.size == (1280, 1024)

    def test_not_all_black(self, prom_framebuffer):
        """At least 90% of pixels should be non-black.

        The PROM gradient fills almost the entire screen. If all or most
        pixels are black, the CMAP palette is broken (wrong indices).
        """
        pixels = list(prom_framebuffer.getdata())
        black_count = sum(1 for p in pixels if p == (0, 0, 0))
        total = len(pixels)
        assert black_count / total < 0.10, (
            f"{black_count}/{total} pixels are black "
            f"({100 * black_count / total:.1f}%)"
        )

    def test_top_is_light_blue(self, prom_framebuffer):
        """Row 1 should be light blue (high blue channel).

        The PROM gradient goes from light blue at top to dark blue at bottom.
        """
        r, g, b = prom_framebuffer.getpixel((640, 1))
        assert b > 200, f"Top blue channel too low: {b} (pixel={r},{g},{b})"
        assert r > 100, f"Top red channel too low: {r} (pixel={r},{g},{b})"
        assert b > r, f"Blue should dominate at top (r={r}, b={b})"

    def test_bottom_is_dark_blue(self, prom_framebuffer):
        """Row 1023 should be dark blue (lower blue channel)."""
        r, g, b = prom_framebuffer.getpixel((640, 1023))
        assert b < 200, f"Bottom blue too bright: {b} (pixel={r},{g},{b})"
        assert b > 80, f"Bottom blue too dark: {b} (pixel={r},{g},{b})"

    def test_vertical_gradient(self, prom_framebuffer):
        """Blue channel should decrease from top to bottom."""
        top_b = prom_framebuffer.getpixel((640, 1))[2]
        mid_b = prom_framebuffer.getpixel((640, 512))[2]
        bot_b = prom_framebuffer.getpixel((640, 1023))[2]
        assert top_b > mid_b > bot_b, (
            f"Not a gradient: top={top_b} mid={mid_b} bot={bot_b}"
        )

    def test_horizontal_uniformity(self, prom_framebuffer):
        """Each row should be a single color (no horizontal variation).

        The gradient varies only vertically. Within a single scanline,
        all pixels (excluding 2-pixel black borders) should be identical.
        """
        row = 512
        colors = set()
        for x in range(2, 1278):  # Skip border pixels
            colors.add(prom_framebuffer.getpixel((x, row)))
        assert len(colors) <= 2, (
            f"Row {row} has {len(colors)} distinct colors (expected 1-2)"
        )

    def test_black_left_border(self, prom_framebuffer):
        """Columns 0-1 should be black (border)."""
        for y in [1, 256, 512, 768, 1023]:
            pixel = prom_framebuffer.getpixel((0, y))
            assert pixel == (0, 0, 0), (
                f"Border pixel (0,{y}) = {pixel}, expected black"
            )

    def test_unique_colors(self, prom_framebuffer):
        """Should have 50+ unique colors (smooth gradient)."""
        colors = set(prom_framebuffer.getdata())
        assert len(colors) > 50, (
            f"Only {len(colors)} unique colors — gradient too coarse or flat"
        )


class TestReferenceComparison:
    """Compare framebuffer against saved reference images."""

    def test_save_reference(self, request, prom_framebuffer):
        """Save current framebuffer as reference. Only runs with --save-reference."""
        if not request.config.getoption("--save-reference", default=False):
            pytest.skip("Use --save-reference to save reference images")
        save_reference(prom_framebuffer, "prom_boot.png")

    def test_matches_reference(self, prom_framebuffer):
        """Framebuffer should match saved reference within 1% pixel tolerance."""
        ref = load_reference("prom_boot.png")
        if ref is None:
            pytest.skip("No reference image. Run with --save-reference first.")
        diff_frac, max_diff = pixel_diff_percentage(prom_framebuffer, ref)
        assert diff_frac < 0.01, (
            f"{diff_frac*100:.2f}% pixels differ (max channel diff={max_diff})"
        )
