"""Import scanned magazine issues from pre-prepared JPG files.

Scans must be organised as:
    scans/<Magazine>/<Year>/<Issue>/
        <Magazine>_<Year>_<Issue>_001.jpg
        <Magazine>_<Year>_<Issue>_002.jpg
        ...
        <Magazine>_<Year>_<Issue>_OCR.pdf   (optional companion OCR PDF)

Pages must already be at the desired output resolution — no resizing is done.
Covers are derived from page 001 and resized to 1200px height.

Text index priority per issue:
  1. Companion PDF (any *.pdf in the issue folder) — PyMuPDF text layer
  2. Tesseract OCR on page images (requires tesseract + pytesseract)
  3. Skipped if neither is available or --no-ocr is set

Usage:
    python import_scans.py                          # all issues under scans/
    python import_scans.py scans/TM/2014/10         # one specific issue
    python import_scans.py --no-ocr                 # skip text index
    python import_scans.py --no-thumbnails          # skip thumbnails
    python import_scans.py --tesseract-lang fin+eng # override OCR language
"""

import argparse
import io
import json
import re
import shutil
import sys
from pathlib import Path

from PIL import Image

# Reuse shared helpers from extract.py — no need to duplicate them.
from extract import (
    JPG_DIR,
    MANIFEST_FILE,
    SEARCH_INDEX_FILE,
    _resize_jpeg,
    _create_collage,
    _make_thumbnail,
    _clean_text,
    _text_from_tesseract,
    update_manifest,
    update_magazines_html,
)

SCAN_DIR          = Path("scans")
PAGE_RE           = re.compile(r"^.+_(\d{4})_(\d{2,})_(\d{3})\.(jpg|jpeg)$", re.IGNORECASE)
MIN_TEXT_CHARS    = 50
DEFAULT_COVER_H   = 1200
DEFAULT_THUMB_H   = 300
DEFAULT_TESS_LANG = "fin+eng"


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def discover_issue_dirs(root: Path) -> list[tuple[str, str, str, Path]]:
    """Return (magazine, year, issue, scan_dir) tuples found under root.

    root may be:
      - SCAN_DIR                 → walk all mag/year/issue subdirs
      - scans/<Mag>              → walk year/issue subdirs of that magazine
      - scans/<Mag>/<Year>       → walk issue subdirs of that year
      - scans/<Mag>/<Year>/<Iss> → single issue (returned directly)
    """
    try:
        depth = len(root.relative_to(SCAN_DIR).parts)
    except ValueError:
        depth = 0   # root is not under SCAN_DIR — treat as scan root

    def _iter_issues(mag_dir: Path):
        for year_dir in sorted(mag_dir.iterdir()):
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            for issue_dir in sorted(year_dir.iterdir()):
                if issue_dir.is_dir():
                    yield mag_dir.name, year_dir.name, issue_dir.name, issue_dir

    if depth == 3:                          # direct issue dir
        parts = root.relative_to(SCAN_DIR).parts
        return [(*parts, root)]
    elif depth == 2:                        # year dir
        mag, year = root.relative_to(SCAN_DIR).parts
        return [(mag, year, d.name, d) for d in sorted(root.iterdir()) if d.is_dir()]
    elif depth == 1:                        # mag dir
        return list(_iter_issues(root))
    else:                                   # scan root or external path
        found = []
        for mag_dir in sorted(root.iterdir()):
            if mag_dir.is_dir():
                found.extend(_iter_issues(mag_dir))
        return found


def get_page_files(scan_dir: Path) -> list[Path]:
    """Sorted page JPGs matching *_NNN.jpg in scan_dir."""
    return sorted(p for p in scan_dir.iterdir() if PAGE_RE.match(p.name))


def find_companion_pdf(scan_dir: Path) -> Path | None:
    """First PDF found in the issue scan directory, or None."""
    pdfs = sorted(scan_dir.glob("*.pdf"))
    return pdfs[0] if pdfs else None


def discover_all_magazines() -> list[str]:
    """Magazine names from pdf/, jpg/, and scans/ combined."""
    mags = set()
    for base in [Path("pdf"), JPG_DIR, SCAN_DIR]:
        if base.is_dir():
            mags.update(d.name for d in base.iterdir() if d.is_dir())
    return sorted(mags)


# ---------------------------------------------------------------------------
# Stage 1 — copy pages + cover + collage
# ---------------------------------------------------------------------------

