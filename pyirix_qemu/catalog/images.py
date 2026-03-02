"""Disc image discovery and categorization for IRIX software library.

Scans software_library/ to find .img/.iso/.efs.img files, categorizes them
into tiers (OS base, overlays, dev tools, applications, etc.), and provides
ordered install sets for automated IRIX installation.

The categorization is filename-based with optional SQLite DB enrichment
from irix_packages.db (built by irix_pkg_analyzer.py).

Usage:
    from pyirix_qemu.catalog.images import scan_software_library

    catalog = scan_software_library()
    combo = catalog.get_combo_image("6.5.5")
    install_set = catalog.get_install_set("6.5.5", ["os_base", "os_overlay"])
"""

import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOFTWARE_LIBRARY = PROJECT_ROOT / "software_library"

# ── Image categories ────────────────────────────────────────────────────

CATEGORY_COMBO = "combo"
CATEGORY_OS_BASE = "os_base"
CATEGORY_OS_OVERLAY = "os_overlay"
CATEGORY_DEV_COMPILER = "dev_compiler"
CATEGORY_DEV_TOOLS = "dev_tools"
CATEGORY_APPLICATIONS = "applications"
CATEGORY_DEMOS = "demos"
CATEGORY_NETWORKING = "networking"
CATEGORY_THIRD_PARTY = "third_party"
CATEGORY_UNKNOWN = "unknown"

ALL_CATEGORIES = [
    CATEGORY_COMBO, CATEGORY_OS_BASE, CATEGORY_OS_OVERLAY,
    CATEGORY_DEV_COMPILER, CATEGORY_DEV_TOOLS, CATEGORY_APPLICATIONS,
    CATEGORY_DEMOS, CATEGORY_NETWORKING, CATEGORY_THIRD_PARTY,
    CATEGORY_UNKNOWN,
]


@dataclass
class ImageInfo:
    """Metadata for a single disc image."""
    path: str
    category: str
    version_family: str  # e.g. "6.5.5", "6.5.22", "6.5", "6.2", "5.3"
    display_name: str
    product_count: int = 0  # from SQLite DB, 0 if unknown
    is_efs: bool = False  # .efs.img format
    is_iso: bool = False  # .iso format
    is_combo: bool = False  # all-in-one combined image
    has_sash: bool = False  # contains sashARCS (bootable)

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    @property
    def scsi_suffix(self) -> str:
        """Return the appropriate SCSI drive suffix for QEMU attachment.

        :ro for large EFS combo images (avoids QEMU crash from oversized
        READ(10) during CD-ROM probe), :cdrom for bootable CDs.
        """
        if self.is_combo:
            return ":ro"
        if self.has_sash:
            return ":cdrom"
        return ":ro"


