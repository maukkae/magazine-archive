import json
import os
import re
import sqlite3
import unicodedata
from pathlib import Path


SEARCH_INDEX_JSON = Path(os.environ.get("ARCHIVE_SEARCH_INDEX_FILE", "search_index.json"))
SEARCH_DB_FILE = Path(os.environ.get("ARCHIVE_SEARCH_DB_FILE", "search.db"))
SEARCH_STOPWORDS = {"a", "an", "and", "the", "of"}


def normalize_issue_id(issue: str) -> str:
    issue = str(issue or "").strip()
    if not issue:
        return ""
    m = re.fullmatch(r"(\d{1,4})(?:-(\d{1,4}))?", issue)
    if not m:
        return issue
    first = m.group(1).zfill(2) if len(m.group(1)) == 1 else m.group(1)
    second = m.group(2)
    if second is None:
        return first
    second = second.zfill(2) if len(second) == 1 else second
    return f"{first}-{second}"


def normalize_search_text(text: str) -> str:
    text = text or ""
    text = re.sub(r"([\w\d])([A-Z])", r"\1 \2", text)
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).strip()
    return text


def search_tokens(text: str) -> list[str]:
    return [
        token for token in normalize_search_text(text).split()
        if token and token not in SEARCH_STOPWORDS
    ]


def compact_search_text(text: str) -> str:
    return "".join(search_tokens(text))


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def read_index_json(index_path: Path = SEARCH_INDEX_JSON) -> dict:
    index_path = Path(index_path)
    if index_path.exists():
        data = json.loads(index_path.read_text(encoding="utf-8-sig"))
        if isinstance(data, list):
            return {"pages": data, "reviews": [], "done": [], "no_text": []}
        return {
            "pages": data.get("pages", []),
            "reviews": data.get("reviews", []),
            "done": data.get("done", []),
            "no_text": data.get("no_text", []),
        }
    return {"pages": [], "reviews": [], "done": [], "no_text": []}


