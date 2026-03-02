"""
Tests for the external software library scanner.

Verifies format detection, categorization, SQLite indexing, search,
nekoware name parsing, staging, and incremental scan behavior.

These tests are FAST (no network I/O, uses temp directories).
"""

import os
import struct
import tempfile
import pytest
from pathlib import Path

from pyirix_qemu.catalog.library import (
    FileFormat,
    LibraryEntry,
    LibraryIndex,
    LibraryScanner,
    categorize_entry,
    detect_format_deep,
    detect_format_from_filename,
    make_display_name,
    parse_nekoware_name,
    stage_file,
    stage_entry_info,
)


# ── Format Detection (filename-based) ────────────────────────────────

class TestFilenameFormatDetection:
    """Format detection using only the filename, no I/O."""

    def test_efs_image(self):
        assert detect_format_from_filename(
            "IRIX 6.5 Foundation 1.efs.img") == FileFormat.EFS_IMAGE

    def test_iso(self):
        assert detect_format_from_filename(
            "Quake3.iso") == FileFormat.ISO9660

    def test_nekoware_iso(self):
        assert detect_format_from_filename(
            "SGI_Nekoware_August_2010_Part_1_of_8.iso"
        ) == FileFormat.NEKOWARE_ISO

    def test_tardist(self):
        assert detect_format_from_filename(
            "neko_gcc-4.7.1.tardist") == FileFormat.TARDIST

    def test_tarball_gz(self):
        assert detect_format_from_filename(
            "houdini-6.5.51-irix.tar.gz") == FileFormat.TARBALL

    def test_tarball_tgz(self):
        assert detect_format_from_filename("foo.tgz") == FileFormat.TARBALL

    def test_tarball_plain(self):
        assert detect_format_from_filename("sgimegacd.tar") == FileFormat.TARBALL

    def test_tarball_bz2(self):
        assert detect_format_from_filename("foo.tar.bz2") == FileFormat.TARBALL

    def test_raw_image(self):
        """Plain .img without .efs prefix is raw_image."""
        assert detect_format_from_filename(
            "irix_disk.img") == FileFormat.RAW_IMAGE

    def test_unknown_extension(self):
        assert detect_format_from_filename(
            "readme.txt") == FileFormat.UNKNOWN

    def test_zip_unknown(self):
        assert detect_format_from_filename(
            "blender_irix_1.0.zip") == FileFormat.UNKNOWN


# ── Format Detection (deep / magic bytes) ─────────────────────────────

class TestDeepFormatDetection:
    """Magic-byte format detection by reading file headers."""

    def test_efs_with_volume_header(self, tmp_path):
        """SGI volume header + EFS partition type detected."""
        img = tmp_path / "test.img"
        data = bytearray(65536)

        # SGI volume header magic at offset 0
        struct.pack_into('>I', data, 0, 0x0BE5A941)

        # Partition table entry 0: type=7 (EFS), first_block=4, nblks=100
        # Partition table at offset 72, each entry 16 bytes
        struct.pack_into('>III', data, 72, 100, 4, 7)

        img.write_bytes(bytes(data))
        fmt, notes = detect_format_deep(str(img))
        assert fmt == FileFormat.EFS_IMAGE
        assert "EFS partition" in notes

    def test_iso9660_signature(self, tmp_path):
        """ISO 9660 CD001 signature detected at offset 32769."""
        img = tmp_path / "test.iso"
        data = bytearray(65536)
        # ISO 9660 PVD signature at sector 16
        offset = 32769
        data[offset:offset+5] = b'CD001'
        # Volume ID at offset 32808
        vol_id = b'IRIX_6.5_INSTALL'
        data[32808:32808+len(vol_id)] = vol_id

        img.write_bytes(bytes(data))
        fmt, notes = detect_format_deep(str(img))
        assert fmt == FileFormat.ISO9660
        assert "IRIX_6.5_INSTALL" in notes

    def test_gzip_detected(self, tmp_path):
        """Gzip magic bytes detected."""
        img = tmp_path / "test.dat"
        data = bytearray(1024)
        data[0:2] = b'\x1f\x8b'  # gzip magic
        img.write_bytes(bytes(data))
        fmt, notes = detect_format_deep(str(img))
        assert fmt == FileFormat.TARBALL
        assert "gzip" in notes

    def test_tar_ustar_magic(self, tmp_path):
        """POSIX tar ustar magic at offset 257."""
        img = tmp_path / "test.dat"
        data = bytearray(1024)
        data[257:262] = b'ustar'
        img.write_bytes(bytes(data))
        fmt, notes = detect_format_deep(str(img))
        assert fmt == FileFormat.TARBALL

    def test_empty_file_unknown(self, tmp_path):
        """Tiny files detected as unknown."""
        img = tmp_path / "tiny.img"
        img.write_bytes(b'\x00' * 100)
        fmt, notes = detect_format_deep(str(img))
        assert fmt == FileFormat.UNKNOWN

    def test_unreadable_file(self, tmp_path):
        """Non-existent file returns unknown."""
        fmt, notes = detect_format_deep(str(tmp_path / "nope.img"))
        assert fmt == FileFormat.UNKNOWN


