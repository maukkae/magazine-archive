import os
from pathlib import Path


def _path_from_env(name: str, default: str | Path) -> Path:
    if isinstance(default, Path):
        default = str(default)
    return Path(os.environ.get(name, default))


ARCHIVE_ROOT = _path_from_env("ARCHIVE_ROOT", ".")

PDF_DIR = _path_from_env("ARCHIVE_PDF_DIR", ARCHIVE_ROOT / "pdf")
JPG_DIR = _path_from_env("ARCHIVE_JPG_DIR", ARCHIVE_ROOT / "jpg")
SCAN_DIR = _path_from_env("ARCHIVE_SCAN_DIR", ARCHIVE_ROOT / "scans")
MANIFEST_FILE = _path_from_env("ARCHIVE_MANIFEST_FILE", ARCHIVE_ROOT / "manifest.json")
SEARCH_INDEX_FILE = _path_from_env("ARCHIVE_SEARCH_INDEX_FILE", ARCHIVE_ROOT / "search_index.json")

_html_env = os.environ.get("ARCHIVE_HTML_FILES")
if _html_env:
    HTML_FILES = [Path(part) for part in _html_env.split(os.pathsep) if part.strip()]
else:
    HTML_FILES = [
        _path_from_env("ARCHIVE_CAROUSEL_FILE", ARCHIVE_ROOT / "carousel.html"),
        _path_from_env("ARCHIVE_MOBILE_FILE", ARCHIVE_ROOT / "mobile.html"),
    ]
