#!/usr/bin/env python3
"""
2_extract_category.py — Given a category (looked up in topics.csv,
produced by 1_discover.py), fetch and extract clean, content-only
archives for every thread in that category — every page of every
thread, using the total_pages column recorded during discovery.

USAGE:
    pip install requests beautifulsoup4 lxml
    python3 2_extract_category.py --category "بزم سخن"
    python3 2_extract_category.py --category-id 59

Output structure:
    archive/
      index.html                             <- standalone landing page, not touched here
      assets/
        style.css
        mehr.woff2                           <- you download this yourself
      categories/
        index.html                           <- rebuilt at the end of every run
        <category>/
          index.html                         <- rebuilt at the end of every run
          <thread-title>.<thread_id>/
            index.html                       <- page 1's content + page-list footer
            page-2.html
            ...

Resumable: if a thread's last expected page file already exists on
disk, the whole thread is skipped — so re-running the same command
after a stop/crash just continues with whatever threads aren't done.
The category/top-level index pages are always rebuilt by scanning
disk, so they stay correct across resumed/partial runs too.
"""

import csv
import argparse
from pathlib import Path

import requests

from extract_common import get_many, safe_dirname, local_filename, extract_page, page_url_for, \
    sanitize_category_dir_name
from build_indexes import rebuild_category_index, rebuild_categories_index


def crawl_thread(thread, out_root: Path, cat_dir_name: str, max_workers: int = 1):
    dir_name = safe_dirname(thread["thread_title"], thread["thread_id"])
    thread_dir = out_root / "categories" / cat_dir_name / dir_name
    total_pages = int(thread["total_pages"]) if thread.get("total_pages") else 1

    # Resume: if the last page is already saved, assume this thread is done.
    if (thread_dir / local_filename(total_pages)).exists():
        return "skipped"

    thread_dir.mkdir(parents=True, exist_ok=True)

    page_urls = [page_url_for(thread["url"], p) for p in range(1, total_pages + 1)]
    html_by_url = get_many(page_urls, max_workers=max_workers)

    for page, page_url in enumerate(page_urls, start=1):
        html = html_by_url.get(page_url)
        if html is None:
            continue  # fetch failed after retries — skip this page rather than aborting the thread
        page_html, title, post_count = extract_page(html, page_url, page, total_pages)
        if page_html is not None:
            (thread_dir / local_filename(page)).write_text(page_html, encoding="utf-8")

    return "done"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", help="Category name exactly as it appears in categories.csv")
    ap.add_argument("--category-id", help="Category id as it appears in categories.csv")
    ap.add_argument("--out", default="archive")
    ap.add_argument("--max-workers", type=int, default=1,
                     help="Concurrent requests when fetching a thread's pages "
                          "(default 1 = same sequential, one-at-a-time behavior as before). "
                          "This forum has rate-limited/blocked before at high sequential volume — "
                          "raise it deliberately (try 3 first), watch for '! server returned "
                          "429/503' messages, and only go higher if a full category runs clean.")
    args = ap.parse_args()

    if not args.category and not args.category_id:
        raise SystemExit('Pass --category "name" or --category-id ID (see categories.csv)')

    with open("topics.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if args.category_id:
        selected = [r for r in rows if r["category_id"] == str(args.category_id)]
    else:
        selected = [r for r in rows if r["category_name"] == args.category]

    if not selected:
        raise SystemExit("No matching topics found. Check categories.csv for exact names/ids.")

    category_name = selected[0]["category_name"]
    cat_dir_name = sanitize_category_dir_name(category_name)
    out_root = Path(args.out)

    print(f"Archiving {len(selected)} threads in category '{category_name}' "
          f"(max {args.max_workers} concurrent requests per thread)...")
    done, skipped, failed = 0, 0, 0

    for i, thread in enumerate(selected, 1):
        try:
            result = crawl_thread(thread, out_root, cat_dir_name, max_workers=args.max_workers)
        except Exception as e:
            failed += 1
            print(f"[{i}/{len(selected)}] {thread['thread_title']} — FAILED: {e}")
            continue

        if result == "skipped":
            skipped += 1
            print(f"[{i}/{len(selected)}] {thread['thread_title']} — already done, skipped")
        else:
            done += 1
            print(f"[{i}/{len(selected)}] {thread['thread_title']} "
                  f"({thread.get('total_pages') or 1} page(s))")

    if failed:
        print(f"NOTE: {failed} thread(s) failed outright — if that's more than a couple, "
              f"the server may be pushing back on the concurrency level. Consider lowering "
              f"--max-workers and re-running (already-done threads are skipped).")

    print(f"Done. {done} threads archived, {skipped} already done. "
          f"Output under {out_root}/categories/{cat_dir_name}/")

    # Rebuild this category's thread listing + the top-level categories
    # listing by scanning disk — correct even on a resumed/partial run.
    print("Rebuilding index pages...")
    n_threads = rebuild_category_index(selected, category_name, out_root)
    n_cats = rebuild_categories_index(rows, out_root)
    print(f"  {category_name}: {n_threads} threads listed")
    print(f"  categories/index.html: {n_cats} categories listed")


if __name__ == "__main__":
    main()