# ── Categorization ────────────────────────────────────────────────────

class TestCategorization:
    """Path and filename-based categorization."""

    def test_os_by_path(self):
        cat, _, _ = categorize_entry("/lib/IRIX/sgi/6.5_os/some.img")
        assert cat == "os"

    def test_dev_by_path(self):
        cat, _, _ = categorize_entry("/lib/IRIX/sgi/development/mipspro.img")
        assert cat == "dev"

    def test_freeware_by_path(self):
        cat, _, _ = categorize_entry("/lib/IRIX/sgi/freeware/fw.efs.img")
        assert cat == "freeware"

    def test_nekoware_by_path(self):
        cat, _, _ = categorize_entry(
            "/lib/IRIX/nekoware/tardists/neko_gcc-4.7.tardist")
        assert cat == "nekoware"

    def test_tgcware_by_path(self):
        cat, _, _ = categorize_entry(
            "/lib/IRIX/tgcware/jupiterrise.com/tgcware/foo.tardist")
        assert cat == "tgcware"

    def test_mipspro_by_filename(self):
        """Filename fallback when path has no category signal."""
        cat, _, _ = categorize_entry("/tmp/MIPSpro_7.4.tar.gz")
        assert cat == "dev"

    def test_cosmo_graphics(self):
        cat, _, _ = categorize_entry(
            "/lib/IRIX/sgi/cosmo/Cosmo Suite.efs.img")
        assert cat == "graphics"

    def test_part_number_extraction(self):
        _, _, pn = categorize_entry(
            "/lib/IRIX/sgi/freeware/Freeware June 1998 - 812-0773-001.efs.img")
        assert pn == "812-0773-001"

    def test_version_extraction(self):
        _, ver, _ = categorize_entry(
            "/lib/IRIX/sgi/6.5_os/IRIX 6.5.22 Overlays.img")
        assert ver == "6.5.22"

    def test_misc_fallback(self):
        cat, _, _ = categorize_entry("/tmp/something_random.img")
        assert cat == "misc"


# ── Display Name ──────────────────────────────────────────────────────

class TestDisplayName:
    """Human-readable name generation."""

    def test_strip_efs_extension(self):
        name = make_display_name("IRIX 6.5 Foundation 1.efs.img")
        assert name == "IRIX 6.5 Foundation 1"

    def test_strip_iso(self):
        name = make_display_name("Quake3.iso")
        assert name == "Quake3"

    def test_nekoware_tardist(self):
        name = make_display_name("neko_gcc-4.7.1.tardist")
        assert name == "nekoware: gcc 4.7.1"

    def test_freeware_tardist(self):
        name = make_display_name("fw_hexedit-1.2.7.tardist")
        assert name == "freeware: hexedit 1.2.7"

    def test_underscore_cleanup(self):
        name = make_display_name("Cosmo_Suite_August_1998.efs.img")
        assert "Cosmo Suite August 1998" == name


# ── Nekoware Name Parsing ─────────────────────────────────────────────

