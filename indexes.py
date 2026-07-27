#!/usr/bin/env python3
"""
indexes.py — regenerates the browsable listing pages:

    archive/categories/index.html             — every category with any
                                                 archived threads, + counts
    archive/categories/<category>/index.html  — every archived thread
                                                 in that category

Both are rebuilt by scanning the FILESYSTEM (which thread folders
actually have an index.html on disk), not by trusting "what this run
just did" — so a resumed/partial extraction run still produces a
correct, complete listing.

The landing page (archive/index.html) is NOT touched by this script —
that one is standalone and hand-maintained (holds the Pagefind search
UI), since it doesn't depend on how much has been archived.
"""

import argparse
from pathlib import Path

from common import safe_dirname, sanitize_category_dir_name
from db import init_db, get_connection, get_categories, get_category, get_topics_for_category

CATEGORY_INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="ur" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{category_name}</title>
<link rel="stylesheet" href="../../assets/style.css">
</head>
<body data-pagefind-ignore>
<h1>{category_name}</h1>
<p class="meta">ابھی تک {count} موضوع محفوظ ہوئے ہیں</p>
<ul>
{links}
</ul>
<p><a href="../index.html">تمام زمرے دیکھیں &raquo;</a></p>
</body>
</html>
"""

CATEGORIES_INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="ur" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>تمام زمرہ جات</title>
<link rel="stylesheet" href="../assets/style.css">
</head>
<body data-pagefind-ignore>
<h1>زمرہ جات</h1>
<ul>
{links}
</ul>
<p><a href="../index.html">واپس &raquo;</a></p>
</body>
</html>
"""


def rebuild_category_index(db_path, category_id, category_name, out_root: Path):
    init_db(db_path)  # idempotent schema/migration safeguard

    with get_connection(db_path) as conn:
        topics = [dict(r) for r in get_topics_for_category(conn, category_id)]

    cat_dir_name = sanitize_category_dir_name(category_name)
    cat_dir = out_root / "categories" / cat_dir_name
    cat_dir.mkdir(parents=True, exist_ok=True)

    items = []
    for t in topics:
        dir_name = safe_dirname(t["thread_title"], t["thread_id"])
        if (cat_dir / dir_name / "index.html").exists():
            items.append((dir_name, t["thread_title"]))

    items.sort(key=lambda x: x[1])
    links = "\n".join(f'  <li><a href="{d}/index.html">{title}</a></li>' for d, title in items)

    html = CATEGORY_INDEX_TEMPLATE.format(category_name=category_name, count=len(items), links=links)
    (cat_dir / "index.html").write_text(html, encoding="utf-8")
    return len(items)


def rebuild_categories_index(db_path, out_root: Path):
    init_db(db_path)  # idempotent schema/migration safeguard

    with get_connection(db_path) as conn:
        categories = [dict(r) for r in get_categories(conn)]

    entries = []
    for c in categories:
        cat_dir_name = sanitize_category_dir_name(c["category_name"])
        cat_dir = out_root / "categories" / cat_dir_name
        count = len(list(cat_dir.glob("*/index.html"))) if cat_dir.exists() else 0
        if count > 0:
            entries.append((cat_dir_name, c["category_name"], count))

    entries.sort(key=lambda e: e[1])
    links = "\n".join(
        f'  <li><a href="{d}/index.html">{n}</a> ({c} موضوع{"ات" if c != 1 else ""})</li>'
        for d, n, c in entries
    )

    out_dir = out_root / "categories"
    out_dir.mkdir(parents=True, exist_ok=True)
    html = CATEGORIES_INDEX_TEMPLATE.format(links=links)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    return len(entries)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category-id")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--db", default="mehfil.db")
    ap.add_argument("--out", default="archive")
    args = ap.parse_args()

    if not args.category_id and not args.all:
        raise SystemExit("Pass --category-id ID or --all")

    out_root = Path(args.out)

    with get_connection(args.db) as conn:
        categories = [dict(r) for r in get_categories(conn)]

    if args.all:
        for c in categories:
            n = rebuild_category_index(args.db, c["category_id"], c["category_name"], out_root)
            print(f"  {c['category_name']}: {n} threads")
    else:
        c = next((c for c in categories if c["category_id"] == int(args.category_id)), None)
        if not c:
            raise SystemExit(f"No category with id={args.category_id}")
        n = rebuild_category_index(args.db, c["category_id"], c["category_name"], out_root)
        print(f"  {c['category_name']}: {n} threads")

    n_cats = rebuild_categories_index(args.db, out_root)
    print(f"Rebuilt categories/index.html — {n_cats} categories listed")


if __name__ == "__main__":
    main()
