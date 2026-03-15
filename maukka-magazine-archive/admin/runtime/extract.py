"""Magazine extraction pipeline — covers, pages, and thumbnails in one command.

Also keeps the MAGAZINES constant in carousel.html and mobile.html in sync
with whatever subdirectories exist under pdf/.

Stages (all run by default):
  1. Extract cover JPEGs
  2. Extract full-page JPEGs
  3. Generate thumbnails

Usage:
    python extract.py                                 # all stages, all PDFs
    python extract.py --height 1440                   # custom page height
    python extract.py --covers-only                   # stage 1 only
    python extract.py --no-content                    # skip stage 2
    python extract.py --no-thumbnails                 # skip stage 3
    python extract.py --no-html-update                # skip HTML sync
    python extract.py pdf/Magazine/Magazine_1995_06.pdf     # one PDF (stages 1–3)
    python extract.py pdf/Magazine/Magazine_1995_06.pdf --covers-only
"""

import argparse
import io
import json
import os
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageFilter
from archive_paths import HTML_FILES, JPG_DIR, MANIFEST_FILE, PDF_DIR, SEARCH_INDEX_FILE
from ollama_ocr import ollama_enabled_from_env, ocr_pages_with_ollama
from search_store import normalize_issue_id, read_index_json, sync_issue_db, write_index_json

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MIN_TEXT_CHARS     = 50   # chars per page to count as having text content
DEFAULT_TESS_LANG  = "fin+eng"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def parse_pdf(pdf_path: Path):
    """Return (magazine, year, issue) for a PDF under pdf/<Magazine>/.

    Supplement PDFs named ``Magazine_YYYY_MM_liite[_description].pdf``
    get issue code ``MM-liite`` (e.g. ``03-liite``).
    """
    magazine  = pdf_path.parent.name
    remainder = pdf_path.stem[len(magazine) + 1:]   # strip 'Magazine_' prefix
    parts     = remainder.split("_")
    year  = parts[0]
    issue = normalize_issue_id(parts[1]) if len(parts) > 1 else "01"
    if len(parts) > 2 and parts[2].lower() == "liite":
        issue = f"{issue}-liite"
    return magazine, year, issue


# ---------------------------------------------------------------------------
# PDF margin crop (print-ready PDFs with TrimBox)
# ---------------------------------------------------------------------------

def _normalize_print_name(src: Path) -> Path:
    """Derive output filename for a cropped print PDF.

    Strips any ``_print`` suffix, then fixes MM_YYYY date ordering to YYYY_MM:
      Magazine_08_2009_print.pdf  ->  Magazine_2009_08.pdf
      Magazine_01_2010.pdf        ->  Magazine_2010_01.pdf
      SomeMag_2009_08_print.pdf      ->  SomeMag_2009_08.pdf   (already YYYY_MM)
    If neither condition applies a ``_cropped`` suffix is used instead.
    """
    stem = src.stem
    stripped_print = stem.endswith("_print")
    if stripped_print:
        stem = stem[:-6]    # remove "_print"

    parts = stem.split("_")
    date_reordered = False
    if len(parts) >= 2:
        last, second_last = parts[-1], parts[-2]
        # Detect MM_YYYY: last part is 4-digit year, second-last is 1-2 digit issue
        if (len(last) == 4 and last.isdigit()
                and 1 <= len(second_last) <= 2 and second_last.isdigit()):
            parts[-2], parts[-1] = last, second_last.zfill(2)
            stem = "_".join(parts)
            date_reordered = True

    if stripped_print or date_reordered:
        out = src.parent / f"{stem}.pdf"
        # Safety: never silently overwrite an unrelated existing file with a
        # different stem than what we'd expect.
        return out
    return src.parent / f"{src.stem}_cropped.pdf"