@dataclass
class ImageCatalog:
    """Collection of discovered disc images with query methods."""
    images: List[ImageInfo] = field(default_factory=list)
    base_dir: str = ""

    def get_combo_image(self, version: str) -> Optional[ImageInfo]:
        """Find the best all-in-one combo image for a version.

        Prefers exact version match, falls back to version family match.
        Within a version, prefers images with "patched" in the filename
        (pre-applied conflict fixes) over unpatched originals.
        """
        def _patched_key(img: "ImageInfo") -> int:
            return 0 if "patched" in Path(img.path).name.lower() else 1

        # Exact match first
        candidates = [img for img in self.images
                      if img.category == CATEGORY_COMBO and img.version_family == version]
        if candidates:
            return min(candidates, key=_patched_key)

        # Family match (e.g. "6.5.5" matches combo for "6.5")
        major_minor = _major_version(version)
        candidates = [img for img in self.images
                      if img.category == CATEGORY_COMBO and
                      _major_version(img.version_family) == major_minor]
        if candidates:
            return min(candidates, key=_patched_key)

        return None

    def get_install_set(self, version: str,
                        categories: Optional[List[str]] = None
                        ) -> List[ImageInfo]:
        """Return ordered list of images for an installation.

        Args:
            version: Target IRIX version (e.g. "6.5.5", "6.5", "6.2")
            categories: List of categories to include. If None, uses
                [os_base, os_overlay].

        Returns images filtered by version compatibility and category,
        ordered by installation priority (base first, then overlays, etc.).
        """
        if categories is None:
            categories = [CATEGORY_OS_BASE, CATEGORY_OS_OVERLAY]

        major = _major_version(version)

        results = []
        for img in self.images:
            if img.category not in categories:
                continue
            # Version compatibility: exact match or same major.minor
            if img.version_family == version or \
               _major_version(img.version_family) == major:
                results.append(img)

        # Sort by category priority, then by version (newer first)
        priority = {cat: i for i, cat in enumerate(ALL_CATEGORIES)}
        results.sort(key=lambda img: (
            priority.get(img.category, 99),
            img.version_family,
            img.display_name,
        ))

        return results

    def get_boot_cd(self, version: str) -> Optional[ImageInfo]:
        """Find a bootable CD (with sashARCS) for a version.

        For IRIX 6.5.x, prefers overlay/insttools CDs (newer inst).
        Prefers exact version match over family match.
        For older versions, uses whatever has sashARCS.
        """
        exact = []
        family = []
        major = _major_version(version)

        for img in self.images:
            if not img.has_sash:
                continue
            if img.version_family == version:
                exact.append(img)
            elif _major_version(img.version_family) == major:
                family.append(img)

        # Prefer exact version matches, then family matches
        for candidates in [exact, family]:
            if not candidates:
                continue
            # Prefer overlay/insttools CDs (newer inst resolves deps better)
            for img in candidates:
                name_lower = img.display_name.lower()
                if ("overlay" in name_lower and "1 of" in name_lower) or \
                   "installation tools" in name_lower:
                    return img
            return candidates[0]

        return None

    def get_foundation_cds(self, version: str) -> List[ImageInfo]:
        """Find Foundation CDs for a version."""
        return [img for img in self.images
                if img.category == CATEGORY_OS_BASE
                and "foundation" in img.display_name.lower()
                and (_major_version(img.version_family) ==
                     _major_version(version))]

    def by_category(self, category: str) -> List[ImageInfo]:
        """Return all images in a given category."""
        return [img for img in self.images if img.category == category]

    def find_package(self, package_name: str,
                     version: Optional[str] = None) -> List[ImageInfo]:
        """Find disc images containing a specific IRIX package.

        Searches the irix_packages.db SQLite database for images that
        contain the named package (e.g. "netscape", "MIPSpro", "eoe").
        Falls back to filename matching if DB is unavailable.

        Args:
            package_name: Package or product name to search for (case-insensitive)
            version: Optional IRIX version filter

        Returns:
            List of ImageInfo objects whose disc images contain the package
        """
        results = []
        db_path = os.path.join(self.base_dir, "irix_packages.db")

        if os.path.exists(db_path):
            results = _search_db_for_package(
                db_path, package_name, self.images, version)

        if not results:
            # Fallback: match package name against filenames
            pattern = re.compile(re.escape(package_name), re.I)
            for img in self.images:
                if pattern.search(img.display_name):
                    if version is None or _major_version(img.version_family) == \
                       _major_version(version):
                        results.append(img)

        return results

    def summary(self) -> str:
        """Human-readable summary of the catalog."""
        lines = [f"Image catalog: {len(self.images)} images from {self.base_dir}"]
        by_cat: Dict[str, List[ImageInfo]] = {}
        for img in self.images:
            by_cat.setdefault(img.category, []).append(img)
        for cat in ALL_CATEGORIES:
            imgs = by_cat.get(cat, [])
            if imgs:
                lines.append(f"  {cat}: {len(imgs)}")
                for img in imgs:
                    lines.append(f"    {img.display_name} [{img.version_family}]")
        return "\n".join(lines)