class TestNekowareParsing:
    """Parse nekoware tardist filenames."""

    def test_basic(self):
        result = parse_nekoware_name("neko_gcc-4.7.1.tardist")
        assert result == ("gcc", "4.7.1")

    def test_with_suffix(self):
        result = parse_nekoware_name("neko_abiword-2.2.7_gcc.tardist")
        assert result is not None
        assert result[0] == "abiword"

    def test_no_match(self):
        assert parse_nekoware_name("something_else.tar.gz") is None

    def test_underscore_version(self):
        result = parse_nekoware_name("neko_aalib-1.4rc4.tardist")
        assert result is not None
        assert result[0] == "aalib"
        assert result[1] == "1.4rc4"


# ── SQLite Index ──────────────────────────────────────────────────────

class TestLibraryIndex:
    """Persistent SQLite index operations."""

    @pytest.fixture
    def db(self, tmp_path):
        idx = LibraryIndex(str(tmp_path / "test.db"))
        idx.initialize()
        yield idx
        idx.close()

    def _make_entry(self, **kwargs) -> LibraryEntry:
        defaults = dict(
            path="/test/file.img",
            filename="file.img",
            format="efs_image",
            category="os",
            size_bytes=1024000,
            mtime=1000000.0,
            display_name="Test File",
            version="6.5",
            part_number="812-0001-001",
        )
        defaults.update(kwargs)
        return LibraryEntry(**defaults)

    def test_upsert_and_search(self, db):
        entry = self._make_entry()
        db.upsert(entry)
        db._conn.commit()

        results = db.search("Test File")
        assert len(results) >= 1
        assert results[0].filename == "file.img"

    def test_upsert_update(self, db):
        entry = self._make_entry(display_name="Old Name")
        db.upsert(entry)
        db._conn.commit()

        entry2 = self._make_entry(display_name="New Name")
        db.upsert(entry2)
        db._conn.commit()

        results = db.search("", limit=100)
        # Should be 1 entry (updated, not duplicated)
        assert len(results) == 1
        assert results[0].display_name == "New Name"

    def test_search_by_category(self, db):
        db.upsert(self._make_entry(path="/a.img", filename="a.img",
                                   category="os"))
        db.upsert(self._make_entry(path="/b.img", filename="b.img",
                                   category="dev"))
        db._conn.commit()

        results = db.search("", category="dev")
        assert len(results) == 1
        assert results[0].category == "dev"

    def test_search_by_format(self, db):
        db.upsert(self._make_entry(path="/a.img", filename="a.img",
                                   format="efs_image"))
        db.upsert(self._make_entry(path="/b.iso", filename="b.iso",
                                   format="iso9660"))
        db._conn.commit()

        results = db.search("", fmt="iso9660")
        assert len(results) == 1
        assert results[0].format == "iso9660"

    def test_needs_rescan_new_file(self, db):
        assert db.needs_rescan("/new/file.img", 1000.0) is True

    def test_needs_rescan_unchanged(self, db):
        entry = self._make_entry(mtime=1000.0)
        db.upsert(entry)
        db._conn.commit()
        assert db.needs_rescan(entry.path, 1000.0) is False

    def test_needs_rescan_modified(self, db):
        entry = self._make_entry(mtime=1000.0)
        db.upsert(entry)
        db._conn.commit()
        assert db.needs_rescan(entry.path, 2000.0) is True

    def test_remove_missing(self, db):
        db.upsert(self._make_entry(path="/a.img", filename="a.img"))
        db.upsert(self._make_entry(path="/b.img", filename="b.img"))
        db._conn.commit()

        removed = db.remove_missing({"/a.img"})
        assert removed == 1
        assert len(db.get_all()) == 1

    def test_get_stats(self, db):
        db.upsert(self._make_entry(path="/a.img", filename="a.img",
                                   category="os", format="efs_image"))
        db.upsert(self._make_entry(path="/b.img", filename="b.img",
                                   category="dev", format="iso9660"))
        db._conn.commit()

        stats = db.get_stats()
        assert stats["total"] == 2
        assert stats["cat:os"] == 1
        assert stats["cat:dev"] == 1
        assert stats["fmt:efs_image"] == 1
        assert stats["fmt:iso9660"] == 1

    def test_bulk_upsert(self, db):
        entries = [
            self._make_entry(path=f"/file{i}.img", filename=f"file{i}.img")
            for i in range(10)
        ]
        db.bulk_upsert(entries)
        assert len(db.get_all()) == 10