def crop_to_trimbox(src: Path) -> Path | None:
    """Crop a print PDF to its TrimBox and save as a new file.

    Each page is cropped to its own TrimBox.  For uniform PDFs (all pages the
    same size) every output JPEG ends up identical in pixel dimensions.  For
    mixed-size PDFs (e.g. a portrait cover followed by landscape spreads) each
    page group retains its own natural size — this is required so that spread
    detection and splitting in Stage 2 can work on the correct dimensions.

    The original file is never modified.  Returns the output path on success,
    or ``None`` if the PDF has no usable TrimBox margin (TrimBox ≈ MediaBox).
    """
    out_path = _normalize_print_name(src)
    if out_path == src:
        print(f"  {src.name}: output would overwrite source — skipping")
        return None
    # Always re-crop: the _print.pdf is the authoritative original, so the
    # cropped copy can safely be regenerated at any time.

    # Minimum bleed margin to be considered a real print PDF (points).
    # Typical print bleed is 3–5 mm = 8–14 pt.  Scanner noise is < 2 pt.
    MIN_BLEED_PT = 5.0

    doc = fitz.open(src)
    trimboxes = [page.trimbox for page in doc]

    # Check that at least one page has a TrimBox meaningfully inside its MediaBox
    # (margin on any side must exceed MIN_BLEED_PT to count as real print bleed)
    def _has_bleed(page) -> bool:
        mb, tb = page.mediabox, page.trimbox
        return (tb.x0 - mb.x0 > MIN_BLEED_PT or mb.x1 - tb.x1 > MIN_BLEED_PT or
                tb.y0 - mb.y0 > MIN_BLEED_PT or mb.y1 - tb.y1 > MIN_BLEED_PT)

    if not any(_has_bleed(p) for p in doc):
        print(f"  {src.name}: no significant TrimBox margin — skipping")
        doc.close()
        return None

    # Summarise unique page sizes for the log line
    sizes = sorted({f"{tb.width:.1f}x{tb.height:.1f}" for tb in trimboxes})
    print(f"  {src.name}: cropping {len(doc)} pages "
          f"({', '.join(sizes)} pt) -> {out_path.name}")

    for page, tb in zip(doc, trimboxes):
        # Apply each page's own TrimBox as its crop box
        page.set_cropbox(tb)

    doc.save(str(out_path))
    doc.close()
    print(f"    saved: {out_path}")
    return out_path


def issue_dir_for_pdf(pdf_path: Path) -> Path:
    mag, year, issue = parse_pdf(pdf_path)
    return JPG_DIR / mag / year / issue

# ---------------------------------------------------------------------------
# Stage 0 — sync MAGAZINES in HTML files
# ---------------------------------------------------------------------------

def discover_magazines() -> list[str]:
    """Sorted list of magazine names discovered from pdf/ and jpg/.

    Using both roots avoids accidentally shrinking MAGAZINES when only a subset
    of PDFs is present on disk but previously extracted magazines still exist
    under jpg/.
    """
    mags = set()
    if PDF_DIR.is_dir():
        mags.update(d.name for d in PDF_DIR.iterdir() if d.is_dir())
    if JPG_DIR.is_dir():
        mags.update(d.name for d in JPG_DIR.iterdir() if d.is_dir())
    return sorted(mags)


def update_magazines_html(magazines: list[str]) -> None:
    if not magazines:
        return
    mag_list = ", ".join(f"'{m}'" for m in magazines)
    pattern  = re.compile(r"([ \t]*)const MAGAZINES = \[.*?\];")
    for html_path in HTML_FILES:
        if not html_path.exists():
            print(f"  {html_path}: not found, skipping")
            continue
        original = html_path.read_text(encoding="utf-8")
        updated  = pattern.sub(
            lambda m: f"{m.group(1)}const MAGAZINES = [{mag_list}];",
            original,
        )
        if updated != original:
            html_path.write_text(updated, encoding="utf-8")
            print(f"  {html_path}: updated MAGAZINES = [{mag_list}]")
        else:
            print(f"  {html_path}: MAGAZINES already up to date")

# ---------------------------------------------------------------------------
# Manifest update
# ---------------------------------------------------------------------------