# ── Filename-based categorization ────────────────────────────────────

# Patterns tested in order; first match wins.
# Each entry: (compiled_regex, category, has_sash_flag)
_CATEGORIZATION_RULES: List[Tuple[re.Pattern, str, bool]] = [
    # Combo images (in prepackaged_combo_discs/)
    # Detected by directory, not pattern — handled separately

    # OS Base
    (re.compile(r'Foundation', re.I), CATEGORY_OS_BASE, False),
    (re.compile(r'IRIX\s+5\.3\b.*All\b', re.I), CATEGORY_OS_BASE, True),
    (re.compile(r'IRIX\s+6\.2\b.*Part\s+\d', re.I), CATEGORY_OS_BASE, True),

    # OS Overlays / Installation Tools
    (re.compile(r'Installation\s+Tools', re.I), CATEGORY_OS_OVERLAY, True),
    (re.compile(r'Overlay', re.I), CATEGORY_OS_OVERLAY, True),

    # Dev compilers
    (re.compile(r'MIPSpro', re.I), CATEGORY_DEV_COMPILER, False),
    (re.compile(r'Compiler.*Execution\s+Env', re.I), CATEGORY_DEV_COMPILER, False),
    (re.compile(r'All.Compiler', re.I), CATEGORY_DEV_COMPILER, False),
    (re.compile(r'Power\s*C\b', re.I), CATEGORY_DEV_COMPILER, False),
    (re.compile(r'Ada\s*95\s+Compiler', re.I), CATEGORY_DEV_COMPILER, False),

    # Dev tools
    (re.compile(r'ProDev', re.I), CATEGORY_DEV_TOOLS, False),
    (re.compile(r'Development\s+Librar', re.I), CATEGORY_DEV_TOOLS, False),
    (re.compile(r'Development\s+Foundation', re.I), CATEGORY_DEV_TOOLS, False),
    (re.compile(r'Developer\s+Tool', re.I), CATEGORY_DEV_TOOLS, False),
    (re.compile(r'Developer.*Magic', re.I), CATEGORY_DEV_TOOLS, False),
    (re.compile(r'Open\s+Inventor', re.I), CATEGORY_DEV_TOOLS, False),
    (re.compile(r'Performance.Co.Pilot', re.I), CATEGORY_DEV_TOOLS, False),

    # Applications
    (re.compile(r'Applications', re.I), CATEGORY_APPLICATIONS, False),
    (re.compile(r'IndiZone', re.I), CATEGORY_APPLICATIONS, False),

    # Demos
    (re.compile(r'Demo', re.I), CATEGORY_DEMOS, False),

    # Networking
    (re.compile(r'NFS|ONC3', re.I), CATEGORY_NETWORKING, False),
    (re.compile(r'Network\s+File\s+System', re.I), CATEGORY_NETWORKING, False),

    # Third-party applications
    (re.compile(r'Maya', re.I), CATEGORY_THIRD_PARTY, False),
    (re.compile(r'Photoshop', re.I), CATEGORY_THIRD_PARTY, False),
    (re.compile(r'Quake', re.I), CATEGORY_THIRD_PARTY, False),
    (re.compile(r'Alias', re.I), CATEGORY_THIRD_PARTY, False),
    (re.compile(r'Softimage', re.I), CATEGORY_THIRD_PARTY, False),
    (re.compile(r'Houdini', re.I), CATEGORY_THIRD_PARTY, False),
]


def _categorize_filename(filename: str) -> Tuple[str, bool]:
    """Categorize a disc image by its filename.

    Returns (category, has_sash).
    """
    for pattern, category, has_sash in _CATEGORIZATION_RULES:
        if pattern.search(filename):
            return category, has_sash
    return CATEGORY_UNKNOWN, False


# ── Version extraction ───────────────────────────────────────────────

