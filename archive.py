#!/usr/bin/env python3
"""
archive.py — single entry point for the whole urdu-mehfil archive
project. Every action is its own subcommand with scoped arguments and
its own --help, so invalid combinations (e.g. --thread-id on a command
that doesn't use it) simply can't be typed.

USAGE:
    python3 archive.py --help
    python3 archive.py <subcommand> --help

Subcommands:
    fill-page-counts     backfill total_pages for topics missing it (resumable;
                          mainly useful for wayback-submit -- extract no longer
                          needs this pre-filled, it learns page counts inline)
    extract               crawl + archive a category's not-yet-scraped threads
    build-indexes         rebuild categories/index.html + per-category index.html
    stats                 page-count distribution + per-category thread counts
    wayback-submit        submit thread pages to web.archive.org

Assumes the DB is already populated (categories + topics) -- this
version doesn't include a discovery/migration step. If you're
starting fresh and need to (re-)build the DB from the site or from
old CSVs, that logic will need to be reintroduced.

Typical flow:
    python3 archive.py extract --category-id 59 --thread-workers 2 --max-workers 3
    python3 archive.py stats
    python3 archive.py wayback-submit --category-id 59
"""

import argparse
import logging

from config import DB_PATH, ARCHIVE_OUT_DIR, DEFAULT_MAX_WORKERS, DEFAULT_SAVE_EVERY
from logging_setup import setup_logging

logger = logging.getLogger(__name__)


# ------------------------------------------------------------ subcommand handlers

def cmd_fill_page_counts(args):
    from discover import run_fill_page_counts
    run_fill_page_counts(
        args.db,
        category_id=args.category_id,
        thread_id=args.thread_id,
        max_workers=args.max_workers,
        save_every=args.save_every,
        progress_only=args.progress,
    )


def cmd_extract(args):
    from extract import run_extract
    run_extract(
        args.db,
        category_id=args.category_id,
        category_name=args.category,
        thread_workers=args.thread_workers,
        max_workers=args.max_workers,
        out=args.out,
    )


def cmd_build_indexes(args):
    from pathlib import Path
    from indexes import rebuild_category_index, rebuild_categories_index
    from db import get_connection, get_categories

    out_root = Path(args.out)
    with get_connection(args.db) as conn:
        categories = [dict(r) for r in get_categories(conn)]

    if args.all:
        for c in categories:
            n = rebuild_category_index(args.db, c["category_id"], c["category_name"], out_root)
            logger.info(f"  {c['category_name']}: {n} threads")
    elif args.category_id:
        c = next((c for c in categories if c["category_id"] == int(args.category_id)), None)
        if not c:
            raise SystemExit(f"No category with id={args.category_id}")
        n = rebuild_category_index(args.db, c["category_id"], c["category_name"], out_root)
        logger.info(f"  {c['category_name']}: {n} threads")
    else:
        raise SystemExit("Pass --category-id ID or --all")

    n_cats = rebuild_categories_index(args.db, out_root)
    logger.info(f"Rebuilt categories/index.html — {n_cats} categories listed")


def cmd_stats(args):
    from stats import run_stats
    run_stats(args.db, category_id=args.category_id, buckets=args.buckets, sort_by=args.sort_by)


def cmd_wayback_submit(args):
    from wayback import run_wayback_submit
    run_wayback_submit(args.db, category_id=args.category_id, submit_all=args.all)


# ------------------------------------------------------------------------ CLI

def build_parser():
    ap = argparse.ArgumentParser(
        prog="archive.py",
        description="Archive urduweb.org/mehfil to clean, searchable, static HTML.",
    )
    ap.add_argument("--verbose", action="store_true", help="Debug-level logging to console + file")
    sub = ap.add_subparsers(dest="command", required=True)

    # fill-page-counts
    p = sub.add_parser("fill-page-counts", help="Backfill total_pages for topics missing it (resumable)")
    p.add_argument("--category-id", help="Only this category")
    p.add_argument("--thread-id", help="Only this single thread")
    p.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS,
                    help=f"Concurrent requests (default {DEFAULT_MAX_WORKERS} = sequential). "
                         f"This forum has rate-limited/blocked before — raise deliberately and "
                         f"watch for '! server returned 429/503' / backoff log messages.")
    p.add_argument("--save-every", type=int, default=DEFAULT_SAVE_EVERY,
                    help="[legacy] Progress-report cadence; each thread's update is already its "
                         "own DB commit regardless of this value")
    p.add_argument("--progress", action="store_true", help="Just report done/remaining, no requests")
    p.add_argument("--db", default=DB_PATH)
    p.set_defaults(func=cmd_fill_page_counts)

    # extract
    p = sub.add_parser("extract", help="Crawl + archive a category's threads to clean HTML")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--category", help="Category name exactly as stored")
    g.add_argument("--category-id", help="Category id")
    p.add_argument("--thread-workers", type=int, default=1,
                    help="How many forum THREADS to crawl at once (default 1 = sequential). "
                         "NOTE: total concurrent requests = thread-workers x max-workers.")
    p.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS,
                    help=f"Per thread, how many of ITS OWN pages to fetch at once "
                         f"(default {DEFAULT_MAX_WORKERS} = sequential). See thread-workers note above.")
    p.add_argument("--out", default=ARCHIVE_OUT_DIR)
    p.add_argument("--db", default=DB_PATH)
    p.set_defaults(func=cmd_extract)

    # build-indexes
    p = sub.add_parser("build-indexes", help="Rebuild categories/index.html + per-category index.html")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--category-id")
    g.add_argument("--all", action="store_true")
    p.add_argument("--out", default=ARCHIVE_OUT_DIR)
    p.add_argument("--db", default=DB_PATH)
    p.set_defaults(func=cmd_build_indexes)

    # stats
    p = sub.add_parser("stats", help="Page-count distribution + per-category thread counts")
    p.add_argument("--category-id")
    p.add_argument("--buckets", default="10,50,100,500,1000,5000")
    p.add_argument("--sort-by", choices=["count", "id", "name"], default="count")
    p.add_argument("--db", default=DB_PATH)
    p.set_defaults(func=cmd_stats)

    # wayback-submit
    p = sub.add_parser("wayback-submit", help="Submit thread pages to web.archive.org")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--category-id")
    g.add_argument("--all", action="store_true")
    p.add_argument("--db", default=DB_PATH)
    p.set_defaults(func=cmd_wayback_submit)

    return ap


def main():
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(verbose=args.verbose)

    if hasattr(args, "db"):
        from db import init_db
        init_db(args.db)  # safe/idempotent — creates tables only if they don't exist yet

    args.func(args)


if __name__ == "__main__":
    main()