COVER_RE = re.compile(r"^(.+)_(\d{4})_(\d{1,4}(?:-[\w]+)?)_cover\.jpg$")


def update_manifest() -> None:
    """Rebuild manifest.json by scanning the jpg/ directory tree."""
    manifest: dict = {}
    if not JPG_DIR.is_dir():
        return
    for mag_dir in sorted(JPG_DIR.iterdir()):
        if not mag_dir.is_dir():
            continue
        mag_data: dict = {}
        for year_dir in sorted(mag_dir.iterdir()):
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            year_data: dict = {}
            for fname in sorted(f.name for f in year_dir.iterdir()):
                m = COVER_RE.match(fname)
                if not m:
                    continue
                issue     = m.group(3)
                issue_dir = year_dir / issue
                page_count = 0
                if issue_dir.is_dir():
                    # Count JPEGs directly in issue_dir (non-recursive —
                    # thumbnails/ subdir files are not enumerated here).
                    page_count = sum(
                        1 for f in issue_dir.iterdir()
                        if f.suffix.lower() == ".jpg" and f.is_file()
                    )
                year_data[issue] = page_count
            if year_data:
                mag_data[year_dir.name] = year_data
        if mag_data:
            manifest[mag_dir.name] = mag_data

    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    total = sum(len(issues) for mag in manifest.values() for issues in mag.values())
    print(f"  {MANIFEST_FILE}: {len(manifest)} magazine(s), {total} issue(s)")


# ---------------------------------------------------------------------------
# Stage 1 — covers
# ---------------------------------------------------------------------------

def _apply_unsharp(img: Image.Image,
                   radius: float, percent: int, threshold: int) -> Image.Image:
    return img.filter(ImageFilter.UnsharpMask(
        radius=radius, percent=percent, threshold=threshold))


def _resize_jpeg(jpeg_bytes: bytes, target_height: int,
                 sharpen: tuple | None = None) -> bytes:
    img   = Image.open(io.BytesIO(jpeg_bytes))
    ratio = target_height / img.height
    img   = img.resize((int(img.width * ratio), target_height), Image.LANCZOS)
    if sharpen:
        img = _apply_unsharp(img, *sharpen)
    buf   = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _extract_page_as_jpeg(doc, page_num: int):
    if page_num > len(doc):
        return None
    page     = doc[page_num - 1]
    has_text = bool(page.get_text().strip())
    embedded = page.get_images(full=True)

    if len(embedded) == 1 and not has_text:
        xref     = embedded[0][0]
        img_dict = doc.extract_image(xref)
        if img_dict["ext"].lower() in ("jpg", "jpeg"):
            return img_dict["image"]
        pix = fitz.Pixmap(doc, xref)
        if pix.n > 3:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        return pix.tobytes("jpeg")

    if not has_text and not embedded:
        return None

    pix = page.get_pixmap(matrix=fitz.Matrix(150 / 72.0, 150 / 72.0))
    return pix.tobytes("jpeg")


def run_covers(pdf_files: list[Path],
               sharpen: tuple | None = None,
               force: bool = False) -> None:
    print(f"\n[Stage 1] Extracting covers ({len(pdf_files)} PDF(s))...")

    for pdf_path in pdf_files:
        magazine, year, issue = parse_pdf(pdf_path)
        prefix      = f"{magazine}_{year}_{issue}"
        out_dir     = JPG_DIR / magazine / year
        jpg_path    = out_dir / f"{prefix}_cover.jpg"

        if jpg_path.exists() and not force:
            print(f"  {pdf_path.name}: cover exists, skipping")
            continue

        print(f"  {pdf_path.name}: extracting cover...")
        doc        = fitz.open(pdf_path)
        jpeg_bytes = _extract_page_as_jpeg(doc, 1)
        doc.close()
        if not jpeg_bytes:
            print(f"    WARNING: no cover image found")
            continue
        jpeg_bytes = _resize_jpeg(jpeg_bytes, 1200, sharpen=sharpen)
        out_dir.mkdir(parents=True, exist_ok=True)
        jpg_path.write_bytes(jpeg_bytes)
        print(f"    saved: {jpg_path}")

