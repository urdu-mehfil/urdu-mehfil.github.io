#!/usr/bin/env python3
"""
discover.py — page-count filling against the SQLite DB.

populate-categories / populate-topics and their helpers were removed:
the database is already populated (2000+ threads scraped), so
re-discovering categories/topics from the site isn't needed anymore.
If you ever DO need to discover brand-new threads in an existing
category again (e.g. the forum added new content), that logic would
need to be re-added — it's a straightforward mirror of what's in
extract.py's page-fetching, just for listing pages instead.

fill-page-counts is kept: it's still useful for wayback-submit, which
needs total_pages known ahead of time to build its list of page URLs
to submit (extract.py no longer needs this pre-filled — it now learns
total_pages from page 1 itself during extraction).
"""

import logging

from common import get, get_many, parse_total_pages
from db import (
    init_db, get_connection, get_topics_missing_page_counts, set_total_pages, progress_report, thread_url,
)

logger = logging.getLogger(__name__)


def fill_page_counts_batch(topic_rows, max_workers=1):
    """Fetch page counts for a list of topic rows concurrently and
    return {thread_id: total_pages}."""
    urls = {t["thread_id"]: thread_url(t["thread_slug"], t["thread_id"]) for t in topic_rows}
    html_by_url = get_many(list(urls.values()), max_workers=max_workers)
    results = {}
    for tid, url in urls.items():
        html = html_by_url.get(url)
        results[tid] = parse_total_pages(html) if html else None
    return results


def print_progress_report(db_path):
    with get_connection(db_path) as conn:
        rows = progress_report(conn)

    print(f"{'ID':>6}  {'Category':<30} {'Done':>7} / {'Total':<7}")
    grand_total, grand_done = 0, 0
    for r in rows:
        print(f"{r['category_id']:>6}  {r['category_name'][:30]:<30} {r['done']:>7} / {r['total']:<7}")
        grand_total += r["total"]
        grand_done += r["done"]
    print("-" * 60)
    if grand_total:
        print(f"{'TOTAL':>6}  {'':<30} {grand_done:>7} / {grand_total:<7}  "
              f"({grand_done / grand_total * 100:.1f}%)")
    else:
        print("No rows found.")


def run_fill_page_counts(db_path, category_id=None, thread_id=None, max_workers=1,
                          save_every=20, progress_only=False):
    init_db(db_path)  # idempotent schema/migration safeguard, same reasoning as extract.py

    if progress_only:
        print_progress_report(db_path)
        return

    with get_connection(db_path) as conn:
        targets = get_topics_missing_page_counts(
            conn,
            category_id=int(category_id) if category_id else None,
            thread_id=int(thread_id) if thread_id else None,
        )
        targets = [dict(r) for r in targets]

    if not targets:
        logger.info("Nothing to do — every matching row already has total_pages. "
                    "Use --progress to see the full breakdown.")
        return

    logger.info(f"{len(targets)} row(s) need total_pages. Fetching with max {max_workers} "
                f"concurrent request(s), saving every {save_every}...")
    logger.info("(Ctrl+C any time — progress made so far is saved as it goes.)")

    processed = 0
    try:
        for batch_start in range(0, len(targets), save_every):
            batch = targets[batch_start:batch_start + save_every]
            results = fill_page_counts_batch(batch, max_workers=max_workers)
            with get_connection(db_path) as conn:
                for tid, total_pages in results.items():
                    if total_pages is not None:
                        set_total_pages(conn, tid, total_pages)
            processed += len(batch)
            logger.info(f"  ...{processed}/{len(targets)} done.")
    except KeyboardInterrupt:
        logger.warning("Interrupted — whatever completed so far is already saved (each "
                       "batch commits as it goes).")
    logger.info(f"Done. {processed}/{len(targets)} filled in this run.")
