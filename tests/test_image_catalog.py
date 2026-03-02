"""Tests for tools/image_catalog.py — disc image discovery and categorization.

Tests cover:
  - Category detection from filenames
  - Version extraction from filenames
  - Combo image preference
  - Package search (filename fallback, no DB required)
  - Install set ordering
  - Missing image graceful behavior
"""

import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch

from pyirix_qemu.catalog.images import (
    _categorize_filename,
    _extract_version,
    _major_version,
    _make_display_name,
    scan_software_library,
    resolve_images,
    ImageInfo,
    ImageCatalog,
    CATEGORY_COMBO,
    CATEGORY_OS_BASE,
    CATEGORY_OS_OVERLAY,
    CATEGORY_DEV_COMPILER,
    CATEGORY_DEV_TOOLS,
    CATEGORY_APPLICATIONS,
    CATEGORY_DEMOS,
    CATEGORY_NETWORKING,
    CATEGORY_THIRD_PARTY,
    CATEGORY_UNKNOWN,
)


# ── Category detection tests ──────────────────────────────────────────

class TestCategorizeFilename:
    """Test filename-based category detection."""

    def test_foundation_cd(self):
        cat, sash = _categorize_filename("IRIX 6.5 Foundation 1.img")
        assert cat == CATEGORY_OS_BASE
        assert sash is False

    def test_foundation_2(self):
        cat, _ = _categorize_filename("IRIX 6.5 Foundation 2.img")
        assert cat == CATEGORY_OS_BASE

    def test_overlay_cd(self):
        cat, sash = _categorize_filename(
            "IRIX 6.5.22 Overlays 1 of 3.img")
        assert cat == CATEGORY_OS_OVERLAY
        assert sash is True  # Overlays have sashARCS

    def test_installation_tools(self):
        cat, sash = _categorize_filename(
            "IRIX 6.5.5 Installation Tools and Overlays (1 of 2) - 812-0818-005.efs.img")
        assert cat == CATEGORY_OS_OVERLAY
        assert sash is True

    def test_irix_53_all(self):
        cat, sash = _categorize_filename(
            "IRIX 5.3 All Indigo2 IMPACT - 812-0119-010.efs.img")
        assert cat == CATEGORY_OS_BASE
        assert sash is True

    def test_irix_62_part(self):
        cat, sash = _categorize_filename(
            "IRIX 6.2 (Part 1 of 2) - 812-0469-001.efs.img")
        assert cat == CATEGORY_OS_BASE
        assert sash is True

    def test_mipspro(self):
        cat, _ = _categorize_filename("MIPSpro C Compiler 7.4.iso")
        assert cat == CATEGORY_DEV_COMPILER

    def test_compiler_execution_env(self):
        cat, _ = _categorize_filename("Compiler Execution Environment 7.4.iso")
        assert cat == CATEGORY_DEV_COMPILER

    def test_all_compiler(self):
        cat, _ = _categorize_filename(
            "MIPSpro_All-Compiler_CD_May_1999_for_IRIX_6.5_and_later-812-0925-001.efs.img")
        assert cat == CATEGORY_DEV_COMPILER

    def test_development_libraries(self):
        cat, _ = _categorize_filename(
            "IRIX 6.5 Development Libraries February 2002 - 812-0766-003.efs.img")
        assert cat == CATEGORY_DEV_TOOLS

    def test_developer_magic(self):
        cat, _ = _categorize_filename(
            "Silicon_Graphics_Developer_Magic_Soft_Dev_812-8101-012.iso")
        assert cat == CATEGORY_DEV_TOOLS

    def test_applications(self):
        cat, _ = _categorize_filename(
            "IRIX 6.5 Applications 2004 April.img")
        assert cat == CATEGORY_APPLICATIONS

    def test_indizone(self):
        cat, _ = _categorize_filename("IndiZone 1 - 812-8102-002.efs.img")
        assert cat == CATEGORY_APPLICATIONS

    def test_demos(self):
        cat, _ = _categorize_filename(
            "O2 Demos 1.3 for IRIX 6.5 - 812-0780-002.efs.img")
        assert cat == CATEGORY_DEMOS

    def test_impact_demos(self):
        cat, _ = _categorize_filename(
            "Impact Demos CD 6.2 - 812-0527-001.efs.img")
        assert cat == CATEGORY_DEMOS

    def test_nfs(self):
        cat, _ = _categorize_filename(
            "Network File System 6.1 - 812-0305-003.efs.img")
        assert cat == CATEGORY_NETWORKING

    def test_onc3(self):
        cat, _ = _categorize_filename(
            "ONC3 NFS Version 3 for IRIX 6.2, 6.3, 6.4, and 6.5 - 812-0774-002.efs.img")
        assert cat == CATEGORY_NETWORKING

    def test_maya(self):
        cat, _ = _categorize_filename("Alias_Maya_6.5_Unlimited_Irix.iso")
        assert cat == CATEGORY_THIRD_PARTY

    def test_photoshop(self):
        cat, _ = _categorize_filename("Photoshop3.0.1_IRIX.iso")
        assert cat == CATEGORY_THIRD_PARTY

    def test_quake(self):
        cat, _ = _categorize_filename("Quake3.iso")
        assert cat == CATEGORY_THIRD_PARTY

    def test_unknown(self):
        cat, _ = _categorize_filename("random_stuff.img")
        assert cat == CATEGORY_UNKNOWN

    def test_ada95(self):
        cat, _ = _categorize_filename(
            "Ada95 Compiler 1.3 for IRIX 6.2 to 6.5 - 812-0373-004.efs.img")
        assert cat == CATEGORY_DEV_COMPILER

    def test_power_c(self):
        cat, _ = _categorize_filename("Power C 2.0 - 812-0043-002.efs.img")
        assert cat == CATEGORY_DEV_COMPILER

    def test_open_inventor(self):
        cat, _ = _categorize_filename(
            "Open Inventor 2.1.5 for IRIX 6.2, 6.3, 6.5, and 6.5 - 812-0794-002.efs.img")
        assert cat == CATEGORY_DEV_TOOLS

    def test_developer_tools_maintenance(self):
        cat, _ = _categorize_filename(
            "Developer Tools Maintenance Release 7.3.1.2m - 812-0980-002.efs.img")
        assert cat == CATEGORY_DEV_TOOLS

    def test_prodev(self):
        cat, _ = _categorize_filename("ProDev WorkShop 2.8.iso")
        assert cat == CATEGORY_DEV_TOOLS