def connect_search_db(db_path: Path = SEARCH_DB_FILE) -> sqlite3.Connection:
    db_path = Path(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY,
            mag TEXT NOT NULL,
            year TEXT NOT NULL,
            issue TEXT NOT NULL,
            page INTEGER NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            page_tags_json TEXT NOT NULL DEFAULT '[]',
            is_hidden_search INTEGER NOT NULL DEFAULT 0,
            search_direct TEXT NOT NULL DEFAULT '',
            search_normalized TEXT NOT NULL DEFAULT '',
            search_compact TEXT NOT NULL DEFAULT '',
            UNIQUE (mag, year, issue, page)
        );
        CREATE INDEX IF NOT EXISTS idx_pages_mag_issue ON pages (mag, year, issue, page);
        CREATE INDEX IF NOT EXISTS idx_pages_compact ON pages (search_compact);
        CREATE INDEX IF NOT EXISTS idx_pages_hidden ON pages (is_hidden_search);

        CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
            text,
            search_normalized,
            content='pages',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
            INSERT INTO pages_fts(rowid, text, search_normalized)
            VALUES (new.id, new.text, new.search_normalized);
        END;
        CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
            INSERT INTO pages_fts(pages_fts, rowid, text, search_normalized)
            VALUES ('delete', old.id, old.text, old.search_normalized);
        END;
        CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
            INSERT INTO pages_fts(pages_fts, rowid, text, search_normalized)
            VALUES ('delete', old.id, old.text, old.search_normalized);
            INSERT INTO pages_fts(rowid, text, search_normalized)
            VALUES (new.id, new.text, new.search_normalized);
        END;

        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY,
            game TEXT NOT NULL,
            mag TEXT NOT NULL,
            year TEXT NOT NULL,
            issue TEXT NOT NULL,
            page INTEGER,
            type TEXT NOT NULL,
            score REAL,
            score_scale INTEGER,
            reviewers_json TEXT NOT NULL DEFAULT '[]',
            notes TEXT,
            toteutus INTEGER,
            pelattavuus INTEGER,
            kiinnostavuus INTEGER,
            keskiarvo REAL,
            search_direct TEXT NOT NULL DEFAULT '',
            search_normalized TEXT NOT NULL DEFAULT '',
            search_compact TEXT NOT NULL DEFAULT '',
            reviewers_text TEXT NOT NULL DEFAULT '',
            UNIQUE (game, mag, year, issue, page, type)
        );
        CREATE INDEX IF NOT EXISTS idx_reviews_mag_issue ON reviews (mag, year, issue, page);
        CREATE INDEX IF NOT EXISTS idx_reviews_compact ON reviews (search_compact);

        CREATE VIRTUAL TABLE IF NOT EXISTS reviews_fts USING fts5(
            game,
            reviewers_text,
            search_normalized,
            content='reviews',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TRIGGER IF NOT EXISTS reviews_ai AFTER INSERT ON reviews BEGIN
            INSERT INTO reviews_fts(rowid, game, reviewers_text, search_normalized)
            VALUES (new.id, new.game, new.reviewers_text, new.search_normalized);
        END;
        CREATE TRIGGER IF NOT EXISTS reviews_ad AFTER DELETE ON reviews BEGIN
            INSERT INTO reviews_fts(reviews_fts, rowid, game, reviewers_text, search_normalized)
            VALUES ('delete', old.id, old.game, old.reviewers_text, old.search_normalized);
        END;
        CREATE TRIGGER IF NOT EXISTS reviews_au AFTER UPDATE ON reviews BEGIN
            INSERT INTO reviews_fts(reviews_fts, rowid, game, reviewers_text, search_normalized)
            VALUES ('delete', old.id, old.game, old.reviewers_text, old.search_normalized);
            INSERT INTO reviews_fts(rowid, game, reviewers_text, search_normalized)
            VALUES (new.id, new.game, new.reviewers_text, new.search_normalized);
        END;
        """
    )
    conn.commit()


def _page_row(entry: dict) -> tuple:
    text = entry.get("text", "") or ""
    tags = entry.get("page_tags", []) or []
    return (
        entry["mag"],
        str(entry["year"]),
        normalize_issue_id(entry["issue"]),
        int(entry["page"]),
        text,
        _json_dumps(tags),
        1 if any(tag in {"ad_only", "ad_fullpage"} for tag in tags) else 0,
        text.lower(),
        " ".join(search_tokens(text)),
        compact_search_text(text),
    )


def _review_row(entry: dict) -> tuple:
    game = entry.get("game", "") or ""
    reviewers = entry.get("reviewers", []) or []
    return (
        game,
        entry["mag"],
        str(entry["year"]),
        normalize_issue_id(entry["issue"]),
        int(entry["page"]) if entry.get("page") not in (None, "") else None,
        entry["type"],
        entry.get("score"),
        entry.get("score_scale"),
        _json_dumps(reviewers),
        entry.get("notes"),
        entry.get("toteutus"),
        entry.get("pelattavuus"),
        entry.get("kiinnostavuus"),
        entry.get("keskiarvo"),
        game.lower(),
        " ".join(search_tokens(game)),
        compact_search_text(game),
        ", ".join(reviewers),
    )


def rebuild_search_db(index_data: dict, db_path: Path = SEARCH_DB_FILE) -> None:
    db_path = Path(db_path)
    conn = connect_search_db(db_path)
    try:
        conn.execute("DELETE FROM pages")
        conn.execute("DELETE FROM reviews")
        conn.execute("DELETE FROM meta")
        conn.executemany(
            """
            INSERT INTO pages (
                mag, year, issue, page, text, page_tags_json, is_hidden_search,
                search_direct, search_normalized, search_compact
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [_page_row(entry) for entry in index_data.get("pages", [])],
        )
        conn.executemany(
            """
            INSERT INTO reviews (
                game, mag, year, issue, page, type, score, score_scale,
                reviewers_json, notes, toteutus, pelattavuus, kiinnostavuus, keskiarvo,
                search_direct, search_normalized, search_compact, reviewers_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [_review_row(entry) for entry in index_data.get("reviews", [])],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO meta (key, value_json) VALUES (?, ?)",
            [
                ("done", _json_dumps(sorted(index_data.get("done", [])))),
                ("no_text", _json_dumps(sorted(index_data.get("no_text", [])))),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _normalized_payload(index_data: dict) -> dict:
    return {
        "pages": index_data.get("pages", []),
        "reviews": index_data.get("reviews", []),
        "done": sorted(index_data.get("done", [])),
        "no_text": sorted(index_data.get("no_text", [])),
    }


def write_index_json(
    index_data: dict,
    index_path: Path = SEARCH_INDEX_JSON,
    db_path: Path = SEARCH_DB_FILE,
    rebuild_db: bool = True,
) -> None:
    index_path = Path(index_path)
    db_path = Path(db_path)
    payload = _normalized_payload(index_data)
    index_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    if rebuild_db:
        rebuild_search_db(payload, db_path=db_path)


def sync_issue_db(index_data: dict, mag: str, year: str, issue: str, db_path: Path = SEARCH_DB_FILE) -> None:
    db_path = Path(db_path)
    conn = connect_search_db(db_path)
    year = str(year)
    issue = normalize_issue_id(issue)
    payload = _normalized_payload(index_data)
    try:
        conn.execute(
            "DELETE FROM pages WHERE mag = ? AND year = ? AND issue = ?",
            (mag, year, issue),
        )
        issue_pages = [
            entry for entry in payload.get("pages", [])
            if entry["mag"] == mag
            and str(entry["year"]) == year
            and normalize_issue_id(entry["issue"]) == issue
        ]
        if issue_pages:
            conn.executemany(
                """
                INSERT INTO pages (
                    mag, year, issue, page, text, page_tags_json, is_hidden_search,
                    search_direct, search_normalized, search_compact
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [_page_row(entry) for entry in issue_pages],
            )
        conn.executemany(
            "INSERT OR REPLACE INTO meta (key, value_json) VALUES (?, ?)",
            [
                ("done", _json_dumps(payload.get("done", []))),
                ("no_text", _json_dumps(payload.get("no_text", []))),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def sync_magazine_db(index_data: dict, mag: str, db_path: Path = SEARCH_DB_FILE) -> None:
    db_path = Path(db_path)
    conn = connect_search_db(db_path)
    payload = _normalized_payload(index_data)
    try:
        conn.execute("DELETE FROM pages WHERE mag = ?", (mag,))
        magazine_pages = [
            entry for entry in payload.get("pages", [])
            if entry["mag"] == mag
        ]
        if magazine_pages:
            conn.executemany(
                """
                INSERT INTO pages (
                    mag, year, issue, page, text, page_tags_json, is_hidden_search,
                    search_direct, search_normalized, search_compact
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [_page_row(entry) for entry in magazine_pages],
            )
        conn.executemany(
            "INSERT OR REPLACE INTO meta (key, value_json) VALUES (?, ?)",
            [
                ("done", _json_dumps(payload.get("done", []))),
                ("no_text", _json_dumps(payload.get("no_text", []))),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def sync_db_from_json(index_path: Path = SEARCH_INDEX_JSON, db_path: Path = SEARCH_DB_FILE) -> None:
    index_path = Path(index_path)
    db_path = Path(db_path)
    rebuild_search_db(read_index_json(index_path=index_path), db_path=db_path)