# Version patterns, tested in order of specificity (most specific first)
# Shared version extraction patterns. Also imported by pyirix_qemu.catalog.library.
_VERSION_PATTERNS = [
    # "IRIX 6.5.22 Overlays" or "6.5.5 Installation Tools"
    re.compile(r'(?:IRIX\s+)?(\d+\.\d+\.\d+)'),
    # "IRIX 6.5 Foundation" or "IRIX 6.2 (Part 1 of 2)"
    re.compile(r'IRIX\s+(\d+\.\d+)'),
    # "for IRIX 6.5" or "for IRIX 6.2, 6.3 and 6.4"
    re.compile(r'for\s+IRIX\s+(\d+\.\d+)'),
    # Version-tagged community packages: v1.2.3, 4.9.2
    re.compile(r'v?(\d+\.\d+\.\d+)'),
    # Fallback: any version-like number in the filename
    re.compile(r'(\d+\.\d+)'),
]


def _extract_version(filename: str) -> str:
    """Extract IRIX version from a filename.

    Returns the most specific version found (e.g. "6.5.22" over "6.5").
    Returns "" if no version detected.
    """
    for pattern in _VERSION_PATTERNS:
        m = pattern.search(filename)
        if m:
            ver = m.group(1)
            # Skip part numbers like "1 of 2" that look like versions
            if ver in ("1.3", "2.0", "2.1", "3.0"):
                # These could be app versions, not IRIX versions.
                # Check if "IRIX" precedes it
                full_match = re.search(r'IRIX\s+' + re.escape(ver), filename)
                if full_match:
                    return ver
                continue
            return ver
    return ""


def _major_version(version: str) -> str:
    """Extract major.minor from a version string.

    "6.5.22" -> "6.5", "6.2" -> "6.2", "5.3" -> "5.3"
    """
    parts = version.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return version


def _make_display_name(filename: str) -> str:
    """Create a human-readable display name from a filename.

    Strips extension and common noise like part numbers.
    """
    name = filename
    # Strip extensions
    for ext in [".efs.img", ".img", ".iso"]:
        if name.lower().endswith(ext):
            name = name[:-len(ext)]
            break
    return name.strip()


# ── SQLite DB enrichment ─────────────────────────────────────────────

def _enrich_from_db(images: List[ImageInfo], db_path: str) -> None:
    """Enrich image metadata from irix_packages.db if available.

    Updates product_count and may refine category/version_family for
    images that were categorized as "unknown" by filename alone.
    """
    if not os.path.exists(db_path):
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if the images table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='images'"
        )
        if not cursor.fetchone():
            conn.close()
            return

        for img in images:
            basename = os.path.basename(img.path)
            # Try matching by filename
            cursor.execute(
                "SELECT product_count, version_family FROM images "
                "WHERE filename = ? OR filename LIKE ?",
                (basename, f"%{basename}%")
            )
            row = cursor.fetchone()
            if row:
                if row[0]:
                    img.product_count = row[0]
                if row[1] and img.version_family == "":
                    img.version_family = row[1]

        conn.close()
    except (sqlite3.Error, Exception):
        pass  # DB not available or incompatible schema