# ── Scanner Integration ──────────────────────────────────────────────

class TestLibraryScanner:
    """End-to-end scanner with temp directory."""

    @pytest.fixture
    def lib_tree(self, tmp_path):
        """Create a mock library directory tree."""
        # OS images
        os_dir = tmp_path / "sgi" / "6.5_os"
        os_dir.mkdir(parents=True)
        (os_dir / "IRIX 6.5 Foundation 1.efs.img").write_bytes(b'\x00' * 2048)

        # Freeware
        fw_dir = tmp_path / "sgi" / "freeware"
        fw_dir.mkdir(parents=True)
        (fw_dir / "Freeware June 1998 - 812-0773-001.efs.img").write_bytes(
            b'\x00' * 4096)

        # Nekoware tardists
        neko_dir = tmp_path / "nekoware" / "tardists"
        neko_dir.mkdir(parents=True)
        (neko_dir / "neko_gcc-4.7.1.tardist").write_bytes(b'\x00' * 8192)
        (neko_dir / "neko_vim-7.4.tardist").write_bytes(b'\x00' * 4096)

        # Skip files (too small, or wrong extension)
        (tmp_path / "readme.txt").write_bytes(b"hello")
        (tmp_path / "tiny.img").write_bytes(b'\x00' * 100)

        return tmp_path

    def test_scan_finds_files(self, lib_tree, tmp_path):
        db_path = str(tmp_path / "test.db")
        scanner = LibraryScanner(str(lib_tree), db_path=db_path)
        stats = scanner.scan()

        assert stats["total"] >= 4  # 4 valid files
        assert stats["errors"] == 0

    def test_scan_categorizes(self, lib_tree, tmp_path):
        db_path = str(tmp_path / "test.db")
        scanner = LibraryScanner(str(lib_tree), db_path=db_path)
        scanner.scan()

        results = scanner.search("Foundation")
        assert len(results) >= 1
        assert results[0].category == "os"

    def test_scan_nekoware(self, lib_tree, tmp_path):
        db_path = str(tmp_path / "test.db")
        scanner = LibraryScanner(str(lib_tree), db_path=db_path)
        scanner.scan()

        results = scanner.search("gcc", category="nekoware")
        assert len(results) >= 1
        assert "nekoware" in results[0].display_name.lower()

    def test_incremental_scan(self, lib_tree, tmp_path):
        db_path = str(tmp_path / "test.db")
        scanner = LibraryScanner(str(lib_tree), db_path=db_path)

        # First scan
        stats1 = scanner.scan()
        assert stats1["new"] >= 4

        # Second scan — everything unchanged
        stats2 = scanner.scan()
        assert stats2["unchanged"] >= 4
        assert stats2["new"] == 0

    def test_scan_skips_tiny_files(self, lib_tree, tmp_path):
        db_dir = tmp_path / "dbdir"
        db_dir.mkdir()
        db_path = str(db_dir / "test.db")
        scanner = LibraryScanner(str(lib_tree), db_path=db_path)
        scanner.scan()

        # The 100-byte "tiny.img" should not be indexed (< 1KB)
        all_entries = scanner.index.get_all()
        filenames = {e.filename for e in all_entries}
        assert "tiny.img" not in filenames

    def test_scan_skips_text_files(self, lib_tree, tmp_path):
        db_dir = tmp_path / "dbdir2"
        db_dir.mkdir()
        db_path = str(db_dir / "test.db")
        scanner = LibraryScanner(str(lib_tree), db_path=db_path)
        scanner.scan()

        all_entries = scanner.index.get_all()
        filenames = {e.filename for e in all_entries}
        assert "readme.txt" not in filenames

    def test_stats_after_scan(self, lib_tree, tmp_path):
        db_path = str(tmp_path / "test.db")
        scanner = LibraryScanner(str(lib_tree), db_path=db_path)
        scanner.scan()

        stats = scanner.get_stats()
        assert stats["total"] >= 4
        scanner.close()