# ── Version extraction tests ─────────────────────────────────────────

class TestExtractVersion:
    """Test IRIX version extraction from filenames."""

    def test_three_part_version(self):
        assert _extract_version(
            "IRIX 6.5.22 Overlays 1 of 3.img") == "6.5.22"

    def test_two_part_version(self):
        assert _extract_version("IRIX 6.5 Foundation 1.img") == "6.5"

    def test_irix_prefix(self):
        assert _extract_version("IRIX 6.2 (Part 1 of 2).img") == "6.2"

    def test_version_in_dir(self):
        assert _extract_version(
            "IRIX 6.5.5 Installation Tools and Overlays.efs.img") == "6.5.5"

    def test_for_irix_prefix(self):
        assert _extract_version(
            "O2 Demos 1.3 for IRIX 6.5 - 812-0780-002.efs.img") == "6.5"

    def test_no_version(self):
        # Quake3.iso has no IRIX version
        ver = _extract_version("Quake3.iso")
        # Should get "" or possibly "3" — either is acceptable
        assert ver in ("", "3")

    def test_irix_53(self):
        assert _extract_version(
            "IRIX 5.3 All Indigo2 IMPACT - 812-0119-010.efs.img") == "5.3"


# ── Major version helper ─────────────────────────────────────────────

class TestMajorVersion:
    def test_three_part(self):
        assert _major_version("6.5.22") == "6.5"

    def test_two_part(self):
        assert _major_version("6.5") == "6.5"

    def test_single_part(self):
        assert _major_version("6") == "6"


# ── Display name ─────────────────────────────────────────────────────

class TestDisplayName:
    def test_efs_img(self):
        name = _make_display_name("IRIX 5.3 All Indigo2 IMPACT - 812-0119-010.efs.img")
        assert name == "IRIX 5.3 All Indigo2 IMPACT - 812-0119-010"

    def test_iso(self):
        name = _make_display_name("MIPSpro C Compiler 7.4.iso")
        assert name == "MIPSpro C Compiler 7.4"

    def test_plain_img(self):
        name = _make_display_name("IRIX 6.5 Foundation 1.img")
        assert name == "IRIX 6.5 Foundation 1"


# ── ImageCatalog tests ───────────────────────────────────────────────

