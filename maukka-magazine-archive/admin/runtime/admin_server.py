"""Admin server for magazine archive housekeeping.

Run:   python admin_server.py
Open:  http://127.0.0.1:8001/

Requires: pip install flask
"""

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from archive_paths import JPG_DIR, SCAN_DIR, PDF_DIR, SEARCH_INDEX_FILE
from extract import update_manifest, update_magazines_html

HOST     = os.environ.get("ADMIN_HOST", "127.0.0.1")
PORT     = int(os.environ.get("ADMIN_PORT", "8001"))

app  = Flask(__name__, static_folder=None)
jobs = {}   # job_id -> {"queue": Queue, "status": "running"|"done"|"error"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_index():
    if SEARCH_INDEX_FILE.exists():
        return json.loads(SEARCH_INDEX_FILE.read_text(encoding="utf-8-sig"))
    return {"pages": [], "done": [], "no_text": []}


def _write_index(data):
    SEARCH_INDEX_FILE.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp



def _all_magazines():
    mags = set()
    for base in [PDF_DIR, JPG_DIR, SCAN_DIR]:
        if base.is_dir():
            mags.update(d.name for d in base.iterdir() if d.is_dir())
    return sorted(mags)


def _discover_magazines():
    if not JPG_DIR.is_dir():
        return []
    idx = _read_index()
    pages_by_key = {}
    for p in idx.get("pages", []):
        k = f"{p['mag']}/{p['year']}/{p['issue']}"
        pages_by_key[k] = pages_by_key.get(k, 0) + 1
    done_set    = set(idx.get("done",    []))
    no_text_set = set(idx.get("no_text", []))

    result = []
    for mag_dir in sorted(JPG_DIR.iterdir()):
        if not mag_dir.is_dir():
            continue
        mag, years = mag_dir.name, []
        for year_dir in sorted(mag_dir.iterdir()):
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            issues = []
            for issue_dir in sorted(year_dir.iterdir()):
                if not issue_dir.is_dir():
                    continue
                issue = issue_dir.name
                key   = f"{mag}/{year_dir.name}/{issue}"
                issues.append({
                    "issue":       issue,
                    "pages":       len(list(issue_dir.glob("*_[0-9][0-9][0-9].jpg"))),
                    "indexed":     key in done_set,
                    "no_text":     key in no_text_set,
                    "index_pages": pages_by_key.get(key, 0),
                })
            if issues:
                years.append({"year": year_dir.name, "issues": issues})
        if years:
            result.append({
                "mag":          mag,
                "years":        years,
                "total_issues": sum(len(y["issues"]) for y in years),
                "total_pages":  sum(i["index_pages"] for y in years for i in y["issues"]),
            })
    return result


# ---------------------------------------------------------------------------
# Job runner with SSE streaming
# ---------------------------------------------------------------------------

def _start_job(cmd: list[str]) -> str:
    job_id = uuid.uuid4().hex
    q = queue.Queue()
    jobs[job_id] = {"queue": q, "status": "running"}

    def _run():
        try:
            # Insert -u after the interpreter so Python flushes stdout after
            # every print() instead of batching into 8 KB blocks.
            unbuffered_cmd = [cmd[0], "-u"] + cmd[1:]
            proc = subprocess.Popen(
                unbuffered_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=Path(".").resolve(),
            )
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                q.put(line.rstrip())
            proc.wait()
            jobs[job_id]["status"] = "done" if proc.returncode == 0 else "error"
        except Exception as e:
            q.put(f"ERROR: {e}")
            jobs[job_id]["status"] = "error"
        q.put(None)

    threading.Thread(target=_run, daemon=True).start()
    return job_id


