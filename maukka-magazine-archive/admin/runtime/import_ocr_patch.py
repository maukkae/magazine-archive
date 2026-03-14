"""Import OCR patch JSON into search_index.json/search.db.

Usage:
    python import_ocr_patch.py patch.json
    python import_ocr_patch.py patch.json --index umbrel/search_index.json --db umbrel/search.db
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from search_store import normalize_issue_id, read_index_json, sync_issue_db, write_index_json


def load_patch(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {"mag", "year", "issue", "pages"}
    missing = required - set(data)
    if missing:
        raise ValueError(f"Patch missing required fields: {sorted(missing)}")
    return data


def import_patch(patch: dict, index_path: Path, db_path: Path) -> None:
    mag = str(patch["mag"])
    year = str(patch["year"])
    issue = normalize_issue_id(patch["issue"])
    key = f"{mag}/{year}/{issue}"

    index = read_index_json(index_path=index_path)
    existing_issue_pages = {}
    retained_pages = []
    for entry in index.get("pages", []):
        same_issue = (
            entry["mag"] == mag
            and str(entry["year"]) == year
            and normalize_issue_id(entry["issue"]) == issue
        )
        if same_issue:
            existing_issue_pages[int(entry["page"])] = entry
        else:
            retained_pages.append(entry)

    no_text_pages = {int(page) for page in patch.get("no_text_pages", [])}
    patch_page_numbers = {int(entry["page"]) for entry in patch["pages"]}

    for entry in patch["pages"]:
        page_num = int(entry["page"])
        merged = dict(existing_issue_pages.get(page_num, {}))
        merged.update(
            {
                "mag": mag,
                "year": year,
                "issue": issue,
                "page": page_num,
                "text": entry["text"],
            }
        )
        retained_pages.append(merged)

    # The patch is authoritative for this issue: pages omitted from the patch
    # are treated as intentionally empty/no-text and any previous OCR is removed.
    for page_num in no_text_pages:
        existing_entry = existing_issue_pages.get(page_num)
        if existing_entry:
            # Keep only non-text metadata if the page had tags we may still care about.
            page_stub = {
                "mag": mag,
                "year": year,
                "issue": issue,
                "page": page_num,
                "text": "",
            }
            if existing_entry.get("page_tags"):
                page_stub["page_tags"] = existing_entry.get("page_tags", [])
            retained_pages.append(page_stub)

    index["pages"] = retained_pages

    done = set(index.get("done", []))
    no_text = set(index.get("no_text", []))
    issue_has_text = any(
        entry["mag"] == mag
        and str(entry["year"]) == year
        and normalize_issue_id(entry["issue"]) == issue
        and (entry.get("text", "") or "").strip()
        for entry in retained_pages
    )
    if issue_has_text:
        done.add(key)
        no_text.discard(key)
    else:
        done.discard(key)
        no_text.add(key)
    index["done"] = sorted(done)
    index["no_text"] = sorted(no_text)

    write_index_json(index, index_path=index_path, db_path=db_path, rebuild_db=False)
    sync_issue_db(index, mag=mag, year=year, issue=issue, db_path=db_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("patch", type=Path)
    parser.add_argument("--index", type=Path, default=Path("search_index.json"))
    parser.add_argument("--db", type=Path, default=Path("search.db"))
    args = parser.parse_args()

    patch = load_patch(args.patch)
    import_patch(patch, index_path=args.index, db_path=args.db)
    print(
        f"Imported OCR patch for {patch['mag']}/{patch['year']}/{normalize_issue_id(patch['issue'])} "
        f"with {len(patch['pages'])} page(s)"
    )


if __name__ == "__main__":
    main()
