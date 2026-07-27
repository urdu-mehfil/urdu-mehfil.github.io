#!/usr/bin/env python3
"""
extract.py — fetch and extract clean, content-only archives for every
not-yet-scraped thread in a category.

ITERATIVE "FOLLOW-THE-LINK" PATTERN (no more pre-calculate-then-fetch):
Page 1 is fetched first, alone. That single response serves double
duty — it's both the content for page 1 AND the source of the total
page count (via its own pagination nav, same as before). Only once
that's known do we fetch the remaining pages. There is no longer a
separate, dedicated "learn how many pages this thread has" request
before extraction starts — the old two-step (discover.py records
total_pages, THEN extract.py fetches based on that stored number) is
now one step.

SCRAPED-STATUS CHECK: before touching the network at all, a thread's
DB row is checked for `scraped_at`. If already set, it's skipped
immediately — zero cost. If not yet set (e.g. one of the 2000+ threads
scraped before this column existed), a single local file check (does
its index.html already exist on disk?) is tried BEFORE any network
request — if so, it's marked scraped in the DB right there, with zero
HTTP calls. This makes the DB self-heal to reflect reality over the
first run or two after this change, after which every skip is a pure
SQL filter with no disk I/O either.

Concurrency is still two levels, both opt-in (default 1 = sequential):
  - --thread-workers: how many forum threads are being crawled at once
  - --max-workers: for each thread, once its total page count is known,
    how many of ITS OWN remaining pages are fetched at once

Total concurrent requests = thread-workers x max-workers.
"""

import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from common import get, get_many, safe_dirname, local_filename, extract_page, page_url_for, \
    parse_total_pages, sanitize_category_dir_name
from db import init_db, get_connection, get_category, get_categories, get_topics_for_category, \
    get_unscraped_topics_for_category, mark_scraped, thread_url
from indexes import rebuild_category_index, rebuild_categories_index

logger = logging.getLogger(__name__)


def crawl_thread(db_path, thread, out_root: Path, cat_dir_name: str, max_workers: int = 1):
    dir_name = safe_dirname(thread["thread_title"], thread["thread_id"])
    thread_dir = out_root / "categories" / cat_dir_name / dir_name

    # Legacy fallback: not marked scraped_at yet, but may already be
    # fully archived on disk from before this column existed. One
    # local file check, zero network requests.
    if (thread_dir / "index.html").exists():
        with get_connection(db_path) as conn:
            mark_scraped(conn, thread["thread_id"])
        return "already-on-disk"

    thread_dir.mkdir(parents=True, exist_ok=True)
    base_url = thread_url(thread["thread_slug"], thread["thread_id"])

    # Fetch page 1 ALONE first — this is the actual fix for the
    # pre-calculate-then-fetch bottleneck. This one request gives us
    # both the content and (via its own pagination nav) the total page
    # count, instead of a separate earlier request just to learn the
    # number.
    try:
        html = get(base_url)
    except Exception as e:
        logger.error(f"Failed to fetch page 1 of '{thread['thread_title']}': {e}")
        return "failed"

    total_pages = parse_total_pages(html)
    page_html, _, _ = extract_page(html, base_url, 1, total_pages)
    if page_html is not None:
        (thread_dir / "index.html").write_text(page_html, encoding="utf-8")

    if total_pages > 1:
        remaining_urls = [page_url_for(base_url, p) for p in range(2, total_pages + 1)]
        html_by_url = get_many(remaining_urls, max_workers=max_workers)
        for page, page_url in enumerate(remaining_urls, start=2):
            html_p = html_by_url.get(page_url)
            if html_p is None:
                continue
            page_html, _, _ = extract_page(html_p, page_url, page, total_pages)
            if page_html is not None:
                (thread_dir / local_filename(page)).write_text(page_html, encoding="utf-8")

    with get_connection(db_path) as conn:
        mark_scraped(conn, thread["thread_id"], total_pages=total_pages)

    return "done"


def run_extract(db_path, category_id=None, category_name=None,
                 thread_workers: int = 1, max_workers: int = 1, out="archive"):
    init_db(db_path)  # idempotent — guarantees scraped_at etc. exist even if this
                       # wasn't called via archive.py's main() (e.g. an older/direct
                       # invocation), which is exactly what caused the
                       # "no such column: scraped_at" error this is fixing.

    with get_connection(db_path) as conn:
        if category_id is not None:
            cat = get_category(conn, int(category_id))
            if cat is None:
                raise SystemExit(f"No category with id={category_id}.")
        else:
            matches = [c for c in get_categories(conn) if c["category_name"] == category_name]
            if not matches:
                raise SystemExit(f"No category named '{category_name}'. Check the exact name.")
            cat = matches[0]

        total_in_category = len(get_topics_for_category(conn, cat["category_id"]))
        selected = [dict(r) for r in get_unscraped_topics_for_category(conn, cat["category_id"])]

    already_scraped = total_in_category - len(selected)
    cat_dir_name = sanitize_category_dir_name(cat["category_name"])
    out_root = Path(out)

    if not selected:
        logger.info(f"All {total_in_category} threads in '{cat['category_name']}' are already "
                    f"scraped. Nothing to do.")
    else:
        total_concurrent_ceiling = thread_workers * max_workers
        logger.info(f"{cat['category_name']}: {len(selected)} thread(s) to scrape "
                    f"({already_scraped} already done) — thread-workers={thread_workers} x "
                    f"max-workers={max_workers} = up to {total_concurrent_ceiling} concurrent requests.")

    done, on_disk, failed = 0, 0, 0

    def process(thread):
        return thread, crawl_thread(db_path, thread, out_root, cat_dir_name, max_workers=max_workers)

    if thread_workers <= 1:
        for i, thread in enumerate(selected, 1):
            try:
                _, result = process(thread)
            except Exception as e:
                failed += 1
                logger.error(f"[{i}/{len(selected)}] {thread['thread_title']} — FAILED: {e}")
                continue
            done, on_disk = _log_and_count(i, len(selected), thread, result, done, on_disk)
    else:
        with ThreadPoolExecutor(max_workers=thread_workers) as ex:
            futures = {ex.submit(process, t): t for t in selected}
            for i, future in enumerate(as_completed(futures), 1):
                thread = futures[future]
                try:
                    _, result = future.result()
                except Exception as e:
                    failed += 1
                    logger.error(f"[{i}/{len(selected)}] {thread['thread_title']} — FAILED: {e}")
                    continue
                done, on_disk = _log_and_count(i, len(selected), thread, result, done, on_disk)

    if failed:
        logger.warning(f"{failed} thread(s) failed outright — if that's more than a couple, "
                       f"the server may be pushing back on the concurrency level. Consider "
                       f"lowering --thread-workers/--max-workers and re-running.")

    logger.info(f"Done. {done} freshly scraped, {on_disk} recovered from existing disk data, "
                f"{failed} failed. Output under {out_root}/categories/{cat_dir_name}/")

    logger.info("Rebuilding index pages...")
    n_threads = rebuild_category_index(db_path, cat["category_id"], cat["category_name"], out_root)
    n_cats = rebuild_categories_index(db_path, out_root)
    logger.info(f"  {cat['category_name']}: {n_threads} threads listed")
    logger.info(f"  categories/index.html: {n_cats} categories listed")


def _log_and_count(i, total, thread, result, done, on_disk):
    if result == "already-on-disk":
        on_disk += 1
        logger.info(f"[{i}/{total}] {thread['thread_title']} — already on disk, marked scraped")
    elif result == "done":
        done += 1
        logger.info(f"[{i}/{total}] {thread['thread_title']} — done")
    return done, on_disk