@app.get("/api/stream/<job_id>")
def stream(job_id):
    if job_id not in jobs:
        return jsonify({"error": "not found"}), 404

    def _generate():
        q = jobs[job_id]["queue"]
        while True:
            line = q.get()
            if line is None:
                yield "data: __done__\n\n"
                break
            yield f"data: {json.dumps(line)}\n\n"

    return Response(
        _generate(), mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/magazines")
def api_magazines():
    return jsonify(_discover_magazines())


@app.post("/api/update-page")
def api_update_page():
    data  = request.json or {}
    mag   = data.get("mag",   "").strip()
    year  = data.get("year",  "").strip()
    issue = data.get("issue", "").strip()
    page  = data.get("page")
    text  = data.get("text",  "")
    if not (mag and year and issue and page is not None):
        return jsonify({"error": "missing fields"}), 400

    idx = _read_index()
    updated = False
    for p in idx.get("pages", []):
        if p["mag"] == mag and p["year"] == year and p["issue"] == issue and p["page"] == page:
            p["text"] = text
            updated = True
            break
    if not updated:
        return jsonify({"error": "page not found"}), 404

    _write_index(idx)
    return jsonify({"ok": True})


@app.get("/api/index-data")
def api_index_data():
    mag   = request.args.get("mag",   "").strip()
    year  = request.args.get("year",  "").strip()
    issue = request.args.get("issue", "").strip()
    if not (mag and year and issue):
        return jsonify({"error": "missing fields"}), 400

    idx   = _read_index()
    pages = sorted(
        (p for p in idx.get("pages", [])
         if p["mag"] == mag and p["year"] == year and p["issue"] == issue),
        key=lambda p: p["page"],
    )
    total_chars = sum(len(p["text"]) for p in pages)
    avg_chars   = total_chars // len(pages) if pages else 0

    return jsonify({
        "mag": mag, "year": year, "issue": issue,
        "total_pages": len(pages),
        "total_chars": total_chars,
        "avg_chars":   avg_chars,
        "pages": [{"page": p["page"], "chars": len(p["text"]), "text": p["text"], "page_tags": p.get("page_tags", [])}
                  for p in pages],
    })


@app.route("/api/page-tags", methods=["GET", "POST", "OPTIONS"])
def api_page_tags():
    if request.method == "OPTIONS":
        return ("", 204)

    if request.method == "GET":
        mag   = request.args.get("mag",   "").strip()
        year  = request.args.get("year",  "").strip()
        issue = request.args.get("issue", "").strip()
        if not (mag and year and issue):
            return jsonify({"error": "missing fields"}), 400

        idx = _read_index()
        pages = [
            {
                "page": p["page"],
                "page_tags": p.get("page_tags", []),
            }
            for p in idx.get("pages", [])
            if p["mag"] == mag and p["year"] == year and p["issue"] == issue
        ]
        existing_tags = sorted({
            tag
            for p in idx.get("pages", [])
            for tag in p.get("page_tags", [])
        })
        pages.sort(key=lambda p: p["page"])
        return jsonify({
            "mag": mag,
            "year": year,
            "issue": issue,
            "pages": pages,
            "existing_tags": existing_tags,
        })

    data = request.json or {}
    mag   = data.get("mag",   "").strip()
    year  = data.get("year",  "").strip()
    issue = data.get("issue", "").strip()
    page  = data.get("page")
    tags  = data.get("page_tags", [])
    if not (mag and year and issue and page is not None):
        return jsonify({"error": "missing fields"}), 400
    if not isinstance(tags, list):
        return jsonify({"error": "page_tags must be a list"}), 400

    clean_tags = []
    for tag in tags:
        tag = str(tag).strip()
        if tag and tag not in clean_tags:
            clean_tags.append(tag)

    idx = _read_index()
    updated = False
    for p in idx.get("pages", []):
        if p["mag"] == mag and p["year"] == year and p["issue"] == issue and p["page"] == page:
            if clean_tags:
                p["page_tags"] = clean_tags
            else:
                p.pop("page_tags", None)
            updated = True
            break
    if not updated:
        if not clean_tags:
            return jsonify({"error": "page not found"}), 404
        idx.setdefault("pages", []).append({
            "mag": mag,
            "year": year,
            "issue": issue,
            "page": page,
            "text": "",
            "page_tags": clean_tags,
        })
        updated = True

    _write_index(idx)
    return jsonify({"ok": True, "page_tags": clean_tags})


@app.post("/api/remove/magazine")
def api_remove_magazine():
    mag = (request.json or {}).get("mag", "").strip()
    if not mag:
        return jsonify({"error": "missing mag"}), 400

    mag_dir = JPG_DIR / mag
    if mag_dir.is_dir():
        shutil.rmtree(mag_dir)

    idx = _read_index()
    idx["pages"]   = [p for p in idx["pages"]   if p["mag"] != mag]
    idx["done"]    = [k for k in idx["done"]    if not k.startswith(f"{mag}/")]
    idx["no_text"] = [k for k in idx["no_text"] if not k.startswith(f"{mag}/")]
    _write_index(idx)

    update_magazines_html(_all_magazines())
    update_manifest()
    return jsonify({"ok": True})


@app.post("/api/remove/issue")
def api_remove_issue():
    data  = request.json or {}
    mag   = data.get("mag",   "").strip()
    year  = data.get("year",  "").strip()
    issue = data.get("issue", "").strip()
    if not (mag and year and issue):
        return jsonify({"error": "missing fields"}), 400

    issue_dir = JPG_DIR / mag / year / issue
    if issue_dir.is_dir():
        shutil.rmtree(issue_dir)

    cover = JPG_DIR / mag / year / f"{mag}_{year}_{issue}_cover.jpg"
    if cover.exists():
        cover.unlink()

    # Remove empty year dir
    year_dir = JPG_DIR / mag / year
    if year_dir.is_dir() and not any(year_dir.iterdir()):
        year_dir.rmdir()

    # Remove empty mag dir
    mag_dir = JPG_DIR / mag
    if mag_dir.is_dir() and not any(mag_dir.iterdir()):
        mag_dir.rmdir()
        update_magazines_html(_all_magazines())

    key = f"{mag}/{year}/{issue}"
    idx = _read_index()
    idx["pages"]   = [p for p in idx["pages"]
                      if not (p["mag"] == mag and p["year"] == year and p["issue"] == issue)]
    idx["done"]    = [k for k in idx["done"]    if k != key]
    idx["no_text"] = [k for k in idx["no_text"] if k != key]
    _write_index(idx)

    update_manifest()
    return jsonify({"ok": True})


@app.post("/api/clear-index")
def api_clear_index():
    data  = request.json or {}
    mag   = data.get("mag",   "").strip()
    year  = data.get("year",  "").strip()
    issue = data.get("issue", "").strip()

    idx = _read_index()
    if mag and year and issue:
        key = f"{mag}/{year}/{issue}"
        idx["pages"]   = [p for p in idx["pages"]
                          if not (p["mag"] == mag and p["year"] == year and p["issue"] == issue)]
        idx["done"]    = [k for k in idx["done"]    if k != key]
        idx["no_text"] = [k for k in idx["no_text"] if k != key]
    elif mag:
        idx["pages"]   = [p for p in idx["pages"]   if p["mag"] != mag]
        idx["done"]    = [k for k in idx["done"]    if not k.startswith(f"{mag}/")]
        idx["no_text"] = [k for k in idx["no_text"] if not k.startswith(f"{mag}/")]
    _write_index(idx)
    return jsonify({"ok": True})


@app.post("/api/rebuild-manifest")
def api_rebuild_manifest():
    update_manifest()
    return jsonify({"ok": True})


@app.post("/api/upload/pdf")
def api_upload_pdf():
    mag              = request.form.get("mag",              "").strip()
    year             = request.form.get("year",             "").strip()
    issue            = request.form.get("issue",            "").strip()
    crop             = request.form.get("crop",             "").strip() == "1"
    sharpen          = request.form.get("sharpen",          "").strip() == "1"
    sharpen_radius   = request.form.get("sharpen_radius",   "0.3").strip()
    sharpen_percent  = request.form.get("sharpen_percent",  "250").strip()
    sharpen_threshold= request.form.get("sharpen_threshold","3").strip()
    f                = request.files.get("file")
    if not (mag and year and issue and f):
        return jsonify({"error": "missing fields"}), 400
    if not re.match(r"^\d{4}$", year):
        return jsonify({"error": "invalid year"}), 400
    if not re.match(r"^\d{2,}$", issue):
        return jsonify({"error": "invalid issue"}), 400

    dest_dir = PDF_DIR / mag
    dest_dir.mkdir(parents=True, exist_ok=True)

    cmd_extra = []
    if crop:
        dest = dest_dir / f"{mag}_{year}_{issue}_print.pdf"
        cmd_extra += ["--crop", "--force"]
    else:
        dest = dest_dir / f"{mag}_{year}_{issue}.pdf"
    f.save(dest)

    if sharpen:
        cmd_extra += ["--sharpen",
                      "--sharpen-radius",    sharpen_radius,
                      "--sharpen-percent",   sharpen_percent,
                      "--sharpen-threshold", sharpen_threshold]

    job_id = _start_job([sys.executable, "extract.py", str(dest)] + cmd_extra)
    return jsonify({"ok": True, "job_id": job_id})


@app.post("/api/upload/scans")
def api_upload_scans():
    mag   = request.form.get("mag",   "").strip()
    year  = request.form.get("year",  "").strip()
    issue = request.form.get("issue", "").strip()
    if not (mag and year and issue):
        return jsonify({"error": "missing fields"}), 400

    dest_dir = SCAN_DIR / mag / year / issue
    dest_dir.mkdir(parents=True, exist_ok=True)

    i = 0
    while True:
        f = request.files.get(f"page_{i}")
        if f is None:
            break
        (dest_dir / f"{mag}_{year}_{issue}_{i + 1:03d}.jpg").write_bytes(f.read())
        i += 1

    if not i:
        return jsonify({"error": "no page files received"}), 400

    ocr_pdf = request.files.get("ocr_pdf")
    if ocr_pdf:
        ocr_pdf.save(dest_dir / f"{mag}_{year}_{issue}_OCR.pdf")

    job_id = _start_job([sys.executable, "import_scans.py", str(dest_dir)])
    return jsonify({"ok": True, "job_id": job_id})


# ---------------------------------------------------------------------------
# Serve admin UI
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory("admin", "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    return send_from_directory("admin", filename)


if __name__ == "__main__":
    # Flask's built-in server is used deliberately: waitress buffers the entire
    # response body before sending, which breaks SSE log streaming.
    print(f"Admin panel: http://{HOST}:{PORT}/")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
