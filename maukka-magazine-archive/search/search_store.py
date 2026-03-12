import json
import re
import sqlite3
import unicodedata
from pathlib import Path


SEARCH_INDEX_JSON = Path("search_index.json")
SEARCH_DB_FILE = Path("search.db")
SEARCH_STOPWORDS = {"a", "an", "and", "the", "of"}


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
