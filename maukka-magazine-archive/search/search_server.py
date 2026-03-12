"""SQLite-backed search API for the magazine archive."""

import json
import os
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from search_store import SEARCH_DB_FILE, compact_search_text, connect_search_db, search_tokens


HOST = os.environ.get("SEARCH_HOST", "0.0.0.0")
PORT = int(os.environ.get("SEARCH_PORT", "8002"))
STATIC_ROOT = Path(os.environ.get("SEARCH_STATIC_ROOT", "/data")).resolve()

app = Flask(__name__, static_folder=None)


def _connect() -> sqlite3.Connection:
    return connect_search_db(Path(os.environ.get("SEARCH_DB", str(SEARCH_DB_FILE))))


def _fts_query(tokens: list[str]) -> str:
    safe = []
    for token in tokens:
        if token:
            safe.append(f'"{token.replace(chr(34), "")}"')
    return " AND ".join(safe)


def _parse_review_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["reviewers"] = json.loads(item.pop("reviewers_json") or "[]")
    return item


def _parse_page_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["page_tags"] = json.loads(item.pop("page_tags_json") or "[]")
    return item


def search_reviews(conn: sqlite3.Connection, raw: str, limit: int) -> list[dict]:
    tokens = search_tokens(raw)
    normalized = " ".join(tokens)
    compact = compact_search_text(raw)
    direct = raw.lower()
    fts = _fts_query(tokens)
    rows = conn.execute(
        """
        SELECT DISTINCT r.*
        FROM reviews r
        WHERE
            r.search_direct LIKE '%' || :direct || '%'
            OR (:normalized != '' AND r.search_normalized LIKE '%' || :normalized || '%')
            OR (:compact != '' AND r.search_compact LIKE '%' || :compact || '%')
            OR (:fts != '' AND r.id IN (
                SELECT rowid FROM reviews_fts WHERE reviews_fts MATCH :fts
            ))
        ORDER BY
            CASE
                WHEN r.search_direct LIKE '%' || :direct || '%' THEN 0
                WHEN :normalized != '' AND r.search_normalized LIKE '%' || :normalized || '%' THEN 1
                WHEN :compact != '' AND r.search_compact LIKE '%' || :compact || '%' THEN 2
                ELSE 3
            END,
            CAST(r.year AS INTEGER) DESC,
            CAST(r.issue AS INTEGER) DESC,
            COALESCE(r.page, 0) ASC
        LIMIT :limit
        """,
        {
            "direct": direct,
            "normalized": normalized,
            "compact": compact,
            "fts": fts,
            "limit": limit,
        },
    ).fetchall()
    return [_parse_review_row(row) for row in rows]


def search_pages(conn: sqlite3.Connection, raw: str, limit: int) -> list[dict]:
    tokens = search_tokens(raw)
    normalized = " ".join(tokens)
    compact = compact_search_text(raw)
    direct = raw.lower()
    fts = _fts_query(tokens)
    rows = conn.execute(
        """
        SELECT DISTINCT p.*
        FROM pages p
        WHERE
            p.is_hidden_search = 0 AND (
                p.search_direct LIKE '%' || :direct || '%'
                OR (:normalized != '' AND p.search_normalized LIKE '%' || :normalized || '%')
                OR (:compact != '' AND p.search_compact LIKE '%' || :compact || '%')
                OR (:fts != '' AND p.id IN (
                    SELECT rowid FROM pages_fts WHERE pages_fts MATCH :fts
                ))
            )
        ORDER BY
            CASE
                WHEN p.search_direct LIKE '%' || :direct || '%' THEN 0
                WHEN :normalized != '' AND p.search_normalized LIKE '%' || :normalized || '%' THEN 1
                WHEN :compact != '' AND p.search_compact LIKE '%' || :compact || '%' THEN 2
                ELSE 3
            END,
            CAST(p.year AS INTEGER) DESC,
            CAST(p.issue AS INTEGER) DESC,
            p.page ASC
        LIMIT :limit
        """,
        {
            "direct": direct,
            "normalized": normalized,
            "compact": compact,
            "fts": fts,
            "limit": limit,
        },
    ).fetchall()
    return [_parse_page_row(row) for row in rows]


@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/api/search", methods=["GET", "OPTIONS"])
def api_search():
    if request.method == "OPTIONS":
        return ("", 204)

    raw = (request.args.get("q") or "").strip()
    limit = min(max(int(request.args.get("limit", "100")), 1), 500)
    if not raw:
        return jsonify({"query": raw, "reviews": [], "pages": [], "total": 0})

    conn = _connect()
    try:
        reviews = search_reviews(conn, raw, limit)
        pages = search_pages(conn, raw, limit)
    finally:
        conn.close()

    return jsonify({
        "query": raw,
        "reviews": reviews,
        "pages": pages,
        "total": len(reviews) + len(pages),
    })


@app.get("/api/health")
def api_health():
    return jsonify({"ok": True})


@app.get("/")
def static_index():
    return send_from_directory(STATIC_ROOT, "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_ROOT, filename)


if __name__ == "__main__":
    print(f"Search server: http://{HOST}:{PORT}/")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