def run_import(issues: list[tuple], cover_height: int) -> None:
    print(f"\n[Stage 1] Importing pages ({len(issues)} issue(s))...")

    cover_images: dict[tuple, list] = {}   # (mag, year) -> [PIL.Image]
    new_covers:   set[tuple]        = set()

    for mag, year, issue, scan_dir in issues:
        pages   = get_page_files(scan_dir)
        out_dir = JPG_DIR / mag / year / issue
        key     = (mag, year)
        cover_images.setdefault(key, [])

        if not pages:
            print(f"  {mag}/{year}/{issue}: no page JPGs found, skipping")
            continue

        # Skip if last expected page already present in output
        if (out_dir / pages[-1].name).exists():
            print(f"  {mag}/{year}/{issue}: already imported ({len(pages)} pages), skipping")
            cover_jpg = JPG_DIR / mag / year / f"{mag}_{year}_{issue}_cover.jpg"
            if cover_jpg.exists():
                cover_images[key].append(Image.open(cover_jpg))
            continue

        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"  {mag}/{year}/{issue}: copying {len(pages)} page(s)...")
        for page in pages:
            dst = out_dir / page.name
            if not dst.exists():
                shutil.copy2(page, dst)
            print(f"    {page.name}")

        # Cover from first page, resized
        cover_dst = JPG_DIR / mag / year / f"{mag}_{year}_{issue}_cover.jpg"
        if not cover_dst.exists():
            data = _resize_jpeg(pages[0].read_bytes(), cover_height)
            cover_dst.write_bytes(data)
            print(f"    cover -> {cover_dst.name}")
        cover_images[key].append(Image.open(cover_dst))
        new_covers.add(key)

    # Collages — only rebuild if at least one new cover was added for that year
    print("  Creating collages...")
    for (mag, year), imgs in sorted(cover_images.items()):
        collage_path = JPG_DIR / mag / f"collage_{year}.jpg"
        if (mag, year) not in new_covers and collage_path.exists():
            print(f"    collage_{year}.jpg: exists, skipping")
            continue
        if imgs:
            _create_collage(imgs, collage_path)


# ---------------------------------------------------------------------------
# Stage 2 — thumbnails
# ---------------------------------------------------------------------------

def run_thumbnails(issues: list[tuple], thumb_height: int) -> None:
    print(f"\n[Stage 2] Generating thumbnails ({len(issues)} issue(s), {thumb_height}px)...")
    for mag, year, issue, _ in issues:
        issue_dir = JPG_DIR / mag / year / issue
        if not issue_dir.is_dir():
            print(f"  {mag}/{year}/{issue}: output dir not found, skipping")
            continue
        pages = sorted(issue_dir.glob("*_[0-9][0-9][0-9].jpg"))
        if not pages:
            print(f"  {mag}/{year}/{issue}: no pages found, skipping")
            continue
        thumb_dir = issue_dir / "thumbnails"
        to_do = [p for p in pages if not (thumb_dir / p.name).exists()]
        if not to_do:
            print(f"  {mag}/{year}/{issue}: all {len(pages)} thumbnails exist, skipping")
            continue
        thumb_dir.mkdir(exist_ok=True)
        print(f"  {mag}/{year}/{issue}: {len(to_do)}/{len(pages)} thumbnails...")
        for p in to_do:
            _make_thumbnail(p, thumb_dir, thumb_height)


# ---------------------------------------------------------------------------
# Stage 3 — text index
# ---------------------------------------------------------------------------

def _text_from_pdf(pdf_path: Path, mag: str, year: str, issue: str) -> list[dict]:
    """Extract text from companion OCR PDF via PyMuPDF."""
    try:
        import fitz
    except ImportError:
        print("    pymupdf not installed — skipping PDF text extraction")
        return []

    doc, entries = fitz.open(pdf_path), []
    for i, page in enumerate(doc):
        page_num = i + 1
        if page_num == 1:
            continue  # skip cover
        text = _clean_text(page.get_text())
        if len(text) >= MIN_TEXT_CHARS:
            entries.append({"mag": mag, "year": year, "issue": issue,
                            "page": page_num, "text": text})
    doc.close()
    return entries



