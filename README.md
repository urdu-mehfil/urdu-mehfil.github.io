# Archiving urduweb.org/mehfil (XenForo forum)

## Dependencies

- python

## Python dependencies

More detail in requirements.txt. it is best to use virtualenv and pip to handle these. you need to install the following:

- beautifulsoup4
- lxml
- pagefind
- requests


## Testing Installation

1. Run `python -m venv env`.

        cd /path/to/urdu-mehfil && python -m venv ./venv/

2. Activate the virtualenv.

        source ./venv/bin/activate

3. Install dependencies through `pip`.

        pip install -r requirements.txt

## Confirmed URL structure (checked against a live thread page):

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

Three scripts, run locally (Claude's sandbox can't reach urduweb.org).
All are polite/rate-limited by default — please don't remove the delays.

## Step 1 — fast discovery (`1_discover.py`)

```bash
python3 1_discover.py --no-page-counts
```

Writes `categories.csv` (every subforum) and `topics.csv` (every
thread: category, id, title, slug, page-1 url) with `total_pages` left
blank for now. This is the cheap pass — just listing pages, no
per-thread requests — so it's fine to run for the whole site.

## Step 2 — backfill page counts, resumable

```bash
python 1_discover.py --fill-page-counts
```

This is the part that was taking a while, now split out and resumable.
It reads `topics.csv`, visits each thread once to read its real page
count off the pagination nav, and writes the result back into the same
CSV — used as its own progress marker, so you can stop and restart
freely.

```bash
# see where you stand — no network calls
python3 1_discover.py --fill-page-counts topics.csv --progress

# backfill everything still missing (safe to Ctrl+C any time)
python3 1_discover.py --fill-page-counts topics.csv

# or work through it one category at a time
python3 1_discover.py --fill-page-counts topics.csv --category-id 59

# fix/backfill just one thread
python3 1_discover.py --fill-page-counts topics.csv --thread-id 2080
```

How the resume works: progress is saved back into `topics.csv` itself
every 20 threads by default (`--save-every N` to change that), and
again on exit — including on Ctrl+C. Any row that already has a
`total_pages` value is skipped on the next run, so re-running the same
command just continues from wherever it stopped. No separate state
file needed.

`--progress` prints a per-category done/remaining table without
making any requests, e.g.:

```
    ID  Category                          Done / Total
    59  بزم سخن                            1200 / 26800
    34  متفرقات                             300 / 300
------------------------------------------------------------
 TOTAL                                     1500 / 27100  (5.5%)
```

Good way to decide which category to prioritize finishing next.

## Step 3 — extract a category

```bash
python3 2_extract_category.py --category "بزم سخن"
# or:
python3 2_extract_category.py --category-id 59
```

Reads the matching rows from `topics.csv`, fetches **every page** of
every thread in that category (using `total_pages` — rows still
missing it are treated as 1 page, so it's worth running step 2 for a
category before extracting it), and writes lean, content-only HTML to:

```
archive/<category>/<thread-slug>/page-1.html
archive/<category>/<thread-slug>/page-2.html
...
```

## Step 4 (optional) — back it up on the Wayback Machine

```bash
pip install savepagenow
export SAVEPAGENOW_ACCESS_KEY=...      # from https://archive.org/account/s3.php
export SAVEPAGENOW_SECRET_KEY=...

python3 3_wayback_submit.py --category "بزم سخن"
python3 3_wayback_submit.py --all       # every thread in topics.csv
```

Expands each thread into all of its page URLs via `total_pages` before
submitting to `web.archive.org/save`. Same caveat as step 3: threads
without a filled-in `total_pages` only submit page 1.

## Putting the local archive on GitHub

- Even lean, a full-forum archive could reach several GB. Consider one
  repo per major section, and commit in batches (per category) rather
  than one huge push.
