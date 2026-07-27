#!/usr/bin/env python3
"""
migrate_to_sqlite.py — standalone one-time conversion of old
categories.csv + topics.csv into mehfil.db (SQLite). Not part of the
archive.py subcommand set — run directly.

Safe to re-run: uses upserts, so running it again (e.g. after fixing
something in the CSVs) just re-syncs rather than duplicating.

Note: `total_pages` in the old CSV format used "" (empty string) to
mean "not fetched yet". SQLite uses a real NULL for that instead —
this script converts blank/missing values to NULL automatically.

`scraped_at` is intentionally left NULL for every migrated row — there
was no such concept in the CSV format. This is not a problem: if
you've already extracted some of these threads to archive/ under the
old system, extract.py's built-in legacy fallback will detect their
existing index.html on disk and mark them scraped automatically (zero
network calls) the first time you run `archive.py extract` for that
category — no separate backfill step needed here.

USAGE:
    python3 migrate_to_sqlite.py
    python3 migrate_to_sqlite.py --categories-csv categories.csv --topics-csv topics.csv --db mehfil.db
"""

import csv
import argparse

from db import init_db, get_connection, upsert_category, upsert_topic, \
    category_thread_count, get_categories


def migrate(categories_csv, topics_csv, db_path):
    init_db(db_path)

    with open(categories_csv, encoding="utf-8") as f:
        category_rows = list(csv.DictReader(f))

    with open(topics_csv, encoding="utf-8") as f:
        topic_rows = list(csv.DictReader(f))

    with get_connection(db_path) as conn:
        for r in category_rows:
            upsert_category(
                conn,
                category_id=int(r["category_id"]),
                category_name=r["category_name"],
                category_slug=r.get("category_slug") or "",
            )

        skipped = 0
        for r in topic_rows:
            total_pages_raw = (r.get("total_pages") or "").strip()
            total_pages = int(total_pages_raw) if total_pages_raw else None

            if not r.get("thread_slug"):
                skipped += 1
                continue

            upsert_topic(
                conn,
                thread_id=int(r["thread_id"]),
                category_id=int(r["category_id"]),
                thread_title=r["thread_title"],
                thread_slug=r["thread_slug"],
                total_pages=total_pages,
            )

    if skipped:
        print(f"Skipped {skipped} topic row(s) missing thread_slug — check the source CSV.")

    with get_connection(db_path) as conn:
        cats = get_categories(conn)
        print(f"Migrated {len(cats)} categories into {db_path}:")
        for c in cats:
            n = category_thread_count(conn, c["category_id"])
            print(f"  {c['category_id']:>6}  {c['category_name']:<30} {n} threads")

    print()
    print("Note: scraped_at is NULL for all migrated rows. If some of these threads "
          "were already extracted under the old system, running `archive.py extract` "
          "for that category will detect and mark them automatically (no re-scraping, "
          "no network calls for those) — see extract.py's legacy disk-fallback check.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--categories-csv", default="categories.csv")
    ap.add_argument("--topics-csv", default="topics.csv")
    ap.add_argument("--db", default="mehfil.db")
    args = ap.parse_args()

    migrate(args.categories_csv, args.topics_csv, args.db)


if __name__ == "__main__":
    main()
