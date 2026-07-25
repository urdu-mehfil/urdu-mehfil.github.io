#!/usr/bin/env python3
"""
extract_common.py — shared logic used by 2_extract_category.py and
build_indexes.py: HTTP fetching, HTML cleaning, page templates, nav
building, and the filename-sanitizing helpers. Not run directly.
"""

import re
import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-archive/1.0; contact: YOUR-EMAIL-HERE)"}
session = requests.Session()
session.headers.update(HEADERS)

# Shared across all worker threads: if the server ever returns 429/503
# to ANY request, every worker (not just the one that got throttled)
# pauses until this timestamp before making further requests. This is
# what keeps concurrency from turning "polite but slow" into "fast but
# gets us blocked" — one bad response slows everyone down immediately.
_backoff_lock = threading.Lock()
_backoff_until = 0.0


def _wait_for_backoff():
    while True:
        with _backoff_lock:
            remaining = _backoff_until - time.time()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 5))


def _trigger_backoff(seconds):
    global _backoff_until
    with _backoff_lock:
        _backoff_until = max(_backoff_until, time.time() + seconds)

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ur" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} &mdash; page {page} of {total}</title>
<link rel="stylesheet" href="../../../assets/style.css">
</head>
<body>
<nav class="pagenav" data-pagefind-ignore>{nav}</nav>
<h1>{title}</h1>
{posts}
<nav class="pagenav" data-pagefind-ignore>{nav}</nav>
{page_list}
<p class="source" data-pagefind-ignore>Archived from: <a href="{source_url}">{source_url}</a></p>
</body>
</html>
"""

POST_TEMPLATE = """<div class="post">
  <div class="meta">{author} &mdash; {date}</div>
  <div class="content">{content}</div>
</div>
"""

WHITESPACE_BR_RE = re.compile(r'(?:<br\s*/?>\s*){2,}', re.IGNORECASE)
EMPTY_BLOCK_RE = re.compile(r'<(p|div)[^>]*>(\s|&nbsp;|<br\s*/?>)*</\1>', re.IGNORECASE)


def get(url, retries=3):
    """GET with polite delay + retry on transient failures. Also
    respects/triggers the shared backoff: waits if another worker
    recently got rate-limited, and if THIS request gets rate-limited,
    tells every other worker to pause too."""
    last_exc = None
    for attempt in range(1, retries + 1):
        _wait_for_backoff()
        try:
            time.sleep(1.5 + random.random())
            r = session.get(url, timeout=30)

            if r.status_code in (429, 503):
                retry_after = r.headers.get("Retry-After")
                wait_s = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else 30
                print(f"    ! server returned {r.status_code} for {url} "
                      f"— pausing ALL workers for {wait_s:.0f}s")
                _trigger_backoff(wait_s)
                last_exc = requests.HTTPError(f"{r.status_code} rate-limited: {url}")
                continue  # retry after the backoff clears, if attempts remain

            r.raise_for_status()
            return r.text
        except Exception as e:
            last_exc = e
            if attempt < retries:
                time.sleep(5 * attempt)
    raise last_exc


def get_many(urls, max_workers=1):
    """Fetch multiple URLs concurrently with a small worker pool.
    Each worker still goes through get()'s own per-request delay AND
    the shared backoff above — so raising max_workers increases
    throughput, but a 429/503 from the server still pauses every
    worker immediately rather than letting the rest keep hammering it.

    Returns {url: html} for successes; failed URLs (retries exhausted)
    are simply omitted from the result dict rather than raising, so
    one bad page doesn't take down a whole batch.

    Default is 1 (sequential, same as before this feature existed).
    This is still new/unverified against this specific server's real
    tolerance — if you raise it, start at 3, and watch the first batch
    closely for repeated "pausing ALL workers" messages, which mean
    even that is too aggressive.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_url = {ex.submit(get, u): u for u in urls}
        for future in as_completed(future_to_url):
            u = future_to_url[future]
            try:
                results[u] = future.result()
            except Exception as e:
                print(f"    ! failed permanently: {u} ({e})")
    return results


def clean_post_html(body_el):
    """Strip inline styles (text-align:center, font colors/sizes, etc.)
    that fight our own CSS, and collapse leftover blank lines/empty
    paragraphs that bloat the output."""
    for tag in body_el.find_all(True):
        for attr in ("style", "align", "bgcolor", "color"):
            if tag.has_attr(attr):
                del tag[attr]

    html = body_el.decode_contents().strip()
    html = WHITESPACE_BR_RE.sub("<br>", html)
    html = EMPTY_BLOCK_RE.sub("", html)
    return html