def _search_db_for_package(db_path: str, package_name: str,
                           images: List[ImageInfo],
                           version: Optional[str] = None
                           ) -> List[ImageInfo]:
    """Search irix_packages.db for images containing a package.

    The DB has tables: images (filename, md5_hash, product_count),
    products (product_name, image_hash), and subsystems (name).
    We search product_name and subsystem names.

    Returns ImageInfo objects from the images list that match.
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Search product names and subsystem names
        like_pattern = f"%{package_name}%"
        cursor.execute(
            "SELECT DISTINCT i.filename FROM products p "
            "JOIN images i ON p.image_hash = i.md5_hash "
            "WHERE p.product_name LIKE ? COLLATE NOCASE",
            (like_pattern,)
        )
        matching_filenames = {row[0] for row in cursor.fetchall()}

        # Also search subsystem names
        cursor.execute(
            "SELECT DISTINCT i.filename FROM subsystems s "
            "JOIN products p ON s.product_id = p.id "
            "JOIN images i ON p.image_hash = i.md5_hash "
            "WHERE s.name LIKE ? COLLATE NOCASE",
            (like_pattern,)
        )
        matching_filenames.update(row[0] for row in cursor.fetchall())

        conn.close()

        # Match DB filenames to our ImageInfo objects
        results = []
        for img in images:
            basename = os.path.basename(img.path)
            if basename in matching_filenames:
                if version is None or _major_version(img.version_family) == \
                   _major_version(version):
                    results.append(img)

        return results
    except (sqlite3.Error, Exception):
        return []


# ── Main scanner ─────────────────────────────────────────────────────

def scan_software_library(base_dir: Optional[str] = None) -> ImageCatalog:
    """Walk software_library/ and build an ImageCatalog.

    Discovers all .img, .iso, and .efs.img files, categorizes them
    by filename pattern, and optionally enriches from SQLite DB.

    Args:
        base_dir: Path to software_library/. Defaults to PROJECT_ROOT/software_library.

    Returns:
        ImageCatalog with all discovered images.
    """
    if base_dir is None:
        base_dir = str(DEFAULT_SOFTWARE_LIBRARY)

    base = Path(base_dir)
    if not base.exists():
        return ImageCatalog(images=[], base_dir=str(base))

    images: List[ImageInfo] = []
    combo_dir = base / "prepackaged_combo_discs"

    for root, dirs, files in os.walk(base):
        root_path = Path(root)

        # Skip non-image directories (source code, docs, etc.)
        rel = root_path.relative_to(base)
        skip_prefixes = ("irix-", "extraced_irix_cds", "artifacts",
                         "Ian Mapleson")
        if any(str(rel).startswith(p) for p in skip_prefixes):
            continue

        for filename in sorted(files):
            # Match disc image extensions
            lower = filename.lower()
            if not (lower.endswith(".img") or lower.endswith(".iso")):
                continue

            filepath = str(root_path / filename)
            is_efs = lower.endswith(".efs.img")
            is_iso = lower.endswith(".iso")

            # Check if this is in the combo disc directory
            is_in_combo_dir = False
            try:
                root_path.relative_to(combo_dir)
                is_in_combo_dir = True
            except ValueError:
                pass

            if is_in_combo_dir:
                category = CATEGORY_COMBO
                has_sash = False
            else:
                category, has_sash = _categorize_filename(filename)

            version = _extract_version(filename)
            display_name = _make_display_name(filename)

            images.append(ImageInfo(
                path=filepath,
                category=category,
                version_family=version,
                display_name=display_name,
                is_efs=is_efs,
                is_iso=is_iso,
                is_combo=is_in_combo_dir,
                has_sash=has_sash,
            ))

    # Enrich from SQLite DB if available
    db_path = str(base / "irix_packages.db")
    _enrich_from_db(images, db_path)

    return ImageCatalog(images=images, base_dir=str(base))


# ── External library bridge ──────────────────────────────────────────

@dataclass
class ExternalMatch:
    """A match from the external software library."""
    path: str
    display_name: str
    category: str
    format: str
    size_display: str
    version: str = ""
    is_external: bool = True


def search_all(query: str,
               local_catalog: Optional[ImageCatalog] = None,
               external_db: Optional[str] = None,
               ) -> Tuple[List[ImageInfo], List[ExternalMatch]]:
    """Search both local software_library and external library index.

    Args:
        query: Search string (case-insensitive)
        local_catalog: Pre-scanned local catalog. If None, scans default.
        external_db: Path to external_library.db. If None, uses default.

    Returns:
        (local_matches, external_matches)
    """
    if local_catalog is None:
        local_catalog = scan_software_library()

    # Search local
    local_matches = local_catalog.find_package(query)

    # Search external
    external_matches: List[ExternalMatch] = []
    if external_db is None:
        external_db = str(DEFAULT_SOFTWARE_LIBRARY / "external_library.db")

    if os.path.exists(external_db):
        try:
            from pyirix_qemu.catalog.library import LibraryIndex
            idx = LibraryIndex(external_db)
            for entry in idx.search(query, limit=30):
                external_matches.append(ExternalMatch(
                    path=entry.path,
                    display_name=entry.display_name,
                    category=entry.category,
                    format=entry.format,
                    size_display=entry.size_display,
                    version=entry.version,
                ))
            idx.close()
        except Exception:
            pass  # External DB unavailable

    return local_matches, external_matches


# ── Convenience: resolve images for install_irix.py ──────────────────

@dataclass
class ResolvedInstall:
    """Resolved set of disc images for an IRIX installation."""
    boot_cd: Optional[ImageInfo] = None
    foundation_cds: List[ImageInfo] = field(default_factory=list)
    overlay_cds: List[ImageInfo] = field(default_factory=list)
    combo_image: Optional[ImageInfo] = None
    extra_cds: List[ImageInfo] = field(default_factory=list)
    all_images: List[ImageInfo] = field(default_factory=list)

    @property
    def use_combo(self) -> bool:
        """Whether a combo image is available and should be used."""
        return self.combo_image is not None

    def scsi_drives(self, disk_path: str) -> List[str]:
        """Build SCSI drive list for QEMU.

        Returns list of paths with appropriate suffixes (:cdrom, :ro).
        The install disk is NOT included — caller must prepend it.
        """
        drives = []
        if self.combo_image:
            drives.append(f"{self.combo_image.path}:ro")
        if self.boot_cd:
            drives.append(f"{self.boot_cd.path}:cdrom")
        for cd in self.foundation_cds:
            if cd.path != (self.boot_cd.path if self.boot_cd else ""):
                drives.append(f"{cd.path}:cdrom")
        return drives

    def summary(self) -> str:
        lines = []
        if self.combo_image:
            lines.append(f"Combo: {self.combo_image.display_name}")
        if self.boot_cd:
            lines.append(f"Boot CD: {self.boot_cd.display_name}")
        for cd in self.foundation_cds:
            lines.append(f"Foundation: {cd.display_name}")
        for cd in self.overlay_cds:
            lines.append(f"Overlay: {cd.display_name}")
        for cd in self.extra_cds:
            lines.append(f"Extra: {cd.display_name}")
        return "\n".join(lines)


def resolve_images(version: str,
                   categories: Optional[List[str]] = None,
                   base_dir: Optional[str] = None) -> ResolvedInstall:
    """Resolve disc images for an IRIX installation.

    High-level function that:
    1. Scans software_library
    2. Checks for combo image first (preferred path)
    3. Falls back to individual CDs

    Args:
        version: IRIX version (e.g. "6.5.5", "6.5", "6.2")
        categories: Additional categories beyond os_base/os_overlay
        base_dir: Override software_library path

    Returns:
        ResolvedInstall with all resolved images
    """
    catalog = scan_software_library(base_dir)
    result = ResolvedInstall()

    # Check for combo image first
    combo = catalog.get_combo_image(version)
    if combo:
        result.combo_image = combo
        result.all_images.append(combo)

    # Always find a boot CD (needed even with combo image)
    boot = catalog.get_boot_cd(version)
    if boot:
        result.boot_cd = boot
        result.all_images.append(boot)

    # Find foundation CDs
    foundations = catalog.get_foundation_cds(version)
    result.foundation_cds = foundations
    result.all_images.extend(foundations)

    # Find overlay CDs
    overlays = catalog.get_install_set(version, [CATEGORY_OS_OVERLAY])
    result.overlay_cds = [o for o in overlays if o not in result.all_images]
    result.all_images.extend(result.overlay_cds)

    # Find extra CDs from requested categories
    if categories:
        extras = catalog.get_install_set(version, categories)
        result.extra_cds = [e for e in extras if e not in result.all_images]
        result.all_images.extend(result.extra_cds)

    return result
