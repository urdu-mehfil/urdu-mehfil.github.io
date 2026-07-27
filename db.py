#!/usr/bin/env python3
"""
db.py — SQLite schema and access helpers for categories + topics.
Replaces categories.csv/topics.csv entirely. Every other module reads
and writes through the functions here rather than touching SQL or the
file directly, so the schema can evolve in one place.

Why SQLite over CSV, concretely:
  - Filling in one thread's page count is a single-row UPDATE, not a
    full-file rewrite (topics.csv was hitting ~44MB and every
    checkpoint save rewrote the whole thing).
  - No stored `url` column — full URLs are cheap to reconstruct from
    thread_slug + thread_id, so we don't pay to store the repeated
    "https://www.urduweb.org/mehfil/threads/" prefix on every row.
  - Real NULL for "not yet fetched" instead of an empty-string sentinel.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urljoin

from config import DB_PATH, BASE_URL

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    category_id   INTEGER PRIMARY KEY,
    category_name TEXT NOT NULL,
    category_slug TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    thread_id     INTEGER PRIMARY KEY,
    category_id   INTEGER NOT NULL REFERENCES categories(category_id),
    thread_title  TEXT NOT NULL,
    thread_slug   TEXT NOT NULL,
    total_pages   INTEGER,
    scraped_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_topics_category ON topics(category_id);
CREATE INDEX IF NOT EXISTS idx_topics_pending ON topics(total_pages) WHERE total_pages IS NULL;
CREATE INDEX IF NOT EXISTS idx_topics_unscraped ON topics(scraped_at) WHERE scraped_at IS NULL;
"""


def _ensure_column(conn, table, column, coltype):
    """Adds a column to an existing table if it's not already there.
    Needed because CREATE TABLE IF NOT EXISTS does nothing to a table
    that already exists with an older schema — this is what lets an
    already-populated mehfil.db (e.g. one with 2000+ threads already
    scraped under the old schema) pick up new columns automatically
    the next time any archive.py command runs, with no manual step."""
    cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


@contextmanager
def get_connection(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path=DB_PATH):
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "topics", "scraped_at", "TEXT")


# --------------------------------------------------------------- categories

def upsert_category(conn, category_id, category_name, category_slug):
    conn.execute(
        """INSERT INTO categories (category_id, category_name, category_slug)
           VALUES (?, ?, ?)
           ON CONFLICT(category_id) DO UPDATE SET
             category_name = excluded.category_name,
             category_slug = excluded.category_slug""",
        (category_id, category_name, category_slug),
    )


def get_categories(conn):
    return conn.execute("SELECT * FROM categories ORDER BY category_id").fetchall()


def get_category(conn, category_id):
    return conn.execute(
        "SELECT * FROM categories WHERE category_id = ?", (category_id,)
    ).fetchone()


def category_thread_count(conn, category_id):
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM topics WHERE category_id = ?", (category_id,)
    ).fetchone()
    return row["n"]


# ------------------------------------------------------------------ topics

def upsert_topic(conn, thread_id, category_id, thread_title, thread_slug, total_pages=None):
    conn.execute(
        """INSERT INTO topics (thread_id, category_id, thread_title, thread_slug, total_pages)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(thread_id) DO UPDATE SET
             category_id  = excluded.category_id,
             thread_title = excluded.thread_title,
             thread_slug  = excluded.thread_slug""",
        (thread_id, category_id, thread_title, thread_slug, total_pages),
    )


def set_total_pages(conn, thread_id, total_pages):
    """The whole point: updating one thread's page count touches ONE
    row, not the entire dataset."""
    conn.execute(
        "UPDATE topics SET total_pages = ? WHERE thread_id = ?",
        (total_pages, thread_id),
    )


def get_topics_for_category(conn, category_id):
    return conn.execute(
        "SELECT * FROM topics WHERE category_id = ? ORDER BY thread_id", (category_id,)
    ).fetchall()


def get_topic(conn, thread_id):
    return conn.execute(
        "SELECT * FROM topics WHERE thread_id = ?", (thread_id,)
    ).fetchone()


def get_topics_missing_page_counts(conn, category_id=None, thread_id=None):
    query = "SELECT * FROM topics WHERE total_pages IS NULL"
    params = []
    if category_id is not None:
        query += " AND category_id = ?"
        params.append(category_id)
    if thread_id is not None:
        query += " AND thread_id = ?"
        params.append(thread_id)
    query += " ORDER BY thread_id"
    return conn.execute(query, params).fetchall()


def mark_scraped(conn, thread_id, total_pages=None, when=None):
    """Marks a thread as fully scraped. Optionally also updates
    total_pages in the same statement (extract.py learns the real
    page count from page 1 itself now, so it can save both at once)."""
    when = when or datetime.now(timezone.utc).isoformat()
    if total_pages is not None:
        conn.execute(
            "UPDATE topics SET scraped_at = ?, total_pages = ? WHERE thread_id = ?",
            (when, total_pages, thread_id),
        )
    else:
        conn.execute("UPDATE topics SET scraped_at = ? WHERE thread_id = ?", (when, thread_id))


def get_unscraped_topics_for_category(conn, category_id):
    """Only threads NOT YET marked scraped_at — this is the fast path
    that avoids even considering the 2000+ already-done threads once
    they've been marked (see mark_scraped / the disk-fallback check in
    extract.crawl_thread for how legacy already-scraped-on-disk
    threads get marked the first time without any network request)."""
    return conn.execute(
        "SELECT * FROM topics WHERE category_id = ? AND scraped_at IS NULL ORDER BY thread_id",
        (category_id,),
    ).fetchall()


def all_category_ids_with_topics(conn):
    return {row["category_id"] for row in conn.execute("SELECT DISTINCT category_id FROM topics")}


def progress_report(conn):
    """Per-category done/total counts, for the --progress view."""
    return conn.execute(
        """SELECT c.category_id, c.category_name,
                  COUNT(t.thread_id) AS total,
                  COUNT(t.total_pages) AS done
           FROM categories c
           LEFT JOIN topics t ON t.category_id = c.category_id
           GROUP BY c.category_id
           ORDER BY c.category_id"""
    ).fetchall()


# --------------------------------------------------------------- URL helper

def thread_url(thread_slug, thread_id):
    """Reconstructs the canonical thread URL — no url column is stored,
    this is computed on demand instead."""
    return urljoin(BASE_URL, f"threads/{thread_slug}.{thread_id}/")


def category_url(category_slug, category_id):
    return urljoin(BASE_URL, f"forums/{category_slug}.{category_id}/")