# ── Staging ───────────────────────────────────────────────────────────

class TestStaging:
    """File staging from library to local workspace."""

    @pytest.fixture
    def source_file(self, tmp_path):
        src = tmp_path / "source" / "test.efs.img"
        src.parent.mkdir(parents=True)
        src.write_bytes(b'\x00' * 4096)
        return src

    def test_stage_copy(self, source_file, tmp_path):
        entry = LibraryEntry(
            path=str(source_file),
            filename=source_file.name,
            format="efs_image",
            category="os",
            size_bytes=4096,
        )
        staging = tmp_path / "staging"
        result = stage_file(entry, staging_dir=str(staging))

        assert os.path.exists(result)
        assert os.path.getsize(result) == 4096

    def test_stage_skip_if_exists(self, source_file, tmp_path):
        """Staging skips copy if file already exists with same size."""
        entry = LibraryEntry(
            path=str(source_file),
            filename=source_file.name,
            format="efs_image",
            category="os",
            size_bytes=4096,
        )
        staging = tmp_path / "staging"

        # Stage once
        path1 = stage_file(entry, staging_dir=str(staging))
        mtime1 = os.path.getmtime(path1)

        # Stage again — should skip
        path2 = stage_file(entry, staging_dir=str(staging))
        mtime2 = os.path.getmtime(path2)

        assert path1 == path2
        assert mtime1 == mtime2  # File was not re-copied

    def test_stage_explicit_dest(self, source_file, tmp_path):
        entry = LibraryEntry(
            path=str(source_file),
            filename=source_file.name,
            format="efs_image",
            category="os",
            size_bytes=4096,
        )
        dest = str(tmp_path / "custom_dest.img")
        result = stage_file(entry, dest=dest)
        assert result == os.path.abspath(dest)

    def test_stage_info(self, source_file, tmp_path):
        entry = LibraryEntry(
            path=str(source_file),
            filename=source_file.name,
            format="efs_image",
            category="os",
            size_bytes=4096,
        )
        staging = tmp_path / "staging"
        info = stage_entry_info(entry, staging_dir=str(staging))

        assert info["source_exists"] is True
        assert info["staged"] is False
        assert info["needs_copy"] is True

    def test_stage_info_after_copy(self, source_file, tmp_path):
        entry = LibraryEntry(
            path=str(source_file),
            filename=source_file.name,
            format="efs_image",
            category="os",
            size_bytes=4096,
        )
        staging = tmp_path / "staging"
        stage_file(entry, staging_dir=str(staging))

        info = stage_entry_info(entry, staging_dir=str(staging))
        assert info["staged"] is True
        assert info["needs_copy"] is False


# ── LibraryEntry properties ───────────────────────────────────────────

class TestLibraryEntry:
    """LibraryEntry dataclass properties."""

    def test_size_display_mb(self):
        entry = LibraryEntry(path="/t", filename="t", format="x",
                             category="x", size_bytes=650 * 1024 * 1024)
        assert "650.0 MB" == entry.size_display

    def test_size_display_gb(self):
        entry = LibraryEntry(path="/t", filename="t", format="x",
                             category="x", size_bytes=2 * 1024 * 1024 * 1024)
        assert "2.0 GB" == entry.size_display

    def test_size_display_kb(self):
        entry = LibraryEntry(path="/t", filename="t", format="x",
                             category="x", size_bytes=512 * 1024)
        assert "512.0 KB" == entry.size_display

    def test_size_display_bytes(self):
        entry = LibraryEntry(path="/t", filename="t", format="x",
                             category="x", size_bytes=100)
        assert "100 B" == entry.size_display

    def test_size_mb_property(self):
        entry = LibraryEntry(path="/t", filename="t", format="x",
                             category="x", size_bytes=1024 * 1024)
        assert entry.size_mb == 1.0