class TestImageCatalog:
    """Test ImageCatalog query methods with synthetic data."""

    @pytest.fixture
    def catalog(self):
        """Build a catalog with representative test images."""
        return ImageCatalog(images=[
            ImageInfo(
                path="/sw/combo/IRIX_6.5.5_full.img",
                category=CATEGORY_COMBO,
                version_family="6.5.5",
                display_name="IRIX 6.5.5 full with MIPSpro",
                is_combo=True,
            ),
            ImageInfo(
                path="/sw/combo/IRIX_6.5.22_combined.img",
                category=CATEGORY_COMBO,
                version_family="6.5.22",
                display_name="IRIX 6.5.22 combined dist",
                is_combo=True,
            ),
            ImageInfo(
                path="/sw/Foundation1.img",
                category=CATEGORY_OS_BASE,
                version_family="6.5",
                display_name="IRIX 6.5 Foundation 1",
            ),
            ImageInfo(
                path="/sw/Foundation2.img",
                category=CATEGORY_OS_BASE,
                version_family="6.5",
                display_name="IRIX 6.5 Foundation 2",
            ),
            ImageInfo(
                path="/sw/Overlay1.img",
                category=CATEGORY_OS_OVERLAY,
                version_family="6.5.22",
                display_name="IRIX 6.5.22 Overlays 1 of 3",
                has_sash=True,
            ),
            ImageInfo(
                path="/sw/Overlay2.img",
                category=CATEGORY_OS_OVERLAY,
                version_family="6.5.22",
                display_name="IRIX 6.5.22 Overlays 2 of 3",
                has_sash=True,
            ),
            ImageInfo(
                path="/sw/MIPSpro.iso",
                category=CATEGORY_DEV_COMPILER,
                version_family="6.5",
                display_name="MIPSpro C Compiler 7.4",
            ),
            ImageInfo(
                path="/sw/Apps.img",
                category=CATEGORY_APPLICATIONS,
                version_family="6.5",
                display_name="IRIX 6.5 Applications 2004",
            ),
            ImageInfo(
                path="/sw/Demos.img",
                category=CATEGORY_DEMOS,
                version_family="6.5",
                display_name="O2 Demos 1.3 for IRIX 6.5",
            ),
            ImageInfo(
                path="/sw/Netscape.img",
                category=CATEGORY_APPLICATIONS,
                version_family="6.5",
                display_name="Netscape Navigator for IRIX 6.5",
            ),
        ], base_dir="/sw")

    def test_get_combo_exact(self, catalog):
        combo = catalog.get_combo_image("6.5.5")
        assert combo is not None
        assert combo.version_family == "6.5.5"

    def test_get_combo_family_fallback(self, catalog):
        """6.5 should find the 6.5.22 combo since they share major.minor."""
        combo = catalog.get_combo_image("6.5")
        assert combo is not None
        assert combo.version_family in ("6.5.5", "6.5.22")

    def test_get_combo_nonexistent(self, catalog):
        combo = catalog.get_combo_image("5.3")
        assert combo is None

    def test_get_install_set_base(self, catalog):
        images = catalog.get_install_set("6.5", [CATEGORY_OS_BASE])
        assert len(images) == 2
        assert all(img.category == CATEGORY_OS_BASE for img in images)

    def test_get_install_set_multiple_categories(self, catalog):
        images = catalog.get_install_set(
            "6.5", [CATEGORY_OS_BASE, CATEGORY_DEV_COMPILER])
        assert any(img.category == CATEGORY_OS_BASE for img in images)
        assert any(img.category == CATEGORY_DEV_COMPILER for img in images)

    def test_get_install_set_ordering(self, catalog):
        """Base images should come before dev tools."""
        images = catalog.get_install_set(
            "6.5", [CATEGORY_OS_BASE, CATEGORY_DEV_COMPILER])
        base_idx = next(i for i, img in enumerate(images)
                        if img.category == CATEGORY_OS_BASE)
        dev_idx = next(i for i, img in enumerate(images)
                       if img.category == CATEGORY_DEV_COMPILER)
        assert base_idx < dev_idx

    def test_get_boot_cd(self, catalog):
        boot = catalog.get_boot_cd("6.5")
        assert boot is not None
        assert boot.has_sash is True

    def test_get_boot_cd_prefers_overlay1(self, catalog):
        """Should prefer Overlays 1 (newer inst) for 6.5.x."""
        boot = catalog.get_boot_cd("6.5")
        assert boot is not None
        assert "1 of" in boot.display_name.lower()

    def test_get_foundation_cds(self, catalog):
        foundations = catalog.get_foundation_cds("6.5")
        assert len(foundations) == 2

    def test_by_category(self, catalog):
        demos = catalog.by_category(CATEGORY_DEMOS)
        assert len(demos) == 1
        assert demos[0].display_name == "O2 Demos 1.3 for IRIX 6.5"

    def test_find_package_by_name(self, catalog):
        """Filename fallback search for package name."""
        results = catalog.find_package("Netscape")
        assert len(results) >= 1
        assert any("Netscape" in r.display_name for r in results)

    def test_find_package_case_insensitive(self, catalog):
        results = catalog.find_package("netscape")
        assert len(results) >= 1

    def test_find_package_not_found(self, catalog):
        results = catalog.find_package("nonexistent_package_xyz")
        assert len(results) == 0

    def test_summary(self, catalog):
        s = catalog.summary()
        assert "10 images" in s
        assert "combo: 2" in s


