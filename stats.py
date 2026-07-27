#!/usr/bin/env python3
"""
stats.py — distribution of thread sizes (page counts), plus a
thread-count-per-category breakdown, read from the SQLite DB.

USAGE:
    python3 stats.py
    python3 stats.py --category-id 59
    python3 stats.py --buckets 10,50,100,500,1000,5000
    python3 stats.py --sort-by name
"""

import argparse

from db import init_db, get_connection, get_categories, get_topics_for_category


def print_bucket_stats(rows, bounds, scope_label, db_path):
    unknown = 0
    known_pages = []
    for r in rows:
        if r["total_pages"] is not None:
            known_pages.append(r["total_pages"])
        else:
            unknown += 1

    bucket_counts = [0] * (len(bounds) + 1)
    bucket_sums = [0] * (len(bounds) + 1)
    for p in known_pages:
        placed = False
        for i, b in enumerate(bounds):
            if p < b:
                bucket_counts[i] += 1
                bucket_sums[i] += p
                placed = True
                break
        if not placed:
            bucket_counts[-1] += 1
            bucket_sums[-1] += p

    total_threads = len(rows)
    total_known = len(known_pages)
    total_pages_sum = sum(known_pages)

    print(f"Stats for {scope_label} ({db_path})")
    print(f"Threads: {total_threads}  (total_pages known: {total_known}, pending: {unknown})")
    print()
    print("-- Page-count distribution --")
    print(f"{'Range':<15}{'Threads':>10}{'% of known':>12}{'Total pages':>14}")
    prev = 0
    for i, b in enumerate(bounds):
        count = bucket_counts[i]
        pct = (count / total_known * 100) if total_known else 0
        print(f"{f'{prev}-{b - 1}':<15}{count:>10}{pct:>11.1f}%{bucket_sums[i]:>14}")
        prev = b
    count = bucket_counts[-1]
    pct = (count / total_known * 100) if total_known else 0
    print(f"{f'{bounds[-1]}+':<15}{count:>10}{pct:>11.1f}%{bucket_sums[-1]:>14}")
    print("-" * 51)
    print(f"{'TOTAL known':<15}{total_known:>10}{'100.0%':>12}{total_pages_sum:>14}")

    if unknown:
        print(f"\n{unknown} threads still have no total_pages — run "
              f"`python3 archive.py fill-page-counts` to fill them in first "
              f"if you want complete stats.")


def print_category_counts(db_path, sort_by):
    with get_connection(db_path) as conn:
        categories = [dict(r) for r in get_categories(conn)]
        counts = {}
        for c in categories:
            topics = get_topics_for_category(conn, c["category_id"])
            counts[c["category_id"]] = {"name": c["category_name"], "count": len(topics)}

    items = list(counts.items())
    if sort_by == "name":
        items.sort(key=lambda kv: kv[1]["name"])
    elif sort_by == "id":
        items.sort(key=lambda kv: kv[0])
    else:
        items.sort(key=lambda kv: kv[1]["count"], reverse=True)

    print()
    print("-- Threads per category --")
    print(f"{'ID':>6}  {'Category':<30}{'Threads':>10}")
    total = 0
    for cid, c in items:
        print(f"{cid:>6}  {c['name'][:30]:<30}{c['count']:>10}")
        total += c["count"]
    print("-" * 48)
    print(f"{'TOTAL':>6}  {'':<30}{total:>10}  ({len(items)} categories)")


def run_stats(db_path, category_id=None, buckets="10,50,100,500,1000,5000", sort_by="count"):
    init_db(db_path)  # idempotent schema/migration safeguard

    bounds = [int(b) for b in buckets.split(",")]

    with get_connection(db_path) as conn:
        if category_id:
            rows = [dict(r) for r in get_topics_for_category(conn, int(category_id))]
            scope_label = f"category {category_id}"
        else:
            categories = get_categories(conn)
            rows = []
            for c in categories:
                rows.extend(dict(r) for r in get_topics_for_category(conn, c["category_id"]))
            scope_label = "all categories"

    if not rows:
        print("No rows matched.")
        return

    print_bucket_stats(rows, bounds, scope_label, db_path)

    if not category_id:
        print_category_counts(db_path, sort_by)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="mehfil.db")
    ap.add_argument("--category-id")
    ap.add_argument("--buckets", default="10,50,100,500,1000,5000")
    ap.add_argument("--sort-by", choices=["count", "id", "name"], default="count")
    args = ap.parse_args()

    run_stats(args.db, category_id=args.category_id, buckets=args.buckets, sort_by=args.sort_by)


if __name__ == "__main__":
    main()