def local_filename(page: int) -> str:
    """Page 1's file IS index.html — no separate summary page to click
    through first. Later pages are page-2.html, page-3.html, etc."""
    return "index.html" if page == 1 else f"page-{page}.html"


def build_nav(page: int, total: int) -> str:
    prev_link = f'<a href="{local_filename(page - 1)}">&laquo; پچھلا صفحہ</a>' if page > 1 else "<span></span>"
    next_link = f'<a href="{local_filename(page + 1)}">اگلا صفحہ &raquo;</a>' if page < total else "<span></span>"

    current = f'<span>صفحہ {page} از {total}</span>'
    if page > 1:  # "Index" is redundant on page 1 itself (you're already there)
        current += ' <a href="index.html">Index</a>'

    return f'{next_link}{current}{prev_link}'


def build_page_list(total: int, max_links: int = 30) -> str:
    """Footer list of every page, shown on the index (page 1) only.
    Truncated to first/last 10 for very long threads so the footer
    doesn't itself become a huge wall of links."""
    if total <= 1:
        return ""

    def link(n):
        return f'<a href="{local_filename(n)}">{n}</a>'

    if total <= max_links:
        items = ["1"] + [link(n) for n in range(2, total + 1)]
    else:
        items = ["1"] + [link(n) for n in range(2, 11)] + ["&hellip;"] + \
                [link(n) for n in range(total - 9, total + 1)]

    return f'<div class="pagelist" data-pagefind-ignore>Pages: {" ".join(items)}</div>'


def extract_page(html, source_url, page: int, total: int):
    """Returns (page_html, title, post_count) or (None, title, 0) if no posts found.
    XenForo 2.x markup (article.message, div.bbWrapper, h1.p-title-value)
    is assumed. If this forum's theme customizes class names, adjust
    the selectors below."""
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one("h1.p-title-value")
    title = title_el.get_text(strip=True) if title_el else "Untitled thread"

    posts_html = []
    for article in soup.select("article.message"):
        author_el = article.select_one("a.username") or article.select_one(".message-name")
        date_el = article.select_one("time.u-dt")
        body_el = article.select_one("div.bbWrapper")
        if not body_el:
            continue
        author = author_el.get_text(strip=True) if author_el else "?"
        date = (date_el.get("title") if date_el else None) or \
               (date_el.get_text(strip=True) if date_el else "?")
        for junk in body_el.select("script, style"):
            junk.decompose()
        posts_html.append(POST_TEMPLATE.format(
            author=author, date=date, content=clean_post_html(body_el)
        ))

    if not posts_html:
        return None, title, 0

    nav = build_nav(page, total)
    page_list = build_page_list(total) if page == 1 else ""
    page_html = PAGE_TEMPLATE.format(
        title=title, page=page, total=total, posts="\n".join(posts_html),
        source_url=source_url, nav=nav, page_list=page_list,
    )
    return page_html, title, len(posts_html)


def safe_dirname(title: str, thread_id: str, max_bytes: int = 100) -> str:
    """Build a short, filesystem-safe directory name from a thread title.
    Strips characters that are invalid/problematic in filenames, then
    truncates by UTF-8 BYTE length (not character count) so we stay
    under filesystem limits even with multi-byte Urdu text — most
    filesystems cap filenames at 255 bytes, not 255 characters.
    Appends the thread_id so titles that collide/truncate to the same
    thing still get distinct folders.
    """
    cleaned = re.sub(r'[\\/:*?"<>|]+', '', title).strip()
    cleaned = re.sub(r'\s+', '_', cleaned)
    if not cleaned:
        cleaned = "thread"

    suffix = f".{thread_id}"
    budget = max_bytes - len(suffix.encode("utf-8"))

    encoded = cleaned.encode("utf-8")
    if len(encoded) > budget:
        encoded = encoded[:budget]
        # avoid cutting in the middle of a multi-byte UTF-8 character
        while encoded:
            try:
                cleaned = encoded.decode("utf-8")
                break
            except UnicodeDecodeError:
                encoded = encoded[:-1]
        else:
            cleaned = ""

    return f"{cleaned}{suffix}"


def sanitize_category_dir_name(category_name: str) -> str:
    """Same sanitizing rule used everywhere a category name becomes a
    directory name — kept in one place so 2_extract_category.py and
    build_indexes.py can never drift out of sync with each other."""
    return re.sub(r"[^\w\-]+", "_", category_name)[:40]


def page_url_for(base_url: str, page_num: int) -> str:
    return base_url if page_num == 1 else urljoin(base_url, f"page-{page_num}")