def run_text_index(issues: list[tuple], tess_lang: str) -> None:
    print(f"\n[Stage 3] Building text index ({len(issues)} issue(s))...")

    # Load existing index
    pages: list[dict] = []
    no_text: set[str] = set()
    done: set[str]    = set()
    if SEARCH_INDEX_FILE.exists():
        raw   = json.loads(SEARCH_INDEX_FILE.read_text(encoding="utf-8"))
        pages   = raw.get("pages",   []) if isinstance(raw, dict) else raw
        no_text = set(raw.get("no_text", [])) if isinstance(raw, dict) else set()
        done    = set(raw.get("done",    [])) if isinstance(raw, dict) else set()

    changed = False
    for mag, year, issue, scan_dir in issues:
        key = f"{mag}/{year}/{issue}"

        if key in done:
            print(f"  {key}: already indexed, skipping")
            continue
        if key in no_text:
            print(f"  {key}: no text (cached), skipping")
            continue

        scan_pages = get_page_files(scan_dir)
        companion  = find_companion_pdf(scan_dir)

        if companion:
            print(f"  {key}: companion PDF found ({companion.name}), extracting text...")
            new_entries = _text_from_pdf(companion, mag, year, issue)
        else:
            print(f"  {key}: no companion PDF — running tesseract ({tess_lang})...")
            new_entries = _text_from_tesseract(scan_pages, mag, year, issue, tess_lang)

        if new_entries:
            pages.extend(new_entries)
            done.add(key)
            print(f"    {len(new_entries)} page(s) indexed")
        else:
            no_text.add(key)
            print(f"    no usable text found")
        changed = True

    if changed:
        out = {"pages": pages, "no_text": sorted(no_text), "done": sorted(done)}
        SEARCH_INDEX_FILE.write_text(
            json.dumps(out, ensure_ascii=False, separators=(',', ':')),
            encoding="utf-8",
        )
        print(f"  {SEARCH_INDEX_FILE}: {len(pages)} page(s) total, "
              f"{len(done)} indexed, {len(no_text)} no-text")
    else:
        print(f"  {SEARCH_INDEX_FILE}: no changes")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import scanned magazine issues from JPG files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "dirs", nargs="*",
        help="Specific issue/magazine/year directory/directories to import "
             "(default: all under scans/)",
    )
    parser.add_argument("--no-thumbnails",      action="store_true",
                        help="Skip thumbnail generation (stage 2)")
    parser.add_argument("--no-ocr",             action="store_true",
                        help="Skip text index entirely (stage 3)")
    parser.add_argument("--no-html-update",     action="store_true",
                        help="Skip updating MAGAZINES in HTML files")
    parser.add_argument("--no-manifest-update", action="store_true",
                        help="Skip rebuilding manifest.json")
    parser.add_argument("--cover-height",  type=int, default=DEFAULT_COVER_H,
                        help=f"Cover JPEG height in pixels (default: {DEFAULT_COVER_H})")
    parser.add_argument("--thumb-height",  type=int, default=DEFAULT_THUMB_H,
                        help=f"Thumbnail height in pixels (default: {DEFAULT_THUMB_H})")
    parser.add_argument("--tesseract-lang", default=DEFAULT_TESS_LANG,
                        help=f"Tesseract language string (default: {DEFAULT_TESS_LANG})")
    args = parser.parse_args()

    # Resolve input roots
    if args.dirs:
        roots = [Path(d) for d in args.dirs]
        missing = [r for r in roots if not r.is_dir()]
        if missing:
            for r in missing:
                print(f"ERROR: directory not found: {r}", file=sys.stderr)
            sys.exit(1)
    else:
        if not SCAN_DIR.is_dir():
            print(f"ERROR: scans directory not found: {SCAN_DIR}", file=sys.stderr)
            sys.exit(1)
        roots = [SCAN_DIR]

    # Discover all issues to process
    issues = []
    for root in roots:
        issues.extend(discover_issue_dirs(root))

    if not issues:
        print("No issue directories found.")
        return

    print(f"Found {len(issues)} issue(s):")
    for mag, year, issue, scan_dir in issues:
        pages     = get_page_files(scan_dir)
        companion = find_companion_pdf(scan_dir)
        ocr_note  = f" + OCR PDF ({companion.name})" if companion else " (tesseract fallback)"
        print(f"  {mag}/{year}/{issue}: {len(pages)} page(s){ocr_note}")

    # Stage 1 — copy pages, cover, collage (creates jpg/ dirs first)
    run_import(issues, args.cover_height)

    # Stage 0 — sync MAGAZINES in HTML (run after stage 1 so new mag dirs exist)
    if not args.no_html_update:
        print("\n[Stage 0] Syncing MAGAZINES in HTML files...")
        mags = discover_all_magazines()
        if mags:
            update_magazines_html(mags)

    # Stage 2 — thumbnails
    if not args.no_thumbnails:
        run_thumbnails(issues, args.thumb_height)

    # Stage 3 — text index
    if not args.no_ocr:
        run_text_index(issues, args.tesseract_lang)

    # Manifest update — after all stages so page counts are accurate
    if not args.no_manifest_update:
        print("\n[Manifest] Updating manifest.json...")
        update_manifest()

    print("\nDone!")


if __name__ == "__main__":
    main()