# ---------------------------------------------------------------------------
# Stage 2 — full pages
# ---------------------------------------------------------------------------

def _render_page(page, target_height: int,
                 sharpen: tuple | None = None) -> bytes:
    scale = target_height / page.rect.height
    pix   = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    if pix.n > 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if sharpen:
        img = _apply_unsharp(img, *sharpen)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()


def _detect_spread_pdf(doc) -> bool:
    """Return True if this PDF uses landscape spread pages (two magazine pages side by side).

    Detects by checking whether the second page (first non-cover page) is in
    landscape orientation: width > height × 1.3.  Typical for supplement PDFs
    printed as A3 spreads where each PDF page contains two A4 magazine pages.
    """
    if len(doc) < 2:
        return False
    r = doc[1].rect   # PyMuPDF: cropbox if set, else MediaBox
    return r.width > r.height * 1.3


def _split_spread_jpeg(jpeg_bytes: bytes) -> tuple[bytes, bytes]:
    """Split a landscape spread JPEG into left and right half-pages.

    Returns (left_jpeg_bytes, right_jpeg_bytes).  The split is at the exact
    horizontal midpoint; if the spread has a gutter or bleed that lands off-
    centre the caller should pre-crop the PDF with --crop (TrimBox) first.
    """
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    w, h = img.size
    mid  = w // 2

    def _to_jpeg(im: Image.Image) -> bytes:
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=92, optimize=True)
        return buf.getvalue()

    return _to_jpeg(img.crop((0, 0, mid, h))), _to_jpeg(img.crop((mid, 0, w, h)))


def _is_spread_page(page) -> bool:
    """Return True if this individual page is a landscape spread (two pages side by side)."""
    r = page.rect
    return r.width > r.height * 1.3


def run_content(pdf_files: list[Path], target_height: int,
                sharpen: tuple | None = None,
                force: bool = False) -> None:
    print(f"\n[Stage 2] Extracting pages ({len(pdf_files)} PDF(s), {target_height}px)...")
    for pdf_path in pdf_files:
        magazine, year, issue = parse_pdf(pdf_path)
        prefix    = f"{magazine}_{year}_{issue}"
        out_dir   = JPG_DIR / magazine / year / issue
        doc       = fitz.open(pdf_path)
        n_pages   = len(doc)
        # Count output pages per PDF page: spreads (i > 0, landscape) yield 2; others yield 1
        page_is_spread = [i > 0 and _is_spread_page(p) for i, p in enumerate(doc)]
        n_output  = sum(2 if s else 1 for s in page_is_spread)
        last_file = out_dir / f"{prefix}_{n_output:03d}.jpg"
        if last_file.exists() and not force:
            print(f"  {pdf_path.name}: already extracted ({n_output} pages), skipping")
            doc.close()
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        n_spreads = sum(page_is_spread)
        spread_note = f" [spread: {n_spreads} spread page(s) -> {n_output} output pages]" if n_spreads else ""
        print(f"  {pdf_path.name}: {n_pages} page(s){spread_note} -> {out_dir}")
        out_num = 1
        for page, is_spread in zip(doc, page_is_spread):
            if is_spread:
                # Render the full spread, then split into left + right portrait pages
                spread_bytes = _render_page(page, target_height, sharpen=sharpen)
                left_bytes, right_bytes = _split_spread_jpeg(spread_bytes)
                for page_bytes in (left_bytes, right_bytes):
                    filename = f"{prefix}_{out_num:03d}.jpg"
                    (out_dir / filename).write_bytes(page_bytes)
                    print(f"    {filename}")
                    out_num += 1
            else:
                filename = f"{prefix}_{out_num:03d}.jpg"
                (out_dir / filename).write_bytes(_render_page(page, target_height, sharpen=sharpen))
                print(f"    {filename}")
                out_num += 1
        doc.close()