# ── Scan with temp directory ──────────────────────────────────────────

class TestScanSoftwareLibrary:
    """Test scan_software_library with a temporary directory structure."""

    def test_scan_empty_dir(self, tmp_path):
        catalog = scan_software_library(str(tmp_path))
        assert len(catalog.images) == 0

    def test_scan_nonexistent_dir(self):
        catalog = scan_software_library("/nonexistent/path/12345")
        assert len(catalog.images) == 0

    def test_scan_discovers_images(self, tmp_path):
        # Create a minimal software_library structure
        (tmp_path / "IRIX 6.5 Foundation 1.img").write_bytes(b"\x00" * 100)
        (tmp_path / "MIPSpro C 7.4.iso").write_bytes(b"\x00" * 100)
        combo_dir = tmp_path / "prepackaged_combo_discs"
        combo_dir.mkdir()
        (combo_dir / "IRIX_6.5.5_full.img").write_bytes(b"\x00" * 100)

        catalog = scan_software_library(str(tmp_path))
        assert len(catalog.images) == 3

        # Check categories
        cats = {img.category for img in catalog.images}
        assert CATEGORY_COMBO in cats
        assert CATEGORY_OS_BASE in cats
        assert CATEGORY_DEV_COMPILER in cats

    def test_scan_skips_source_dirs(self, tmp_path):
        # Source directories should be skipped
        src_dir = tmp_path / "irix-655-source"
        src_dir.mkdir()
        (src_dir / "kernel.img").write_bytes(b"\x00" * 100)

        (tmp_path / "IRIX 6.5 Foundation 1.img").write_bytes(b"\x00" * 100)

        catalog = scan_software_library(str(tmp_path))
        assert len(catalog.images) == 1

    def test_combo_in_subdir(self, tmp_path):
        combo_dir = tmp_path / "prepackaged_combo_discs"
        combo_dir.mkdir()
        (combo_dir / "IRIX_6.5.5_full_with_MIPSpro_and_demos.img").write_bytes(
            b"\x00" * 100)

        catalog = scan_software_library(str(tmp_path))
        assert len(catalog.images) == 1
        assert catalog.images[0].category == CATEGORY_COMBO
        assert catalog.images[0].is_combo is True


# ── ImageInfo properties ──────────────────────────────────────────────

class TestImageInfo:
    def test_scsi_suffix_combo(self):
        img = ImageInfo(path="/test.img", category=CATEGORY_COMBO,
                        version_family="6.5", display_name="test",
                        is_combo=True)
        assert img.scsi_suffix == ":ro"

    def test_scsi_suffix_boot_cd(self):
        img = ImageInfo(path="/test.img", category=CATEGORY_OS_OVERLAY,
                        version_family="6.5", display_name="test",
                        has_sash=True)
        assert img.scsi_suffix == ":cdrom"

    def test_scsi_suffix_data_cd(self):
        img = ImageInfo(path="/test.img", category=CATEGORY_APPLICATIONS,
                        version_family="6.5", display_name="test")
        assert img.scsi_suffix == ":ro"

    def test_filename_property(self):
        img = ImageInfo(path="/sw/IRIX 6.5 Foundation 1.img",
                        category=CATEGORY_OS_BASE,
                        version_family="6.5",
                        display_name="IRIX 6.5 Foundation 1")
        assert img.filename == "IRIX 6.5 Foundation 1.img"


# ── resolve_images tests ─────────────────────────────────────────────

class TestResolveImages:
    def test_resolve_nonexistent_dir(self):
        result = resolve_images("6.5", base_dir="/nonexistent/path/12345")
        assert result.combo_image is None
        assert result.boot_cd is None

    def test_resolve_with_combo(self, tmp_path):
        combo_dir = tmp_path / "prepackaged_combo_discs"
        combo_dir.mkdir()
        (combo_dir / "IRIX_6.5.5_full.img").write_bytes(b"\x00" * 100)
        (tmp_path / "IRIX 6.5 Foundation 1.img").write_bytes(b"\x00" * 100)

        result = resolve_images("6.5.5", base_dir=str(tmp_path))
        assert result.combo_image is not None
        assert result.use_combo is True

    def test_resolve_summary(self, tmp_path):
        combo_dir = tmp_path / "prepackaged_combo_discs"
        combo_dir.mkdir()
        (combo_dir / "IRIX_6.5.5_full.img").write_bytes(b"\x00" * 100)

        result = resolve_images("6.5.5", base_dir=str(tmp_path))
        s = result.summary()
        assert "Combo:" in s
