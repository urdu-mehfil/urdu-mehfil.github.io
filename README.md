# Archiving urduweb.org/mehfil (XenForo forum)

A resumable, polite scraper + static-HTML archive builder for the
Mehfil forum on urduweb.org, with full-text search (Pagefind) and
optional backup to the Internet Archive's Wayback Machine.

Confirmed URL structure (checked against a live thread page):

```
Category:        /mehfil/forums/<slug>.<id>/
Thread, page 1:  /mehfil/threads/<slug>.<id>/
Thread, page N:  /mehfil/threads/<slug>.<id>/page-N      (N >= 2)
Post permalink:  /mehfil/threads/<slug>.<id>/post-<postid>
```

A thread's page-1 HTML tells you its total page count directly (a
pagination link with `title="Last"`, and/or literal text like
`"30 از 51"`), so discovery reads that off once per thread instead of
guessing or fetching pages until one 404s.

## Architecture

Everything runs through one entry point, **`archive.py`**, with each
action as its own subcommand:

| File | Purpose |
|---|---|
| `archive.py` | CLI entry point — every subcommand lives here |
| `config.py` | Central constants: User-Agent, base URL, DB path, timeouts, retry/backoff settings |
| `db.py` | SQLite schema + all reads/writes (categories, topics, scraped status) |
| `common.py` | Shared fetching (HTTPAdapter+Retry, backoff), HTML cleaning, page templates |
| `logging_setup.py` | Logging config — console + `archive.log` file |
| `discover.py` | Page-count filling (used by `wayback-submit`) |
| `extract.py` | Crawls and archives a category's not-yet-scraped threads |
| `indexes.py` | Rebuilds the browsable category/thread listing pages |
| `stats.py` | Page-count distribution + per-category thread counts |
| `wayback.py` | Submits archived pages to web.archive.org |

This assumes categories and topics are already populated in
**`mehfil.db`** (SQLite) — there's no discovery/migration subcommand
in this version. If you ever need to (re-)discover brand-new threads
in an existing category, or import from the old CSV-based setup,
that logic would need to be reintroduced; it isn't part of the
current toolset since the database is already built.

## Setup

```bash
python -m venv ./venv/
source ./venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` should include: `requests`, `beautifulsoup4`,
`lxml`, `urllib3`. Optional: `savepagenow` (for the Wayback step),
`pagefind` (for search — see below).

## Usage

```bash
python3 archive.py --help
python3 archive.py <subcommand> --help    # each subcommand has its own scoped options
```

Typical flow:

```bash
python3 archive.py extract --category-id 59 --thread-workers 2 --max-workers 3
python3 archive.py build-indexes --category-id 59
python3 archive.py stats
python3 archive.py wayback-submit --category-id 59
```

### `fill-page-counts`

Backfills `total_pages` for any topic missing it. **No longer needed
before running `extract`** — extraction now learns each thread's page
count from its own page-1 fetch (see below). This subcommand mainly
exists for `wayback-submit`, which needs `total_pages` known ahead of
time to build its list of page URLs before submitting.

Fully resumable — each thread's result is its own DB commit, so
Ctrl+C or a crash never loses more than the request in flight.
`--progress` shows a per-category done/total table with no network
calls:

```
    ID  Category                          Done / Total
    59  بزم سخن                            1200 / 26800
    34  متفرقات                             300 / 300
------------------------------------------------------------
 TOTAL                                     1500 / 27100  (5.5%)
```

### `extract`

Fetches every page of every **not-yet-scraped** thread in a category
and writes lean, content-only HTML (no nav/sidebar/scripts from the
original site) to:

```
archive/
  index.html                     <- standalone landing page + Pagefind search UI
  assets/
    style.css
    mehr.woff2                   <- download yourself, place alongside style.css
  categories/
    index.html                   <- rebuilt after every extract run
    <category>/
      index.html                 <- rebuilt after every extract run
      <thread-title>.<thread_id>/
        index.html               <- page 1's content + a page-list footer
        page-2.html
        ...
```

**Iterative "follow-the-link" fetching** — no more pre-calculate-then-
fetch: page 1 is fetched alone first, and that single response serves
double duty — it's both the content for page 1 *and* the source of the
total page count (via its own pagination nav). Only once that's known
are the remaining pages fetched. There's no separate, dedicated "learn
how many pages this thread has" request before extraction starts.

**Scraped-status tracking** — before any network activity, a thread's
`scraped_at` column in the DB is checked. Already set → skipped
immediately, no cost at all. Not set (e.g. threads scraped before this
column existed) → one local file check (does its `index.html` already
exist on disk?) is tried before any network request; if so, it's
marked scraped with zero HTTP calls and its existing content is left
untouched. This makes the DB self-heal to reflect reality over the
first run or two, after which every skip is a pure SQL filter with no
disk I/O either.

**Concurrency is two levels, both opt-in (default 1 = fully sequential,
identical to no concurrency at all):**

- `--thread-workers N` — how many forum *threads* are crawled at once
- `--max-workers N` — for each of those threads, how many of *its own
  pages* are fetched at once

**Total concurrent requests = thread-workers × max-workers.** Both
numbers are shown in the log line at the start of every `extract` run
so the ceiling is never a surprise. This forum has rate-limited/
blocked before — raise these deliberately and watch `archive.log` (or
console with `--verbose`) for `"pausing ALL workers"` messages, which
mean the current combination is too aggressive.

Retries are split two ways: transport-level failures (connection
resets, 500/502/504) are retried automatically via `HTTPAdapter` +
`urllib3.util.Retry`. `429`/`503` are handled separately, because
those need to pause *every* worker sharing the session, not just retry
the one throttled request — something urllib3's `Retry` has no way to
coordinate across threads.

### `build-indexes`

Rebuilds `categories/index.html` and a category's `index.html` by
scanning the filesystem for what's actually archived — safe to run any
time, and what makes resumed/partial `extract` runs still produce a
correct listing.

### `stats`

Page-count distribution (bucketed) plus a per-category thread-count
table:

```bash
python3 archive.py stats
python3 archive.py stats --category-id 59
python3 archive.py stats --buckets 10,50,100,500,1000,5000
python3 archive.py stats --sort-by name
```

### `wayback-submit`

Submits every page of every thread in a category (or `--all`) to
`web.archive.org/save`, using `total_pages` to expand each thread into
its full page list rather than just page 1.

```bash
pip install savepagenow
export SAVEPAGENOW_ACCESS_KEY=...      # from https://archive.org/account/s3.php
export SAVEPAGENOW_SECRET_KEY=...

python3 archive.py wayback-submit --category-id 59
python3 archive.py wayback-submit --all
```

## Search (Pagefind)

```bash
pip install pagefind
python3 -m pagefind --site archive
```

Indexes every page under `archive/`, writing the index + UI assets to
`archive/pagefind/`. Nav bars, the page-list footer, and the source
link are already marked `data-pagefind-ignore` in the generated HTML,
so search snippets show actual post content rather than repeated site
chrome. The category/thread listing pages are excluded from the index
entirely (pure navigation, not forum content).

`archive/index.html` is the landing page with the search box — it's
standalone and hand-maintained, not touched by any script.

## Logging

Console output stays close to plain print-style output. Everything
also goes to `archive.log` with full timestamps/levels, so an
unattended run can be reviewed afterward. `--verbose` on any
`archive.py` command bumps both to debug level.

## Putting the archive on GitHub

Even lean, a full-forum archive could reach several GB. Consider one
repo per major section, and commit in batches (per category) rather
than one huge push.