# ---------------------------------------------------------------------------
# Stage 3 — thumbnails
# ---------------------------------------------------------------------------

def _make_thumbnail(src: Path, out_dir: Path, target_height: int) -> None:
    img   = Image.open(src).convert("RGB")
    ratio = target_height / img.height
    img   = img.resize((max(1, int(img.width * ratio)), target_height), Image.LANCZOS)
    img.save(out_dir / src.name, "JPEG", quality=75, optimize=True)


def run_thumbnails(issue_dirs: list[Path], target_height: int, manifest: dict) -> None:
    print(f"\n[Stage 3] Generating thumbnails ({len(issue_dirs)} issue(s), {target_height}px)...")
    for issue_dir in issue_dirs:
        mag, year, issue = issue_dir.relative_to(JPG_DIR).parts
        page_count = manifest.get(mag, {}).get(year, {}).get(issue, 0)
        thumb_dir  = issue_dir / "thumbnails"

        # Fast path: check if last expected thumbnail already exists
        if page_count > 0 and thumb_dir.is_dir():
            last_thumb = thumb_dir / f"{mag}_{year}_{issue}_{page_count:03d}.jpg"
            if last_thumb.exists():
                print(f"  {issue_dir}: all {page_count} thumbnails exist, skipping")
                continue

        pages = sorted(issue_dir.glob("*_[0-9][0-9][0-9].jpg"))
        if not pages:
            print(f"  {issue_dir}: no pages found, skipping")
            continue
        thumb_dir.mkdir(exist_ok=True)
        to_process = [p for p in pages if not (thumb_dir / p.name).exists()]
        if not to_process:
            print(f"  {issue_dir}: all {len(pages)} thumbnails exist, skipping")
            continue
        print(f"  {issue_dir}: {len(to_process)}/{len(pages)} thumbnails...")
        for p in to_process:
            _make_thumbnail(p, thumb_dir, target_height)

# ---------------------------------------------------------------------------
# Stage 4 — text index (digital/text-based PDFs only)
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    """Normalize extracted PDF text.

    - Remove soft hyphens (PDF line-break artifact)
    - Strip characters outside basic Latin + Latin Extended (0xC0-0x24F),
      which catches garbled OCR symbols while keeping Finnish/European chars
    - Join hyphenated line-break words like ``kuin -ka`` -> ``kuinka`` and
      ``kaiutin -ten`` -> ``kaiutinten``
    - Join likely dehyphenated line-break fragments like
      ``menevyyt\n tä`` -> ``menevyyttä`` and ``pal\n jon`` -> ``paljon``
    - Collapse whitespace
    """
    text = text.replace('\xad', '')  # soft hyphen
    cleaned = []
    for c in text:
        cp = ord(c)
        if 0x20 <= cp <= 0x7E or 0xC0 <= cp <= 0x24F or c in '\n\t':
            cleaned.append(c)
        else:
            cleaned.append(' ')
    text = ''.join(cleaned)
    # Some embedded-text PDFs lose the actual hyphen but keep a line break in the
    # middle of the word. Join only short lowercase continuations so we do not
    # aggressively merge ordinary word boundaries across lines.
    finnish_short_words = (
        "ja|on|ei|se|ne|jo|kuin|kun|tai|vaan|että|joka|joka|jos|oli|ovat|voi|myös|vain"
    )
    text = re.sub(
        rf'(?<![.!?:;])\b([a-zåäö]{{3,}})\s*\n\s*(?!{finnish_short_words}\b)([a-zåäö]{{1,4}})\b',
        r'\1\2',
        text,
    )
    # Join cases where the next line starts with a hyphenated continuation:
    # "kaiutin\n-ten" -> "kaiutinten"
    text = re.sub(
        r'([A-Za-zÅÄÖåäö]+)\s+-\s*([A-Za-zÅÄÖåäö]+)',
        r'\1\2',
        text,
    )
    text = re.sub(
        r'([A-Za-zÅÄÖåäö]+)\s*-\s+([A-Za-zÅÄÖåäö]+)',
        r'\1\2',
        text,
    )
    return ' '.join(text.split())


