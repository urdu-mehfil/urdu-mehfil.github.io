#!/usr/bin/env python3
"""
wayback.py — submit thread pages to the Internet Archive's Wayback
Machine (web.archive.org), reading targets from the SQLite DB.

Setup (free):
    1. Sign up at https://archive.org
    2. Get S3-style keys at https://archive.org/account/s3.php
    3. export SAVEPAGENOW_ACCESS_KEY=...
       export SAVEPAGENOW_SECRET_KEY=...

Expands each thread into ALL of its page URLs via total_pages before
submitting — a 51-page thread submits 51 URLs, not just page 1.
Threads still missing total_pages only submit page 1.
"""

import time
import logging
import argparse
import os

from db import init_db, get_connection, get_categories, get_topics_for_category, thread_url
from common import page_url_for

logger = logging.getLogger(__name__)

try:
    import savepagenow
except ImportError:
    savepagenow = None


def expand_to_page_urls(topic_row: dict) -> list:
    total_pages = topic_row["total_pages"] or 1
    base_url = thread_url(topic_row["thread_slug"], topic_row["thread_id"])
    return [page_url_for(base_url, p) for p in range(1, total_pages + 1)]


def run_wayback_submit(db_path, category_id=None, submit_all=False):
    init_db(db_path)  # idempotent schema/migration safeguard

    if savepagenow is None:
        raise SystemExit("pip install savepagenow")

    with get_connection(db_path) as conn:
        if submit_all:
            categories = get_categories(conn)
            rows = []
            for c in categories:
                rows.extend(dict(r) for r in get_topics_for_category(conn, c["category_id"]))
        elif category_id is not None:
            rows = [dict(r) for r in get_topics_for_category(conn, int(category_id))]
        else:
            raise SystemExit("Pass --category-id ID or --all")

    if not rows:
        raise SystemExit("No matching topics found.")

    authenticated = bool(os.environ.get("SAVEPAGENOW_ACCESS_KEY"))
    if not authenticated:
        logger.warning("No SAVEPAGENOW_ACCESS_KEY set — running unauthenticated, "
                       "which is slower and less reliable.")

    all_urls = []
    for r in rows:
        all_urls.extend(expand_to_page_urls(r))

    logger.info(f"{len(rows)} threads -> {len(all_urls)} total page URLs to submit")

    for i, url in enumerate(all_urls, 1):
        try:
            archived_url, captured = savepagenow.capture_or_cache(url, authenticate=authenticated)
            logger.info(f"[{i}/{len(all_urls)}] {url} -> {archived_url}")
        except Exception as e:
            logger.error(f"[{i}/{len(all_urls)}] FAILED {url}: {e}")
        time.sleep(10 if authenticated else 20)  # stay comfortably under ~6/min


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category-id")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--db", default="mehfil.db")
    args = ap.parse_args()
    run_wayback_submit(args.db, category_id=args.category_id, submit_all=args.all)


if __name__ == "__main__":
    main()
