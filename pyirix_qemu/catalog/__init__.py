"""IRIX disc image and external library catalog tools."""
from pyirix_qemu.catalog.images import (
    scan_software_library,
    resolve_images,
    ImageCatalog,
    ImageInfo,
    ResolvedInstall,
    CATEGORY_OS_BASE,
    CATEGORY_OS_OVERLAY,
    CATEGORY_DEV_COMPILER,
    CATEGORY_DEV_TOOLS,
    CATEGORY_APPLICATIONS,
    CATEGORY_DEMOS,
    CATEGORY_NETWORKING,
)
from pyirix_qemu.catalog.library import (
    LibraryIndex,
    LibraryScanner,
    LibraryEntry,
    FileFormat,
    stage_file,
)