def _text_from_tesseract(page_files: list[Path], mag: str, year: str,
                          issue: str, lang: str) -> list[dict]:
    """OCR a list of page image files with tesseract and return index entries."""
    try:
        import pytesseract
    except ImportError:
        print("    pytesseract not installed (pip install pytesseract) — skipping")
        return []

    entries = []
    for page_path in sorted(page_files):
        m = re.search(r'_(\d{3})\.jpe?g$', page_path.name, re.IGNORECASE)
        if not m:
            continue
        page_num = int(m.group(1))
        if page_num == 1:
            continue  # skip cover
        try:
            text = _clean_text(
                pytesseract.image_to_string(Image.open(page_path), lang=lang)
            )
        except (pytesseract.TesseractNotFoundError, FileNotFoundError):
            print("    tesseract executable not found — skipping image OCR fallback")
            return []
        if len(text) >= MIN_TEXT_CHARS:
            entries.append({"mag": mag, "year": year, "issue": issue,
                            "page": page_num, "text": text})
            print(f"    page {page_num:03d}: {len(text)} chars")
        else:
            print(f"    page {page_num:03d}: no usable text")
    return entries


def run_text_index(pdf_files: list[Path], manifest: dict,
                   tess_lang: str = DEFAULT_TESS_LANG,
                   no_tesseract: bool = False) -> None:
    print(f"\n[Stage 4] Building text index ({len(pdf_files)} PDF(s))...")

    # Load existing index.
    # Format: {"pages": [...], "no_text": [...], "done": [...]}
    #   no_text: issues confirmed scanned (no extractable text)
    #   done:    issues fully processed (text extracted, may have sparse pages)
    pages: list[dict] = []
    no_text: set[str] = set()
    done: set[str]    = set()

    if SEARCH_INDEX_FILE.exists():
        data = read_index_json(SEARCH_INDEX_FILE)
        pages = data.get("pages", [])
        no_text = set(data.get("no_text", []))
        done = set(data.get("done", []))

    changed = False
    changed_issues: set[tuple[str, str, str]] = set()
    for pdf_path in pdf_files:
        magazine, year, issue = parse_pdf(pdf_path)
        issue_key = f"{magazine}/{year}/{issue}"
        issue_dir = JPG_DIR / magazine / year / issue
        page_files = sorted(issue_dir.glob("*_[0-9][0-9][0-9].jpg"))

        # Fast skip: already processed (either scanned or fully indexed)
        if issue_key in no_text or issue_key in done:
            label = "no text (cached)" if issue_key in no_text else "already indexed"
            print(f"  {pdf_path.name}: {label}, skipping")
            continue

        doc = fitz.open(pdf_path)

        # Detect scanned PDF (no selectable text on first 10 pages)
        has_text = any(
            len(doc[i].get_text().strip()) >= MIN_TEXT_CHARS
            for i in range(min(10, len(doc)))
        )

        if ollama_enabled_from_env():
            if has_text:
                print(f"  {pdf_path.name}: embedded text layer detected — remote Ollama OCR forced by request...")
            else:
                print(f"  {pdf_path.name}: no text layer — trying remote Ollama OCR...")

            if page_files:
                new_entries = ocr_pages_with_ollama(
                    page_files, magazine, year, issue
                )
                if new_entries:
                    doc.close()
                    pages.extend(new_entries)
                    done.add(issue_key)
                    changed = True
                    changed_issues.add((magazine, year, issue))
                    print(f"    {len(new_entries)} page(s) indexed via remote Ollama OCR")
                    continue
                print("    no usable text via remote Ollama OCR")
                if has_text:
                    print("    falling back to embedded PDF text extraction...")
                elif not no_tesseract:
                    print(f"    falling back to tesseract ({tess_lang})...")
            else:
                if has_text:
                    print(f"  {pdf_path.name}: embedded text layer detected — no page JPGs for remote Ollama OCR, using native PDF text extraction")
                else:
                    print(f"  {pdf_path.name}: no text layer, no page JPGs for remote Ollama OCR")

        if not has_text:
            doc.close()
            if not no_tesseract:
                if page_files:
                    new_entries = _text_from_tesseract(
                        page_files, magazine, year, issue, tess_lang)
                    if new_entries:
                        pages.extend(new_entries)
                        done.add(issue_key)
                        changed = True
                        changed_issues.add((magazine, year, issue))
                        print(f"    {len(new_entries)} page(s) indexed via tesseract")
                        continue
                    else:
                        print(f"    no usable text via tesseract")
                else:
                    print(f"  {pdf_path.name}: no text layer, no page JPGs for tesseract fallback")
            else:
                print(f"  {pdf_path.name}: no text layer, skipping (--no-tesseract)")
            no_text.add(issue_key)
            changed = True
            continue

        if not ollama_enabled_from_env():
            print(f"  {pdf_path.name}: embedded text layer detected — using native PDF text extraction...")

        print(f"  {pdf_path.name}: indexing text...")
        new_pages = 0
        for i, page in enumerate(doc):
            page_num = i + 1
            if page_num == 1:
                continue  # skip cover page
            text = _clean_text(page.get_text())
            if len(text) < MIN_TEXT_CHARS:
                continue
            pages.append({"mag": magazine, "year": year, "issue": issue,
                          "page": page_num, "text": text})
            new_pages += 1

        doc.close()
        done.add(issue_key)
        changed = True
        changed_issues.add((magazine, year, issue))
        print(f"    {new_pages} page(s) indexed")

    if changed:
        out = {"pages": pages, "no_text": sorted(no_text), "done": sorted(done)}
        print("  Saving search index and updating search database...")
        write_index_json(out, index_path=SEARCH_INDEX_FILE, rebuild_db=False)
        for mag, year, issue in sorted(changed_issues):
            sync_issue_db(out, mag, year, issue)
        print(f"  {SEARCH_INDEX_FILE}: {len(pages)} page(s), "
              f"{len(no_text)} scanned, {len(done)} text issue(s)")
    else:
        print(f"  {SEARCH_INDEX_FILE}: no changes")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract covers, pages, and thumbnails from magazine PDFs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "pdfs", nargs="*",
        help="Specific PDF file(s) to process (default: all under pdf/)",
    )
    parser.add_argument("--height",       type=int, default=2160,
                        help="Page JPEG height in pixels (default: 2160)")
    parser.add_argument("--thumb-height", type=int, default=300,
                        help="Thumbnail height in pixels (default: 300)")
    parser.add_argument("--crop", action="store_true",
                        help="(no-op, kept for back-compat) TrimBox cropping is now automatic")
    parser.add_argument("--force", action="store_true",
                        help="Re-extract even if output JPEGs already exist "
                             "(use when re-processing with --crop or --sharpen)")

    # Sharpening
    sharp = parser.add_argument_group("sharpening (unsharp mask, applied to covers + pages)")
    sharp.add_argument("--sharpen", action="store_true",
                       help="Apply unsharp mask to output JPEGs (useful for scanned text)")
    sharp.add_argument("--sharpen-radius",    type=float, default=0.3,
                       help="Blur radius for unsharp mask (default: 0.3)")
    sharp.add_argument("--sharpen-percent",   type=int,   default=250,
                       help="Strength as PIL percent 0-500+ (default: 250)")
    sharp.add_argument("--sharpen-threshold", type=int,   default=3,
                       help="Min pixel difference to sharpen, avoids noise (default: 3)")

    # Stage flags
    stage = parser.add_argument_group("stage selection (default: all stages)")
    stage.add_argument("--covers-only",    action="store_true",
                       help="Run stage 1 only (covers + collages)")
    stage.add_argument("--no-content",     action="store_true",
                       help="Skip stage 2 (page extraction)")
    stage.add_argument("--no-thumbnails",  action="store_true",
                       help="Skip stage 3 (thumbnail generation)")
    stage.add_argument("--no-html-update",     action="store_true",
                       help="Skip updating MAGAZINES in HTML files")
    stage.add_argument("--no-manifest-update", action="store_true",
                       help="Skip regenerating manifest.json")
    stage.add_argument("--no-text-index",      action="store_true",
                       help="Skip stage 4 (text index for search)")
    stage.add_argument("--no-tesseract",       action="store_true",
                       help="Disable tesseract OCR fallback for scanned PDFs")
    stage.add_argument("--tesseract-lang",     default=DEFAULT_TESS_LANG,
                       help=f"Tesseract language string (default: {DEFAULT_TESS_LANG})")
    args = parser.parse_args()

    if args.covers_only:
        args.no_content    = True
        args.no_thumbnails = True
        args.no_text_index = True

    # Resolve PDF list
    if args.pdfs:
        pdf_files = sorted(Path(p) for p in args.pdfs)
        missing   = [p for p in pdf_files if not p.exists()]
        if missing:
            for p in missing:
                print(f"ERROR: file not found: {p}", file=sys.stderr)
            sys.exit(1)
    else:
        pdf_files = sorted(PDF_DIR.rglob("*.pdf"))

    if not pdf_files:
        print("No PDF files found.")
        return

    print(f"Found {len(pdf_files)} PDF(s)")

    # Pre-stage — crop print PDFs to TrimBox (auto-detected; --crop is now a no-op kept for back-compat)
    print("\n[Pre-stage] Checking PDF(s) for TrimBox margins...")
    cropped = []
    for p in pdf_files:
        result = crop_to_trimbox(p)
        cropped.append(result if result is not None else p)
    pdf_files = cropped

    # Stage 0 — HTML sync + manifest
    if not args.no_html_update:
        print("\n[Stage 0] Syncing MAGAZINES in HTML files...")
        mags = discover_magazines()
        if mags:
            update_magazines_html(mags)

    sharpen = (args.sharpen_radius, args.sharpen_percent, args.sharpen_threshold) \
              if args.sharpen else None
    if sharpen:
        print(f"\n[Sharpen] radius={args.sharpen_radius} percent={args.sharpen_percent} "
              f"threshold={args.sharpen_threshold}")

    # Stage 1 — covers
    run_covers(pdf_files, sharpen=sharpen, force=args.force)

    # Stage 2 — pages
    if not args.no_content:
        run_content(pdf_files, args.height, sharpen=sharpen, force=args.force)

    # Load manifest once — used by stages 3 and 4 for fast skip checks
    manifest: dict = {}
    if MANIFEST_FILE.exists():
        manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))

    # Stage 3 — thumbnails
    if not args.no_thumbnails:
        if args.pdfs:
            # Only thumbnails for the issues we just extracted
            issue_dirs = sorted(
                issue_dir_for_pdf(p) for p in pdf_files
                if issue_dir_for_pdf(p).is_dir()
            )
        else:
            issue_dirs = sorted(
                p for p in JPG_DIR.rglob("*")
                if p.is_dir()
                and p.name != "thumbnails"
                and len(p.relative_to(JPG_DIR).parts) == 3
            )
        run_thumbnails(issue_dirs, args.thumb_height, manifest)

    # Stage 4 — text index
    if not args.no_text_index:
        run_text_index(pdf_files, manifest,
                       tess_lang=args.tesseract_lang,
                       no_tesseract=args.no_tesseract)

    # Manifest update — after all stages so page counts are accurate
    if not args.no_manifest_update:
        print("\n[Manifest] Updating manifest.json...")
        update_manifest()

    print("\nDone!")


if __name__ == "__main__":
    main()
